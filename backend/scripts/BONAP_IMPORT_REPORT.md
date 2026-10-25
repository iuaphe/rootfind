BONAP import implementation report — October 25, 2026

Implemented a local CSV importer and dedicated database functions. Nothing in this work was deployed or imported into the hosted database. The existing general-purpose upserts and website enrichment scripts were left unchanged.

**Files created or changed**

- `scripts/import_bonap.py`: offline preview, optional linked-database comparison, transactional batch imports, and confirmed-write checkpoints.
- `supabase/migrations/202610240013_bonap_import.sql`: `upsert_bonap_plant(jsonb)`, `upsert_bonap_plants_batch(jsonb)`, using only existing catalog tables.
- `scripts/test_import_bonap.py`: scientific-name normalization, attribute mapping, county ambiguity, failure handling, and checkpoint isolation tests.
- `tests/bonap-import.test.ts`: runs the migration chain in embedded PostgreSQL and verifies database behavior.
- `.gitignore`: excludes `.bonap/` plans, previews, checkpoints, and local snapshots.
- `README.md`: links this report and the run instructions.

**Source inspection and actual offline preview**

Input: `data-processing/bonap_data/full_bonap_data.csv`, using `bonap_county_map.csv` for FIPS mapping.

- 3,005 source rows and 13 botanical metadata columns.
- 3,031 county presence columns; every value validated as exactly `0` or `1`.
- 1,338,979 positive cells in the original file.
- All 3,005 names accepted after normalization; 2,466 distinct parent species.
- 1,337,277 proposed county pairs after excluding ambiguous county labels. This is a pre-deduplication count, not the number of new database relationships.
- No database matching was performed during this offline preview. The offline unmatched count of zero does not establish that the species already exist remotely.
- Generated local files: `backend/.bonap/plan.jsonl` and `backend/.bonap/preview.json`.

**Scientific names**

The CSV's `Species` field contains the entire scientific name. The importer separates its genus and species epithet before calling the upsert.

Subspecies, varieties, and forms are collapsed into their parent species, including chained ranks such as `Ruellia caroliniensis ssp. caroliniensis var. serrulata`. Named hybrid species are retained. Multiplication signs are normalized to `x`, including a known text-encoding variation. Unsupported names are reported rather than guessed.

The database upsert also strips explicit infraspecific suffixes from its species field. Multiple varieties contribute to one parent plant. Original source records remain in the local import plan only.

**Plant field handling**

| Source or destination | Behavior |
|---|---|
| Genus and Species | Exact normalized parent `(genus, species)` identifies the plant; existing IDs remain stable. |
| Duration → `life_cycle` | Fill only when the existing value is null and the source has one clear annual/biennial/perennial value. Preserve conflicting existing scalar values. Multiple source durations remain in the local plan only, since this column is a scalar enum. |
| Habit → `is_tree`, `is_woody` | Clear tree evidence sets both true; clear shrub evidence sets woody true. Boolean evidence is unioned: true is retained. Missing evidence does not set false or erase existing flags. |
| Flowering Season → `bloom_seasons` | Union and deduplicate Spring/Summer/Fall/Winter. No conversion of seasons into invented months. |
| Habitat and Special Locations → `misc` | Split pipe-delimited descriptions, then union with existing tags. No removal of existing tags. |
| Family → `plant_taxonomy.family` | Fill a missing taxonomy row, blank family, or `Unknown`; preserve an existing known family and lineage. |
| Nativity, Flower Color, Major Plant Group, Rarity & Endemism | Keep source values in the local plan only. These do not have matching typed columns in the current plants schema. |
| Global Rank | Preserve the source text. This CSV contains a generic `Global rank` label or blanks rather than usable rank values. |
| Height, width, zones, bloom months, sun, water, foliage, edible, thorns, image | This CSV supplies no reliable direct measurements/values for these fields; preserve existing data. Habitat does not imply a watering or sun requirement. |

No additional database tables are created. Source attributes without a matching catalog field remain in the local plan.

**Efficient county junction updates**

For each batch, the database function:

1. Upserts plants and taxonomy and collects their IDs in input order.
2. Resolves distinct county FIPS values in one set-based county insert. Existing county names remain unchanged.
3. Unnests the batch's county arrays, maps each source record to its resolved plant ID, and deduplicates `(plant_id, fips)` pairs.
4. Inserts all pairs in one statement with `ON CONFLICT (fips, plant_id) DO NOTHING`.

The existing junction primary key supports conflict detection. There is no HTTP call or SQL round trip per county. The default batch contains 10 source rows; the maximum is 25. Each batch is one database function call and one transaction. A failure rolls back the entire batch, including its plant, taxonomy, and county changes.

Only `1` cells produce links. `0` cells never delete existing county links. Parent species receive the union of all imported subspecies/variety county links.

Four county labels are ambiguous in the mapping file and are skipped by default:

| Label | Candidate FIPS |
|---|---|
| Baltimore, Maryland | 24005, 24510 |
| St. Louis, Missouri | 29189, 29510 |
| Richmond, Virginia | 51159, 51760 |
| Roanoke, Virginia | 51161, 51770 |

The scraper keys its sampling coordinates by county name plus state, so duplicate labels overwrite earlier coordinates. The CSV alone does not distinguish the two jurisdictions. The importer therefore does not assign both FIPS values. An optional JSON file passed with `--county-overrides` can select one known integer FIPS per label after verification. Unknown labels and invalid selections are rejected.

**Access and recovery**

The new write functions are executable by `service_role` only; public, anonymous, and authenticated roles have no execution grant. The Python importer uses the existing linked Supabase CLI/database login, not a public Edge Function, and does not embed credentials.

Linked previews and apply runs save a timestamped plant/schema snapshot before importing. This is a plant/schema snapshot, not a full county/taxonomy backup or an automatic rollback tool. Existing county links are never removed.

The importer appends a checkpoint only after confirming success, the exact batch count, and a positive returned plant ID for every source row. Checkpoints are scoped to project, complete plan fingerprint, and importer version. Restarting with the same command resumes confirmed records; a changed plan or project cannot reuse their checkpoints. A crash after commit but before checkpoint can replay a batch safely because the writes are idempotent. Preserve and repair a malformed progress JSONL before retrying; it is never silently ignored.

Run only one BONAP importer per output directory. Avoid simultaneous importers/migrations while applying schema changes.

**How to run**

From PowerShell:

```powershell
cd C:\Users\petre\Documents\ChatGPT\rootfind\backend

# Local file validation and proposal generation; no database connection.
..\.venv\Scripts\python.exe scripts/import_bonap.py preview

# Compare exact parent names with the linked database; still no writes.
..\.venv\Scripts\python.exe scripts/import_bonap.py preview --linked --allow-create
```

Read `.bonap/preview.json`, especially unmatched names, rejected names, and skipped counties. `--allow-create` includes missing parent species in the proposal. Without it, a linked preview or apply skips unmatched plants.

Before deployment, resolve the repository migration collision described below. Then review pending migrations carefully:

```powershell
npx supabase db push --dry-run --include-all
npx supabase db push --include-all
```

These commands include every pending migration, not just BONAP. Migration `202610240013_bonap_import.sql` must be applied before importing. Do not replay older migrations manually.

When ready to import all supported source rows, including missing parent species:

```powershell
..\.venv\Scripts\python.exe scripts/import_bonap.py apply --allow-create --batch-size 10
```

To update existing parent species only, omit `--allow-create`. The apply command regenerates its plan with the supplied options and a fresh database comparison; use the same options reviewed in the preview. The terminal must have working Supabase CLI credentials; if required, set `SUPABASE_DB_PASSWORD` in that terminal. No credentials are printed or stored in plans.

**Validation and outstanding repository issues**

- All 48 Python tests passed, including four new BONAP tests.
- The dedicated BONAP embedded-PostgreSQL integration test passed after the final changes. It verifies untouched general-upsert definitions, parent-species collapsing, repeated-batch idempotency, county preservation, absence of additional tables and taxonomy updates, trait/season/tag merging, transaction rollback, and restricted write permissions.
- The earlier full backend test run had 43 tests: 40 passed and three failed. The duplicate `202610240010` migration prefix is resolved by moving the county-count sequence after BONAP. County-count fixtures now insert `sun_type[]` values after the catalog migration; the schema compatibility test explicitly exercises both scalar and array `sun` filtering. The two affected county-count test files pass (6 tests).
- The catalog-enrichment migration uses `202610240010`; BONAP uses `202610240013`. The county-count migrations follow as `202610240014_count_plants_by_county.sql`, `202610240015_search_county.sql`, and `202610240016_count_query_plan.sql` to keep every migration version unique. Confirm the hosted migration history before pushing these renumbered migrations.
- No live import, migration application, website scraping, or remote database writes were performed for this BONAP implementation.
