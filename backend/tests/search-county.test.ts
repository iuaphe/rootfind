import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { parseQuery } from '../supabase/functions/search-plants/query.ts';
import { createHandler } from '../supabase/functions/search-plants/handler.ts';
import { parseQuery as parseCountQuery } from '../supabase/functions/count-plants-by-county/query.ts';

test('search accepts one county FIPS, preserves traits, and forwards it to the RPC', async () => {
  assert.deepEqual(parseQuery(new URLSearchParams('county=01001&water=med&edible=true&page=2&pageSize=20')),
    { county: 1001, water: ['medium'], edible: true, page: 2, pageSize: 20 });
  assert.deepEqual(parseQuery(new URLSearchParams('county=99999')), { county: 99999 });
  for (const county of ['', '0', '00000', '-1', '1.5', '1e3', '100000', ' 1001', '1001,1003', 'abc']) {
    assert.throws(() => parseQuery(new URLSearchParams({ county })), undefined, county);
  }
  assert.throws(() => parseQuery(new URLSearchParams('county=1001&county=1003')));
  assert.throws(() => parseCountQuery(new URLSearchParams('county=1001')), /Unknown parameter/);
  const handler = createHandler({ url: 'https://example.supabase.co', anonKey: 'anon-key' }, async (_url, init) => {
    assert.deepEqual(JSON.parse(init?.body as string), {
      p_filters: { county: 1001, search: 'oak', edible: true, pageSize: 20 },
    });
    return Response.json([3]);
  });
  const response = await handler(new Request('https://edge.test/?county=01001&search=oak&edible=true&pageSize=20'));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), [3]);
  const invalid = createHandler({ url: 'https://example.supabase.co', anonKey: 'anon-key' }, async () => {
    throw new Error('Must not fetch');
  });
  assert.equal((await invalid(new Request('https://edge.test/?county=bad'))).status, 400);
});

test('Postgres applies county and trait filters together before pagination', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, edible, life_cycle, water, minheight, maxheight)
        select 'Quercus', 'species' || i, i % 2 = 0, 'perennial', array['low']::public.water_level_enum[], 12, 60
        from generate_series(1,125) i;
      insert into public.county_info values (1001, 'Autauga'), (1003, 'Baldwin'), (1005, 'Empty');
      insert into public.plant_county select 1001, plant_id from public.plants where plant_id > 60;
      insert into public.plant_county select 1003, plant_id from public.plants where plant_id <= 75;
      insert into public.common_names values ('White oak'), ('White oak alias');
      insert into public.common_plant values ('White oak', 62), ('White oak alias', 62);
      set role anon;
    `);
    const search = async (filters: unknown) => (await db.query<{ result: number[] }>(
      'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)],
    )).rows[0].result;
    assert.deepEqual(await search({ county: 1001, pageSize: 20 }), Array.from({ length: 20 }, (_, i) => i + 61));
    assert.deepEqual(await search({ county: 1001, pageSize: 20, page: 4 }), [121, 122, 123, 124, 125]);
    assert.deepEqual(await search({ county: 1001, pageSize: 20, page: 5 }), []);
    assert.deepEqual(await search({ county: 1003, edible: true, pageSize: 100 }),
      Array.from({ length: 37 }, (_, i) => (i + 1) * 2));
    assert.deepEqual(await search({ county: 1001, search: 'WHITE OAK', edible: true,
      water: ['low'], lifeCycle: ['perennial'], minHeight: 60, maxHeight: 60 }), [62]);
    assert.deepEqual(await search({ county: 1001, search: 'WHITE OAK', edible: false }), []);
    assert.deepEqual(await search({ county: 1001, search: 'WHITE OAK', water: ['high'] }), []);
    assert.deepEqual(await search({ county: 1001, minHeight: 61 }), []);
    assert.deepEqual(await search({ county: 1005 }), []);
    assert.deepEqual(await search({ county: 99999 }), []);
    assert.deepEqual(await search({ pageSize: 1 }), [1], 'county stays optional');
    const { rows } = await db.query<{ counts: { fips: number; plant_count: number }[] }>(
      'select public.count_plants_by_county($1::jsonb) as counts', [JSON.stringify({ edible: true })],
    );
    for (const { fips, plant_count } of rows[0].counts) {
      const ids = await search({ county: fips, edible: true, pageSize: 100 });
      assert.equal(ids.length, plant_count, 'selected county matches its heatmap count');
    }
    for (const county of [null, '1001', [], {}, true, 0, -1, 1.5, 100000]) {
      await assert.rejects(search({ county }), { code: '22023' }, JSON.stringify(county));
    }
    await assert.rejects(db.query('select * from public.plant_county'), /permission denied/);
    await assert.rejects(db.query("select public.count_plants_by_county('{\"county\":1001}')"), { code: '22023' });
    await db.exec('reset role; set role authenticated;');
    assert.deepEqual(await search({ county: 1001, search: 'White oak' }), [62]);
  } finally {
    await db.close();
  }
});
