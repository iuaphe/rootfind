"""Preview or import local BONAP CSV through dedicated, transactional upserts."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import enrich_mobot as core

ROOT = core.BACKEND.parent
META = ['Family', 'Genus', 'Species', 'Major Plant Group', 'Habit', 'Duration',
        'Nativity', 'Flower Color', 'Flowering Season', 'Habitat', 'Global Rank',
        'Rarity & Endemism', 'Special Locations']
VERSION = 1


def parent_name(genus, scientific):
    """Collapse explicit subspecies/varieties/forms, retaining named hybrids."""
    def normalize(value):
        value = value.replace('Ã—', '×').replace('×', 'x')
        return ' '.join(value.split())
    genus = normalize(genus)
    scientific = normalize(scientific)
    match = re.fullmatch(re.escape(genus) + r'\s+((?:x\s+)?[a-z][a-z-]*)(.*)', scientific)
    if not match:
        raise ValueError(f'Unrecognized scientific name: {scientific}')
    species, suffix = match.groups()
    if suffix and not re.fullmatch(r'(?:\s+(?:subsp\.|ssp\.|var\.|f\.)\s+(?:[xX]\s+)?[a-z][a-z-]*)+', suffix):
        raise ValueError(f'Unsupported scientific-name suffix: {scientific}')
    return genus, species


def county_mapping(path, overrides):
    choices = {}
    with path.open(encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            label = row['county'].strip() + ', ' + row['state'].strip()
            fips = int(row['fips'])
            if not 0 < fips <= 99999:
                raise ValueError(f'Invalid FIPS for {label}')
            choices.setdefault(label, {})[fips] = row['county'].strip()
    mapping, ambiguous = {}, {}
    for label, candidates in choices.items():
        if label in overrides:
            fips = overrides[label]
            if type(fips) is not int or fips not in candidates:
                raise ValueError(f'Override must select a known FIPS for {label}')
            mapping[label] = (fips, candidates[fips])
        elif len(candidates) == 1:
            fips, name = next(iter(candidates.items()))
            mapping[label] = (fips, name)
        else:
            ambiguous[label] = sorted(candidates)
    if set(overrides) - set(choices):
        raise ValueError('Unknown county override labels')
    return mapping, ambiguous


def payload_for(row, headers, mapping, fingerprint):
    genus, species = parent_name(row['Genus'], row['Species'])
    plant = {'genus': genus, 'species': species, 'measurement_units': 'inches'}
    durations = [v.strip().lower() for v in row['Duration'].split(',') if v.strip()]
    if len(durations) == 1 and durations[0] in {'annual', 'biennial', 'perennial'}:
        plant['life_cycle'] = durations[0]
    habits = {v.strip().lower() for v in row['Habit'].split(',')}
    if 'tree' in habits:
        plant['is_tree'] = True
        plant['is_woody'] = True
    if habits & {'shrub or subshrub', 'dwarf or creeping shrub', 'woody vine'}:
        plant['is_woody'] = True
    # Keep ecological tags searchable without inferring watering/sun requirements.
    tags = {tag.strip() for key in ('Habitat', 'Special Locations')
            for tag in row[key].split('|') if tag.strip()}
    if tags:
        plant['misc'] = sorted(tags)
    seasons = sorted({season for season in ('Spring', 'Summer', 'Fall', 'Winter')
                      if re.search(r'\b' + season + r' flowering\b', row['Flowering Season'])})
    counties = {}
    for header in headers:
        value = row[header]
        if value not in {'0', '1'}:
            raise ValueError(f'Invalid county presence {value!r} for {row["Species"]}, {header}')
        if value == '1' and header in mapping:
            fips, name = mapping[header]
            counties[fips] = {'fips': fips, 'name': name}
    return {'plant': plant, 'counties': [counties[k] for k in sorted(counties)],
            'bonap': {'source_sha256': fingerprint, 'raw': {k: row[k] for k in META},
                      'bloom_seasons': seasons}}


def preview(args, db=None):
    overrides = json.loads(args.county_overrides.read_text()) if args.county_overrides else {}
    mapping, ambiguous = county_mapping(args.county_map, overrides)
    fingerprint = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    report = {'source_sha256': fingerprint, 'rows': 0, 'county_pairs_proposed': 0,
              'ambiguous_counties_skipped': ambiguous, 'unmatched_names': [],
              'rejected_names': [], 'infraspecific_rows_collapsed': [], 'new_plants_allowed': args.allow_create,
              'database_compared': db is not None, 'plan': str(args.directory / 'plan.jsonl')}
    existing = None
    if db:
        snapshot = db.snapshot('before-bonap')
        if snapshot['dimension_unit'] != 'inches':
            raise ValueError('Apply enrichment migrations before importing BONAP')
        existing = {(p['genus'], p['species']) for p in snapshot['plants']}
    temporary = args.directory / 'plan.jsonl.tmp'
    parent_keys = set()
    with args.csv.open(encoding='utf-8-sig', newline='') as source, temporary.open('w', encoding='utf-8') as out:
        reader = csv.DictReader(source)
        if not reader.fieldnames or reader.fieldnames[:len(META)] != META:
            raise ValueError('Unexpected BONAP metadata columns')
        headers = reader.fieldnames[len(META):]
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('Duplicate CSV headers')
        if set(headers) - set(mapping) - set(ambiguous):
            raise ValueError('County columns missing from mapping')
        for number, row in enumerate(reader, 2):
            report['rows'] += 1
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Malformed CSV row {number}')
            try:
                payload = payload_for(row, headers, mapping, fingerprint)
            except ValueError as error:
                if str(error).startswith(('Unrecognized scientific name:', 'Unsupported scientific-name suffix:')):
                    report['rejected_names'].append({'line': number, 'name': row['Species'], 'reason': str(error)})
                    continue
                raise
            key = (payload['plant']['genus'], payload['plant']['species'])
            if row['Species'] != ' '.join(key):
                report['infraspecific_rows_collapsed'].append({'source': row['Species'], 'parent': ' '.join(key)})
            if existing is not None and key not in existing:
                report['unmatched_names'].append(row['Species'])
                if not args.allow_create:
                    continue
            parent_keys.add(key)
            report['county_pairs_proposed'] += len(payload['counties'])
            out.write(json.dumps(payload, ensure_ascii=False) + '\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, args.directory / 'plan.jsonl')
    report['distinct_parent_species'] = len(parent_keys)
    report['county_pairs_note'] = 'Before cross-row deduplication and existing-relationship checks; zeros never delete.'
    (args.directory / 'preview.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"Preview: {report['rows']} rows; {len(parent_keys)} parent species; {report['county_pairs_proposed']} proposed county pairs.")
    print(f"Skipped {len(ambiguous)} ambiguous county labels; {len(report['rejected_names'])} rejected names; {len(report['unmatched_names'])} unmatched names.")
    print(f"Report: {args.directory / 'preview.json'}")
    return report


def apply_plan(db, directory, batch_size):
    plan = directory / 'plan.jsonl'
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    context = f'{VERSION}:{db.project_ref}:{digest}'
    progress = directory / 'progress.jsonl'
    completed = set()
    if progress.exists():
        for number, line in enumerate(progress.read_text(encoding='utf-8').splitlines(), 1):
            try:
                entry = json.loads(line)
            except ValueError as error:
                raise ValueError(f'Invalid progress JSONL line {number}; preserve and repair before retry') from error
            if entry['context'] == context:
                completed.update(entry['rows'])
    pending = []

    def flush(out):
        if not pending:
            return
        payloads = [record for _, record in pending]
        sql = ('select public.upsert_bonap_plants_batch(' + core.sql_literal(json.dumps(payloads)) + '::jsonb) as result')
        rows = db.query(sql)
        result = rows[0].get('result') if len(rows) == 1 else None
        if (not isinstance(result, dict) or result.get('success') is not True
                or type(result.get('count')) is not int or result['count'] != len(pending)
                or not isinstance(result.get('plant_ids'), list) or len(result['plant_ids']) != len(pending)
                or any(type(value) is not int or value <= 0 for value in result['plant_ids'])):
            raise RuntimeError('BONAP batch not confirmed; progress preserved for retry')
        out.write(json.dumps({'context': context, 'rows': [index for index, _ in pending]}) + '\n')
        out.flush()
        os.fsync(out.fileno())
        print(f'Confirmed {len(pending)} BONAP records through plan row {pending[-1][0]}', flush=True)
        pending.clear()

    with plan.open(encoding='utf-8') as source, progress.open('a', encoding='utf-8') as out:
        for index, line in enumerate(source, 1):
            if index in completed:
                continue
            pending.append((index, json.loads(line)))
            if len(pending) >= batch_size:
                flush(out)
        flush(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview', 'apply'])
    parser.add_argument('--csv', type=Path, default=ROOT / 'data-processing/bonap_data/full_bonap_data.csv')
    parser.add_argument('--county-map', type=Path, default=ROOT / 'data-processing/bonap_data/bonap_county_map.csv')
    parser.add_argument('--county-overrides', type=Path, help='JSON object mapping ambiguous county labels to integer FIPS')
    parser.add_argument('--directory', type=Path, default=core.BACKEND / '.bonap')
    parser.add_argument('--project-ref')
    parser.add_argument('--linked', action='store_true', help='Compare preview with linked database; always used for apply')
    parser.add_argument('--allow-create', action='store_true', help='Create unmatched parent species instead of skipping them')
    parser.add_argument('--batch-size', type=int, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 25:
        parser.error('batch-size must be 1–25')
    args.directory = args.directory.resolve()
    args.directory.mkdir(parents=True, exist_ok=True)
    db = core.Database(args.directory, args.project_ref) if args.linked or args.command == 'apply' else None
    preview(args, db)
    if args.command == 'apply':
        # The dedicated function is introduced in migration 013; no fallback to general upserts.
        db.query("select 'public.upsert_bonap_plants_batch(jsonb)'::regprocedure::text as function")
        apply_plan(db, args.directory, args.batch_size)
    else:
        print('Preview only. No database rows were changed.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
