import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { parseQuery } from '../supabase/functions/count-plants-by-county/query.ts';
import { createHandler } from '../supabase/functions/count-plants-by-county/handler.ts';

const settings = { url: 'https://example.supabase.co', anonKey: 'anon-key' };
const counts = [{ fips: 1001, plant_count: 2 }, { fips: 12095, plant_count: 0 }];

test('county count HTTP requests share search filters and use anonymous database credentials', async () => {
  const handler = createHandler(settings, async (url, init) => {
    assert.equal(url, settings.url + '/rest/v1/rpc/count_plants_by_county');
    assert.equal(init?.method, 'POST');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer anon-key');
    assert.equal(new Headers(init?.headers).get('apikey'), 'anon-key');
    assert.deepEqual(JSON.parse(init?.body as string), { p_filters: {
      search: 'butterfly weed', lifeCycle: ['perennial', 'annual'], water: ['medium', 'low'],
      edible: true, minHeight: 12, maxHeight: 72,
    } });
    assert.ok(init?.signal);
    return Response.json(counts);
  });
  const query = new URLSearchParams({ search: '  butterfly   weed ', lifeCycle: 'perennial,annual',
    water: 'med,medium,low', edible: 'true', minHeight: '12', maxHeight: '72' });
  const response = await handler(new Request(`https://edge.test/?${query}`, {
    headers: { Authorization: 'Bearer caller-token', apikey: 'caller-key' },
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), counts);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal(response.headers.get('Cache-Control'), 'public, max-age=30, s-maxage=60');
});

test('county counts allow empty filters and reject pagination, invalid, and repeated filters', async () => {
  assert.deepEqual(parseQuery(new URLSearchParams()), {});
  const handler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  for (const query of ['page=1', 'pageSize=50', 'page=1&page=2', 'unknown=true',
    'edible=maybe', 'lifeCycle=', 'water=bog', 'water=low&water=high', 'minHeight=01',
    'minWidth=21&maxWidth=20', 'search=a&search=b', `search=${'a'.repeat(201)}`,
    `search=${'a'.repeat(8192)}`]) {
    const response = await handler(new Request(`https://edge.test/?${query}`));
    assert.equal(response.status, 400, query);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  }
  const preflight = await handler(new Request('https://edge.test', { method: 'OPTIONS' }));
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('Access-Control-Allow-Methods'), 'GET, OPTIONS');
  const unsupported = await handler(new Request('https://edge.test', { method: 'POST' }));
  assert.equal(unsupported.status, 405);
  assert.equal(unsupported.headers.get('Allow'), 'GET, OPTIONS');
  let calls = 0;
  const browse = createHandler(settings, async (_url, init) => {
    calls++;
    assert.deepEqual(JSON.parse(init?.body as string), { p_filters: {} });
    return Response.json([]);
  });
  assert.deepEqual(await (await browse(new Request('https://edge.test'))).json(), []);
  assert.equal(calls, 1);
});

test('county count HTTP failures are sanitized and never cached', async () => {
  for (const [upstream, expected] of [[401, 401], [403, 403], [400, 502], [500, 502]]) {
    const handler = createHandler(settings, async () => new Response('private database detail', { status: upstream }));
    const response = await handler(new Request('https://edge.test'));
    assert.equal(response.status, expected);
    assert.doesNotMatch(await response.text(), /private database detail/);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  }
  for (const fetcher of [async () => { throw new Error('private network detail'); },
    async () => new Response('not json')]) {
    const response = await createHandler(settings, fetcher)(new Request('https://edge.test'));
    assert.equal(response.status, 500);
    assert.deepEqual(await response.json(), { error: 'County count unavailable' });
  }
  const missingSettings = createHandler({ url: '', anonKey: '' }, async () => { throw new Error('Must not fetch'); });
  assert.equal((await missingSettings(new Request('https://edge.test'))).status, 500);
});

test('county counts throttle before querying and reset after the rate window', async (t) => {
  let now = 1_000_000;
  t.mock.method(Date, 'now', () => now);
  let calls = 0;
  const handler = createHandler(settings, async () => { calls++; return Response.json(counts); });
  const request = (ip = '192.0.2.50') => new Request('https://edge.test', { headers: { 'x-forwarded-for': ip } });
  for (let i = 0; i < 120; i++) assert.equal((await handler(request())).status, 200);
  const blocked = await handler(request());
  assert.equal(blocked.status, 429);
  assert.equal(blocked.headers.get('Retry-After'), '60');
  assert.equal(blocked.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal(calls, 120);
  const secondHandler = createHandler(settings, async () => { throw new Error('Must not fetch'); });
  assert.equal((await secondHandler(request())).status, 429);
  assert.equal((await handler(request('192.0.2.51'))).status, 200);
  assert.equal((await handler(new Request('https://edge.test', {
    method: 'OPTIONS', headers: { 'x-forwarded-for': '192.0.2.50' },
  }))).status, 204);
  now += 60_000;
  assert.equal((await handler(request())).status, 200);
  assert.equal(calls, 122);
});

test('Postgres county counts match unpaginated search and preserve the existing search contract', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    const migration = '202610240010_count_plants_by_county.sql';
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql') && name < migration).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, life_cycle, foliage, sun, water,
        is_woody, is_tree, edible, thorns, minheight, maxheight, minwidth, maxwidth)
      values ('Asclepias', 'tuberosa', 'perennial', 'deciduous', 'full sun', array['low','medium']::public.water_level_enum[],
          false, false, true, false, 12, 60, 20, 40),
        ('Asclepias', 'incarnata', 'annual', 'evergreen', 'full shade', array['high']::public.water_level_enum[],
          false, false, false, true, 30, 100, 30, 50),
        ('Quercus', 'alba', 'perennial', 'deciduous', 'part shade', array['medium']::public.water_level_enum[],
          true, true, false, null, 100, 500, 100, 400);
      insert into public.plants (genus, species) values ('Unknown', 'traits'), ('Unlinked', 'plant');
      insert into public.plants (genus, species) select 'Bulk', 'species' || i from generate_series(1, 105) i;
      insert into public.county_info values (1001, 'Autauga'), (12095, 'Orange'), (51059, 'Fairfax'), (1003, 'Empty');
      insert into public.plant_county values (1001,1), (12095,1), (12095,2), (51059,3), (1001,4);
      insert into public.plant_county select 1001, plant_id from public.plants where genus = 'Bulk';
      insert into public.common_names values ('Butterfly milkweed'), ('Butterfly weed'), ('Swamp milkweed'), ('Unused name');
      insert into public.common_plant values ('Butterfly milkweed',1), ('Butterfly weed',1), ('Swamp milkweed',2);
    `);
    const links = (await db.query<{ fips: number; plant_id: number }>('select * from public.plant_county')).rows;
    const fips = [1001, 1003, 12095, 51059];
    const filters = [{}, { search: 'BUTTERFLY' }, { search: '  Asclepias   tuberosa  ' },
      { search: "x%_' OR true; --" }, { search: 'Unused name' }, { search: 'unlinked' }, { search: 'Bulk' },
      { lifeCycle: ['annual', 'perennial'] }, { lifeCycle: ['perennial'], water: ['low'] },
      { foliage: ['evergreen'], sun: ['full shade'] }, { sun: ['full sun', 'part shade'] },
      { water: ['med'] }, { water: ['medium'] }, { water: ['low', 'high'] },
      { isWoody: true, isTree: true }, { isWoody: false, isTree: false },
      { edible: true }, { edible: false }, { thorns: false }, { thorns: true },
      { minHeight: 60, maxHeight: 60 }, { minHeight: 61, maxHeight: 99 },
      { minWidth: 40, maxWidth: 40 }, { minWidth: 51, maxWidth: 99 },
      { search: 'butterfly', water: ['low'], edible: true, minHeight: 12, maxWidth: 40 }];
    const search = async (filters: unknown) => (await db.query<{ result: number[] }>(
      'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)],
    )).rows[0].result;
    const count = async (filters: unknown) => (await db.query<{ result: typeof counts }>(
      'select public.count_plants_by_county($1::jsonb) as result', [JSON.stringify(filters)],
    )).rows[0].result;
    const baseline: number[][] = [];
    for (const filter of filters) {
      const ids: number[] = [];
      for (let page = 1; ; page++) {
        const batch = await search({ ...filter, page, pageSize: 100 });
        ids.push(...batch);
        if (batch.length < 100) break;
      }
      baseline.push(ids);
    }
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql') && name >= migration).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec('set role anon;');
    for (const [i, filter] of filters.entries()) {
      const expected = fips.map(fips => ({ fips,
        plant_count: links.filter(link => link.fips === fips && baseline[i].includes(link.plant_id)).length,
      }));
      assert.deepEqual(await count(filter), expected, JSON.stringify(filter));
      assert.deepEqual(await search({ ...filter, pageSize: 100 }), baseline[i].slice(0, 100));
    }
    assert.equal((await search({})).length, 50);
    assert.equal((await count({}))[0].plant_count, 107, 'counts include every match beyond the first search page');
    assert.deepEqual(await search({ search: 'Bulk', page: 3, pageSize: 50 }), [106, 107, 108, 109, 110]);
    assert.deepEqual(await search({ search: 'Bulk', page: 4, pageSize: 50 }), []);
    assert.deepEqual((await db.query<{ result: typeof counts }>('select public.count_plants_by_county() as result')).rows[0].result,
      await count({}), 'omitted filters default to all plants');
    for (const invalid of [null, [], 'search', { search: null }, { search: 'a'.repeat(201) },
      { isWoody: 'true' }, { edible: null }, { thorns: 1 }, { lifeCycle: [] }, { lifeCycle: ['unknown'] },
      { foliage: 'deciduous' }, { sun: [null] }, { water: ['low', null] }, { water: ['bog'] },
      { minHeight: -1 }, { minWidth: 1.5 }, { maxWidth: 32768 }, { minHeight: '12' },
      { minHeight: 30, maxHeight: 20 }, { minWidth: 30, maxWidth: 20 }, { unknown: true }]) {
      await assert.rejects(count(invalid), { code: '22023' }, JSON.stringify(invalid));
      await assert.rejects(search(invalid), { code: '22023' }, JSON.stringify(invalid));
    }
    for (const invalid of [{ page: 1 }, { pageSize: 50 }]) {
      await assert.rejects(count(invalid), { code: '22023' });
    }
    for (const invalid of [{ page: 0 }, { page: 1001 }, { page: null }, { pageSize: 101 }, { pageSize: '10' }]) {
      await assert.rejects(search(invalid), { code: '22023' });
    }
    for (const table of ['plants', 'plant_county', 'county_info', 'common_plant', 'common_names']) {
      await assert.rejects(db.query(`select * from public.${table}`), /permission denied/);
    }
    await assert.rejects(db.query("select * from rootfind_private.matching_plant_ids('{}')"), /permission denied/);
    await db.exec('reset role; set role authenticated;');
    assert.deepEqual(await count({ search: 'butterfly' }), [
      { fips: 1001, plant_count: 1 }, { fips: 1003, plant_count: 0 },
      { fips: 12095, plant_count: 1 }, { fips: 51059, plant_count: 0 },
    ], 'multiple common names never multiply counts');
    await db.exec(`reset role; insert into public.county_info select i, 'County ' || i from generate_series(20000,21000) i; set role anon;`);
    assert.equal((await count({})).length, 1005, 'the JSON response covers more than 1000 counties');
    await db.exec('reset role; delete from public.county_info; set role anon;');
    assert.deepEqual(await count({}), []);
    await assert.rejects(count({ water: [] }), { code: '22023' }, 'validate even when no counties exist');
  } finally {
    await db.close();
  }
});
