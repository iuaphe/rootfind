import { InvalidQuery, parseQuery } from './query.ts';

type Settings = { url: string; anonKey: string };

// Forward the caller's token to PostgREST so database RLS remains in force.
export function createHandler(settings: Settings, fetcher: typeof fetch = fetch) {
  return async (request: Request): Promise<Response> => {
    const headers = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    };
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers });
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
    if (request.method !== 'GET') return new Response(JSON.stringify({ error: 'Use GET' }), {
      status: 405, headers: { ...headers, Allow: 'GET, OPTIONS' },
    });
    const authorization = request.headers.get('Authorization');
    if (!authorization || !/^Bearer \S+$/i.test(authorization)) return json({ error: 'Bearer token required' }, 401);
    try {
      if (request.url.length > 8192) throw new InvalidQuery('Query is too long');
      const filters = parseQuery(new URL(request.url).searchParams);
      if (!settings.url || !settings.anonKey) throw new Error('Supabase environment is missing');
      const result = await fetcher(`${settings.url}/rest/v1/rpc/search_plants`, {
        method: 'POST',
        headers: { apikey: settings.anonKey, Authorization: authorization, 'Content-Type': 'application/json' },
        body: JSON.stringify({ p_filters: filters }),
        signal: AbortSignal.timeout(10000),
      });
      if (!result.ok) {
        console.error('search_plants RPC failed', result.status);
        return json({ error: result.status === 401 || result.status === 403 ? 'Search access denied' : 'Plant search failed' },
          result.status === 401 || result.status === 403 ? result.status : 502);
      }
      return json(await result.json());
    } catch (error) {
      if (error instanceof InvalidQuery) return json({ error: error.message }, 400);
      console.error('Plant search failed', error instanceof Error ? error.name : 'Unknown error');
      return json({ error: 'Plant search unavailable' }, 500);
    }
  };
}
