import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { createHandler } from '../supabase/functions/upsert-plant/handler.ts';

test('upsert_plant RPC correctly inserts and updates plants, counties, and common names', async () => {
  const db = new PGlite();

  // Setup roles and run all migrations
  await db.exec('create role anon; create role authenticated; create role service_role;');
  const migrationFiles = [
    '202610240001_plants.sql',
    '202610240002_search_plants.sql',
    '202610240003_public_search.sql',
    '202610240004_rpc_only_public_search.sql',
    '202610240005_name_search_ids.sql',
    '202610240006_upsert_plant.sql',
  ];

  for (const file of migrationFiles) {
    const sql = await readFile(new URL(`../supabase/migrations/${file}`, import.meta.url), 'utf8');
    await db.exec(sql);
  }

  // 1. Insert a single plant via upsert_plant RPC
  const payload = {
    plant: {
      genus: 'Solidago',
      species: 'canadensis',
      life_cycle: 'perennial',
      foliage: 'deciduous',
      minheight: 1,
      maxheight: 4,
      is_woody: false,
      is_tree: false,
      edible: false,
      thorns: false,
      sun: 'full sun',
      water: 'med',
      image_link: 'https://example.com/soca.jpg',
      misc: ['usda_symbol:SOCA6']
    },
    counties: [
      { fips: 51059, name: 'Fairfax' },
      { fips: 12095, name: 'Orange' }
    ],
    common_names: ['Canada goldenrod', 'goldenrod']
  };

  const res = await db.query<{ upsert_plant: number }>(
    `SELECT public.upsert_plant($1::jsonb) as upsert_plant`,
    [JSON.stringify(payload)]
  );

  const plantId = res.rows[0].upsert_plant;
  assert.ok(plantId > 0, 'Plant ID should be positive integer');

  // Verify plant inserted
  const plantCheck = await db.query<{ genus: string; species: string; maxheight: number }>(
    `SELECT genus, species, maxheight FROM public.plants WHERE plant_id = $1`,
    [plantId]
  );
  assert.equal(plantCheck.rows[0].genus, 'Solidago');
  assert.equal(plantCheck.rows[0].species, 'canadensis');
  assert.equal(plantCheck.rows[0].maxheight, 4);

  // Verify counties inserted & linked
  const countyCheck = await db.query<{ fips: number; name: string }>(
    `SELECT c.fips, c.name FROM public.county_info c
     JOIN public.plant_county pc ON pc.fips = c.fips
     WHERE pc.plant_id = $1 ORDER BY c.fips`,
    [plantId]
  );
  assert.equal(countyCheck.rows.length, 2);
  assert.equal(countyCheck.rows[0].fips, 12095);
  assert.equal(countyCheck.rows[0].name, 'Orange');
  assert.equal(countyCheck.rows[1].fips, 51059);
  assert.equal(countyCheck.rows[1].name, 'Fairfax');

  // Verify common names inserted & linked
  const nameCheck = await db.query<{ name: string }>(
    `SELECT cn.name FROM public.common_names cn
     JOIN public.common_plant cp ON cp.name = cn.name
     WHERE cp.plant_id = $1 ORDER BY cn.name`,
    [plantId]
  );
  assert.equal(nameCheck.rows.length, 2);
  assert.equal(nameCheck.rows[0].name, 'Canada goldenrod');
  assert.equal(nameCheck.rows[1].name, 'goldenrod');

  // 2. Test upsert update on conflict
  const updatePayload = {
    plant: {
      genus: 'Solidago',
      species: 'canadensis',
      maxheight: 5 // Updated height
    },
    counties: [
      { fips: 56001, name: 'Albany' } // Additional county
    ],
    common_names: ['tall goldenrod'] // Additional name
  };

  const updateRes = await db.query<{ upsert_plant: number }>(
    `SELECT public.upsert_plant($1::jsonb) as upsert_plant`,
    [JSON.stringify(updatePayload)]
  );
  assert.equal(updateRes.rows[0].upsert_plant, plantId, 'Should return existing plant_id on conflict');

  const updatedPlant = await db.query<{ maxheight: number; life_cycle: string }>(
    `SELECT maxheight, life_cycle FROM public.plants WHERE plant_id = $1`,
    [plantId]
  );
  assert.equal(updatedPlant.rows[0].maxheight, 5);
  assert.equal(updatedPlant.rows[0].life_cycle, 'perennial'); // Preserved from previous insert

  // 3. Test batch upsert
  const batchPayload = [
    {
      plant: { genus: 'Populus', species: 'x canadensis' },
      counties: [{ fips: 51059, name: 'Fairfax' }],
      common_names: ['Carolina poplar']
    },
    {
      plant: { genus: 'Asclepias', species: 'tuberosa' },
      counties: [{ fips: 51059, name: 'Fairfax' }],
      common_names: ['butterfly milkweed']
    }
  ];

  const batchRes = await db.query<{ upsert_plants_batch: { count: number; success: boolean } }>(
    `SELECT public.upsert_plants_batch($1::jsonb) as upsert_plants_batch`,
    [JSON.stringify(batchPayload)]
  );
  assert.equal(batchRes.rows[0].upsert_plants_batch.success, true);
  assert.equal(batchRes.rows[0].upsert_plants_batch.count, 2);
});

test('upsert-plant Edge Function validates authorization and handles RPC forwarding', async () => {
  const settings = {
    url: 'https://test.supabase.co',
    serviceRoleKey: 'secret-service-role-key',
  };

  let lastUrl = '';
  let lastHeaders: HeadersInit = {};
  let lastBody = '';

  const mockFetch: typeof fetch = async (input, init) => {
    lastUrl = String(input);
    lastHeaders = (init?.headers as Record<string, string>) ?? {};
    lastBody = String(init?.body ?? '');
    return new Response(JSON.stringify({ success: true, plant_id: 42 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const handler = createHandler(settings, mockFetch);

  // 1. Rejects GET
  const getRes = await handler(new Request('https://test.supabase.co/functions/v1/upsert-plant', { method: 'GET' }));
  assert.equal(getRes.status, 405);

  // 2. Rejects unauthorized
  const unauthRes = await handler(new Request('https://test.supabase.co/functions/v1/upsert-plant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plant: { genus: 'Abies', species: 'alba' } })
  }));
  assert.equal(unauthRes.status, 401);

  // 3. Accepts authorized request
  const authRes = await handler(new Request('https://test.supabase.co/functions/v1/upsert-plant', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${settings.serviceRoleKey}`,
    },
    body: JSON.stringify({ plant: { genus: 'Abies', species: 'alba' } })
  }));
  assert.equal(authRes.status, 200);
  assert.equal(lastUrl, 'https://test.supabase.co/rest/v1/rpc/upsert_plant');
  assert.deepEqual(JSON.parse(lastBody), {
    p_payload: { plant: { genus: 'Abies', species: 'alba' } }
  });
});
