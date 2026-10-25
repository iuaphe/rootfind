import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('optimized search and county counts preserve scalar and hosted array sun filters', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus,species,sun) values
        ('Test','one',array['full sun']::public.sun_type[]),
        ('Test','two',array['full shade']::public.sun_type[]), ('Test','unknown',null);
      insert into public.county_info values (1001,'Autauga'), (1003,'Baldwin'), (1005,'Empty');
      insert into public.plant_county values (1001,1), (1003,1), (1003,2), (1001,3);
      insert into public.common_names values ('Partial sun plant'), ('Partial sun alias');
      insert into public.common_plant values ('Partial sun plant',1), ('Partial sun alias',1);
      set role anon;
    `);
    const search = async (filters: unknown) => (await db.query<{ result: number[] }>(
      'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)],
    )).rows[0].result;
    const count = async (filters: unknown) => (await db.query<{ result: { fips: number; plant_count: number }[] }>(
      'select public.count_plants_by_county($1::jsonb) as result', [JSON.stringify(filters)],
    )).rows[0].result;
    assert.deepEqual(await search({ sun: ['full sun'] }), [1]);
    assert.deepEqual(await search({ sun: ['full shade'] }), [2]);
    assert.deepEqual(await search({ sun: ['part shade'] }), []);
    const expected = [{ fips: 1001, plant_count: 1 }, { fips: 1003, plant_count: 1 }, { fips: 1005, plant_count: 0 }];
    assert.deepEqual(await count({ sun: ['full sun'] }), expected);
    assert.deepEqual(await count({ search: 'Partial sun' }), expected, 'aliases never multiply matches');
    await db.exec(`reset role;
      drop view public.plants_with_taxonomy;
      alter table public.plants alter column sun type public.sun_type using
        case when sun is null then null else sun[1] end;
      set role anon;`);
    assert.deepEqual(await search({ sun: ['full sun'] }), [1]);
    assert.deepEqual(await search({ sun: ['full shade'] }), [2]);
    assert.deepEqual(await search({ sun: ['part shade'] }), []);
    assert.deepEqual(await search({ sun: ['full shade'], county: 1001 }), []);
    assert.deepEqual(await count({ search: 'Partial sun', sun: ['full sun'] }), expected);
    assert.deepEqual(await count({ sun: ['full shade'] }), [
      { fips: 1001, plant_count: 0 }, { fips: 1003, plant_count: 1 }, { fips: 1005, plant_count: 0 },
    ]);
    await db.exec(`reset role;
      alter table public.plants alter column sun type public.sun_type[] using
        case when sun is null then null else array[sun] end;
      update public.plants set sun=array['full sun','part shade']::public.sun_type[] where plant_id=1;
      set role anon;`);
    assert.deepEqual(await search({ sun: ['part shade'] }), [1]);
    assert.deepEqual(await search({ sun: ['part shade', 'full shade'] }), [1, 2]);
    assert.deepEqual(await count({ sun: ['part shade'] }), [
      { fips: 1001, plant_count: 1 }, { fips: 1003, plant_count: 1 }, { fips: 1005, plant_count: 0 },
    ]);
    await assert.rejects(db.query('select * from public.plant_county'), /permission denied/);
  } finally {
    await db.close();
  }
});
