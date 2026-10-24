import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdir } from 'node:fs/promises';

test('SQL migration filenames have unique numeric versions', async () => {
  const files = (await readdir(new URL('../supabase/migrations/', import.meta.url)))
    .filter((name) => name.endsWith('.sql'));
  const seen = new Map<string, string>();
  for (const file of files) {
    const version = /^(\d+)_/.exec(file)?.[1];
    assert.ok(version, `Missing numeric version in ${file}`);
    assert.ok(!seen.has(version), `Duplicate migration version ${version}: ${seen.get(version)} and ${file}`);
    seen.set(version, file);
  }
  const ordered = files.sort();
  assert.ok(ordered.indexOf('20261024000601_upsert_plant.sql') < ordered.indexOf('202610240007_plant_details.sql'));
  assert.ok(seen.has('20261024000701'), 'Search filters must have their own migration version');
  assert.ok(seen.has('20261024000702'), 'Taxonomy must have its own migration version');
  assert.ok(ordered.indexOf('20261024000702_plant_taxonomy.sql') < ordered.indexOf('202610240008_mobot_enrichment.sql'));
  assert.ok(ordered.indexOf('20261024000701_search_filters.sql') < ordered.indexOf('202610240008_mobot_enrichment.sql'));
});
