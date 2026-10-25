#!/usr/bin/env python3
"""NC State / PFAF enrichment, sharing MOBOT's backup, cache, and live recovery."""
import argparse
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from html.parser import HTMLParser
from pathlib import Path
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse

import enrich_mobot as core

SUN = ['full sun', 'part shade', 'full shade']
SEASONS = ['Spring', 'Summer', 'Fall', 'Winter']
FIELDS = [*core.DIMENSIONS, 'min_zone', 'max_zone', 'water', 'sun', 'bloom_months', 'bloom_seasons', 'drainage']


class NoProfile(ValueError):
    pass


class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def text(self):
        return core.normalize(' '.join(child.text() if isinstance(child, Node) else child for child in self.children))

    def nodes(self, tag=None):
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes(tag)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not any(node.tag in {'script', 'style'} for node in self.stack):
            self.stack[-1].children.append(data)

    def by_id_suffix(self, suffix):
        return next((node.text() for node in self.root.nodes() if node.attrs.get('id', '').endswith(suffix)), '')


def bounded_measure(value, factor=Decimal(1)):
    value = Decimal(value) * factor
    if not 0 <= value <= 32767:
        raise ValueError('Measurement outside SMALLINT inch range')
    return value


def nc_dimension(text):
    def inches(value):
        match = re.fullmatch(r'(?:(\d+(?:\.\d+)?)\s*ft\.?)?\s*(?:(\d+(?:\.\d+)?)\s*in\.?)?', value.strip(), re.I)
        if not match or not any(match.groups()):
            raise ValueError('Unrecognized feet/inches measurement')
        return bounded_measure(Decimal(match[1] or 0) * 12 + Decimal(match[2] or 0))
    parts = re.split(r'\s*[-–—]\s*|\s+to\s+', text)
    if len(parts) not in {1, 2}:
        raise ValueError('Unrecognized dimension range')
    low, high = inches(parts[0]), inches(parts[-1])
    if low > high:
        raise ValueError('Reversed dimension range')
    return int(low.to_integral_value(rounding=ROUND_FLOOR)), int(high.to_integral_value(rounding=ROUND_CEILING))


def parse_ncstate(html):
    doc = Document(html)
    detail = next((node for node in doc.root.nodes() if node.attrs.get('id') == 'plant_detail'), None)
    if detail is None:
        raise ValueError('No recognized NC State plant profile')
    heading = next(detail.nodes('h1'), None)
    if heading is None:
        raise ValueError('Missing scientific-name heading')
    # Cultivar names may sit outside <em>; exclude only the pronunciation UI.
    name = core.normalize(' '.join(child.text() if isinstance(child, Node) else child
                                  for child in heading.children if not isinstance(child, Node) or child.tag != 'small'))
    raw = {}
    for dl in detail.nodes('dl'):
        label = None
        for child in dl.children:
            if not isinstance(child, Node):
                continue
            if child.tag == 'dt':
                label = child.text().rstrip(':')
            elif child.tag == 'dd' and label:
                raw.setdefault(label, []).append(child.text())
    patch, warnings = {}, []
    for value in raw.get('Dimensions', []):
        match = re.fullmatch(r'(Height|Width):\s*(.+)', value, re.I)
        if match:
            try:
                low, high = nc_dimension(match[2])
                suffix = match[1].lower()
                patch['min' + suffix], patch['max' + suffix] = low, high
            except ValueError as error:
                warnings.append(f'{value}: {error}')
    light = {'full sun': 'full sun', 'partial shade': 'part shade', 'deep shade': 'full shade', 'dappled sunlight': 'part shade'}
    sun = set()
    for value in raw.get('Light', []):
        label = value.split('(')[0].strip().lower()
        if label in light:
            sun.add(light[label])
            if label == 'dappled sunlight':
                warnings.append('Dappled sunlight approximated as part shade; raw label preserved')
        else:
            warnings.append('Unrecognized light: ' + value)
    if sun:
        patch['sun'] = [level for level in SUN if level in sun]
    moisture = {'dry': 'low', 'occasionally dry': 'low', 'moist': 'medium', 'wet': 'high',
                'occasionally wet': 'high', 'frequent standing water': 'high'}
    water = set()
    drainage = []
    for value in raw.get('Soil Drainage', []):
        key = value.lower()
        if key in moisture:
            water.add(moisture[key])
        if key in {'good drainage', 'well-drained', 'frequent standing water', 'occasionally wet', 'occasionally dry'}:
            drainage.append(value)
        elif key not in moisture:
            warnings.append('Unrecognized soil moisture/drainage: ' + value)
    if water:
        patch['water'] = [level for level in core.WATER if level in water]
    if drainage:
        patch['drainage'] = sorted(set(drainage))
    zone_text = ', '.join(raw.get('USDA Plant Hardiness Zone', []))
    if zone_text:
        try:
            zones = [core.parse_zone(token.strip()) for token in zone_text.split(',')]
            patch['min_zone'] = min(zone[0] for zone in zones)
            patch['max_zone'] = max(zone[1] for zone in zones)
        except ValueError as error:
            warnings.append(f'USDA zones: {error}: {zone_text}')
    seasons, months = set(), set()
    for value in raw.get('Flower Bloom Time', []):
        if value in SEASONS:
            seasons.add(value)
        else:
            try:
                months.update(core.parse_months(value))
            except ValueError as error:
                warnings.append(f'Bloom time: {error}: {value}')
    # Only read explicit, unqualified month wording from the flower description.
    for description in raw.get('Flower Description', []):
        for match in re.finditer(r'\bblooms?\s+from\s+([^.;]+)', description, re.I):
            try:
                months.update(core.parse_months(match[1]))
            except ValueError as error:
                warnings.append(f'Flower description: {error}: {match[1]}')
    if seasons:
        patch['bloom_seasons'] = [season for season in SEASONS if season in seasons]
    if months:
        patch['bloom_months'] = [month for month in core.MONTHS if month in months]
    return dict(source_name=name, raw_fields=raw, patch=patch, warnings=warnings)


def parse_pfaf(html):
    doc = Document(html)
    raw_name = doc.by_id_suffix('lbldisplatinname')
    physical = doc.by_id_suffix('lblPhystatment')
    ids = [node.attrs.get('id', '') for node in doc.root.nodes()]
    if (not raw_name and not physical and any(value.endswith('lbldisplatinname') for value in ids)
            and any(value.endswith('lblPhystatment') for value in ids)):
        raise NoProfile('PFAF returned its empty plant-profile template')
    if not raw_name or not physical:
        raise ValueError('No recognized PFAF plant profile')
    # PFAF puts the botanical authority after a separate " - " delimiter.
    # Do not remove cultivar/infraspecific suffixes from the species part.
    name = re.split(r'\s+-\s+', raw_name, maxsplit=1)[0].strip()
    raw = {'Scientific heading': raw_name, 'Physical Characteristics': physical,
           'USDA hardiness': doc.by_id_suffix('lblUSDAhardiness')}
    patch, warnings = {}, []
    if raw['USDA hardiness']:
        try:
            patch['min_zone'], patch['max_zone'] = core.parse_zone(raw['USDA hardiness'])
        except ValueError as error:
            warnings.append(f'USDA hardiness: {error}: {raw["USDA hardiness"]}')
    size = re.search(r'\bgrowing to\s+(\d+(?:\.\d+)?)\s*m\b(?:\s*\([^)]*\))?(?:\s+by\s+(\d+(?:\.\d+)?)\s*m\b)?', physical, re.I)
    if size:
        for key, value in [('maxheight', size[1]), ('maxwidth', size[2])]:
            if value:
                try:
                    patch[key] = int(bounded_measure(value, Decimal(100) / Decimal('2.54')).to_integral_value(rounding=ROUND_CEILING))
                except ValueError as error:
                    warnings.append(f'{key}: {error}')
        warnings.append('PFAF growing-to measurements provide maxima only; minima preserved')
    flower = re.search(r'\bin flower\s+(?:from\s+)?([^.;]+)', physical, re.I)
    if flower:
        try:
            patch['bloom_months'] = core.parse_months(flower[1])
        except ValueError as error:
            warnings.append(f'Bloom months: {error}: {flower[1]}')
    # Parse affirmative, scoped suitability sentences; never the Related Plants table.
    sun = set()
    if re.search(r'\bcannot grow in the shade\b', physical, re.I):
        sun.add('full sun')
    for phrase in re.finditer(r'\b(?:can grow in|prefers)\s+([^.;]+)', physical, re.I):
        value = phrase[1].lower()
        for label, level in [('full shade', 'full shade'), ('semi-shade', 'part shade'), ('no shade', 'full sun'), ('full sun', 'full sun')]:
            if label in value:
                sun.add(level)
    if sun:
        patch['sun'] = [level for level in SUN if level in sun]
    water = set()
    for phrase in re.finditer(r'\bprefers\s+([^.;]+?)\s+soil\b', physical, re.I):
        value = phrase[1].lower()
        for label, level in [('dry', 'low'), ('moist', 'medium'), ('wet', 'high')]:
            if re.search(r'\b' + label + r'\b', value):
                water.add(level)
        if re.search(r'\bdry\s+to\s+wet\b', value):
            water.add('medium')
    if re.search(r'\b(?:prefers|can grow in)\s+(?:standing\s+)?water\b', physical, re.I):
        water.add('high')
        patch['drainage'] = ['Frequent Standing Water']
    if water:
        patch['water'] = [level for level in core.WATER if level in water]
    if re.search(r'\bwell[- ]drained soil\b', physical, re.I):
        patch['drainage'] = sorted(set(patch.get('drainage', []) + ['Good Drainage']))
    return dict(source_name=name, raw_fields=raw, patch=patch, warnings=warnings)


def build_sql(records, run_id):
    sql = core.build_apply_sql(records, run_id)
    sql = sql.replace('source_url text,', 'source_url text, sun public.sun_type[], bloom_seasons text[], drainage text[],')
    sql = sql.replace('bloom_months = coalesce(s.bloom_months, p.bloom_months),', '''
        bloom_months = case when s.bloom_months is null then p.bloom_months else
          (select array_agg(distinct value order by value) from unnest(coalesce(p.bloom_months, '{}'::public.month_enum[]) || s.bloom_months) value) end,
        sun = case when s.sun is null then p.sun else
          (select array_agg(distinct value order by value) from unnest(coalesce(p.sun, '{}'::public.sun_type[]) || s.sun) value) end,
        bloom_seasons = case when s.bloom_seasons is null then p.bloom_seasons else
          (select array_agg(distinct value order by value) from unnest(coalesce(p.bloom_seasons, '{}'::text[]) || s.bloom_seasons) value) end,
        drainage = case when s.drainage is null then p.drainage else
          (select array_agg(distinct value order by value) from unnest(coalesce(p.drainage, '{}'::text[]) || s.drainage) value) end,''')
    return sql


class Source:
    fields = FIELDS
    build_sql = staticmethod(build_sql)

    def __init__(self, name):
        self.name = name
        self.base_url = 'https://plants.ces.ncsu.edu' if name == 'ncstate' else 'https://pfaf.org'
        self._known_slugs = None

    def get_known_slugs(self, fetcher=None):
        if self.name != 'ncstate':
            return None
        if self._known_slugs is not None:
            return self._known_slugs
        cache_file = None
        directory = getattr(fetcher, 'directory', None)
        if isinstance(directory, Path):
            cache_file = directory / 'sitemap_slugs.txt'
            if cache_file.exists():
                try:
                    self._known_slugs = set(filter(None, cache_file.read_text(encoding='utf-8').splitlines()))
                    return self._known_slugs
                except Exception:
                    pass
        try:
            sitemap_url = self.base_url + '/sitemap.xml'
            req = urllib.request.Request(sitemap_url, headers={'User-Agent': 'RootfindPlantEnrichment/1.0'})
            ctx = getattr(fetcher, 'tls_context', None)
            if not isinstance(ctx, ssl.SSLContext):
                ctx = None
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                content = resp.read().decode('utf-8', errors='replace')
            slugs = set(re.findall(r'<loc>https://plants\.ces\.ncsu\.edu/plants/([^/]+)/</loc>', content))
            if slugs:
                self._known_slugs = slugs
                if cache_file:
                    try:
                        cache_file.write_text('\n'.join(sorted(slugs)), encoding='utf-8')
                    except Exception:
                        pass
                return self._known_slugs
        except Exception:
            pass
        return None

    def validate_snapshot(self, snapshot):
        columns = {column['column_name']: column['udt_name'] for column in snapshot['schema']['columns']}
        if snapshot['dimension_unit'] != 'inches' or columns.get('sun') != '_sun_type' or not {'bloom_seasons', 'drainage'} <= columns.keys():
            raise ValueError('Apply migration 202610240010_catalog_enrichment.sql before using NC State/PFAF')

    def profile_url(self, name):
        if self.name == 'ncstate':
            slug = re.sub(r'\s+', '-', name.replace('×', 'x').lower())
            return self.base_url + '/plants/' + urllib.parse.quote(slug, safe='-') + '/'
        return self.base_url + '/user/Plant.aspx?' + urllib.parse.urlencode({'LatinName': name})

    def lookup(self, name, fetcher):
        url = self.profile_url(name)
        if self.name == 'ncstate':
            known = self.get_known_slugs(fetcher)
            slug = re.sub(r'\s+', '-', name.replace('×', 'x').lower())
            if known is not None and slug not in known:
                return dict(search_url=url, source_url=url, status='no_match')
        parser = parse_ncstate if self.name == 'ncstate' else parse_pfaf
        record = dict(search_url=url, source_url=url)
        try:
            html = fetcher.get(url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return dict(record, status='no_match')
            raise
        core.check_page(html)
        try:
            record.update(parser(html))
        except NoProfile:
            return dict(record, status='no_match')
        record['selected_name'] = record['source_name']
        record['status'] = ('name_mismatch' if not core.scientific_match(name, record['source_name']) else
                            'matched' if record['patch'] else 'unparsed')
        return record

    def preview(self, plant, patch):
        result = core.merge_preview(plant, patch, 'inches')
        for key, ordering in [('sun', SUN), ('bloom_months', core.MONTHS), ('bloom_seasons', SEASONS), ('drainage', None)]:
            old = plant.get(key)
            incoming = patch.get(key)
            values = set(old or []) | set(incoming or [])
            result[key] = ([value for value in ordering if value in values] if ordering else sorted(values)) if values else old
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', choices=['ncstate', 'pfaf'])
    parser.add_argument('command', choices=['collect', 'apply'])
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--project-ref')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--delay', type=float, default=1)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--retry', action='store_true')
    parser.add_argument('--apply-live', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16 or args.delay < 0 or (args.limit is not None and args.limit < 1):
        parser.error('workers must be 1–16, delay non-negative, and limit positive')
    if args.apply_live and args.command != 'collect':
        parser.error('--apply-live requires collect')
    args.directory = (args.directory or core.BACKEND / '.enrichment' / args.source).resolve()
    args.source_adapter = Source(args.source)
    db = core.Database(args.directory, args.project_ref)
    if args.command == 'collect':
        if args.source == 'ncstate':
            fetcher_dir = args.directory / 'cache'
            fetcher_dir.mkdir(parents=True, exist_ok=True)
            mock_fetcher = core.Fetcher(fetcher_dir, args.delay, args.source_adapter.base_url)
            slugs = args.source_adapter.get_known_slugs(mock_fetcher)
            if slugs:
                print(f'Indexed {len(slugs)} NC State profiles from sitemap. Non-matching catalog entries will be skipped without HTTP requests.', flush=True)
        core.collect(db, args)
    else:
        snapshot = db.snapshot('before-apply')
        args.source_adapter.validate_snapshot(snapshot)
        records = list(core.read_plan(args.directory / 'plan.jsonl').values())
        if any(record.get('project_ref') != db.project_ref or record.get('source') != args.source for record in records):
            raise ValueError('Plan belongs to another project/source')
        current = {plant['plant_id']: core.normalize(plant['genus'] + ' ' + plant['species']) for plant in snapshot['plants']}
        with (args.directory / 'plan.jsonl').open('a', encoding='utf-8') as out:
            writer = core.LiveWriter(db, out, build_sql=build_sql)
            for record in records:
                if (record['status'] == 'matched' and record['parser_version'] == core.PARSER_VERSION
                        and current.get(record['plant_id']) == record['scientific_name']):
                    writer.add(record)
            writer.flush()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
