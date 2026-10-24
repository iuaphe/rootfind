import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('search after enrichment uses water-array membership and bounds every ID page', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const migration = async (name: string) => db.exec(await readFile(new URL(`../supabase/migrations/${name}`, import.meta.url), 'utf8'));
    await migration('202610240001_plants.sql');
    await migration('20261024000701_search_filters.sql');
    await db.exec(`insert into public.plants(genus,species,water,is_woody)
      values ('Asclepias','tuberosa','low',false), ('Salix','alba','high',true),
        ('Acer','rubrum','med',true), ('Unknown','water',null,null);`);
    await migration('202610240008_mobot_enrichment.sql');
    await migration('202610240009_search_water_pagination.sql');
    await db.exec("update public.plants set water=array['low','medium']::public.water_level_enum[] where plant_id=3; set role anon;");
    const search = async (filters: Record<string, unknown>) => (await db.query<{ ids: number[] }>(
      'select public.search_plants($1::jsonb) as ids', [JSON.stringify(filters)],
    )).rows[0].ids;
    assert.deepEqual(await search({ water: ['med'] }), [3]);
    assert.deepEqual(await search({ water: ['medium'] }), [3]);
    assert.deepEqual(await search({ water: ['low'] }), [1, 3]);
    assert.deepEqual(await search({ water: ['high'] }), [2]);
    assert.deepEqual(await search({ water: ['low', 'high'] }), [1, 2, 3]);
    assert.deepEqual(await search({ water: ['medium'], isWoody: true }), [3]);
    assert.deepEqual(await search({ water: ['medium'], search: 'Asclepias' }), []);
    assert.deepEqual(await search({ search: 'unknown', water: ['low', 'medium', 'high'] }), []);
    for (const [key, valid] of Object.entries({ lifeCycle: 'perennial', foliage: 'deciduous', sun: 'full sun', water: 'low' })) {
      for (const values of [[null], [valid, null]]) {
        await assert.rejects(search({ [key]: values }), new RegExp(`Invalid ${key} option`));
      }
    }
    for (const filters of [{ page: 0 }, { page: 1001 }, { page: null }, { pageSize: 0 }, { pageSize: 101 },
      { pageSize: '10' }, { pageSize: 1.5 }, { page: true }, { water: ['bog'] }]) {
      await assert.rejects(search(filters));
    }
    await db.exec(`reset role; insert into public.plants(genus,species)
      select 'Bulk', 'species' || i from generate_series(1,600) i; set role anon;`);
    assert.equal((await search({})).length, 50);
    assert.equal((await search({ search: '' })).length, 50);
    assert.equal((await search({ search: 'Bulk' })).length, 50);
    const combined: number[] = [];
    for (let page = 1; page <= 6; page++) {
      const ids = await search({ search: 'Bulk', page, pageSize: 100 });
      assert.equal(ids.length, 100);
      combined.push(...ids);
    }
    assert.equal(new Set(combined).size, 600);
    assert.deepEqual(combined, [...combined].sort((a,b) => a-b));
    assert.deepEqual(await search({ search: 'Bulk', page: 7, pageSize: 100 }), []);
    assert.deepEqual(await search({ isWoody: true, page: 1, pageSize: 1 }), [2]);
    assert.deepEqual(await search({ isWoody: true, page: 2, pageSize: 1 }), [3]);
  } finally { await db.close(); }
});
