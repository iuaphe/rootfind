import argparse
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import enrich_catalog as catalog
import enrich_mobot as core


NC = '''<div id="plant_detail"><h1><em>Echinacea purpurea</em><small>Play pronunciation</small></h1>
<dl><dt>Dimensions:</dt><dd>Height: 3 ft. 0 in. - 4 ft. 0 in.</dd><dd>Width: 1 ft. 0 in. - 2 ft. 0 in.</dd>
<dt>Light:</dt><dd>Full sun (6 or more hours of direct sunlight a day)</dd><dd>Partial Shade (2-6 hours)</dd>
<dt>Soil Drainage:</dt><dd>Good Drainage</dd><dd>Moist</dd><dd>Occasionally Dry</dd>
<dt>USDA Plant Hardiness Zone:</dt><dd>3a, 3b, 4a, 8b</dd>
<dt>Flower Bloom Time:</dt><dd>Summer</dd>
<dt>Flower Description:</dt><dd>Flowers bloom from June to August.</dd>
<dt>Flower Size:</dt><dd>3-6 inches</dd></dl></div>'''
PFAF = '''<h1><span id="ContentPlaceHolder1_lbldisplatinname">Echinacea purpurea - (L.)Moench.</span></h1>
<span id="ContentPlaceHolder1_lblUSDAhardiness">3-10</span>
<span id="ContentPlaceHolder1_lblPhystatment">Echinacea purpurea is a PERENNIAL growing to 1.2 m (4ft) by 0.5 m (1ft 8in) at a medium rate.
It is hardy to UK zone 3. It is in flower from July to August.
It cannot grow in the shade. It prefers dry or moist soil and can tolerate drought.</span>
<p>Related Plants: 200 m tall; hardy to zone 1. Seed harvest September to November.</p>'''


class TestParsers(unittest.TestCase):
    def test_nc_fields_and_seasons_do_not_invent_months(self):
        result = catalog.parse_ncstate(NC)
        self.assertEqual(result['source_name'], 'Echinacea purpurea')
        self.assertEqual(result['patch'], {'minheight': 36, 'maxheight': 48, 'minwidth': 12, 'maxwidth': 24,
            'sun': ['full sun', 'part shade'], 'water': ['low', 'medium'],
            'drainage': ['Good Drainage', 'Occasionally Dry'], 'min_zone': 3, 'max_zone': 8,
            'bloom_seasons': ['Summer'], 'bloom_months': ['June', 'July', 'August']})
        seasonal = catalog.parse_ncstate(NC.replace('Flowers bloom from June to August.', 'Blooms in summer.'))
        self.assertNotIn('bloom_months', seasonal['patch'])
        self.assertEqual(seasonal['patch']['bloom_seasons'], ['Summer'])
        cultivar = catalog.parse_ncstate(NC.replace('</em>', "</em> 'Magnus'"))
        self.assertFalse(core.scientific_match('Echinacea purpurea', cultivar['source_name']))

    def test_nc_unknown_and_drainage_do_not_invent_water(self):
        html = NC.replace('<dd>Moist</dd><dd>Occasionally Dry</dd>', '')
        html = html.replace('Full sun (6 or more hours of direct sunlight a day)', 'Dappled Sunlight (filtered all day)')
        html = html.replace('3 ft. 0 in. - 4 ft. 0 in.', '5 ft. - 1 ft.')
        result = catalog.parse_ncstate(html)
        self.assertNotIn('water', result['patch'])
        self.assertNotIn('minheight', result['patch'])
        self.assertTrue(any('Dappled' in warning for warning in result['warnings']))

    def test_pfaf_scoped_fields_authority_and_metric_units(self):
        result = catalog.parse_pfaf(PFAF)
        self.assertEqual(result['source_name'], 'Echinacea purpurea')
        self.assertEqual(result['patch'], {'min_zone': 3, 'max_zone': 10, 'maxheight': 48, 'maxwidth': 20,
            'bloom_months': ['July', 'August'], 'sun': ['full sun'], 'water': ['low', 'medium']})
        self.assertNotIn('minheight', result['patch'])
        self.assertFalse(core.scientific_match('Echinacea purpurea', catalog.parse_pfaf(
            PFAF.replace('purpurea -', "purpurea 'White Swan' -"))['source_name']))
        no_usda = catalog.parse_pfaf(PFAF.replace('3-10</span>', '</span>'))
        self.assertNotIn('min_zone', no_usda['patch'], 'UK hardiness must not become USDA zone')
        wet_range = catalog.parse_pfaf(PFAF.replace('dry or moist soil', 'dry to wet soil'))
        self.assertEqual(wet_range['patch']['water'], ['low', 'medium', 'high'])

    def test_direct_lookups_reject_wrong_species_and_record_missing_profiles(self):
        source = catalog.Source('pfaf')
        fetcher = Mock()
        fetcher.get.return_value = PFAF
        self.assertEqual(source.lookup('Echinacea pallida', fetcher)['status'], 'name_mismatch')
        fetcher.get.return_value = '<span id="ContentPlaceHolder1_lbldisplatinname"></span><span id="ContentPlaceHolder1_lblPhystatment"></span>'
        self.assertEqual(source.lookup('Missing plant', fetcher)['status'], 'no_match')
        failure = urllib.error.HTTPError(source.profile_url('Missing plant'), 404, 'missing', {}, None)
        fetcher.get.side_effect = failure
        self.assertEqual(source.lookup('Missing plant', fetcher)['status'], 'no_match')
        failure.close()
        self.assertIn('LatinName=Echinacea+purpurea', source.profile_url('Echinacea purpurea'))
        self.assertTrue(catalog.Source('ncstate').profile_url('Echinacea purpurea').endswith('/echinacea-purpurea/'))

    def test_wrong_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'migration 202610240010'):
            catalog.Source('ncstate').validate_snapshot({'dimension_unit': 'inches', 'schema': {'columns': []}})

    def test_old_mobot_prepare_cannot_restore_scalar_sun_upserts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'Database') as factory:
            factory.return_value.snapshot.return_value = {'schema': {'columns': [
                {'column_name': 'sun', 'udt_name': '_sun_type'}]}}
            with self.assertRaisesRegex(ValueError, 'skip MOBOT prepare'):
                core.main(['prepare', '--directory', temp])
            factory.return_value.query.assert_not_called()

    def test_source_collection_uses_separate_plan_and_live_sql(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            db = Mock(project_ref='a' * 20)
            db.snapshot.return_value = {'plants': [{'plant_id': 1, 'genus': 'Echinacea', 'species': 'purpurea'}],
                'dimension_unit': 'inches', 'schema': {'columns': [
                    {'column_name': 'sun', 'udt_name': '_sun_type'},
                    {'column_name': 'bloom_seasons', 'udt_name': '_text'},
                    {'column_name': 'drainage', 'udt_name': '_text'}]}}
            db.query.return_value = [{'updated_count': 1}]
            args = argparse.Namespace(directory=directory, source_adapter=catalog.Source('ncstate'),
                apply_live=True, workers=1, delay=1, retry=False, limit=None)
            with patch.object(core, 'Fetcher') as factory, patch('sys.stdout', new_callable=io.StringIO):
                factory.return_value.get.return_value = NC
                core.collect(db, args)
            record = next(iter(core.read_plan(directory / 'plan.jsonl').values()))
            self.assertEqual(record['source'], 'ncstate')
            self.assertTrue(record['live_applied'])
            self.assertIn('sun public.sun_type[]', db.query.call_args.args[0])
            args.source_adapter = catalog.Source('pfaf')
            with self.assertRaisesRegex(ValueError, 'different enrichment source'):
                core.collect(db, args)


if __name__ == '__main__':
    unittest.main()
