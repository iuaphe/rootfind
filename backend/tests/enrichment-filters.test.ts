import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { parseQuery } from '../supabase/functions/search-plants/query.ts';
import { parseQuery as parseCountQuery } from '../supabase/functions/count-plants-by-county/query.ts';
import { parseSuggestionsQuery } from '../supabase/functions/suggest-plants/query.ts';
import { createFilters, filterParams, countFilters, enumFilters } from '../../frontend/src/lib/plant-filters.ts';

test('enrichment menu options serialize into validated search, count, and suggestion filters', () => {
  const filters = createFilters();
  assert.equal(filterParams(filters).toString(), '');
  assert.equal(countFilters(filters), 0);
  Object.assign(filters, { minZone: 5, maxZone: 8, bloomMonths: ['May', 'June'],
    bloomSeasons: ['Summer'], drainage: ['good drainage'] });
  const params = filterParams(filters);
  const expected = { minZone: 5, maxZone: 8, bloomMonths: ['May', 'June'],
    bloomSeasons: ['Summer'], drainage: ['good drainage'] };
  assert.equal(countFilters(filters), 4);
  assert.deepEqual(parseQuery(params), expected);
  assert.deepEqual(parseCountQuery(params), expected);
  params.set('q', 'acer');
  assert.deepEqual(parseSuggestionsQuery(params), { query: 'acer', filters: expected });
  for (const { key, options } of enumFilters) {
    assert.doesNotThrow(() => parseQuery(new URLSearchParams({ [key]: options.join(',') })));
  }
  assert.deepEqual(parseQuery(new URLSearchParams('drainage=well-drained,good+drainage')),
    { drainage: ['good drainage'] });
  for (const invalid of ['minZone=0', 'maxZone=14', 'minZone=1.5', 'minZone=01',
    'minZone=9&maxZone=3', 'minZone=3&minZone=4', 'bloomMonths=', 'bloomMonths=Smarch',
    'bloomMonths=May,', 'bloomSeasons=Autumn', 'drainage=anything']) {
    assert.throws(() => parseQuery(new URLSearchParams(invalid)), undefined, invalid);
    assert.throws(() => parseCountQuery(new URLSearchParams(invalid)), undefined, invalid);
  }
});

test('enrichment filters intersect taxonomy, county search, counts, and autocomplete in Postgres', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus,species,min_zone,max_zone,bloom_months,bloom_seasons,drainage) values
        ('Acer','rubrum',3,9,array['May','June']::public.month_enum[],array['Spring','Summer'],array['Good Drainage']),
        ('Acer','griseum',4,8,array['October']::public.month_enum[],array['Fall'],array['well-drained']),
        ('Viola','sororia',3,7,array['May']::public.month_enum[],array['Spring'],array['Occasionally Wet']),
        ('Unknown','unknown',null,null,null,null,null);
      insert into public.plant_taxonomy (plant_id,family) values (1,'Aceraceae'),(2,'Aceraceae'),(3,'Violaceae');
      insert into public.common_names values ('Red maple'),('Paperbark maple');
      insert into public.common_plant values ('Red maple',1),('Paperbark maple',2);
      insert into public.county_info values (1001,'Autauga'),(1003,'Baldwin');
      insert into public.plant_county values (1001,1),(1001,2),(1001,4),(1003,1),(1003,3);
      set role anon;
    `);
    const search = async (filters: unknown) => (await db.query<{ result: number[] }>(
      'select public.search_plants($1::jsonb) result', [JSON.stringify(filters)])).rows[0].result;
    assert.deepEqual(await search({}), [1,2,3,4]);
    assert.deepEqual(await search({ minZone: 8, maxZone: 8 }), [1,2]);
    assert.deepEqual(await search({ minZone: 9 }), [1]);
    assert.deepEqual(await search({ maxZone: 3 }), [1,3]);
    assert.deepEqual(await search({ minZone: 10 }), []);
    assert.deepEqual(await search({ bloomMonths: ['May','October'] }), [1,2,3]);
    assert.deepEqual(await search({ bloomSeasons: ['Fall'] }), [2]);
    assert.deepEqual(await search({ bloomMonths: ['May'], bloomSeasons: ['Fall'] }), []);
    assert.deepEqual(await search({ drainage: ['good drainage'] }), [1,2]);
    assert.deepEqual(await search({ drainage: ['well-drained'] }), [1,2]);
    assert.deepEqual(await search({ drainage: ['occasionally wet'] }), [3]);
    assert.deepEqual(await search({ drainage: ['occasionally dry','good drainage'] }), [1,2]);
    const filters = { minZone: 8, maxZone: 9, bloomMonths: ['May'], bloomSeasons: ['Summer'],
      drainage: ['good drainage'], family: 'Aceraceae', search: 'maple' };
    assert.deepEqual(await search(filters), [1]);
    assert.deepEqual(await search({ drainage: ['good drainage'], county: 1001, page: 2, pageSize: 1 }), [2]);
    const counts = (await db.query<{ result: unknown }>(
      'select public.count_plants_by_county($1::jsonb) result', [JSON.stringify(filters)])).rows[0].result;
    assert.deepEqual(counts, [{ fips: 1001, plant_count: 1 },{ fips: 1003, plant_count: 1 }]);
    const { search: _search, ...context } = filters;
    const suggestions = (await db.query<{ result: { value: string }[] }>(
      "select public.suggest_plants('acer',$1::jsonb) result", [JSON.stringify(context)])).rows[0].result;
    assert.ok(suggestions.some(s => s.value === 'Acer rubrum'));
    assert.ok(!suggestions.some(s => s.value === 'Acer griseum'));
    for (const bad of [{ minZone: 0 },{ maxZone: 14 },{ minZone: '6' },{ minZone: null },
      { minZone: 9, maxZone: 3 },{ bloomMonths: [] },{ bloomMonths: ['Smarch'] },
      { bloomSeasons: ['Autumn'] },{ drainage: ['anything'] },{ drainage: null }]) {
      await assert.rejects(search(bad), { code: '22023' });
    }
    await assert.rejects(db.query("select rootfind_private.validate_plant_filters('{}')"), /permission denied/);
  } finally { await db.close(); }
});
