import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { parseQuery } from '../supabase/functions/plant-details/input.ts';
import { createHandler } from '../supabase/functions/plant-details/handler.ts';

test('requires one positive 32-bit plantId and rejects other parameters', () => {
  assert.equal(parseQuery(new URLSearchParams('plantId=6')), 6);
  assert.equal(parseQuery(new URLSearchParams('plantId=2147483647')), 2147483647);
  for (const query of ['', 'plantId=', 'plantId=0', 'plantId=-1', 'plantId=1.5', 'plantId=abc',
    'plantId=2147483648', 'plantId=1&plantId=2', 'plantId=1&extra=true']) {
    assert.throws(() => parseQuery(new URLSearchParams(query)));
  }
});

const settings = { url: 'https://example.supabase.co', anonKey: 'anon-key' };
const url = 'https://edge.test/plant-details?plantId=6';

test('public lookup uses anon credentials and returns a single plant object', async () => {
  const plant = { plant_id: 6, genus: 'Asclepias', species: 'tuberosa', common_names: ['Butterfly weed'], county_fips: [12095] };
  const handler = createHandler(settings, async (target, init) => {
    assert.equal(target, settings.url + '/rest/v1/rpc/plant_details');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer anon-key');
    assert.deepEqual(JSON.parse(init?.body as string), { p_plant_id: 6 });
    return Response.json(plant);
  });
  const result = await handler(new Request(url, { headers: { Authorization: 'Bearer ignored-caller-token' } }));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), plant);
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), '*');
});

test('returns 404 for missing plants and validates requests before database access', async () => {
  const missing = createHandler(settings, async () => Response.json(null));
  const result = await missing(new Request(url));
  assert.equal(result.status, 404);
  assert.deepEqual(await result.json(), { error: 'Plant not found' });
  const handler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  assert.equal((await handler(new Request(url, { method: 'OPTIONS' }))).status, 204);
  assert.equal((await handler(new Request(url, { method: 'POST' }))).status, 405);
  assert.equal((await handler(new Request('https://edge.test/?plantId=0'))).status, 400);
});

test('sanitizes upstream failures', async () => {
  for (const [status, expected] of [[401, 401], [403, 403], [500, 502]]) {
    const handler = createHandler(settings, async () => new Response('private details', { status }));
    const result = await handler(new Request(url));
    assert.equal(result.status, expected);
    assert.doesNotMatch(await result.text(), /private details/);
  }
});

test('limits lookup requests before calling Postgres', async (t) => {
  let now = 2_000_000;
  t.mock.method(Date, 'now', () => now);
  let calls = 0;
  const handler = createHandler(settings, async () => { calls++; return Response.json({ plant_id: 6 }); });
  const request = () => new Request(url, { headers: { 'cf-connecting-ip': '192.0.2.60' } });
  for (let i = 0; i < 120; i++) assert.equal((await handler(request())).status, 200);
  const limited = await handler(request());
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('Retry-After'), '60');
  assert.equal(calls, 120);
  now += 60000;
  assert.equal((await handler(request())).status, 200);
});

test('Postgres returns all plant columns and linked arrays without multiplying records', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const name of (await readdir(dir)).filter((name) => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(name, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, life_cycle, foliage, minheight, maxheight,
        minwidth, maxwidth, is_woody, is_tree, edible, thorns, sun, water, image_link, misc)
      values ('Asclepias', 'tuberosa', 'perennial', 'deciduous', 10, 60, 20, 40, false,
        false, false, false, 'full sun', 'low', 'https://example.test/plant.jpg', array['fragrant']),
        ('Quercus', 'alba', null, null, null, null, null, null, null, null, null, null, null, null, null, null);
      insert into public.county_info values (51059, 'Fairfax'), (12095, 'Orange');
      insert into public.plant_county values (51059, 1), (12095, 1);
      insert into public.common_names values ('Butterfly weed'), ('Butterfly milkweed'), ('Unlinked');
      insert into public.common_plant values ('Butterfly weed', 1), ('Butterfly milkweed', 1);
      set role anon;
    `);
    const lookup = async (id: number | null) => (await db.query<{ result: Record<string, unknown> | null }>(
      'select public.plant_details($1::integer) as result', [id],
    )).rows[0].result;
    assert.deepEqual(await lookup(1), {
      plant_id: 1, genus: 'Asclepias', species: 'tuberosa', life_cycle: 'perennial', foliage: 'deciduous',
      minheight: 10, maxheight: 60, minwidth: 20, maxwidth: 40, is_woody: false, is_tree: false,
      edible: false, thorns: false, sun: 'full sun', water: 'low', image_link: 'https://example.test/plant.jpg',
      misc: ['fragrant'], common_names: ['Butterfly milkweed', 'Butterfly weed'], county_fips: [12095, 51059],
    });
    const noLinks = await lookup(2);
    assert.deepEqual(noLinks?.common_names, []);
    assert.deepEqual(noLinks?.county_fips, []);
    assert.equal(noLinks?.misc, null);
    assert.equal(await lookup(999), null);
    for (const id of [0, -1, null]) await assert.rejects(lookup(id), /positive integer/);
    await assert.rejects(db.query('select * from public.plants'), /permission denied/);
    await db.exec('reset role; set role authenticated;');
    assert.equal((await lookup(1))?.plant_id, 1);
  } finally {
    await db.close();
  }
});
