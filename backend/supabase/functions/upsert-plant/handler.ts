export type Settings = {
  url: string;
  serviceRoleKey: string;
};

function getRoleFromToken(token: string): string | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonStr = decodeURIComponent(
      atob(base64).split('').map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join('')
    );
    const payload = JSON.parse(jsonStr);
    return payload?.role ?? null;
  } catch {
    return null;
  }
}

export function createHandler(settings: Settings, fetcher: typeof fetch = fetch) {
  return async (request: Request): Promise<Response> => {
    const headers: Record<string, string> = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    };

    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers });

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers });
    }

    if (request.method !== 'POST') {
      return json({ error: 'Use POST' }, 405);
    }

    try {
      const url = settings.url || Deno?.env?.get('SUPABASE_URL') || '';
      if (!url) {
        throw new Error('Supabase environment is missing SUPABASE_URL');
      }

      // Verify caller is authorized with service_role key or service_role JWT
      const authHeader = request.headers.get('authorization')?.replace(/^Bearer\s+/i, '').trim();
      const apiKeyHeader = request.headers.get('apikey')?.trim();
      const providedKey = authHeader || apiKeyHeader || '';

      const isMatch = settings.serviceRoleKey && (providedKey === settings.serviceRoleKey || authHeader === settings.serviceRoleKey);
      const isServiceRoleJwt = getRoleFromToken(providedKey) === 'service_role';

      if (!isMatch && !isServiceRoleJwt) {
        return json({ error: 'Unauthorized: Service role key required for plant upsert' }, 401);
      }

      const keyToUse = settings.serviceRoleKey || providedKey;

      const body = await request.json();
      if (!body || typeof body !== 'object') {
        return json({ error: 'Invalid JSON body: expected object or array' }, 400);
      }

      const isBatch = Array.isArray(body);
      const rpcName = isBatch ? 'upsert_plants_batch' : 'upsert_plant';

      const response = await fetcher(`${url}/rest/v1/rpc/${rpcName}`, {
        method: 'POST',
        headers: {
          apikey: keyToUse,
          Authorization: `Bearer ${keyToUse}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ p_payload: body }),
        signal: AbortSignal.timeout(15000),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`RPC ${rpcName} failed (${response.status}):`, errorText);
        return json({ error: 'Database upsert failed', details: errorText }, response.status >= 500 ? 502 : response.status);
      }

      const result = await response.json();
      return json(result, 200);
    } catch (err: unknown) {
      console.error('upsert-plant function error:', err instanceof Error ? err.message : String(err));
      return json({ error: err instanceof Error ? err.message : 'Internal error' }, 500);
    }
  };
}
