import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseQuery } from '../supabase/functions/search-plants/query.ts';
import { createHandler } from '../supabase/functions/search-plants/handler.ts';

test('normalizes scientific and common name searches', () => {
  assert.deepEqual(parseQuery(new URLSearchParams('search=++Asclepias++tuberosa++')), { search: 'Asclepias tuberosa' });
  assert.deepEqual(parseQuery(new URLSearchParams('search=butterfly+weed')), { search: 'butterfly weed' });
});

test('requires one nonempty name search and rejects other parameters', () => {
  for (const query of [
    '', 'search=', 'search=+++', 'search=a&search=b', 'search=oak&isWoody=true',
    'search=oak&page=1', 'search=oak&pageSize=10', 'toString=x', '__proto__=x',
    `search=${'a'.repeat(201)}`,
  ]) assert.throws(() => parseQuery(new URLSearchParams(query)), undefined, query);
});

test('search punctuation stays literal data', () => {
  const search = "x%_' OR true; --";
  assert.equal(parseQuery(new URLSearchParams({ search })).search, search);
});

const settings = { url: 'https://example.supabase.co', anonKey: 'public-key' };
test('public requests return only ID arrays and use anon database credentials', async () => {
  const payload = [1, 3];
  const handler = createHandler(settings, async (url, init) => {
    assert.equal(url, settings.url + '/rest/v1/rpc/search_plants');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer public-key');
    assert.deepEqual(JSON.parse(init?.body as string), { p_filters: { search: 'milkweed' } });
    return Response.json(payload);
  });
  const result = await handler(new Request('https://edge.test/?search=milkweed'));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), payload);
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal(result.headers.get('Cache-Control'), 'public, max-age=30, s-maxage=60');
});

test('caller credentials cannot elevate public search privileges', async () => {
  const handler = createHandler(settings, async (_url, init) => {
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer public-key');
    return Response.json([]);
  });
  assert.equal((await handler(new Request('https://edge.test/?search=oak', {
    headers: { Authorization: 'Bearer caller-token', apikey: 'caller-key' },
  }))).status, 200);
});

test('preflight, method, and invalid query fail before database access', async () => {
  const handler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  assert.equal((await handler(new Request('https://edge.test', { method: 'OPTIONS' }))).status, 204);
  assert.equal((await handler(new Request('https://edge.test', { method: 'POST' }))).status, 405);
  assert.equal((await handler(new Request('https://edge.test/?edible=maybe'))).status, 400);
});

test('database errors are sanitized', async () => {
  for (const [upstream, expected] of [[401, 401], [403, 403], [500, 502]]) {
    const handler = createHandler(settings, async () => new Response('private database detail', { status: upstream }));
    const result = await handler(new Request('https://edge.test/?search=oak', { headers: { Authorization: 'Bearer x' } }));
    assert.equal(result.status, expected);
    assert.doesNotMatch(await result.text(), /private database detail/);
  }
});
