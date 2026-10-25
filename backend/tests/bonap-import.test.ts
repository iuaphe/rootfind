import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

test('dedicated BONAP batches union counties/seasons/tags and preserve existing enrichment', async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role;');
    const directory = new URL('../supabase/migrations/', import.meta.url);
    let original;
    for (const name of (await readdir(directory)).filter(name => name.endsWith('.sql')).sort()) {
      if (name.startsWith('202610240013')) {
        original = (await db.query("select pg_get_functiondef('public.upsert_plant(jsonb)'::regprocedure) as definition")).rows[0].definition;
      }
      await db.exec(await readFile(new URL(name, directory), 'utf8'));
    }
    assert.equal((await db.query("select pg_get_functiondef('public.upsert_plant(jsonb)'::regprocedure) as definition")).rows[0].definition, original);
    await db.exec("insert into public.plants(genus,species,life_cycle,minheight,maxheight,is_tree,is_woody,misc,bloom_seasons,water) values('Acer','rubrum','perennial',120,600,false,false,array['fragrant'],array['Spring'],array['medium']::public.water_level_enum[]); insert into public.county_info values(1001,'Existing name'); insert into public.plant_county values(1001,1);");
    const record = (species: string, fips: number) => ({plant:{genus:'Acer',species,is_tree:true,is_woody:true,life_cycle:'annual',misc:['Wetland plant']},counties:[{fips,name:'Autauga'}],bonap:{raw:{Species:'Acer '+species,Family:'SAPINDACEAE'},bloom_seasons:['Summer'],source_sha256:'test'}});
    const payload = [record('rubrum subsp. drummondii',1001), record('rubrum var. trilobum',1003)];
    await db.exec('set role service_role');
    for (let i=0;i<2;i++) {
      const result = (await db.query<{result:{plant_ids:number[]}}>('select public.upsert_bonap_plants_batch($1::jsonb) as result',[JSON.stringify(payload)])).rows[0].result;
      assert.deepEqual(result.plant_ids,[1,1]);
    }
    await db.exec('reset role');
    const plant = (await db.query<{plant:Record<string,unknown>}>('select to_jsonb(p) as plant from public.plants p')).rows[0].plant;
    assert.equal(plant.life_cycle,'perennial');
    assert.equal(plant.minheight,120); assert.equal(plant.maxheight,600);
    assert.equal(plant.is_tree,true); assert.equal(plant.is_woody,true);
    assert.deepEqual(plant.misc,['Wetland plant','fragrant']);
    assert.deepEqual(plant.bloom_seasons,['Spring','Summer']);
    assert.deepEqual(plant.water,['medium']);
    assert.equal((await db.query('select count(*)::int as n from public.plant_county')).rows[0].n,2);
    assert.equal((await db.query("select to_regclass('public.plant_bonap') as name")).rows[0].name,null);
    assert.equal((await db.query('select family from public.plant_taxonomy where plant_id=1')).rows[0].family,'SAPINDACEAE');
    assert.equal((await db.query('select name from public.county_info where fips=1001')).rows[0].name,'Existing name');
    // A malformed late record rolls back the complete batch, including earlier new plants.
    const fresh = {...record('saccharum',1005),plant:{genus:'Acer',species:'saccharum'}};
    await assert.rejects(db.query('select public.upsert_bonap_plants_batch($1::jsonb)',[JSON.stringify([fresh,{plant:{genus:'Bad',species:'name'}}])]),/BONAP raw Species/);
    assert.equal((await db.query("select count(*)::int as n from public.plants where species='saccharum'")).rows[0].n,0);
    await db.exec('set role authenticated');
    await assert.rejects(db.query("select public.upsert_bonap_plants_batch('[]')"),/permission denied/);
    await db.exec('reset role; set role anon');
    await assert.rejects(db.query("select public.upsert_bonap_plant('{}')"),/permission denied/);
  } finally { await db.close(); }
});
