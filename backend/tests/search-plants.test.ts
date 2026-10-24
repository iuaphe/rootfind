import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseQuery } from '../supabase/functions/search-plants/query.ts';
import { createHandler } from '../supabase/functions/search-plants/handler.ts';

test('defaults, repeated options, false booleans, FIPS, and normalized name', () => {
  assert.deepEqual(parseQuery(new URLSearchParams('search=Asclepias++tuberosa&county=00123&county=12095&thorns=false&sun=full+sun&sun=part+shade')), {
    page: 1, pageSize: 25, search: 'Asclepias tuberosa', county: ['00123', '12095'], thorns: false,
    sun: ['full sun', 'part shade'],
  });
});

test('rejects unknown, invalid, duplicate, and contradictory filters', () => {
  for (const query of [
    'native=true', 'thorns=1', 'sun=mostly+sun', 'county=Orange', 'page=0', 'pageSize=101',
    'minHeight=-1', 'minHeight=10&maxHeight=2', 'search=a&search=b', 'misc=', 'water=high,low',
    'toString=x', '__proto__=x',
  ]) assert.throws(() => parseQuery(new URLSearchParams(query)), undefined, query);
});

test('search punctuation stays literal data', () => {
  const search = "x%_' OR true; --";
  assert.equal(parseQuery(new URLSearchParams({ search })).search, search);
});

const settings = { url: 'https://example.supabase.co', anonKey: 'public-key' };
test('forwards token and validated JSON, including empty pages and totals', async () => {
  const payload = { data: [], total: 5, page: 3, pageSize: 25 };
  const handler = createHandler(settings, async (url, init) => {
    assert.equal(url, settings.url + '/rest/v1/rpc/search_plants');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer caller-token');
    assert.deepEqual(JSON.parse(init?.body as string), { p_filters: { page: 3, pageSize: 25, edible: false } });
    return Response.json(payload);
  });
  const result = await handler(new Request('https://edge.test/?page=3&edible=false', { headers: { Authorization: 'Bearer caller-token' } }));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), payload);
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), '*');
});

test('preflight, method, auth, and invalid query fail before database access', async () => {
  const handler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  assert.equal((await handler(new Request('https://edge.test', { method: 'OPTIONS' }))).status, 204);
  assert.equal((await handler(new Request('https://edge.test', { method: 'POST' }))).status, 405);
  assert.equal((await handler(new Request('https://edge.test'))).status, 401);
  assert.equal((await handler(new Request('https://edge.test/?edible=maybe', { headers: { Authorization: 'Bearer x' } }))).status, 400);
});

test('database errors are sanitized', async () => {
  for (const [upstream, expected] of [[401, 401], [403, 403], [500, 502]]) {
    const handler = createHandler(settings, async () => new Response('private database detail', { status: upstream }));
    const result = await handler(new Request('https://edge.test', { headers: { Authorization: 'Bearer x' } }));
    assert.equal(result.status, expected);
    assert.doesNotMatch(await result.text(), /private database detail/);
  }
});
