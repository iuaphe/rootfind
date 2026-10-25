# NC State and PFAF enrichment

These adapters share the MOBOT collector's verified HTTPS, backups, exact-name
checks, failed-match console output, four workers with a single request limiter,
local cache, audit trail, and optional live database commits. No new credentials
or dependencies beyond the existing CLI setup and optional truststore are needed.
Run commands from `backend/` with the project's virtual-environment interpreter.

## First apply the new migration

Stop other importers before changing the schema. Do not rerun MOBOT `prepare`.
Migration `202610240010_catalog_enrichment.sql` must follow 008/009:

```powershell
npx supabase db push --dry-run --include-all
npx supabase db push --include-all
```

It converts `sun` from a scalar to `sun_type[]`, preserving known values as
singleton arrays, and adds `bloom_seasons text[]` and `drainage text[]`.
It updates the search RPC's sun predicate and preserves legacy scalar-sun upsert
clients through normalization. Existing water and inch dimensions remain intact.
Plant-details responses now return a sun array and the two new columns. Existing
search query parameters and endpoints remain unchanged.

## Try each source without updating plant rows

```powershell
..\.venv\Scripts\python.exe scripts/enrich_catalog.py ncstate collect --limit 5
..\.venv\Scripts\python.exe scripts/enrich_catalog.py pfaf collect --limit 5
```

Each run reads the full current plant list and saves a backup, then processes the
first five plants. Inspect `.enrichment/ncstate/plan.jsonl` and
`.enrichment/pfaf/plan.jsonl`: raw_fields, patch, before, after_inches, warnings,
status, and source_url. Neither sample changes plant rows.

## Full runs with progressive Supabase updates

```powershell
..\.venv\Scripts\python.exe scripts/enrich_catalog.py ncstate collect --apply-live --workers 4 --delay 1
..\.venv\Scripts\python.exe scripts/enrich_catalog.py pfaf collect --apply-live --workers 4 --delay 1
```

Run these sequentially, rather than in competing terminals. Existing matched
sample records are applied automatically when switching to live mode. Commits
occur every 10 matched records or on the next main-loop check after 30 seconds;
normal completion commits any smaller final batch. Watch `Live Supabase commit`.
Committed data survives interruption; restart the same command to resume.
Failed database writes stop without marking the batch applied. Source blocks
stop new requests. A larger robots.txt crawl delay takes precedence over --delay.

To apply a separately collected plan, stop collection and run:

```powershell
..\.venv\Scripts\python.exe scripts/enrich_catalog.py ncstate apply
..\.venv\Scripts\python.exe scripts/enrich_catalog.py pfaf apply
```

Each source has its own cache, plan, and backups under `.enrichment/<source>/`
(ignored by Git). Never share a custom --directory across sources/projects or run
two processes against the same plan. `--retry` recollects completed entries from
cached pages. A partially written final JSONL line must be repaired before resume.

## Source rules and limitations

- NC State uses a direct `/plants/<scientific-name-slug>/` lookup. A 404 is logged
  as no_match; it does not guess alternate species or automatically follow synonyms.
  A valid page's complete scientific heading must match, including rejecting
  cultivar/infraspecific suffixes. Other URL slugs can therefore be missed.
- PFAF uses `/user/Plant.aspx?LatinName=<encoded-name>`. A confirmed empty profile
  template or 404 is no_match. PFAF's separate author attribution after ` - ` is
  excluded from the identity; cultivar/variety/subspecies names are retained and
  rejected for bare species. Unrecognized page structure is an error, not a skip.
- Dimensions stay SMALLINT inches. NC State labeled height/width ranges supply
  minima and maxima. PFAF's metric `growing to ... by ...` values supply maxima
  only, rounded up; its rounded imperial parentheses are not added a second time.
- Minima merge downward, maxima upward, zones span all known numeric zones.
  Only explicitly labeled USDA zones are parsed, not PFAF UK hardiness numbers.
- Sun, water, explicit bloom months, seasons, and drainage merge by union, without
  clearing existing values. Months and seasons remain distinct; summer does not
  automatically become June/July/August. Source/raw/audit records retain context.
- NC State light/moisture mapping follows [NCSU_RULES.md](NCSU_RULES.md).
  PFAF physical characteristics provide no-shade/full-sun, semi-shade/part-shade,
  full-shade/full-shade, and dry/low, moist/medium, wet-or-water/high mappings.
  Only affirmative supported phrases in the main physical-characteristics field
  are parsed; Related Plants tables, reader comments, seed-harvest months, and
  cultivation/medicinal narratives cannot populate these fields.
- PFAF is often single-height/width rather than a true range. Unsupported wording
  is logged and left unchanged. Neither source is guaranteed to cover every plant
  in the USDA-derived catalogue. County links and unrelated flags are unchanged.

Checked source pages: [NC State purple coneflower](https://plants.ces.ncsu.edu/plants/echinacea-purpurea/),
[PFAF purple coneflower](https://pfaf.org/user/Plant.aspx?LatinName=Echinacea+purpurea).
At implementation, NC State robots.txt returned 404; PFAF robots.txt allowed the
plant-profile path. These are access observations, not a data-reuse license.

## Tests

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s scripts -p 'test_*.py' -v
npm test
```

Tests use offline fixtures and embedded PostgreSQL. No migration or enrichment
is automatically deployed by running them.
