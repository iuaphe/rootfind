import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

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
    def test_failed_searches_print_requested_and_returned_names_and_urls_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            class FakeDB:
                project_ref = 'a' * 20
                def snapshot(self, _label):
                    return {'plants': [
                        {'plant_id': 1, 'genus': 'Acer', 'species': 'rubrum'},
                        {'plant_id': 2, 'genus': 'Missing', 'species': 'plant'},
                    ], 'dimension_unit': 'inches'}
            class FakeFetcher:
                def __init__(self, *_args): pass
                def get(self, url):
                    if 'basic=Missing' in url:
                        return '<html>PlantFinder: no results</html>'
                    if 'ProfileResults' in url:
                        return '<a href="/PlantFinderDetails.aspx?taxonid=1">Robinia pseudoacacia</a>'
                    return detail_html(**{'Zone': '3 to 8'})
            args = argparse.Namespace(directory=directory, delay=1, limit=None, retry=False, workers=2)
            with patch.object(mobot, 'Fetcher', FakeFetcher):
                for resumed in [False, True]:
                    with patch('sys.stdout', new_callable=io.StringIO) as output:
                        mobot.collect(FakeDB(), args)
                    text = output.getvalue()
                    prefix = 'resumed ' if resumed else ''
                    self.assertIn(f'[{prefix}name_mismatch] plant_id=1', text)
                    self.assertIn("searched='Acer rubrum'", text)
                    self.assertIn("First result label: 'Robinia pseudoacacia'", text)
                    self.assertIn("Detail scientific name: 'Robinia pseudoacacia'", text)
                    self.assertIn('basic=Acer%20rubrum', text)
                    self.assertIn('taxonid=1', text)
                    self.assertIn(f'[{prefix}no_match] plant_id=2', text)
                    self.assertIn('no PlantFinder detail link returned', text)

    def test_workers_share_request_spacing_and_stop_before_new_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(mobot.Fetcher, 'get', return_value=''):
                fetcher = mobot.Fetcher(Path(temp), 1)
            fetcher.delay = 0.03
            starts = []
            def open_page(request, **kwargs):
                starts.append(time.monotonic())
                response = MagicMock()
                response.__enter__.return_value = response
                response.url = request.full_url
                response.headers.get_content_charset.return_value = 'utf-8'
                response.read.return_value = b'Web Page Blocked' if request.full_url.endswith('/blocked') else b'<html>PlantFinder</html>'
                return response
            with patch.object(mobot.urllib.request, 'urlopen', side_effect=open_page) as open_url:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(fetcher.get, [mobot.BASE_URL + f'/test-{i}' for i in range(4)]))
                self.assertEqual(len(starts), 4)
                self.assertTrue(all(b - a >= 0.025 for a, b in zip(starts, starts[1:])), starts)
                # Cache hits consume no network request or pacing slot.
                fetcher.get(mobot.BASE_URL + '/test-0')
                self.assertEqual(open_url.call_count, 4)
                with self.assertRaises(mobot.WebsiteBlocked):
                    fetcher.get(mobot.BASE_URL + '/blocked')
                self.assertTrue(fetcher.stopped.is_set())
                with self.assertRaises(mobot.WebsiteBlocked):
                    fetcher.get(mobot.BASE_URL + '/new')
                self.assertEqual(open_url.call_count, 5)

    def test_parallel_collection_writes_complete_resumable_records(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            plants = [{'plant_id': i, 'genus': 'Robinia', 'species': 'pseudoacacia'} for i in range(1, 5)]
            writes = []
            class FakeDB:
                project_ref = 'a' * 20
                def snapshot(self, _label):
                    return {'plants': plants, 'dimension_unit': 'inches'}
                def query(self, sql):
                    writes.append(sql)
                    return [{'updated_count': 4}]
            active = peak = 0
            lock = threading.Lock()
            class FakeFetcher:
                def __init__(self, *_args): pass
                def get(self, url):
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(active, peak)
                    try:
                        time.sleep(0.02)
                        if 'ProfileResults' in url:
                            return '<a href="/PlantFinderDetails.aspx?taxonid=1">Robinia pseudoacacia</a>'
                        return detail_html(**{'Zone': '3 to 8'})
                    finally:
                        with lock:
                            active -= 1
            args = argparse.Namespace(directory=directory, delay=1, limit=None, retry=False, workers=4)
            with patch.object(mobot, 'Fetcher', FakeFetcher):
                mobot.collect(FakeDB(), args)
                self.assertGreater(peak, 1)
                records = list(mobot.read_plan(directory / 'plan.jsonl').values())
                self.assertEqual({row['plant_id'] for row in records}, {1, 2, 3, 4})
                self.assertTrue(all(row['status'] == 'matched' for row in records))
                mobot.collect(FakeDB(), args)
                self.assertEqual(len((directory / 'plan.jsonl').read_text().splitlines()), 4)
                # Switching an existing read-only collection to live mode must
                # apply its saved matches, even with no new pages to collect.
                args.apply_live = True
                mobot.collect(FakeDB(), args)
                self.assertEqual(len(writes), 1)
                self.assertTrue(all(record['live_applied'] for record in mobot.read_plan(directory / 'plan.jsonl').values()))
                mobot.collect(FakeDB(), args)
                self.assertEqual(len(writes), 1, 'confirmed live commits must not be retried')

    def test_live_writer_checkpoints_only_confirmed_commits_and_retries_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'plan.jsonl'
            record = {'plant_id': 1, 'scientific_name': 'Robinia pseudoacacia',
                      'source_name': 'Robinia pseudoacacia', 'source_url': mobot.BASE_URL,
                      'parser_version': mobot.PARSER_VERSION, 'project_ref': 'a' * 20,
                      'status': 'matched', 'patch': {'min_zone': 3}}
            path.write_text(json.dumps(record) + '\n', encoding='utf-8')
            db = MagicMock(project_ref='a' * 20)
            with path.open('a', encoding='utf-8') as out:
                writer = mobot.LiveWriter(db, out, batch_size=1)
                db.query.side_effect = RuntimeError('database unavailable')
                with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                    writer.add(record)
                self.assertEqual(len(writer.pending), 1)
                self.assertNotIn('live_applied', next(iter(mobot.read_plan(path).values())))
                db.query.side_effect = None
                db.query.return_value = [{'updated_count': None}]
                with self.assertRaisesRegex(RuntimeError, 'no confirmed result'):
                    writer.flush()
                self.assertNotIn('live_applied', next(iter(mobot.read_plan(path).values())))
                db.query.return_value = [{'updated_count': 0, 'confirmed_count': 0}]
                with self.assertRaisesRegex(RuntimeError, 'did not confirm every record'):
                    writer.flush()
                self.assertEqual(len(writer.pending), 1)
                self.assertNotIn('live_applied', next(iter(mobot.read_plan(path).values())))
                # A replay can confirm an earlier commit from its durable audit row.
                db.query.return_value = [{'updated_count': 0, 'confirmed_count': 1}]
                writer.flush()
                self.assertEqual(writer.pending, [])
                self.assertTrue(next(iter(mobot.read_plan(path).values()))['live_applied'])
                calls = db.query.call_count
                writer.flush()
                self.assertEqual(db.query.call_count, calls)

    def test_new_live_collection_commits_before_collection_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            state = {'fetched': 0, 'commits': []}
            plants = [{'plant_id': i, 'genus': 'Robinia', 'species': 'pseudoacacia'} for i in range(1, 13)]
            class FakeDB:
                project_ref = 'a' * 20
                def snapshot(self, _label):
                    return {'plants': plants, 'dimension_unit': 'inches'}
                def query(self, _sql):
                    state['commits'].append(state['fetched'])
                    return [{'updated_count': 10 if len(state['commits']) == 1 else 2}]
            class FakeFetcher:
                def __init__(self, *_args): pass
                def get(self, url):
                    if 'ProfileResults' in url:
                        state['fetched'] += 1
                        return '<a href="/PlantFinderDetails.aspx?taxonid=1">Robinia pseudoacacia</a>'
                    return detail_html(**{'Zone': '3 to 8'})
            args = argparse.Namespace(directory=directory, delay=1, limit=None, retry=False, workers=1, apply_live=True)
            with patch.object(mobot, 'Fetcher', FakeFetcher):
                mobot.collect(FakeDB(), args)
            self.assertEqual(state['commits'], [10, 12])
            self.assertTrue(all(record['live_applied'] for record in mobot.read_plan(directory / 'plan.jsonl').values()))

    def test_cli_rows_accept_user_and_agent_output_including_empty_results(self):
        rows = [{'plant': {'plant_id': 1, 'water': ['medium']}}]
        for result in [rows, {'boundary': 'example', 'rows': rows}, [], {'rows': []}]:
            with self.subTest(result=result):
                self.assertEqual(mobot.parse_cli_rows('Initialising login role...\n' + json.dumps(result)),
                                 result['rows'] if isinstance(result, dict) else result)
        for output in ['No results', '{"rows": null}', '[1, 2]', '{"error": "query failed"}']:
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'no recognized JSON rows'):
                mobot.parse_cli_rows(output)

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
