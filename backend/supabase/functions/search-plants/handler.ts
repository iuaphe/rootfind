import { InvalidQuery, parseQuery } from './query.ts';

type Settings = { url: string; anonKey: string };

const RATE_WINDOW_MS = 60_000;
const RATE_LIMIT = 120;
const MAX_RATE_LIMIT_BUCKETS = 5_000;
const requestsByIp = new Map<string, { count: number; resetAt: number }>();

function clientIp(request: Request) {
  return request.headers.get('cf-connecting-ip')?.trim()
    || request.headers.get('x-real-ip')?.trim()
    || request.headers.get('x-forwarded-for')?.split(',')[0]?.trim()
    || '';
}

function isRateLimited(ip: string, now = Date.now()) {
  if (!ip) return false;
  const current = requestsByIp.get(ip);
  if (!current || current.resetAt <= now) {
    requestsByIp.delete(ip);
    if (requestsByIp.size >= MAX_RATE_LIMIT_BUCKETS) {
      for (const [key, bucket] of requestsByIp) {
        if (bucket.resetAt <= now) requestsByIp.delete(key);
      }
      while (requestsByIp.size >= MAX_RATE_LIMIT_BUCKETS) {
        const oldest = requestsByIp.keys().next().value;
        if (oldest === undefined) break;
        requestsByIp.delete(oldest);
      }
    }
    requestsByIp.set(ip, { count: 1, resetAt: now + RATE_WINDOW_MS });
    return false;
  }
  current.count += 1;
  return current.count > RATE_LIMIT;
}

// Public plant search returns ID arrays; retain the best-effort per-runtime throttle.
export function createHandler(settings: Settings, fetcher: typeof fetch = fetch) {
  return async (request: Request): Promise<Response> => {
    const headers = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    };
    const json = (body: unknown, status = 200, extraHeaders: Record<string, string> = {}) =>
      new Response(JSON.stringify(body), { status, headers: { ...headers, ...extraHeaders } });
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    if (request.method !== 'GET') return new Response(JSON.stringify({ error: 'Use GET' }), {
      status: 405, headers: { ...headers, Allow: 'GET, OPTIONS' },
    });
    try {
      if (request.url.length > 8192) throw new InvalidQuery('Query is too long');
      if (isRateLimited(clientIp(request))) {
        return new Response(JSON.stringify({ error: 'Too many searches; try again shortly' }), {
          status: 429, headers: { ...headers, 'Retry-After': '60' },
        });
      }
      const filters = parseQuery(new URL(request.url).searchParams);
      if (!settings.url || !settings.anonKey) throw new Error('Supabase environment is missing');
      const result = await fetcher(`${settings.url}/rest/v1/rpc/search_plants`, {
        method: 'POST',
        headers: { apikey: settings.anonKey, Authorization: `Bearer ${settings.anonKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ p_filters: filters }),
        signal: AbortSignal.timeout(10000),
      });
      if (!result.ok) {
        console.error('search_plants RPC failed', result.status);
        return json({ error: result.status === 401 || result.status === 403 ? 'Search access denied' : 'Plant search failed' },
          result.status === 401 || result.status === 403 ? result.status : 502);
      }
      return json(await result.json(), 200, { 'Cache-Control': 'public, max-age=30, s-maxage=60' });
    } catch (error) {
      if (error instanceof InvalidQuery) return json({ error: error.message }, 400);
      console.error('Plant search failed', error instanceof Error ? error.name : 'Unknown error');
      return json({ error: 'Plant search unavailable' }, 500);
    }
  };
}
