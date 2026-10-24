import { InvalidInput, parseInput } from './input.ts';

type Settings = { url: string; anonKey: string };
const MAX_BODY_BYTES = 16384;
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

// Same best-effort per-runtime throttle as search-plants; not a distributed limit.
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

async function readJson(request: Request): Promise<unknown> {
  if (!request.body) throw new InvalidInput('JSON body required');
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new InvalidInput('JSON body must not exceed 16 KiB');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new InvalidInput('Invalid JSON body');
  }
}

export function createHandler(settings: Settings, fetcher: typeof fetch = fetch) {
  return async (request: Request): Promise<Response> => {
    const headers = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    };
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers });
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    if (request.method !== 'POST') return new Response(JSON.stringify({ error: 'Use POST' }), {
      status: 405, headers: { ...headers, Allow: 'POST, OPTIONS' },
    });
    // Reject before reading the batch body or making a database request.
    if (isRateLimited(clientIp(request))) {
      return new Response(JSON.stringify({ error: 'Too many county lookups; try again shortly' }), {
        status: 429, headers: { ...headers, 'Retry-After': '60' },
      });
    }
    if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
      return json({ error: 'Use Content-Type: application/json' }, 415);
    }
    try {
      const plantIds = parseInput(await readJson(request));
      if (!settings.url || !settings.anonKey) throw new Error('Supabase environment is missing');
      const response = await fetcher(`${settings.url}/rest/v1/rpc/plant_counties`, {
        method: 'POST',
        headers: { apikey: settings.anonKey, Authorization: `Bearer ${settings.anonKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ p_plant_ids: plantIds }),
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        console.error('plant_counties RPC failed', response.status);
        const denied = response.status === 401 || response.status === 403;
        return json({ error: denied ? 'County lookup access denied' : 'County lookup failed' }, denied ? response.status : 502);
      }
      return json(await response.json());
    } catch (error) {
      if (error instanceof InvalidInput) return json({ error: error.message }, 400);
      console.error('County lookup failed', error instanceof Error ? error.name : 'Unknown error');
      return json({ error: 'County lookup unavailable' }, 500);
    }
  };
}
