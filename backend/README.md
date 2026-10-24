# Plant name search

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
in ascending order. No matches return `[]`. All matching IDs are returned, without
pagination, counts, or plant fields.

The only accepted parameter is `search`, supplied exactly once, with 1–200
characters after trimming and whitespace normalization. Other parameters,
including previous trait filters, `page`, and `pageSize`, return HTTP 400.

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

For an existing hosted project with migrations 001–004 applied:

1. Run `supabase/migrations/202610240005_name_search_ids.sql` in the SQL Editor.
2. Redeploy `search-plants`, including `index.ts`, `handler.ts`, and `query.ts`.
3. Keep JWT verification off in the Dashboard (matching `config.toml`).

The SQL update replaces the existing RPC while preserving its argument and return
SQL types. Its JSON response changes from a paginated object to an ID array, so
update callers accordingly. Existing RPC clients still use
`{"p_filters":{"search":"milkweed"}}`; other filter keys are rejected.

No hosted migration or deployment is performed by editing these files.

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

## Tests

From `backend/`, with Node 24+:

```sh
npm ci
npm test
```

Tests run all migrations in PGlite (embedded Postgres), checking scientific names,
common-name aliases, duplicate prevention, literal punctuation, more than 100
matches, anonymous RPC access, input validation, and denial of direct table reads
and writes. HTTP tests use a mocked database API. These do not replace testing the
deployed Edge Function.
