import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import enrich_mobot as mobot


def detail_html(**fields):
    rows = ''.join(f'<div id="ctl00_MainContentPlaceHolder_{suffix}">{label}: {fields[label]}</div>'
                   for suffix, label in mobot.FIELDS.items() if label in fields)
    return '<span id="ctl00_dnn_srTitle_lblTitle"><i>Robinia</i> <i>pseudoacacia</i></span>' + rows


class TestParsing(unittest.TestCase):
    def test_first_result_not_first_arbitrary_link(self):
        html = '''<a href="/Home.aspx">Home</a>
          <a href="/PlantFinderDetails.aspx?taxonid=1&amp;basic=Robinia%20pseudoacacia"><i>Robinia</i> pseudoacacia</a>
          <a href="/PlantFinderDetails.aspx?taxonid=2">Cultivar</a>'''
        first = mobot.first_result(html)
        self.assertEqual(first['name'], 'Robinia pseudoacacia')
        self.assertEqual(first['url'], mobot.BASE_URL + '/PlantFinderDetails.aspx?taxonid=1&basic=Robinia%20pseudoacacia')
        self.assertIsNone(mobot.first_result('<html>PlantFinder: no results</html>'))

    def test_user_example(self):
        parsed = mobot.parse_details(detail_html(**{
            'Zone': '3 to 8', 'Height': '30.00 to 50.00 feet', 'Spread': '20.00 to 35.00 feet',
            'Bloom Time': 'May to June', 'Water': 'Dry to medium',
        }))
        self.assertEqual(parsed['source_name'], 'Robinia pseudoacacia')
        self.assertEqual(parsed['patch'], {
            'min_zone': 3, 'max_zone': 8, 'minheight': 360, 'maxheight': 600,
            'minwidth': 240, 'maxwidth': 420, 'bloom_months': ['May', 'June'], 'water': ['low', 'medium'],
        })
        self.assertEqual(parsed['warnings'], [])

    def test_measurement_conversion_rounding_and_limits(self):
        self.assertEqual(mobot.parse_measurement('0.1 to 0.4 feet'), (1, 5))
        self.assertEqual(mobot.parse_measurement('5 feet'), (60, 60))
        self.assertEqual(mobot.parse_measurement('1.5 to 2.5 inches'), (1, 3))
        for text in ['50 to 30 feet', '3000 feet', '-1 feet', '10 meters', 'Unknown']:
            with self.assertRaises(ValueError):
                mobot.parse_measurement(text)

    def test_zones_month_ranges_and_unknown_bloom(self):
        self.assertEqual(mobot.parse_zone('5'), (5, 5))
        self.assertEqual(mobot.parse_zone('3a to 8b'), (3, 8))
        self.assertEqual(mobot.parse_months('May to August'), ['May', 'June', 'July', 'August'])
        self.assertEqual(mobot.parse_months('November to February'), ['January', 'February', 'November', 'December'])
        self.assertEqual(mobot.parse_months('May, July and September'), ['May', 'July', 'September'])
        self.assertEqual(mobot.parse_months('Non-flowering'), [])
        with self.assertRaises(ValueError):
            mobot.parse_months('May to frost')
        parsed = mobot.parse_details(detail_html(**{'Zone': '5', 'Bloom Time': 'Spring'}))
        self.assertEqual(parsed['patch'], {'min_zone': 5, 'max_zone': 5})
        self.assertEqual(len(parsed['warnings']), 1)

    def test_water_ranges_and_merge(self):
        self.assertEqual(mobot.parse_water('Dry to wet'), ['low', 'medium', 'high'])
        self.assertEqual(mobot.parse_water('Medium to wet'), ['medium', 'high'])
        self.assertEqual(mobot.parse_water('Dry, wet'), ['low', 'high'])
        merged = mobot.merge_preview({'minheight': 10, 'maxheight': 20, 'water': 'high'}, {
            'minheight': 144, 'maxheight': 300, 'water': ['low', 'medium'],
        }, 'feet')
        self.assertEqual(merged['minheight'], 120)
        self.assertEqual(merged['maxheight'], 300)
        self.assertEqual(merged['water'], ['low', 'medium', 'high'])

    def test_firewall_and_scientific_name_checks(self):
        for html in ['Web Page Blocked!', 'Attack ID: 20000008', '<html>CAPTCHA</html>']:
            with self.assertRaises(mobot.WebsiteBlocked):
                mobot.first_result(html)
        for actual in ["Robinia pseudoacacia 'Lace Lady'", 'Robinia pseudoacacia var. rectissima',
                       'Robinia pseudoacacia subsp. pseudoacacia', 'Robinia pseudoacacia ssp. pseudoacacia',
                       'Robinia pseudoacacia f. inermis', 'Robinia pseudoacacia L.']:
            self.assertFalse(mobot.scientific_match('Robinia pseudoacacia', actual), actual)
        self.assertTrue(mobot.scientific_match('Robinia pseudoacacia', '  ROBINIA\xa0 PSEUDOACACIA  '))
        self.assertTrue(mobot.scientific_match('Populus x canadensis', 'Populus × canadensis'))
        self.assertTrue(mobot.scientific_match('Populus x canadensis', 'Populus ×canadensis'))
        self.assertFalse(mobot.scientific_match('Populus x canadensis', "Populus × canadensis 'Robusta'"))
        self.assertFalse(mobot.scientific_match('Acer rubrum', 'Acer rubrumfoo'))
        self.assertFalse(mobot.scientific_match('Robinia pseudoacacia', 'Robinia hispida'))
        self.assertFalse(mobot.scientific_match('Robinia pseudoacacia', None))


class TestCollection(unittest.TestCase):
    def test_apply_rejects_old_plans_and_suffix_matches_even_if_marked_matched(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            names = ['Robinia pseudoacacia'] * 5
            sources = ['Robinia pseudoacacia', "Robinia pseudoacacia 'Lace Lady'",
                       'Robinia pseudoacacia var. rectissima', 'Robinia pseudoacacia subsp. pseudoacacia',
                       '  ROBINIA   PSEUDOACACIA  ']
            records = [{
                'plant_id': i + 1, 'scientific_name': names[i], 'source_name': sources[i],
                'parser_version': 1 if i == 0 else mobot.PARSER_VERSION,
                'project_ref': 'a' * 20, 'status': 'matched',
                'source_url': f'{mobot.BASE_URL}/PlantFinderDetails.aspx?taxonid={i+1}',
                'patch': {'max_zone': 8},
            } for i in range(5)]
            (directory / 'plan.jsonl').write_text('\n'.join(json.dumps(record) for record in records), encoding='utf-8')
            queries = []
            class FakeDB:
                project_ref = 'a' * 20
                def snapshot(self, _label):
                    return {'dimension_unit': 'inches', 'plants': [
                        {'plant_id': i + 1, 'genus': 'Robinia', 'species': 'pseudoacacia'} for i in range(5)
                    ]}
                def query(self, sql):
                    queries.append(sql)
                    return [{'updated_count': 1}]
            mobot.apply(FakeDB(), argparse.Namespace(directory=directory))
            self.assertEqual(len(queries), 1)
            for plant_id in range(1, 5):
                self.assertNotIn(f'"plant_id": {plant_id}', queries[0])
            self.assertIn('"plant_id": 5', queries[0])

    def test_snapshot_keeps_json_arrays_and_uses_current_database_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            db = mobot.Database(Path(temp), 'a' * 20)
            metadata = [{'dimension_comment': 'Unit: inches (MOBOT enrichment v1)', 'columns': [], 'functions': []}]
            with patch.object(db, 'query', side_effect=[metadata, [
                {'plant': {'plant_id': 5, 'genus': 'Acer', 'species': 'rubrum', 'water': ['medium']}},
                {'plant': {'plant_id': 10000, 'genus': 'Quercus', 'species': 'alba', 'water': None}},
            ], []]) as query:
                saved = db.snapshot('test')
                self.assertEqual(saved['dimension_unit'], 'inches')
                self.assertEqual(saved['plants'][0]['water'], ['medium'])
                self.assertEqual(len(saved['plants']), 2)
                self.assertIn('plant_id > 10000', query.call_args_list[-1].args[0])
                self.assertEqual(len(list(Path(temp).glob('test-*.json'))), 1)

    def test_url_encoding_resume_and_newly_added_plants(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plants = [{'plant_id': 1, 'genus': 'Robinia', 'species': 'pseudoacacia'}]
            class FakeDB:
                project_ref = 'a' * 20
                def snapshot(self, _label):
                    return {'plants': plants, 'dimension_unit': 'feet'}
            requests = []
            class FakeFetcher:
                def __init__(self, *_args): pass
                def get(self, url):
                    requests.append(url)
                    if 'ProfileResults' in url:
                        return '<a href="/PlantFinderDetails.aspx?taxonid=1">Robinia pseudoacacia</a>'
                    return detail_html(**{'Zone': '3 to 8'})
            args = argparse.Namespace(directory=directory, delay=1.5, limit=None, retry=False)
            with patch.object(mobot, 'Fetcher', FakeFetcher):
                mobot.collect(FakeDB(), args)
                self.assertIn('basic=Robinia%20pseudoacacia', requests[0])
                self.assertEqual(len(requests), 2)
                plants.append({'plant_id': 2, 'genus': 'Robinia', 'species': 'pseudoacacia'})
                mobot.collect(FakeDB(), args)
                self.assertEqual(len(requests), 4, 'already collected plant is skipped; new plant is collected')
            records = list(mobot.read_plan(directory / 'plan.jsonl').values())
            self.assertEqual([record['plant_id'] for record in records], [1, 2])
            self.assertTrue(all(record['status'] == 'matched' for record in records))

    def test_quote_sql_is_not_string_interpolation(self):
        record = {'plant_id': 1, 'scientific_name': "Plant o'brien", 'source_url': mobot.BASE_URL,
                  'patch': {'water': ['low'], 'minheight': 1}}
        sql = mobot.build_apply_sql([record], 'test')
        self.assertIn("o''brien", sql)
        self.assertIn('for update of p', sql)
        self.assertIn('rootfind_private.mobot_changes', sql)


if __name__ == '__main__':
    unittest.main()
