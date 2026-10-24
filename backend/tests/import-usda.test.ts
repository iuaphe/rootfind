import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { PGlite } from '@electric-sql/pglite';

const execFileAsync = promisify(execFile);

test('import_usda.py correctly populates schema and relations in Postgres', async () => {
  const db = new PGlite();
  const testDir = join(tmpdir(), `usda-import-test-${Date.now()}`);
  await mkdir(testDir, { recursive: true });

  try {
    // 1. Setup Supabase schema and roles
    await db.exec('create role anon; create role authenticated;');
    for (const file of ['202610240001_plants.sql', '202610240002_search_plants.sql']) {
      await db.exec(await readFile(new URL(`../supabase/migrations/${file}`, import.meta.url), 'utf8'));
    }

    // 2. Create test CSV files
    // File A: Standard Species with multiple counties and non-county lines
    const socaCsv = `Distribution Data
Symbol,Country,State,State FIP,County,County FIP
SOCA6,Canada,Alberta,01,,
SOCA6,United States,Alaska,02,,
SOCA6,United States,Virginia,51,Fairfax,059
SOCA6,United States,Florida,12,Orange,095
SOCA6,United States,Wyoming,56,Albany,001
`;
    await writeFile(join(testDir, 'SOCA6.csv'), socaCsv, 'utf8');

    // File B: Hybrid Plant (Populus x canadensis)
    const poxcaCsv = `Symbol,Country,State,State FIP,County,County FIP
POXCA,United States,Virginia,51,Fairfax,059
`;
    await writeFile(join(testDir, 'POXCA.csv'), poxcaCsv, 'utf8');

    // File C: Variety (MUST be skipped)
    const socacCsv = `Distribution Data
Symbol,Country,State,State FIP,County,County FIP
SOCAC,United States,Virginia,51,Fairfax,059
`;
    await writeFile(join(testDir, 'SOCAC.csv'), socacCsv, 'utf8');

    // File D: Subspecies (MUST be skipped)
    const vivivCsv = `Distribution Data
Symbol,Country,State,State FIP,County,County FIP
VIVIV8,United States,Florida,12,Orange,095
`;
    await writeFile(join(testDir, 'VIVIV8.csv'), vivivCsv, 'utf8');

    // File E: Species with missing characteristics (NULL verification)
    const astuCsv = `Symbol,Country,State,State FIP,County,County FIP
ASTU,United States,Virginia,51,Fairfax,059
`;
    await writeFile(join(testDir, 'ASTU.csv'), astuCsv, 'utf8');

    // 3. Create metadata JSON for offline resolution
    const metadata = {
      SOCA6: {
        Symbol: 'SOCA6',
        ScientificName: '<i>Solidago canadensis</i> L.',
        CommonName: 'Canada goldenrod',
        Rank: 'Species',
        Durations: ['Perennial'],
        GrowthHabits: ['Forb/herb'],
        ProfileImageFilename: 'soca6.jpg',
        Characteristics: [
          { PlantCharacteristicName: 'Leaf Retention', PlantCharacteristicValue: 'No' },
          { PlantCharacteristicName: 'Height, Mature (feet)', PlantCharacteristicValue: '3.5' },
          { PlantCharacteristicName: 'Shade Tolerance', PlantCharacteristicValue: 'High' },
          { PlantCharacteristicName: 'Moisture Use', PlantCharacteristicValue: 'Medium' },
          { PlantCharacteristicName: 'Palatable Human', PlantCharacteristicValue: 'No' }
        ]
      },
      POXCA: {
        Symbol: 'POXCA',
        ScientificName: 'Populus ×canadensis Moench',
        CommonName: 'Carolina poplar',
        Rank: 'Species',
        Durations: ['Perennial'],
        GrowthHabits: ['Tree'],
        Characteristics: [
          { PlantCharacteristicName: 'Height, Mature (feet)', PlantCharacteristicValue: '80' }
        ]
      },
      SOCAC: {
        Symbol: 'SOCAC',
        ScientificName: 'Solidago canadensis L. var. canadensis',
        CommonName: 'Canada goldenrod',
        Rank: 'Variety'
      },
      VIVIV8: {
        Symbol: 'VIVIV8',
        ScientificName: 'Vicia villosa Roth ssp. varia (Host) Corb.',
        CommonName: 'winter vetch',
        Rank: 'Subspecies'
      },
      ASTU: {
        Symbol: 'ASTU',
        ScientificName: 'Asclepias tuberosa L.',
        CommonName: 'Butterfly milkweed',
        Rank: 'Species'
        // Intentionally missing characteristics to verify NULL handling
      }
    };
    const metaPath = join(testDir, 'metadata.json');
    await writeFile(metaPath, JSON.stringify(metadata), 'utf8');

    // 4. Run import_usda.py generating SQL
    const sqlOutPath = join(testDir, 'import.sql');
    const scriptPath = join(new URL('../scripts/import_usda.py', import.meta.url).pathname);

    await execFileAsync('python3', [
      scriptPath,
      join(testDir, '*.csv'),
      '--sql-out', sqlOutPath,
      '--metadata', metaPath,
      '--offline'
    ]);

    // 5. Execute generated SQL in PGlite
    const generatedSql = await readFile(sqlOutPath, 'utf8');
    await db.exec(generatedSql);

    // 6. Assert database contents
    // 6a. Plants table
    const { rows: plants } = await db.query<{
      plant_id: number;
      genus: string;
      species: string;
      life_cycle: string | null;
      foliage: string | null;
      sun: string | null;
      water: string | null;
      maxheight: number | null;
      is_tree: boolean | null;
      is_woody: boolean | null;
      edible: boolean | null;
      image_link: string | null;
    }>('select * from public.plants order by genus, species;');

    // Only 3 plants must be present (Solidago canadensis, Populus x canadensis, Asclepias tuberosa)
    // Variety (SOCAC) and Subspecies (VIVIV8) MUST NOT be present
    assert.equal(plants.length, 3, 'varieties and subspecies are excluded');

    const asclepias = plants.find(p => p.genus === 'Asclepias' && p.species === 'tuberosa');
    assert.ok(asclepias, 'Asclepias tuberosa was inserted');
    assert.equal(asclepias.life_cycle, null, 'missing life_cycle is null');
    assert.equal(asclepias.foliage, null, 'missing foliage is null');
    assert.equal(asclepias.sun, null, 'missing sun is null');
    assert.equal(asclepias.water, null, 'missing water is null');
    assert.equal(asclepias.is_tree, null, 'missing is_tree is null');

    const populus = plants.find(p => p.genus === 'Populus');
    assert.ok(populus, 'Populus hybrid was inserted');
    assert.equal(populus.species, 'x canadensis', 'hybrid species formatted as "x hybridname"');
    assert.equal(populus.is_tree, true, 'is_tree is true for tree');
    assert.equal(populus.maxheight, 80);

    const solidago = plants.find(p => p.genus === 'Solidago');
    assert.ok(solidago, 'Solidago canadensis was inserted');
    assert.equal(solidago.species, 'canadensis');
    assert.equal(solidago.life_cycle, 'perennial');
    assert.equal(solidago.foliage, 'deciduous');
    assert.equal(solidago.sun, 'full shade');
    assert.equal(solidago.water, 'med');
    assert.equal(solidago.edible, false);
    assert.equal(solidago.maxheight, 4);
    assert.ok(solidago.image_link?.includes('soca6.jpg'));

    // 6b. Counties table
    const { rows: counties } = await db.query<{ fips: number; name: string }>(
      'select * from public.county_info order by fips;'
    );
    // 12095 (Orange), 51059 (Fairfax), 56001 (Albany)
    assert.equal(counties.length, 3);
    assert.deepEqual(counties.map(c => c.fips), [12095, 51059, 56001]);
    assert.equal(counties.find(c => c.fips === 51059)?.name, 'Fairfax');
    assert.equal(counties.find(c => c.fips === 12095)?.name, 'Orange');
    assert.equal(counties.find(c => c.fips === 56001)?.name, 'Albany');

    // 6c. Plant County Join table
    const { rows: plantCounties } = await db.query<{ fips: number; plant_id: number }>(
      'select * from public.plant_county order by fips, plant_id;'
    );
    // Solidago is in Fairfax (51059), Orange (12095), Albany (56001)
    // Populus is in Fairfax (51059)
    // Asclepias is in Fairfax (51059)
    // Total plant-county joins = 5
    assert.equal(plantCounties.length, 5);

    const solidagoCounties = plantCounties.filter(pc => pc.plant_id === solidago.plant_id).map(pc => pc.fips);
    assert.deepEqual(solidagoCounties.sort((a, b) => a - b), [12095, 51059, 56001]);

    // 6d. Common Names & Join table
    const { rows: commonNames } = await db.query<{ name: string }>(
      'select * from public.common_names order by name;'
    );
    assert.deepEqual(commonNames.map(cn => cn.name), [
      'Butterfly milkweed',
      'Canada goldenrod',
      'Carolina poplar'
    ]);

    // 6e. Verify with search_plants RPC
    setRole: await db.exec('set role authenticated;');
    const search = async (filters: Record<string, unknown>) => {
      const { rows } = await db.query<{ result: { data: { plant_id: number; genus: string; species: string }[]; total: number } }>(
        'select public.search_plants($1::jsonb) as result;', [JSON.stringify(filters)]
      );
      return rows[0].result;
    };

    // Search by county FIPS
    const albanySearch = await search({ county: ['56001'] });
    assert.equal(albanySearch.total, 1);
    assert.equal(albanySearch.data[0].genus, 'Solidago');

    const fairfaxSearch = await search({ county: ['51059'] });
    assert.equal(fairfaxSearch.total, 3, 'all 3 species present in Fairfax');

    // Search by common name
    const milkweedSearch = await search({ search: 'milkweed' });
    assert.equal(milkweedSearch.total, 1);
    assert.equal(milkweedSearch.data[0].genus, 'Asclepias');

    // Search by hybrid name
    const hybridSearch = await search({ search: 'canadensis' });
    // Matches Solidago canadensis and Populus x canadensis
    assert.equal(hybridSearch.total, 2);

  } finally {
    await db.close();
    await rm(testDir, { recursive: true, force: true });
  }
});
