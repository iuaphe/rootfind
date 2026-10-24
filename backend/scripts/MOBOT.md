# MOBOT enrichment

This script uses the linked Supabase project's existing CLI login. There are no
embedded passwords, API keys, fixed plant counts, or new Python dependencies.
Python 3.10+ and Node 24+ with `npm ci` in `backend/` are required. Run commands
below from `backend/`.

**Finish the current USDA import first. Do not run prepare/apply while another
process is inserting feet-based rows or replaying old generated SQL.**

## 1. Collect a small sample without changing the database

```powershell
python scripts/enrich_mobot.py collect --limit 5
```

This queries the linked database at runtime, saves a complete plant backup and
schema/function metadata under `backend/.mobot/`, and writes proposals to
`.mobot/plan.jsonl`. This directory is ignored by Git. Inspect each record's
`raw_fields`, `patch`, `before_unit`, `before`, `after_inches`, `source_url`, and
warnings. No row or schema changes occur during collection.

The project defaults to `supabase/.temp/project-ref`. Use
`--project-ref YOUR_PROJECT_REF` to explicitly choose another project. The script
passes this project reference to every CLI database query.

## 2. Collect all currently present plants

```powershell
python scripts/enrich_mobot.py collect
```

Each run reads the plant list afresh. If more plants are added, rerun this command;
completed existing plant/name combinations are skipped and new ones are included.
There is no hardcoded count or maximum plant ID. A collection run ends after the
list it read is processed; it does not continuously watch for future inserts.

The first PlantFinder detail link is selected, exactly as on the supplied search
results page. Its scientific name must exactly match the requested species/hybrid
after normalizing whitespace, letter case, and the hybrid sign `×` to `x`.
Cultivar, variety, subspecies, form, and authority suffixes do not match a bare
species/hybrid. Mismatches are logged and never applied. It does not
silently switch to a different result. Missing search results do not delete or
clear anything. Unrecognized field wording is logged and that field is left
unchanged. No HTML, script, or page text is executed as instructions.

Collection is sequential, caches pages, waits at least 1.5 seconds between
requests by default, and respects a larger robots.txt crawl delay. `--delay 3`
slows requests further. A 403/429, firewall, or challenge stops the run instead of
attempting a bypass. Fix access with the site before resuming. HTTP 404 robots.txt
means no robots directives were found, not that the website grants reuse rights.

Rerunning the same command resumes from the JSONL log and page cache. `--retry`
reparses completed records using cached HTML. To force a new download of a page,
remove its corresponding cache file only after resolving access problems. Error
records are retried on the next collection. A truncated last JSONL line after an
abrupt machine shutdown must be removed/repaired before resuming.

Matcher version 2 rejects suffix matches that version 1 previously accepted.
Rerun `collect` to reparse the cached pages under the new rules. `apply` rejects
version-1 plan entries and independently rechecks each source scientific name.

## 3. Prepare the schema and convert existing feet to inches

After reviewing the plan and finishing the old import:

```powershell
python scripts/enrich_mobot.py prepare
```

This makes another complete plant/schema backup, then runs
`202610240008_mobot_enrichment.sql` followed by
`202610240009_search_water_pagination.sql` against the selected project. It does not push
unrelated pending migrations or run deletion migrations.

The migration transaction:

- Adds `min_zone SMALLINT`, `max_zone SMALLINT`, and `bloom_months month_enum[]`
  (January through December).
- Changes `water` to `water_level_enum[]`: `low`, `medium`, `high`. Existing `med`
  becomes `medium`; existing NULL remains NULL.
- Converts all existing height/width values from integer feet to integer inches.
  A private unit marker prevents multiplying a second time on reruns. Values
  that cannot fit SMALLINT cancel and roll back the entire migration.
- Updates `upsert_plant` for the new water type. Legacy scalar water inputs and
  feet-based API clients still work; new clients can pass `measurement_units:
  "inches"` and a water array. Upserts merge measurements and water to preserve
  enriched ranges. The existing USDA script's direct SQL output is the old schema
  format and must not be replayed after this migration.
- Creates a private audit table inaccessible to anonymous/authenticated API
  callers. It does not modify unrelated plant attributes or join tables.

Migration 009 updates water search to enum-array membership, normalizes `med` to
`medium`, and bounds search responses with `page`/`pageSize` (default 1/50; maximum
1000/100). Apply it after 008, before deploying the updated search Edge Function.

If using the Supabase migration runner, apply migrations 008/009 through that runner
instead of `prepare` (take your own backup first). The `prepare` command executes
the SQL directly through `db query`; **it does not mark CLI migration history**.
If later using `db push`, reconcile versions `202610240008` and `202610240009`
only after verifying the corresponding migration actually ran. This avoids replaying old table-creation migrations
that may already have been applied manually to your hosted project.

The original upsert migration was renamed from `202610240006_upsert_plant.sql` to
`20261024000601_upsert_plant.sql` to give it a unique version while retaining its
position before migrations 007/008. The county migration keeps version
`202610240006`. If either original 006 file was already run on an existing project,
inspect both function definitions and migration history before using `db push`.
Only mark the renamed upsert version applied when its SQL is already present (or
superseded by migration 008). Do not rerun the old upsert SQL after applying 008;
it would restore the old scalar-water implementation. Renaming a file does not
change any existing hosted migration history. No remote history is edited by this fix.

`plant-details` already returns all plant columns, so its response will gain the
new fields and `water` will become an array. Its dimensions will now be inches.
Update frontend displays that previously expected feet/scalar water.

## 4. Apply the reviewed plan

```powershell
python scripts/enrich_mobot.py apply
```

This requires the new inch schema, saves another current backup, and applies only
matched entries whose plant ID and scientific name still agree with the current
database. The site URL and both before/after rows are recorded in
`rootfind_private.mobot_changes`. Updates are transactional batches of 100.

The database uses its **current** values when merging, not stale values from the
collection snapshot. Minimum dimensions/zones use the lower known value; maximum
dimensions/zones use the higher. MOBOT decimal feet are converted to inches with
minimums rounded down and maximums rounded up. Source water levels are unioned
with existing water values, deduplicated and ordered low/medium/high. “Dry to wet”
includes medium; “Dry to medium” includes low and medium.

Explicit bloom ranges are inclusive (“May to June” stores May and June). Winter
ranges can cross December. The parsed source bloom months replace the bloom-month
array for a matched plant; missing/qualitative seasons leave it unchanged. Explicit
“Non-flowering” stores an empty array. Missing numeric/source values never erase
existing measurements, zones, or water.

Restart `apply` after interruption: a hash of the plan identifies its audit run,
and previously applied rows in that same plan are skipped. Appending/recollecting
changes the plan hash; merging the same data again still does not narrow ranges
or duplicate water values. Plants removed/renamed since collection are skipped.
Backups/audit records permit recovery; this script does not automatically revert
changes or promise to preserve concurrent writes during a rollback.

## Offline tests

From the repository root:

```powershell
python -m unittest discover -s backend/scripts -p test_enrich_mobot.py -v
```

From `backend/`:

```powershell
node --experimental-strip-types --test tests/mobot-enrichment.test.ts
npm test
```

The tests use synthetic fixtures and embedded Postgres, with no hosted database
updates or live website requests. The parser has also been checked against the
two supplied saved HTML examples.
