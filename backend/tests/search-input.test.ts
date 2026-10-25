import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  taxonomyFields, tokenizeSearchInput, parseSearchInput, applySearchInput, setSpecifier,
} from '../../frontend/src/lib/plant-search-input.ts';
import { taxonomyFields as serverFields } from '../supabase/functions/_shared/plant-search-query.ts';

test('search chips support all server fields and combine with remaining free text', () => {
  assert.deepEqual(taxonomyFields, serverFields);
  const parsed = parseSearchInput('family=Aceraceae, class_common=Dicotyledons, purple', []);
  assert.deepEqual(parsed, { specifiers: [
    { field: 'family', value: 'Aceraceae' }, { field: 'class_common', value: 'Dicotyledons' },
  ], text: 'purple' });
  const params = applySearchInput(new URLSearchParams('edible=true'), parsed.text, parsed.specifiers);
  assert.deepEqual(Object.fromEntries(params), {
    edible: 'true', search: 'purple', family: 'Aceraceae', class_common: 'Dicotyledons',
  });
  assert.deepEqual(parseSearchInput('FAMILY = Violaceae', parsed.specifiers).specifiers, [
    { field: 'family', value: 'Violaceae' }, { field: 'class_common', value: 'Dicotyledons' },
  ]);
});

test('typing preserves incomplete specifiers and common-name spaces until committed', () => {
  assert.deepEqual(tokenizeSearchInput('family=Ac', []), { specifiers: [], text: 'family=Ac' });
  assert.deepEqual(tokenizeSearchInput('family_common=Maple family, purple', []), {
    specifiers: [{ field: 'family_common', value: 'Maple family' }], text: ' purple',
  });
  assert.deepEqual(parseSearchInput('purple, family=Aceraceae, ', []), {
    specifiers: [{ field: 'family', value: 'Aceraceae' }], text: 'purple',
  });
  assert.equal(parseSearchInput(' purple   coneflower ', []).text, 'purple coneflower');
  assert.equal(parseSearchInput('maple, red', []).text, 'maple, red', 'name punctuation is preserved');
  assert.equal(setSpecifier([{ field: 'family', value: 'A' }], { field: 'family', value: 'B' }).length, 1);
  for (const text of ['family=', 'plant_id=1', 'lineage=x', 'unknown=x', `family=${'x'.repeat(101)}`, 'x'.repeat(201)]) {
    assert.throws(() => parseSearchInput(text, []), undefined, text);
  }
});
