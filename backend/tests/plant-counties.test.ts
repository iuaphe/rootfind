import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { parseInput } from '../supabase/functions/plant-counties/input.ts';
import { createHandler } from '../supabase/functions/plant-counties/handler.ts';

test('accepts ordered lists including duplicates and an empty list', () => {
  assert.deepEqual(parseInput({ plantIds: [3, 1, 3] }), [3, 1, 3]);
  assert.deepEqual(parseInput({ plantIds: [] }), []);
  assert.equal(parseInput({ plantIds: Array(1000).fill(2147483647) }).length, 1000);
  for (const input of [null, [], {}, { plantIds: '1,2' }, { plantIds: [0] }, { plantIds: [-1] },
    { plantIds: [1.5] }, { plantIds: ['1'] }, { plantIds: [null] }, { plantIds: [2147483648] },
    { plantIds: [NaN] }, { plantIds: Array(1001).fill(1) }, { plantIds: [], extra: true }]) {
    assert.throws(() => parseInput(input));
  }
});

const settings = { url: 'https://example.supabase.co', anonKey: 'anon-key' };
const request = (body: string) => new Request('https://edge.test/plant-counties', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
});

test('public HTTP request forwards the list using anon credentials and returns nested arrays', async () => {
  const handler = createHandler(settings, async (url, init) => {
    assert.equal(url, settings.url + '/rest/v1/rpc/plant_counties');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer anon-key');
    assert.deepEqual(JSON.parse(init?.body as string), { p_plant_ids: [3, 1, 3] });
    return Response.json([[], [12095, 51059], []]);
  });
  const response = await handler(request('{"plantIds":[3,1,3]}'));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), [[], [12095, 51059], []]);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
});

test('rejects unsupported methods, malformed JSON, oversized bodies, and invalid inputs before fetching', async () => {
  const handler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  assert.equal((await handler(new Request('https://edge.test', { method: 'OPTIONS' }))).status, 204);
  assert.equal((await handler(new Request('https://edge.test'))).status, 405);
  assert.equal((await handler(new Request('https://edge.test', { method: 'POST', body: '{}' }))).status, 415);
  for (const body of ['{', '{}', '{"plantIds":["1"]}', ' '.repeat(16385)]) {
    assert.equal((await handler(request(body))).status, 400);
  }
});

test('upstream failures are sanitized', async () => {
  for (const [status, expected] of [[401, 401], [403, 403], [500, 502]]) {
    const handler = createHandler(settings, async () => new Response('private details', { status }));
    const result = await handler(request('{"plantIds":[1]}'));
    assert.equal(result.status, expected);
    assert.doesNotMatch(await result.text(), /private details/);
  }
  const handler = createHandler(settings, async () => { throw new Error('private network details'); });
  assert.equal((await handler(request('{"plantIds":[1]}'))).status, 500);
});

test('throttles batches per forwarded IP before reading the body or calling the database, then resets', async (t) => {
  let now = 1_000_000;
  t.mock.method(Date, 'now', () => now);
  let databaseCalls = 0;
  const handler = createHandler(settings, async () => {
    databaseCalls += 1;
    return Response.json([]);
  });
  const body = JSON.stringify({ plantIds: Array(1000).fill(1) });
  const batch = (headers: Record<string, string>, text = body) => new Request('https://edge.test/plant-counties', {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: text,
  });
  for (let i = 0; i < 120; i++) {
    assert.equal((await handler(batch({ 'x-forwarded-for': '192.0.2.10, 192.0.2.20' }))).status, 200);
  }
  // A new handler in the same runtime shares the same counters.
  const secondHandler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  const blocked = await secondHandler(batch({ 'x-forwarded-for': '192.0.2.10, 192.0.2.99' }, '{invalid JSON'));
  assert.equal(blocked.status, 429);
  assert.equal(blocked.headers.get('Retry-After'), '60');
  assert.equal(blocked.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal(blocked.headers.get('Cache-Control'), 'no-store');
  assert.equal(databaseCalls, 120);
  assert.equal((await handler(batch({ 'x-real-ip': '192.0.2.10' }))).status, 429);
  assert.equal((await handler(batch({ 'cf-connecting-ip': '192.0.2.11', 'x-forwarded-for': '192.0.2.10' }))).status, 200);
  assert.equal(databaseCalls, 121, 'different clients have independent quotas');
  // Preflight requests do not consume or require quota.
  assert.equal((await handler(new Request('https://edge.test', {
    method: 'OPTIONS', headers: { 'x-real-ip': '192.0.2.10' },
  }))).status, 204);
  now += 60_000;
  assert.equal((await handler(batch({ 'cf-connecting-ip': '192.0.2.10' }))).status, 200);
  assert.equal(databaseCalls, 122);
});

test('Postgres returns county FIPS per input position without granting table access', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const name of (await readdir(dir)).filter((name) => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(name, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species) values ('Asclepias', 'tuberosa'), ('Quercus', 'alba'), ('Acer', 'rubrum');
      insert into public.county_info values (51059, 'Fairfax'), (12095, 'Orange'), (1001, 'Autauga');
      insert into public.plant_county values (51059, 1), (12095, 1), (1001, 2);
      set role anon;
    `);
    async function counties(ids: unknown) {
      const { rows } = await db.query<{ result: number[][] }>(
        'select public.plant_counties($1::integer[]) as result', [ids],
      );
      return rows[0].result;
    }
    assert.deepEqual(await counties([2, 1, 3, 999, 1]), [[1001], [12095, 51059], [], [], [12095, 51059]]);
    assert.deepEqual(await counties([]), []);
    assert.equal((await counties(Array(1000).fill(1))).length, 1000);
    for (const invalid of [null, [0], [-1], [null], [[1, 2], [2, 1]], Array(1001).fill(1)]) {
      await assert.rejects(counties(invalid), /positive integer plant IDs/);
    }
    await assert.rejects(db.query('select * from public.plant_county'), /permission denied/);
    await db.exec('reset role; set role authenticated;');
    assert.deepEqual(await counties([1]), [[12095, 51059]]);
  } finally {
    await db.close();
  }
});
