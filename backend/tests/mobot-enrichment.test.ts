import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { PGlite } from '@electric-sql/pglite';

const exec = promisify(execFile);
const migration = (name: string) => readFile(new URL(`../supabase/migrations/${name}`, import.meta.url), 'utf8');
const setup = async (db: PGlite) => {
  await db.exec('create role anon; create role authenticated; create role service_role;');
  await db.exec(await migration('202610240001_plants.sql'));
  await db.exec(await migration('20261024000601_upsert_plant.sql'));
  await db.exec(await migration('20261024000702_plant_taxonomy.sql'));
};

test('taxonomy writes reject anon and the view survives water conversion and reruns', async () => {
  const db = new PGlite();
  try {
    await setup(db);
    // A manual SQL Editor run may have created the table, policy, and view
    // without recording this migration in Supabase's history.
    await db.exec(await migration('20261024000702_plant_taxonomy.sql'));
    await db.exec("insert into public.plants(genus,species,water) values ('Acer','rubrum','med'); set role anon;");
    await assert.rejects(db.query("select public.upsert_plant_taxonomy(1, '{\"family\":\"Wrong\"}'::jsonb)"), /permission denied/);
    await db.exec("reset role; set role authenticated; select public.upsert_plant_taxonomy(1, '{\"family\":\"Sapindaceae\"}'::jsonb); reset role;");
    const schema = await migration('202610240008_mobot_enrichment.sql');
    for (let run = 0; run < 2; run++) {
      await db.exec(schema);
      await db.exec('set role anon');
      assert.deepEqual((await db.query('select family, to_jsonb(water) as water, min_zone from public.plants_with_taxonomy')).rows,
        [{ family: 'Sapindaceae', water: ['medium'], min_zone: null }]);
      await assert.rejects(db.query("select public.upsert_plant_taxonomy(1, '{}'::jsonb)"), /permission denied/);
      await db.exec('reset role');
    }
  } finally { await db.close(); }
});

test('MOBOT migration converts feet once, preserves water, merges ranges, and audits repeat-safe updates', async () => {
  const db = new PGlite();
  try {
    await setup(db);
    await db.exec(`insert into public.plants(genus,species,minheight,maxheight,minwidth,maxwidth,water)
      values ('Robinia','pseudoacacia',10,20,5,10,'high'), ('Acer','rubrum',null,30,null,null,'med');`);
    const schema = await migration('202610240008_mobot_enrichment.sql');
    await db.exec(schema);
    const rows = (await db.query('select minheight,maxheight,minwidth,maxwidth,to_jsonb(water) as water from public.plants order by plant_id')).rows;
    assert.deepEqual(rows[0], { minheight: 120, maxheight: 240, minwidth: 60, maxwidth: 120, water: ['high'] });
    assert.deepEqual(rows[1], { minheight: null, maxheight: 360, minwidth: null, maxwidth: null, water: ['medium'] });
    await db.exec('update public.plants set min_zone=2, max_zone=7 where plant_id=1');
    const records = [{ plant_id: 1, scientific_name: 'Robinia pseudoacacia',
      source_url: 'https://plantfinder.mobot.org/PlantFinderDetails.aspx?taxonid=1', patch: {
        min_zone: 3, max_zone: 8, minheight: 144, maxheight: 300, minwidth: 48, maxwidth: 132,
        water: ['low', 'medium'], bloom_months: ['May', 'June'],
      } }, { plant_id: 2, scientific_name: 'WRONG NAME', source_url: 'https://plantfinder.mobot.org/',
        patch: { maxheight: 900 } }];
    const scripts = fileURLToPath(new URL('../scripts/', import.meta.url));
    const { stdout } = await exec(process.platform === 'win32' ? 'python' : 'python3', [
      '-c', 'import sys,json; sys.path.insert(0,sys.argv[1]); import enrich_mobot; print(enrich_mobot.build_apply_sql(json.loads(sys.argv[2]), "test-run"))',
      scripts, JSON.stringify([...records, records[0]]),
    ], { env: { ...process.env, PYTHONUTF8: '1' } });
    assert.equal((await db.exec(stdout)).at(-1)?.rows[0].updated_count, 1);
    const updated = (await db.query<{ plant: Record<string, unknown> }>('select to_jsonb(p) as plant from public.plants p where plant_id=1')).rows[0].plant;
    assert.equal(updated.minheight, 120);
    assert.equal(updated.maxheight, 300);
    assert.equal(updated.minwidth, 48);
    assert.equal(updated.maxwidth, 132);
    assert.equal(updated.min_zone, 2);
    assert.equal(updated.max_zone, 8);
    assert.deepEqual(updated.water, ['low', 'medium', 'high']);
    assert.deepEqual(updated.bloom_months, ['May', 'June']);
    assert.equal((await db.exec(stdout)).at(-1)?.rows[0].updated_count, 0);
    const audit = (await db.query('select * from rootfind_private.mobot_changes')).rows[0];
    assert.equal((audit.old_row as Record<string, unknown>).maxheight, 240);
    assert.equal((audit.new_row as Record<string, unknown>).maxheight, 300);
    await db.exec(schema);
    assert.equal((await db.query('select maxheight from public.plants where plant_id=1')).rows[0].maxheight, 300);
    await db.exec('set role anon');
    await assert.rejects(db.query('select * from rootfind_private.mobot_changes'), /permission denied/);
  } finally { await db.close(); }
});

test('updated upsert supports old feet/scalar water clients and new inches/array clients', async () => {
  const db = new PGlite();
  try {
    await setup(db);
    await db.exec(await migration('202610240008_mobot_enrichment.sql'));
    await db.query('select public.upsert_plant($1::jsonb)', [JSON.stringify({ plant: {
      genus: 'Acer', species: 'rubrum', minheight: 1.1, maxheight: 2.1, water: 'med',
    } })]);
    let row = (await db.query<{ plant: Record<string, unknown> }>('select to_jsonb(p) as plant from public.plants p')).rows[0].plant;
    assert.equal(row.minheight, 13);
    assert.equal(row.maxheight, 26);
    assert.deepEqual(row.water, ['medium']);
    await db.query('select public.upsert_plant($1::jsonb)', [JSON.stringify({ plant: {
      genus: 'Acer', species: 'rubrum', measurement_units: 'inches', minheight: 15,
      maxheight: 30, water: ['low', 'high'],
    } })]);
    row = (await db.query<{ plant: Record<string, unknown> }>('select to_jsonb(p) as plant from public.plants p')).rows[0].plant;
    assert.equal(row.minheight, 13);
    assert.equal(row.maxheight, 30);
    assert.deepEqual(row.water, ['low', 'medium', 'high']);
    await assert.rejects(db.query("select public.normalize_water_levels('\"bog\"'::jsonb)"), /Invalid water level/);
  } finally { await db.close(); }
});

test('overflow cancels the schema and feet conversion transaction', async () => {
  const db = new PGlite();
  try {
    await setup(db);
    await db.exec("insert into public.plants(genus,species,maxheight,water) values ('Test','overflow',3000,'med')");
    await assert.rejects(db.exec(await migration('202610240008_mobot_enrichment.sql')), /cannot fit/);
    await db.exec('rollback');
    const row = (await db.query('select maxheight,water from public.plants')).rows[0];
    assert.deepEqual(row, { maxheight: 3000, water: 'med' });
  } finally { await db.close(); }
});
