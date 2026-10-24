import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('migrations and search execute against Postgres with common-name relationships', async () => {
  const db = new PGlite();
  try {
    // Supabase normally supplies these roles.
    await db.exec('create role anon; create role authenticated; create role service_role;');
    for (const file of ['202610240001_plants.sql', '202610240002_search_plants.sql', '202610240003_public_search.sql', '202610240004_rpc_only_public_search.sql', '202610240005_name_search_ids.sql', '20261024000701_search_filters.sql', '202610240008_mobot_enrichment.sql', '202610240009_search_water_pagination.sql']) {
      await db.exec(await readFile(new URL(`../supabase/migrations/${file}`, import.meta.url), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, life_cycle, foliage, sun, water, minheight, maxheight, minwidth, maxwidth, thorns)
      values ('Asclepias', 'tuberosa', 'perennial', 'deciduous', 'full sun', array['low']::public.water_level_enum[], 10, 60, 20, 40, false),
        ('Asclepias', 'incarnata', 'annual', 'evergreen', 'full shade', array['high']::public.water_level_enum[], 30, 100, 30, 50, true),
        ('Quercus', 'alba', 'perennial', 'deciduous', 'part shade', array['medium']::public.water_level_enum[], 100, 500, 100, 400, null);
      update public.plants set is_woody = plant_id = 3, is_tree = plant_id = 3, edible = plant_id = 1;
      insert into public.county_info values (51059, 'Fairfax'), (12095, 'Orange');
      insert into public.plant_county values (51059, 1), (12095, 2);
      insert into public.common_names values ('Butterfly milkweed'), ('Butterfly weed'), ('Swamp milkweed'), ('White oak'), ('Unused name');
      insert into public.common_plant values ('Butterfly milkweed', 1), ('Butterfly weed', 1),
        ('Swamp milkweed', 2), ('White oak', 3), ('Butterfly weed', 2);
      set role authenticated;
    `);
    async function search(filters: Record<string, unknown>) {
      const { rows } = await db.query<{ result: number[] }>(
        'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)],
      );
      return rows[0].result;
    }
    assert.deepEqual(await search({ search: 'BUTTERFLY' }), [1, 2], 'multiple aliases never duplicate plants');
    assert.deepEqual(await search({ search: 'milkweed' }), [1, 2]);
    assert.deepEqual(await search({ search: ' Asclepias   tuberosa ' }), [1]);
    assert.deepEqual(await search({ search: 'alba' }), [3], 'scientific search works without matching aliases');
    assert.deepEqual(await search({ search: 'Unused name' }), [], 'unlinked names cannot match plants');
    assert.deepEqual(await search({ search: "%_' OR true; --" }), [], 'punctuation is literal');
    assert.deepEqual(await search({}), [1, 2, 3], 'browse without a name');
    assert.deepEqual(await search({ lifeCycle: ['annual', 'perennial'] }), [1, 2, 3], 'OR within an enum');
    assert.deepEqual(await search({ lifeCycle: ['perennial'], water: ['low'] }), [1], 'AND across filters');
    assert.deepEqual(await search({ search: 'Butterfly', thorns: true }), [2], 'traits apply to common names');
    assert.deepEqual(await search({ foliage: ['evergreen'], sun: ['full shade'] }), [2]);
    assert.deepEqual(await search({ isWoody: true, isTree: true }), [3]);
    assert.deepEqual(await search({ edible: true }), [1]);
    assert.deepEqual(await search({ thorns: false }), [1], 'unknown booleans do not match false');
    assert.deepEqual(await search({ minHeight: 60, maxHeight: 60 }), [1, 2], 'inclusive overlap boundaries');
    assert.deepEqual(await search({ minHeight: 61, maxHeight: 99 }), [2]);
    assert.deepEqual(await search({ minWidth: 40, maxWidth: 40 }), [1, 2]);
    assert.deepEqual(await search({ minWidth: 51, maxWidth: 99 }), []);
    for (const invalid of [{ search: null }, { search: 123 }, { search: 'a'.repeat(201) },
      { isWoody: 'true' }, { edible: null }, { thorns: 1 }, { lifeCycle: [] }, { lifeCycle: ['unknown'] },
      { foliage: 'deciduous' }, { sun: [null] }, { water: ['bog'] }, { minHeight: -1 },
      { minWidth: 1.5 }, { maxWidth: 32768 }, { minHeight: '12' }, { minHeight: 30, maxHeight: 20 },
      { minWidth: 30, maxWidth: 20 }, { unknown: true }]) {
      await assert.rejects(search(invalid), undefined, JSON.stringify(invalid));
    }
    await db.exec(`reset role;
      insert into public.plants (genus, species) values ('Unknown', 'measurements');
      set role authenticated;`);
    assert.deepEqual(await search({ minHeight: 1 }), [1, 2, 3], 'unknown measurements are excluded only by active filters');
    assert.equal((await search({})).length, 4, 'unfiltered searches include unknown measurements');
    // Broad searches return bounded pages in stable ID order.
    await db.exec(`reset role;
      insert into public.plants (genus, species)
      select 'Bulk', 'species' || i from generate_series(1, 105) i;
      set role authenticated;`);
    assert.equal((await search({ search: 'Bulk' })).length, 50);
    assert.equal((await search({ search: 'Bulk', page: 2 })).length, 50);
    assert.equal((await search({ search: 'Bulk', page: 3 })).length, 5);
    assert.deepEqual(await search({ search: 'Bulk', page: 4 }), []);
    await db.exec('reset role; delete from public.common_names where name = \'Butterfly weed\'; set role authenticated;');
    assert.deepEqual(await search({ search: 'Butterfly weed' }), [], 'deleted aliases cascade');
    await db.exec('reset role; set role anon;');
    assert.deepEqual(await search({ search: 'milkweed' }), [1, 2]);
    for (const table of ['plants', 'county_info', 'plant_county', 'common_names', 'common_plant']) {
      await assert.rejects(db.query(`select * from public.${table}`), /permission denied/);
    }
    // Supabase may grant write privileges by default; RLS must still deny writes.
    await db.exec(`reset role;
      grant select, insert, update, delete on public.plants, public.county_info, public.plant_county,
        public.common_names, public.common_plant to anon;
      set role anon;`);
    for (const table of ['plants', 'county_info', 'plant_county', 'common_names', 'common_plant']) {
      const column = table === 'county_info' || table === 'plant_county' ? 'fips' : table === 'common_names' || table === 'common_plant' ? 'name' : 'genus';
      assert.equal((await db.query(`update public.${table} set ${column} = ${column} returning *`)).rows.length, 0);
      assert.equal((await db.query(`delete from public.${table} returning *`)).rows.length, 0);
    }
    await assert.rejects(db.exec("insert into public.common_names values ('Unauthorized alias')"), /row-level security/);
  } finally {
    await db.close();
  }
});
