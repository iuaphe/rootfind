import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('migrations and search execute against Postgres with common-name relationships', async () => {
  const db = new PGlite();
  try {
    // Supabase normally supplies these roles.
    await db.exec('create role anon; create role authenticated;');
    for (const file of ['202610240001_plants.sql', '202610240002_search_plants.sql', '202610240003_public_search.sql', '202610240004_rpc_only_public_search.sql', '202610240005_name_search_ids.sql']) {
      await db.exec(await readFile(new URL(`../supabase/migrations/${file}`, import.meta.url), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, life_cycle, foliage, sun, water, minheight, maxheight, minwidth, maxwidth, thorns)
      values ('Asclepias', 'tuberosa', 'perennial', 'deciduous', 'full sun', 'low', 10, 60, 20, 40, false),
        ('Asclepias', 'incarnata', 'perennial', 'deciduous', 'full sun', 'high', 30, 100, 30, 50, false),
        ('Quercus', 'alba', 'perennial', 'deciduous', 'part shade', 'med', 100, 500, 100, 400, null);
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
    for (const invalid of [{}, { search: '' }, { search: '   ' }, { search: null },
      { search: 123 }, { search: 'oak', isWoody: true }, { search: 'a'.repeat(201) }]) {
      await assert.rejects(search(invalid), /search/i);
    }
    // Return all IDs, not just a default page of 25 or 100 records.
    await db.exec(`reset role;
      insert into public.plants (genus, species)
      select 'Bulk', 'species' || i from generate_series(1, 105) i;
      set role authenticated;`);
    assert.equal((await search({ search: 'Bulk' })).length, 105);
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
