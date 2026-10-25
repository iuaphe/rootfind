import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { PGlite } from '@electric-sql/pglite';

test('catalog migration preserves legacy upserts, merges sun arrays, and keeps public filters bounded', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const directory = new URL('../supabase/migrations/', import.meta.url);
    const migration = '202610240010_catalog_enrichment.sql';
    for (const name of (await readdir(directory)).filter(name => name.endsWith('.sql')).sort()) {
      if (name === migration) {
        await db.exec("insert into public.plants(genus,species,sun,minheight,maxheight,water,bloom_months) values ('Echinacea','purpurea','full sun',24,60,array['medium']::public.water_level_enum[],array['May']::public.month_enum[]);");
      }
      await db.exec(await readFile(new URL(name, directory), 'utf8'));
    }
    const schema = await readFile(new URL(migration, directory), 'utf8');
    await db.exec(schema);
    assert.deepEqual((await db.query('select to_jsonb(sun) as sun from public.plants')).rows, [{ sun: ['full sun'] }]);
    await db.exec("set role authenticated; select public.upsert_plant('{\"plant\":{\"genus\":\"Echinacea\",\"species\":\"purpurea\",\"sun\":\"part shade\"}}'::jsonb); reset role;");
    const records = [{ plant_id: 1, scientific_name: 'Echinacea purpurea', source_url: 'https://pfaf.org/user/Plant.aspx?LatinName=Echinacea+purpurea',
      patch: { maxheight: 48, sun: ['full shade'], water: ['low'], bloom_months: ['July', 'August'], bloom_seasons: ['Summer'], drainage: ['Good Drainage'] } }];
    const { stdout } = await promisify(execFile)(process.platform === 'win32' ? 'python' : 'python3', ['-c',
      'import sys,json; sys.path.insert(0,sys.argv[1]); import enrich_catalog; print(enrich_catalog.build_sql(json.loads(sys.argv[2]), "catalog-test"))',
      fileURLToPath(new URL('../scripts/', import.meta.url)), JSON.stringify(records)], { env: { ...process.env, PYTHONUTF8: '1' } });
    assert.equal((await db.exec(stdout)).at(-1)?.rows[0].updated_count, 1);
    const replay = (await db.exec(stdout)).at(-1)?.rows[0];
    assert.equal(replay.updated_count, 0);
    assert.equal(replay.confirmed_count, 1);
    const skipped = (await db.exec(stdout.replaceAll('Echinacea purpurea', 'Echinacea missing').replaceAll('catalog-test', 'skipped-test'))).at(-1)?.rows[0];
    assert.equal(skipped.updated_count, 0);
    assert.equal(skipped.confirmed_count, 0);
    const plant = (await db.query<{ plant: Record<string, unknown> }>('select to_jsonb(p) as plant from public.plants p')).rows[0].plant;
    assert.deepEqual(plant.sun, ['full sun', 'part shade', 'full shade']);
    assert.equal(plant.minheight, 24);
    assert.equal(plant.maxheight, 60);
    assert.deepEqual(plant.water, ['low', 'medium']);
    assert.deepEqual(plant.bloom_months, ['May', 'July', 'August']);
    assert.deepEqual(plant.bloom_seasons, ['Summer']);
    assert.deepEqual(plant.drainage, ['Good Drainage']);
    await assert.rejects(db.query("select public.normalize_sun_levels('[null]'::jsonb)"), /Invalid sun/);
    await db.exec('set role anon');
    for (const sun of ['full sun', 'part shade', 'full shade']) {
      assert.deepEqual((await db.query<{ ids: number[] }>('select public.search_plants($1::jsonb) as ids', [JSON.stringify({ sun: [sun] })])).rows[0].ids, [1]);
    }
    await assert.rejects(db.query("select public.search_plants('{\"sun\":[null]}'::jsonb)"), /Invalid sun/);
    await assert.rejects(db.query("select public.upsert_plant('{}'::jsonb)"), /permission denied/);
    assert.equal((await db.query('select count(*)::int as count from public.plants_with_taxonomy')).rows[0].count, 1);
  } finally { await db.close(); }
});
