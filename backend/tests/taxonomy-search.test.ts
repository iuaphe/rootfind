import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { taxonomyFields } from '../supabase/functions/_shared/plant-search-query.ts';
import { parseQuery } from '../supabase/functions/search-plants/query.ts';
import { parseQuery as parseCountQuery } from '../supabase/functions/count-plants-by-county/query.ts';
import { parseSuggestionsQuery } from '../supabase/functions/suggest-plants/query.ts';
import { createHandler } from '../supabase/functions/suggest-plants/handler.ts';

test('all taxonomy fields work for counts, search, and autocomplete context', () => {
  for (const field of taxonomyFields) {
    const params = new URLSearchParams({ [field]: '  Flowering   plants ', search: 'purple' });
    const expected = { [field]: 'Flowering plants', search: 'purple' };
    assert.deepEqual(parseQuery(params), expected);
    assert.deepEqual(parseCountQuery(params), expected);
    params.delete('search');
    params.set('q', ' FAMILY = Acer ');
    assert.deepEqual(parseSuggestionsQuery(params), {
      query: 'FAMILY = Acer', filters: { [field]: 'Flowering plants' },
    });
  }
  for (const params of ['family=', 'family=+', `family=${'a'.repeat(101)}`,
    `kingdom=${'a'.repeat(51)}`, 'family=A&family=B', 'lineage=A', 'plant_id=1']) {
    assert.throws(() => parseQuery(new URLSearchParams(params)));
    assert.throws(() => parseCountQuery(new URLSearchParams(params)));
  }
  for (const params of ['', 'q=a&q=b', 'q=plant_id=1', 'q=lineage=foo', 'q=a&page=1',
    'q=a&county=1', 'q=a&search=b', `q=${'a'.repeat(201)}`]) {
    assert.throws(() => parseSuggestionsQuery(new URLSearchParams(params)));
  }
});

test('autocomplete HTTP is bounded, anonymous, validated, and sanitizes failures', async () => {
  const settings = { url: 'https://example.supabase.co', anonKey: 'public-key' };
  const suggestions = [{ kind: 'taxonomy', field: 'family', value: 'Aceraceae' }];
  let calls = 0;
  const handler = createHandler(settings, async (url, init) => {
    calls++;
    assert.equal(url, `${settings.url}/rest/v1/rpc/suggest_plants`);
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer public-key');
    assert.deepEqual(JSON.parse(init?.body as string), {
      p_query: 'family=Acer', p_filters: { division_common: 'Flowering plants', edible: true },
    });
    return Response.json(suggestions);
  });
  const result = await handler(new Request('https://edge.test/?q=family=Acer&division_common=Flowering+plants&edible=true', {
    headers: { authorization: 'Bearer caller-token' },
  }));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), suggestions);
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), '*');
  assert.equal((await handler(new Request('https://edge.test/?q=bad=foo'))).status, 400);
  assert.equal((await handler(new Request('https://edge.test/', { method: 'OPTIONS' }))).status, 204);
  assert.equal((await handler(new Request('https://edge.test/', { method: 'POST' }))).status, 405);
  assert.equal(calls, 1);
  const failure = await createHandler(settings, async () => new Response('private detail', { status: 500 }))(
    new Request('https://edge.test/?q=purple'));
  assert.equal(failure.status, 502);
  assert.doesNotMatch(await failure.text(), /private detail/);
});

test('taxonomy intersects names/traits/counties; autocomplete deduplicates and ranks all name kinds', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const dir = new URL('../supabase/migrations/', import.meta.url);
    for (const file of (await readdir(dir)).filter(name => name.endsWith('.sql')).sort()) {
      await db.exec(await readFile(new URL(file, dir), 'utf8'));
    }
    await db.exec(`
      insert into public.plants (genus, species, edible) values
        ('Acer','purpureum',true), ('Acer','rubrum',false), ('Viola','purpurea',true), ('Unknown','purple',true);
      insert into public.plant_taxonomy (plant_id, kingdom, subkingdom, superdivision, division,
        division_common, class, class_common, subclass, order_name, family, family_common) values
        (1,'Plantae','Tracheobionta','Spermatophyta','Magnoliophyta','Flowering plants',
          'Magnoliopsida','Dicotyledons','Rosidae','Sapindales','Aceraceae','Maple family'),
        (2,'Plantae','Tracheobionta','Spermatophyta','Magnoliophyta','Flowering plants',
          'Magnoliopsida','Dicotyledons','Rosidae','Sapindales','Aceraceae','Maple family'),
        (3,'Plantae',null,null,null,null,null,null,null,null,'Violaceae','Violet family');
      insert into public.common_names values ('Purple maple'), ('Purple maple alias'), ('Purple violet'), ('Acer maple');
      insert into public.common_plant values ('Purple maple',1), ('Purple maple alias',1), ('Purple violet',3), ('Acer maple',1);
      insert into public.county_info values (1001,'Autauga'), (1003,'Baldwin'), (1005,'Empty');
      insert into public.plant_county values (1001,1), (1003,1), (1003,2), (1001,3), (1001,4);
      set role anon;
    `);
    const search = async (filters: unknown) => (await db.query<{ result: number[] }>(
      'select public.search_plants($1::jsonb) as result', [JSON.stringify(filters)])).rows[0].result;
    const suggest = async (query: string | null, filters: unknown = {}) => (await db.query<{
      result: { kind: string; field: string | null; value: string }[];
    }>('select public.suggest_plants($1, $2::jsonb) as result', [query, JSON.stringify(filters)])).rows[0].result;
    const values = ['Plantae','Tracheobionta','Spermatophyta','Magnoliophyta','Flowering plants',
      'Magnoliopsida','Dicotyledons','Rosidae','Sapindales','Aceraceae','Maple family'];
    for (const [index, field] of taxonomyFields.entries()) {
      assert.deepEqual(await search({ [field]: ` ${values[index].toUpperCase()} `, search: 'purple', edible: true }), field === 'kingdom' ? [1,3] : [1]);
      const suggestions = await suggest(`${field}=${values[index].slice(0,3)}`);
      assert.ok(suggestions.some(s => s.field === field && s.value === values[index]), field);
    }
    assert.deepEqual(await search({ family: 'aceraceae', search: 'PURPLE', edible: true, county: 1001 }), [1]);
    assert.deepEqual(await search({ family: 'Aceraceae', class_common: 'Dicotyledons', search: 'rubrum' }), [2]);
    assert.deepEqual(await search({ family: 'Aceraceae', order_name: 'Wrong' }), []);
    assert.deepEqual(await search({ family: 'Acer' }), [], 'taxonomy is exact, not a name substring');
    assert.deepEqual(await search({ family: "Aceraceae' OR true; --" }), []);
    assert.deepEqual(await search({ search: 'purple' }), [1,3,4], 'missing taxonomy does not hide name matches');
    assert.deepEqual(await search({ family: 'Aceraceae', pageSize: 1, page: 2 }), [2]);
    const counts = (await db.query<{ result: unknown }>('select public.count_plants_by_county($1::jsonb) as result',
      [JSON.stringify({ family: 'Aceraceae', search: 'purple' })])).rows[0].result;
    assert.deepEqual(counts, [
      { fips: 1001, plant_count: 1 }, { fips: 1003, plant_count: 1 }, { fips: 1005, plant_count: 0 },
    ]);
    const ranked = await suggest('acer');
    assert.deepEqual(ranked.map(s => s.kind), ['taxonomy','common','scientific','scientific']);
    assert.equal(ranked[0].value, 'Aceraceae');
    assert.equal(ranked.filter(s => s.value === 'Aceraceae').length, 1);
    assert.ok((await suggest('family=')).every(s => s.kind === 'taxonomy' && s.field === 'family'));
    assert.deepEqual(await suggest('PURPLE', { family: 'Aceraceae' }), [
      { kind: 'common', field: null, value: 'Purple maple' },
      { kind: 'common', field: null, value: 'Purple maple alias' },
    ]);
    assert.ok((await suggest('family=Viola', { family: 'Aceraceae' })).some(s => s.value === 'Violaceae'));
    assert.ok(!(await suggest('acer', { edible: true })).some(s => s.value === 'Acer rubrum'));
    assert.deepEqual(await suggest('%_'), [], 'wildcards stay literal');
    assert.deepEqual(await suggest(''), []);
    assert.ok((await suggest('a')).length <= 12);
    for (const bad of [{ lineage: 'x' }, { plant_id: 1 }, { family: [] }, { class: null },
      { family: '' }, { kingdom: 'x'.repeat(51) }, { family: 'x'.repeat(101) }]) {
      await assert.rejects(search(bad), { code: '22023' });
      await assert.rejects(suggest('a', bad), { code: '22023' });
    }
    await assert.rejects(suggest('plant_id=1'), { code: '22023' });
    await assert.rejects(suggest(null), { code: '22023' });
    await assert.rejects(suggest('a', { search: 'purple' }), { code: '22023' });
    await assert.rejects(db.query('select * from public.plants'), /permission denied/);
    await assert.rejects(db.query("select * from rootfind_private.matching_plant_ids('{}')"), /permission denied/);
  } finally { await db.close(); }
});
