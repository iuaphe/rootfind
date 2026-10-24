import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import import_usda as usda


class TestProgress(unittest.TestCase):
    def run_stream(self, directory, symbols, process, write, **options):
        with patch.object(usda, 'process_plant_online_in_memory', side_effect=process) as fetch, \
             patch.object(usda, 'upsert_plant_to_supabase', side_effect=write) as upsert, \
             patch('sys.stdout', new_callable=io.StringIO):
            result = usda.stream_online_import(
                [(symbol, '', '') for symbol in symbols], Mock(),
                options.get('sql_out'), options.get('db_url'), directory / 'progress.txt',
                workers=1, verbose=False, supabase_url=options.get('url', 'https://example.supabase.co'),
                supabase_key=options.get('key', 'test-key'),
            )
            return result, fetch.call_args_list, upsert.call_count

    def test_failed_fetch_and_write_are_retried_and_only_confirmed_skips_are_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            def process(symbol, *_args, **_kwargs):
                if symbol == 'MISS': return None
                if symbol == 'SKIP': raise usda.DeliberateSkip('Excluded non-plant taxon')
                return usda.PlantRecord(genus='Test', species=symbol.lower())
            result, _, _ = self.run_stream(directory, ['MISS', 'FAIL', 'OK', 'SKIP'], process,
                                           lambda record, *_args, **_kwargs: record.species != 'fail')
            self.assertEqual(result, {'written': 1, 'skipped': 1, 'failed': 2})
            events = [json.loads(line) for line in (directory / 'progress.txt').read_text().splitlines()]
            self.assertEqual({event['symbol']: event['status'] for event in events}, {
                'MISS': 'retry', 'FAIL': 'retry', 'OK': 'written', 'SKIP': 'skipped',
            })
            self.assertTrue(next(event for event in events if event['symbol'] == 'SKIP')['reason'])
            result, calls, writes = self.run_stream(directory, ['MISS', 'FAIL', 'OK', 'SKIP'],
                lambda symbol, *_args, **_kwargs: usda.PlantRecord('Test', symbol.lower()), lambda *_args, **_kwargs: True)
            self.assertEqual({call.args[0] for call in calls}, {'MISS', 'FAIL'})
            self.assertEqual(writes, 2)
            self.assertEqual(result['failed'], 0)
            _, calls, writes = self.run_stream(directory, ['MISS', 'FAIL', 'OK', 'SKIP'],
                lambda *_args, **_kwargs: self.fail('Must resume without fetching'), lambda *_args, **_kwargs: False)
            self.assertEqual(len(calls), 0)
            self.assertEqual(writes, 0)

    def test_legacy_progress_is_not_assumed_successful_and_destinations_are_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'progress.txt').write_text('OLD\n')
            process = lambda *_args, **_kwargs: usda.PlantRecord('Test', 'old')
            with patch('sys.stderr', new_callable=io.StringIO) as stderr:
                _, calls, writes = self.run_stream(directory, ['OLD'], process, lambda *_args, **_kwargs: True)
                self.assertIn('unverified', stderr.getvalue())
            self.assertEqual(len(calls), 1)
            self.assertEqual(writes, 1)
            _, calls, _ = self.run_stream(directory, ['OLD'], process, lambda *_args, **_kwargs: True,
                                         url='https://different.supabase.co')
            self.assertEqual(len(calls), 1, 'writing another project requires a new confirmed write')

    def test_direct_db_failure_is_not_hidden_by_sql_export(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            process = lambda *_args, **_kwargs: usda.PlantRecord('Test', 'plant')
            sql = directory / 'plants.sql'
            with patch.object(usda, 'execute_sql_against_db', return_value=False):
                result, _, _ = self.run_stream(directory, ['TEST'], process, lambda *_args, **_kwargs: True,
                    db_url='postgres://user:private-password@localhost/test', sql_out=sql)
            self.assertEqual(result['failed'], 1)
            self.assertNotIn('private-password', (directory / 'progress.txt').read_text())
            with patch.object(usda, 'execute_sql_against_db', return_value=True):
                result, calls, _ = self.run_stream(directory, ['TEST'], process, lambda *_args, **_kwargs: True,
                    db_url='postgres://user:private-password@localhost/test', sql_out=sql)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['written'], 1)
            content = sql.read_text()
            self.assertEqual(content.count('BEGIN;'), 1)
            self.assertEqual(content.count('COMMIT;'), 1)

    def test_resume_preserves_partial_tail_and_retries_only_unconfirmed_plant(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sql = directory / 'plants.sql'
            process = lambda symbol, *_args, **_kwargs: usda.PlantRecord('Test', symbol.lower())
            self.run_stream(directory, ['OK'], process, None, url=None, key=None, sql_out=sql)
            confirmed = sql.read_bytes()
            partial = b"BEGIN;\nINSERT INTO public.plants VALUES ('unfinished"
            with sql.open('ab') as output:
                output.write(partial)
            result, calls, _ = self.run_stream(directory, ['OK', 'NEXT'], process, None,
                                              url=None, key=None, sql_out=sql)
            self.assertEqual([call.args[0] for call in calls], ['NEXT'])
            self.assertEqual(result['written'], 1)
            expected = usda.generate_sql([usda.PlantRecord('Test', 'next')]).replace('\n', usda.os.linesep).encode('utf-8')
            self.assertEqual(sql.read_bytes(), confirmed + expected)
            self.assertEqual(next(directory.glob('plants.sql.unconfirmed-*.bak')).read_bytes(), partial)
            # Recovery must also happen before the all-complete early return.
            complete = sql.read_bytes()
            with sql.open('ab') as output:
                output.write(partial)
            _, calls, _ = self.run_stream(directory, ['OK', 'NEXT'], process, None,
                                          url=None, key=None, sql_out=sql)
            self.assertEqual(calls, [])
            self.assertEqual(sql.read_bytes(), complete)

    def test_sql_only_export_is_durable_and_missing_output_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sql = directory / 'plants.sql'
            process = lambda *_args, **_kwargs: usda.PlantRecord('Test', 'plant')
            result, _, writes = self.run_stream(directory, ['TEST'], process, lambda *_args, **_kwargs: False,
                                                url=None, key=None, sql_out=sql)
            self.assertEqual(result['written'], 1)
            self.assertEqual(writes, 0)
            event = json.loads((directory / 'progress.txt').read_text().splitlines()[-1])
            self.assertEqual(event['status'], 'exported')
            self.assertEqual(event['sql_end'], sql.stat().st_size)
            sql.unlink()
            _, calls, _ = self.run_stream(directory, ['TEST'], process, lambda *_args, **_kwargs: False,
                                          url=None, key=None, sql_out=sql)
            self.assertEqual(len(calls), 1)


class TestFetchFailures(unittest.TestCase):
    def test_partial_fetches_are_retryable_and_exclusions_are_deliberate(self):
        resolver = Mock()
        resolver.get_plant_data.return_value = (None, None)
        with self.assertRaisesRegex(RuntimeError, 'Profile unavailable'):
            usda.process_plant_online_in_memory('TEST', resolver)
        profile = {'Id': 1, 'ScientificName': 'Acer rubrum', 'Rank': 'Species'}
        resolver.get_plant_data.return_value = ({**profile, 'HasCharacteristics': True}, None)
        with self.assertRaisesRegex(RuntimeError, 'Characteristics unavailable'):
            usda.process_plant_online_in_memory('TEST', resolver)
        resolver.get_plant_data.return_value = (profile, [])
        with patch.object(usda, 'download_distribution_csv_in_memory', return_value=None), \
             self.assertRaisesRegex(RuntimeError, 'Distribution CSV unavailable'):
            usda.process_plant_online_in_memory('TEST', resolver)
        with patch.object(usda, 'download_distribution_csv_in_memory', return_value='<html>Server error</html>'), \
             self.assertRaisesRegex(RuntimeError, 'Unrecognized distribution CSV'):
            usda.process_plant_online_in_memory('TEST', resolver)
        with patch.object(usda, 'download_distribution_csv_in_memory', return_value='Symbol,Country,State,County,County FIP\n'):
            record = usda.process_plant_online_in_memory('TEST', resolver)
            self.assertEqual(record.genus, 'Acer')
            self.assertEqual(record.counties, set(), 'valid empty distribution is not a failed request')
        resolver.get_plant_data.return_value = ({**profile, 'Group': 'Fungi'}, [])
        with self.assertRaises(usda.DeliberateSkip):
            usda.process_plant_online_in_memory('TEST', resolver)
        resolver.get_plant_data.return_value = ({**profile, 'Rank': 'Variety'}, [])
        with self.assertRaises(usda.DeliberateSkip):
            usda.process_plant_online_in_memory('TEST', resolver)

    def test_missing_cached_characteristics_are_refetched_until_success(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            profile = {'Id': 1, 'ScientificName': 'Acer rubrum', 'HasCharacteristics': True}
            (cache / 'TEST_profile.json').write_text(json.dumps(profile))
            resolver = usda.PlantMetadataResolver(cache_dir=str(cache))
            with patch.object(resolver, '_fetch_json', side_effect=[None, []]) as fetch:
                self.assertIsNone(resolver.get_plant_data('TEST')[1])
                self.assertEqual(resolver.get_plant_data('TEST')[1], [])
                self.assertEqual(resolver.get_plant_data('TEST')[1], [])
                self.assertEqual(fetch.call_count, 2, 'successful empty characteristics are cached')

    def test_supabase_requires_a_confirmed_id_not_just_http_200(self):
        record = usda.PlantRecord('Test', 'plant')
        for body, expected in [(b'42', True), (b'false', False), (b'null', False),
                               (b'{"error":"failed"}', False), (b'', False)]:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.read.return_value = body
            with patch('urllib.request.urlopen', return_value=response), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(usda.upsert_plant_to_supabase(record, 'https://example.test', 'test-key'), expected)


if __name__ == '__main__':
    unittest.main()
