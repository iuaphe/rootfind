import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('migrations and search execute against Postgres with common-name relationships', async () => {
  const db = new PGlite();
  try {
    // Supabase normally supplies these roles.
    await db.exec('create role anon; create role authenticated;');
    for (const file of ['202610240001_plants.sql', '202610240002_search_plants.sql']) {
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
      const { rows } = await db.query<{ result: { data: { plant_id: number }[]; total: number; page: number } }>(
        'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)],
      );
      return rows[0].result;
    }
    assert.equal((await search({ search: 'BUTTERFLY' })).total, 2, 'multiple aliases never duplicate plants');
    assert.equal((await search({ search: 'milkweed' })).total, 2);
    assert.equal((await search({ search: 'Asclepias tuberosa' })).data[0].plant_id, 1);
    assert.equal((await search({ search: 'alba' })).data[0].plant_id, 3, 'scientific search works without matching aliases');
    assert.equal((await search({ search: 'Unused name' })).total, 0, 'unlinked names cannot match plants');
    assert.equal((await search({ search: "%_' OR true; --" })).total, 0, 'punctuation is literal');
    assert.equal((await search({ search: 'milkweed', county: ['51059'], lifeCycle: ['perennial'],
      foliage: ['deciduous'], sun: ['full sun'], water: ['low'], thorns: false,
      minHeight: 50, maxHeight: 70, minWidth: 30, maxWidth: 40 })).data[0].plant_id, 1);
    assert.equal((await search({ search: 'milkweed', county: ['51059', '12095'] })).total, 2);
    assert.equal((await search({ search: 'milkweed', water: ['low', 'high'] })).total, 2);
    assert.equal((await search({ thorns: false })).total, 2, 'unknown booleans do not count as false');
    const empty = await search({ search: 'milkweed', page: 3, pageSize: 1 });
    assert.deepEqual(empty.data, []);
    assert.equal(empty.total, 2);
    await db.exec('reset role; delete from public.common_names where name = \'Butterfly weed\'; set role authenticated;');
    assert.equal((await search({ search: 'Butterfly weed' })).total, 0, 'deleted aliases cascade');
    await db.exec('reset role; set role anon;');
    await assert.rejects(search({ search: 'milkweed' }), /permission denied/);
  } finally {
    await db.close();
  }
});
