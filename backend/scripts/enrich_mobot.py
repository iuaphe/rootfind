#!/usr/bin/env python3
"""Resumable MOBOT enrichment. Collection is read-only; prepare/apply are explicit.

Uses the repo's installed Supabase CLI and existing login, never an embedded key.
Run --help, or see scripts/MOBOT.md. Requires only Python's standard library.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid

BACKEND = Path(__file__).resolve().parents[1]
BASE_URL = 'https://plantfinder.mobot.org'
PARSER_VERSION = 2
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December']
WATER = ['low', 'medium', 'high']
DIMENSIONS = ['minheight', 'maxheight', 'minwidth', 'maxwidth']
FIELDS = {
    'ZoneRow': 'Zone', 'HeightRow': 'Height', 'SpreadRow': 'Spread',
    'BloomTimeRow': 'Bloom Time', 'WaterRow': 'Water',
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(text: str) -> str:
    return ' '.join(text.replace('\xa0', ' ').split())


class PlantHTML(HTMLParser):
    """Read only known field containers and result anchors; HTML is data, not code."""
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str, list[str]]] = []
        self.elements: list[tuple[str, str]] = []
        self.links: list[dict] = []
        self.active_links: list[dict] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            link = {'href': attrs.get('href', ''), 'id': attrs.get('id', ''), 'parts': []}
            self.active_links.append(link)
            self.links.append(link)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, attrs.get('id', ''), []))

    def handle_endtag(self, tag):
        if tag == 'a' and self.active_links:
            self.active_links.pop()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, element_id, parts in self.stack[index:]:
                    if element_id:
                        self.elements.append((element_id, normalize(''.join(parts))))
                del self.stack[index:]
                break

    def handle_data(self, text):
        if any(tag in {'script', 'style'} for tag, _, _ in self.stack):
            return
        for _, _, parts in self.stack:
            parts.append(text)
        for link in self.active_links:
            link['parts'].append(text)


def check_page(html: str):
    if re.search(r'Web Page Blocked|Attack ID:|Access Denied|captcha', html, re.I):
        raise WebsiteBlocked('MOBOT returned a firewall/challenge page; stopped without bypassing it')


def first_result(html: str) -> dict | None:
    check_page(html)
    document = PlantHTML(html)
    for link in document.links:
        url = urllib.parse.urljoin(BASE_URL, link['href'])
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == 'plantfinder.mobot.org' and parsed.path.lower() == '/plantfinderdetails.aspx':
            return {'url': url, 'name': normalize(''.join(link['parts']))}
    if 'PlantFinder' not in html:
        raise ValueError('Unrecognized search page')
    return None


def parse_zone(text: str) -> tuple[int, int]:
    match = re.fullmatch(r'(\d{1,2})[ab]?(?:\s*(?:to|[-–—])\s*(\d{1,2})[ab]?)?', text, re.I)
    if not match:
        raise ValueError('Unrecognized zone range')
    low, high = int(match[1]), int(match[2] or match[1])
    if not 1 <= low <= high <= 13:
        raise ValueError('Zone outside 1–13')
    return low, high


def parse_measurement(text: str) -> tuple[int, int]:
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s*(?:to|[-–—])\s*(\d+(?:\.\d+)?))?\s*(feet|foot|ft\.?|inches|inch|in\.?)', text, re.I)
    if not match:
        raise ValueError('Unrecognized measurement range/unit')
    factor = 12 if match[3].lower().startswith(('f',)) else 1
    low, high = Decimal(match[1]) * factor, Decimal(match[2] or match[1]) * factor
    if not 0 <= low <= high <= 32767:
        raise ValueError('Measurement outside SMALLINT inch range')
    return int(low.to_integral_value(rounding=ROUND_FLOOR)), int(high.to_integral_value(rounding=ROUND_CEILING))


def parse_months(text: str) -> list[str]:
    if text.lower() in {'non-flowering', 'nonflowering', 'insignificant', 'no flowers'}:
        return []
    # Avoid inventing a month for qualitative seasons, frost, or variable periods.
    month_pattern = '|'.join(MONTHS)
    residual = re.sub(month_pattern, '', text, flags=re.I)
    residual = re.sub(r'\b(?:to|through|and|or)\b|[,;/&\s–—-]', '', residual, flags=re.I)
    if residual:
        raise ValueError('Bloom time contains a season or unrecognized wording')
    tokens = list(re.finditer(month_pattern, text, re.I))
    if not tokens:
        raise ValueError('No explicit bloom months')
    result = set()
    for index, token in enumerate(tokens):
        start = MONTHS.index(token[0].title())
        result.add(start)
        if index + 1 < len(tokens):
            next_token = tokens[index + 1]
            connector = text[token.end():next_token.start()].strip().lower()
            if connector in {'to', 'through', '-', '–', '—'}:
                end = MONTHS.index(next_token[0].title())
                cursor = start
                while cursor != end:
                    cursor = (cursor + 1) % 12
                    result.add(cursor)
    return [month for index, month in enumerate(MONTHS) if index in result]


def parse_water(text: str) -> list[str]:
    labels = {'dry': 'low', 'low': 'low', 'medium': 'medium', 'med': 'medium',
              'wet': 'high', 'high': 'high'}
    tokens = list(re.finditer(r'\b(?:dry|low|medium|med|wet|high)\b', text, re.I))
    residual = re.sub(r'\b(?:dry|low|medium|med|wet|high|to|and|or)\b|[,;/&\s–—-]', '', text, flags=re.I)
    if residual or not tokens:
        raise ValueError('Unrecognized water range')
    result = {labels[token[0].lower()] for token in tokens}
    if re.search(r'\bto\b|[-–—]', text, re.I):
        indexes = [WATER.index(value) for value in result]
        result = set(WATER[min(indexes):max(indexes) + 1])
    return [value for value in WATER if value in result]


def parse_details(html: str) -> dict:
    check_page(html)
    document = PlantHTML(html)
    raw = {}
    title = None
    for element_id, text in document.elements:
        if element_id.endswith('srTitle_lblTitle'):
            title = text
        for suffix, label in FIELDS.items():
            if element_id.endswith(suffix):
                raw[label] = re.sub(r'^' + re.escape(label) + r'\s*:\s*', '', text).strip()
    if not raw:
        raise ValueError('No recognized plant fields in detail page')
    patch, warnings = {}, []
    for label, value in raw.items():
        try:
            if label == 'Zone':
                patch['min_zone'], patch['max_zone'] = parse_zone(value)
            elif label in {'Height', 'Spread'}:
                suffix = 'height' if label == 'Height' else 'width'
                patch['min' + suffix], patch['max' + suffix] = parse_measurement(value)
            elif label == 'Bloom Time':
                patch['bloom_months'] = parse_months(value)
            elif label == 'Water':
                patch['water'] = parse_water(value)
        except ValueError as error:
            warnings.append(f'{label}: {error}: {value}')
    return {'source_name': title, 'raw_fields': raw, 'patch': patch, 'warnings': warnings}


def scientific_match(expected: str, actual: str | None) -> bool:
    if not actual:
        return False
    expected = normalize(expected.replace('×', ' x ')).casefold()
    actual = normalize(actual.replace('×', ' x ')).casefold()
    return actual == expected


def merge_preview(plant: dict, patch: dict, units: str) -> dict:
    result = {key: plant.get(key) for key in [*DIMENSIONS, 'min_zone', 'max_zone', 'water', 'bloom_months']}
    if units == 'feet':
        for key in DIMENSIONS:
            if result[key] is not None:
                result[key] *= 12
    for key in [*DIMENSIONS, 'min_zone', 'max_zone']:
        if key in patch:
            values = [value for value in (result[key], patch[key]) if value is not None]
            result[key] = (min(values) if key.startswith('min') else max(values)) if values else None
    old_water = result['water']
    if isinstance(old_water, str):
        old_water = [old_water]
    if 'water' in patch or old_water is not None:
        values = {'medium' if value == 'med' else value for value in (old_water or []) + patch.get('water', [])}
        result['water'] = [value for value in WATER if value in values]
    if 'bloom_months' in patch:
        result['bloom_months'] = patch['bloom_months']
    return result


class WebsiteBlocked(RuntimeError):
    pass


class Fetcher:
    def __init__(self, directory: Path, delay: float):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.delay = max(1.0, delay)
        self.last_request = 0.0
        self.robots = urllib.robotparser.RobotFileParser()
        robots_url = BASE_URL + '/robots.txt'
        try:
            self.robots.parse(self.get(robots_url, robots_check=False).splitlines())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                self.robots.parse([])
            else:
                raise
        crawl_delay = self.robots.crawl_delay('RootfindPlantEnrichment') or self.robots.crawl_delay('*')
        if crawl_delay:
            self.delay = max(self.delay, float(crawl_delay))

    def get(self, url: str, robots_check=True) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'plantfinder.mobot.org':
            raise ValueError('Unexpected source host')
        if robots_check and not self.robots.can_fetch('RootfindPlantEnrichment', url):
            raise WebsiteBlocked('robots.txt disallows this URL')
        cache = self.directory / (hashlib.sha256(url.encode()).hexdigest() + '.html')
        if cache.exists():
            text = cache.read_text(encoding='utf-8')
            check_page(text)
            return text
        for attempt in range(3):
            time.sleep(max(0.0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'RootfindPlantEnrichment/1.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    if urllib.parse.urlparse(response.url).hostname != 'plantfinder.mobot.org':
                        raise WebsiteBlocked('Unexpected redirect host')
                    text = response.read(5_000_001)
                    if len(text) > 5_000_000:
                        raise ValueError('Unexpectedly large HTML response')
                    text = text.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                check_page(text)
                cache.write_text(text, encoding='utf-8')
                return text
            except urllib.error.HTTPError as error:
                if error.code in {401, 403, 429}:
                    raise WebsiteBlocked(f'MOBOT HTTP {error.code}; stopped, retry later with permission') from error
                if error.code < 500 or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError('Fetch attempts exhausted')


class Database:
    def __init__(self, directory: Path, project_ref: str | None = None):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.project_ref = project_ref or (BACKEND / 'supabase/.temp/project-ref').read_text().strip()
        if not re.fullmatch(r'[a-z0-9]{20}', self.project_ref):
            raise ValueError('Unexpected project reference')
        self.cli = BACKEND / 'node_modules/supabase/dist/supabase.js'
        if not self.cli.exists():
            raise RuntimeError('Install backend dependencies with npm ci first')

    def query(self, sql: str) -> list[dict]:
        path = self.directory / ('query-' + uuid.uuid4().hex + '.sql')
        path.write_text(sql, encoding='utf-8')
        try:
            result = subprocess.run([
                shutil.which('node') or 'node', str(self.cli), 'db', 'query', '--linked',
                '--project-ref', self.project_ref, '--file', str(path.resolve()), '--output', 'json',
            ], cwd=BACKEND, capture_output=True, text=True, encoding='utf-8', timeout=180)
            if result.returncode:
                raise RuntimeError('Supabase query failed: ' + result.stderr[-1000:])
            # The CLI may print a login-role notice before its JSON result.
            for match in re.finditer(r'\{', result.stdout):
                try:
                    output, _ = json.JSONDecoder().raw_decode(result.stdout[match.start():])
                    if isinstance(output, dict) and 'rows' in output:
                        return output['rows']
                except json.JSONDecodeError:
                    pass
            raise RuntimeError('Supabase CLI returned no recognized JSON rows')
        finally:
            path.unlink(missing_ok=True)

    def snapshot(self, label: str) -> dict:
        metadata = self.query("""select
          col_description('public.plants'::regclass, (
            select attnum from pg_attribute where attrelid = 'public.plants'::regclass and attname = 'maxheight'
          )) as dimension_comment,
          (select jsonb_agg(to_jsonb(c)) from information_schema.columns c
           where table_schema='public' and table_name='plants') as columns,
          (select jsonb_agg(pg_get_functiondef(oid)) from pg_proc
           where pronamespace = 'public'::regnamespace and proname in
             ('upsert_plant','upsert_plants_batch','plant_details','search_plants')) as functions""")
        units = 'inches' if metadata[0]['dimension_comment'] == 'Unit: inches (MOBOT enrichment v1)' else 'feet'
        plants, last_id = [], 0
        while True:
            result = self.query(f'select to_jsonb(p) as plant from public.plants p where plant_id > {last_id} order by plant_id limit 1000')
            rows = [row['plant'] for row in result]
            if not rows:
                break
            plants.extend(rows)
            last_id = rows[-1]['plant_id']
        data = {'project_ref': self.project_ref, 'created_at': timestamp(), 'dimension_unit': units,
                'schema': metadata[0], 'plants': plants}
        path = self.directory / (label + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json')
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Saved {len(plants)} plants ({units}) to {path}', flush=True)
        return data


def read_plan(path: Path) -> dict[tuple, dict]:
    records = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f'Invalid JSONL at line {number}; preserve/repair the interrupted line before resuming') from error
            key = (record['plant_id'], record['scientific_name'], record['parser_version'])
            records.pop(key, None)
            records[key] = record
    return records


def collect(db: Database, args):
    snapshot = db.snapshot('before-collection')
    plan_path = args.directory / 'plan.jsonl'
    previous = read_plan(plan_path)
    fetcher = Fetcher(args.directory / 'cache', args.delay)
    plants = snapshot['plants'][:args.limit] if args.limit else snapshot['plants']
    counts = {}
    with plan_path.open('a', encoding='utf-8') as out:
        for index, plant in enumerate(plants, 1):
            name = normalize(plant['genus'] + ' ' + plant['species'])
            key = (plant['plant_id'], name, PARSER_VERSION)
            existing = previous.get(key)
            if existing and existing['status'] in {'matched', 'no_match', 'name_mismatch'} and not args.retry:
                status = 'resumed_' + existing['status']
            else:
                record = {'plant_id': plant['plant_id'], 'scientific_name': name,
                          'project_ref': db.project_ref, 'parser_version': PARSER_VERSION, 'collected_at': timestamp()}
                try:
                    record['search_url'] = BASE_URL + '/PlantFinderProfileResults.aspx?basic=' + urllib.parse.quote(name, safe='')
                    first = first_result(fetcher.get(record['search_url']))
                    if first is None:
                        record['status'] = 'no_match'
                    else:
                        record.update(source_url=first['url'], selected_name=first['name'])
                        record.update(parse_details(fetcher.get(first['url'])))
                        if not scientific_match(name, record['source_name']):
                            record['status'] = 'name_mismatch'
                        elif not record['patch']:
                            record['status'] = 'unparsed'
                        else:
                            record['status'] = 'matched'
                            record['before'] = {key: plant.get(key) for key in [*DIMENSIONS, 'min_zone', 'max_zone', 'water', 'bloom_months']}
                            record['before_unit'] = snapshot['dimension_unit']
                            record['after_inches'] = merge_preview(plant, record['patch'], snapshot['dimension_unit'])
                except WebsiteBlocked as error:
                    record.update(status='blocked', error=str(error))
                    out.write(json.dumps(record, ensure_ascii=False) + '\n')
                    out.flush()
                    raise
                except (ValueError, urllib.error.URLError, TimeoutError) as error:
                    record.update(status='error', error=str(error))
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                out.flush()
                status = record['status']
            counts[status] = counts.get(status, 0) + 1
            if index == 1 or index % 25 == 0 or index == len(plants):
                print(f'{index}/{len(plants)}: {counts}', flush=True)
    print(f'Collection complete. Review {plan_path}. No database rows were changed.', flush=True)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_apply_sql(records: list[dict], run_id: str) -> str:
    entries = [dict(record['patch'], plant_id=record['plant_id'],
                    scientific_name=record['scientific_name'], source_url=record['source_url']) for record in records]
    payload = sql_literal(json.dumps(entries, ensure_ascii=False))
    run = sql_literal(run_id)
    return f"""set standard_conforming_strings = on;
    with incoming as (
      select * from jsonb_to_recordset({payload}::jsonb) as r(
        plant_id integer, scientific_name text, source_url text,
        min_zone smallint, max_zone smallint, minheight smallint, maxheight smallint,
        minwidth smallint, maxwidth smallint, water public.water_level_enum[], bloom_months public.month_enum[])
    ), before as materialized (
      select p.plant_id, to_jsonb(p) as old_row from public.plants p
      join incoming s on s.plant_id = p.plant_id
      where btrim(regexp_replace(p.genus || ' ' || p.species, '\\s+', ' ', 'g')) = s.scientific_name
      and not exists (select 1 from rootfind_private.mobot_changes a
        where a.run_id = {run} and a.plant_id = p.plant_id)
      for update of p
    ), updated as (
      update public.plants p set
        min_zone = least(p.min_zone, s.min_zone), max_zone = greatest(p.max_zone, s.max_zone),
        minheight = least(p.minheight, s.minheight), maxheight = greatest(p.maxheight, s.maxheight),
        minwidth = least(p.minwidth, s.minwidth), maxwidth = greatest(p.maxwidth, s.maxwidth),
        bloom_months = coalesce(s.bloom_months, p.bloom_months),
        water = case when s.water is null then p.water else
          (select array_agg(distinct level order by level) from unnest(
            coalesce(p.water, '{{}}'::public.water_level_enum[]) || s.water) level) end
      from incoming s join before b on b.plant_id = s.plant_id
      where p.plant_id = s.plant_id
      returning p.plant_id, to_jsonb(p) as new_row, b.old_row, s.source_url
    ), audited as (
      insert into rootfind_private.mobot_changes(run_id, plant_id, source_url, old_row, new_row)
      select {run}, plant_id, source_url, old_row, new_row from updated returning plant_id
    ) select count(*)::integer as updated_count from audited;
    """


def apply(db: Database, args):
    plan_path = args.directory / 'plan.jsonl'
    if not plan_path.exists():
        raise ValueError('Run collect before apply')
    records = list(read_plan(plan_path).values())
    if any(record.get('project_ref') != db.project_ref for record in records):
        raise ValueError('Plan project differs from linked/selected project')
    # One latest source record per ID; ignore stale scientific names and failures.
    by_id = {record['plant_id']: record for record in records}
    snapshot = db.snapshot('before-apply')
    if snapshot['dimension_unit'] != 'inches':
        raise ValueError('Run prepare to apply the inches/enum migration before apply')
    current_names = {row['plant_id']: normalize(row['genus'] + ' ' + row['species']) for row in snapshot['plants']}
    candidates = [record for record in by_id.values() if record['status'] == 'matched'
                  and record['parser_version'] == PARSER_VERSION
                  and current_names.get(record['plant_id']) == record['scientific_name']
                  and scientific_match(record['scientific_name'], record.get('source_name'))]
    run_id = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    count = 0
    for index in range(0, len(candidates), 100):
        rows = db.query(build_apply_sql(candidates[index:index + 100], run_id))
        count += rows[0]['updated_count']
        print(f'Applied {min(index + 100, len(candidates))}/{len(candidates)} candidates; {count} rows updated', flush=True)
    print(f'Apply complete: {count} rows updated. Audit run: {run_id}', flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['collect', 'prepare', 'apply'])
    parser.add_argument('--directory', type=Path, default=BACKEND / '.mobot')
    parser.add_argument('--project-ref', help='Defaults to the repo linked project')
    parser.add_argument('--limit', type=int, help='Collect only the first N current plants for a sample')
    parser.add_argument('--delay', type=float, default=1.5, help='Seconds between source requests, minimum 1')
    parser.add_argument('--retry', action='store_true', help='Reparse cached pages instead of skipping completed plan entries')
    args = parser.parse_args(argv)
    args.directory = args.directory.resolve()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    db = Database(args.directory, args.project_ref)
    if args.command == 'collect':
        collect(db, args)
    elif args.command == 'prepare':
        db.snapshot('before-schema')
        sql = (BACKEND / 'supabase/migrations/202610240008_mobot_enrichment.sql').read_text(encoding='utf-8')
        sql += '\n' + (BACKEND / 'supabase/migrations/202610240009_search_water_pagination.sql').read_text(encoding='utf-8')
        db.query(sql + '\nselect \'inches\' as measurement_unit;')
        print('Schema prepared. All existing dimensions are now inches; conversion is guarded against reruns.')
    else:
        apply(db, args)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
