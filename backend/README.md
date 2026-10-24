# Plant search

MOBOT enrichment tooling and its collection/prepare/apply workflow are documented
in [scripts/MOBOT.md](scripts/MOBOT.md). Wait until the current USDA import finishes
before applying its schema migration: it changes stored measurements to inches
and changes `water` from a single enum to an enum array.

## Plant details

`GET /functions/v1/plant-details?plantId=6` returns one JSON object containing all
columns of the plant record, plus `common_names` (sorted strings) and `county_fips`
(sorted integers). Plants without linked records have empty arrays; nullable plant
fields remain null. An unknown ID returns 404 with `{"error":"Plant not found"}`.
Only `plantId` is accepted, exactly once, as a positive 32-bit integer.

```js
const response = await fetch(`${supabaseUrl}/functions/v1/plant-details?plantId=6`, {
  headers: { apikey: publishableKey }
});
if (!response.ok) throw new Error((await response.json()).error ?? 'Plant lookup failed');
const plant = await response.json();
```

For hosted deployment, run `202610240007_plant_details.sql` in the SQL Editor and
deploy the `plant-details` directory (`index.ts`, `handler.ts`, `input.ts`). Keep
JWT verification off. No user login is needed, and anonymous table access is not
granted. The RPC is read-only SECURITY DEFINER with an empty search path. Like
the other endpoints, this function has a best-effort per-runtime throttle of 120
requests/minute/forwarded IP, with bounded counters, and short public response
caching. Direct RPC requests bypass the Edge throttle. All current and future
`plants` columns are included in the public response; keep private fields elsewhere.

```sh
npx supabase migration up --local
npx supabase functions serve plant-details
```

## County lookup

`POST /functions/v1/plant-counties` accepts a JSON body:

```json
{"plantIds":[12,45,81,12]}
```

The response is an array of county-FIPS arrays, one per input position:

```json
[[12069,12095],[51059],[],[12069,12095]]
```

Input order and duplicate IDs are preserved. FIPS codes in each list are sorted
numerically. Missing plants and plants without county records return `[]` at
their position. Empty input returns `[]`. Send up to 1000 positive 32-bit integer
IDs, with a JSON body no larger than 16 KiB. No user login is needed; send the
publishable key in `apikey`, and keep JWT verification off for this function.

Like `search-plants`, this endpoint applies a best-effort limit of 120 POST
requests per 60-second window per forwarded client IP in each Edge runtime.
Excess requests return 429 and `Retry-After: 60` before reading the body or calling
the database. Preflight requests are exempt. Counters are bounded to 5000 IPs and
reset when the runtime restarts. This is not a distributed limit: missing or
spoofed forwarding headers, multiple runtimes, and direct RPC calls can bypass it.

```js
const response = await fetch(`${supabaseUrl}/functions/v1/plant-counties`, {
  method: 'POST',
  headers: { apikey: publishableKey, 'Content-Type': 'application/json' },
  body: JSON.stringify({ plantIds: [12, 45, 81] })
});
if (!response.ok) throw new Error((await response.json()).error ?? 'County lookup failed');
const countiesByPlant = await response.json();
```

For hosted deployment, run `202610240006_plant_counties.sql` in the SQL Editor,
then deploy the `plant-counties` directory (`index.ts`, `handler.ts`, `input.ts`).
The migration uses the existing `plant_county` table and grants only RPC execution;
it does not grant anonymous table access. It can also be applied to an existing
matching schema independently of the search migrations. To serve locally:

```sh
npx supabase migration up --local
npx supabase functions serve plant-counties
```

## Name lookup

`GET /functions/v1/search-plants?search=milkweed` returns only matching plant IDs:

```json
[1, 2]
```

Search is a case-insensitive literal substring of `genus species` or any common
name linked through `common_plant` and `common_names`. Results contain unique IDs
in ascending order. No matches return `[]`. Each response contains one page of
IDs (default 50, maximum 100), without counts or plant fields. Use
`?search=milkweed&page=2&pageSize=50` for another page. An empty page means there
are no further IDs at that offset.

The optional `search` parameter accepts up to 200 characters after trimming and
whitespace normalization. Omitting it browses a bounded page of plants. Optional filters:

| Parameter | Values |
| --- | --- |
| `lifeCycle` | `perennial`, `biennial`, `annual` |
| `foliage` | `deciduous`, `evergreen` |
| `sun` | `full sun`, `part shade`, `full shade` |
| `water` | `high`, `medium`, `low`; `med` is accepted as an alias for `medium` |
| `isWoody`, `isTree`, `edible`, `thorns` | `true` or `false` |
| `minHeight`, `maxHeight`, `minWidth`, `maxWidth` | Whole inches, 0–32767 |
| `page`, `pageSize` | Default 1 and 50; maximum 1000 and 100 |

Pass enum selections as comma-separated values, for example
`?search=oak&lifeCycle=perennial,annual&isWoody=true&minHeight=12&maxHeight=72`.
Selections within a group are combined with OR; different groups use AND.
Measurement intervals overlap the plant's recorded range, including endpoints.
Omitted filters impose no restriction; an active filter excludes unknown values
for the fields it compares. Parameters may appear only once. Unknown parameters,
invalid options, and minimums greater than maximums return HTTP 400.

## Frontend

No login or user bearer token is needed. Send the project publishable key:

```js
const params = new URLSearchParams({ search: 'milkweed' });
const response = await fetch(
  `${supabaseUrl}/functions/v1/search-plants?${params}`,
  { headers: { apikey: publishableKey } }
);
if (!response.ok) throw new Error((await response.json()).error ?? 'Search failed');
const plantIds = await response.json();
```

The Edge Function calls PostgREST with its built-in `SUPABASE_ANON_KEY`. JWT
verification remains disabled. The RPC uses SECURITY DEFINER, a fixed empty search
path, and a static read-only query. Anonymous table access remains revoked by
migration 004; anonymous callers may invoke the RPC. Direct RPC calls bypass the
Edge Function's best-effort 120 requests/minute/IP throttle. Successful HTTP
responses allow short public caching.

## Apply this update

For an existing hosted project with the name search RPC:

1. Apply migration 008 (inches/water arrays), then `supabase/migrations/202610240009_search_water_pagination.sql`. The latter supersedes the earlier search-filter function. The MOBOT `prepare` command runs both 008 and 009 after taking a backup; do not run it until the current feet-based import has finished.
2. Redeploy `search-plants`, including `index.ts`, `handler.ts`, and `query.ts`.
3. Keep JWT verification off in the Dashboard (matching `config.toml`).

The SQL update replaces the existing RPC while preserving its argument and return
types and ID-array response. Direct RPC clients pass enum arrays, boolean values,
and numeric measurements in `p_filters`, for example
`{"p_filters":{"search":"milkweed","lifeCycle":["perennial"],"minHeight":12}}`.

No hosted migration or deployment is performed by editing these files.

## USDA importer checkpoints

The streaming importer records version-2 JSON events at the existing progress
file path. Only confirmed writes to every configured database destination,
durable SQL-only exports, and deliberate exclusions with recorded reasons are
complete. Failed fetches, incomplete profile/characteristic/distribution requests,
and failed writes remain retryable; the CLI exits nonzero when any retries remain.
SQL exports commit and flush each plant before checkpointing.
On resume, bytes after the last confirmed SQL checkpoint are saved to a
`*.unconfirmed-*.bak` file and removed from the active export before retrying.
If no matching checkpoint exists, the existing export is preserved in that backup
and rebuilt from byte zero.

Legacy plain-symbol progress entries cannot prove a write succeeded, so the new
importer ignores them and retries those symbols. Preserve the old file for audit;
do not assume it proves all data was imported. Database upserts are repeatable.
Use a fresh SQL output path if an older export was interrupted inside a statement
or transaction. Checkpoints are scoped to the configured destinations so an
export or a write to another project cannot masquerade as a confirmed live write.

## Local development

Requires Docker Desktop running and the Supabase CLI. From `backend/`:

```sh
npx supabase start
npx supabase migration up --local
npx supabase functions serve search-plants
```

Local endpoints avoid Windows reserved ports: functions/API at
`http://127.0.0.1:15421`, Studio at `http://127.0.0.1:15423`, Postgres at port
`15422`. Use `npx supabase status` to inspect local endpoints and keys.

For a new database, the migration chain creates the supplied plant, county, and
common-name tables, with INTEGER FIPS columns. Do not run the initial CREATE TABLE
migration against an existing schema without reconciling it first.

## Local SQL backups

With Docker Desktop running and `npm ci` completed, export the linked hosted
database from `backend/`:

```sh
npm run db:dump
```

The script uses the installed, lockfile-pinned CLI through
`npx --no-install supabase db dump`. The CLI creates disposable Supabase Postgres
containers to run `pg_dump` against the live source database. A separate local
database server is not required for hosted exports, and the source is only read.
Use `npx supabase login` and `npx supabase link --project-ref YOUR_PROJECT_REF`
first if this checkout is not already linked. The CLI uses its saved credentials;
`SUPABASE_DB_PASSWORD` can supply the database password when needed. To export a
different database, set `SUPABASE_DB_URL` to its percent-encoded PostgreSQL
connection string (use the session pooler on IPv4-only networks).

Each successful run writes a new `backups/rootfind-<timestamp>-<suffix>/` containing
`roles.sql`, `schema.sql`, `data.sql`, and a combined **`rootfind.sql`**. Dumps are
ignored by Git and created with private file permissions. Failed exports do not
publish a completed backup. Roles, schema, and data are exported separately, so
avoid schema changes while a backup runs; this is not a single coordinated
snapshot across all three commands.

To export an existing local Supabase database instead:

```sh
npx supabase db start
npm run db:dump -- --local
```

Restore the combined file into a **fresh Supabase database** with its built-in
roles and managed schemas already initialized (not an empty vanilla Postgres
server). With `psql` available and `LOCAL_DB_URL` pointing to that fresh target
using an administrator role able to restore role settings and parameter grants:

```sh
psql "$LOCAL_DB_URL" --single-transaction --variable ON_ERROR_STOP=1 \
  --file backups/rootfind-<timestamp>-<suffix>/rootfind.sql
```

The local copy created during setup uses the isolated project at `backups/local`,
container `supabase_db_rootfind-backup`, and Postgres port **15432**. Restore into
that container with its `supabase_admin` role:

```sh
docker exec -i supabase_db_rootfind-backup psql -U supabase_admin -d postgres \
  --single-transaction --variable ON_ERROR_STOP=1 \
  < backups/rootfind-<timestamp>-<suffix>/rootfind.sql
```

Run a restore only against a fresh target; the copy already contains the restored
tables. To stop or resume this local database without removing its saved data:

```sh
npx supabase stop --workdir backups/local
npx supabase db start --workdir backups/local
```

The backup follows [Supabase's CLI backup workflow](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore).
The CLI omits Supabase-managed schema definitions and migration history by default;
custom changes inside managed schemas need separate handling. Storage file
contents, Edge Function code, and project settings are not included. Vector
bucket/index metadata is excluded as recommended by Supabase.

## Tests

From `backend/`, with Node 24+:

```sh
npm ci
npm test
```

Tests run the search migrations in PGlite (embedded Postgres), checking scientific names,
common-name aliases, duplicate prevention, literal punctuation, more than 100
matches, anonymous RPC access, input validation, and denial of direct table reads
and writes, trait combinations, measurement overlap, and invalid filters. HTTP tests use a mocked database API. These do not replace testing the
deployed Edge Function.
