# Plant search backend

Provisional Supabase schema and `GET /functions/v1/search-plants` endpoint.
No database has been changed or function deployed by adding these files.

## Schema assumptions

- Tables follow the supplied schema: `plants`, `county_info`, `plant_county`,
  `common_names`, and `common_plant`. Measurement columns are `minheight`,
  `maxheight`, `minwidth`, and `maxwidth`.
- Search matches scientific names (`genus species`) or linked common names using
  `common_plant`. Multiple matching aliases return each plant only once.
- County `fips` is an integer, since full US county codes can exceed smallint.
  Leading zeroes are omitted in storage; format to five digits for display.
- Scientific names use the supplied case-sensitive UNIQUE (genus, species).
- Life cycle, foliage, sun, and water use the supplied PostgreSQL enums.
  Unknown properties are SQL NULL, not false. Filtering excludes unknown values.
- Height and width units are not yet decided. Use one consistent unit for data
  and requests. The API accepts whole numbers from 0 to 32767.
- Search is public: no login or user Authorization token is needed. The frontend
  should still send the project's publishable key in the `apikey` header. The
  function uses its built-in anon key for database requests. Anonymous clients can
  execute the search RPC but cannot query catalog tables directly. The RPC is
  SECURITY DEFINER with a fixed empty search path and static, read-only SQL; no
  service-role key is used.
- The endpoint applies a best-effort per-runtime limit of 120 requests per minute
  per forwarded client IP and allows short public response caching. This is a
  lightweight MVP guard, not a distributed abuse-prevention system; direct calls
  to the public RPC can bypass the Edge Function's rate limit.
- The initial migrations create new tables; reconcile them before applying to an
  existing database. The frontend's sample Drizzle `task` table is unrelated.
  These local migrations have not been deployed. If the supplied tables already
  exist, do not rerun the initial CREATE TABLE migration. Apply the search function
  and public RPC migrations, ensuring the function is SECURITY DEFINER with a
  fixed empty search path and anonymous table grants/policies are removed. Existing
  smallint FIPS columns must be widened to integer in both county tables before
  importing codes above 32767.

## Request contract

```text
?search=milkweed&county=12095&county=12069&lifeCycle=perennial&thorns=false&page=1&pageSize=25
```

| Parameter | Values |
| --- | --- |
| `search` | Case-insensitive literal substring of `genus species` or any linked common name; maximum 200 characters |
| `county` | Numeric FIPS code, repeat for multiple counties |
| `lifeCycle` | `perennial`, `biennial`, `annual` |
| `foliage` | `deciduous`, `evergreen` |
| `sun` | `full sun`, `part shade`, `full shade` |
| `water` | `high`, `med`, `low` |
| `isWoody`, `isTree`, `edible`, `thorns` | `true` or `false`; omit for any |
| `misc` | Exact, case-sensitive tag; repeat for alternatives |
| `minHeight`, `maxHeight`, `minWidth`, `maxWidth` | Whole-number requested interval; matches overlapping recorded ranges |
| `page`, `pageSize` | Defaults 1 and 25; maximums 10000 and 100 |

Filters combine with AND. Repeat multi-select parameters for OR within a filter;
do not use comma-separated lists. A county match means presence in any selected
county. Unknown filters and invalid values return 400. No `native`, flower,
pollinator, or other unsupported filters are accepted yet.

Results are ordered by genus, species, then plant ID. The response contains all
plant columns in `data`, plus `total`, `page`, and `pageSize`. An empty page still
returns the total matching count. Counties are filters, not embedded result rows.

```js
const params = new URLSearchParams({ search: 'milkweed', thorns: 'false' });
params.append('county', '12095');
const response = await fetch(
  `${supabaseUrl}/functions/v1/search-plants?${params}`,
  { headers: { apikey: publishableKey } }
);
if (!response.ok) throw new Error((await response.json()).error ?? 'Search failed');
const { data, total, page, pageSize } = await response.json();
```

## Local development and deployment

Requires Supabase CLI and Docker for local Supabase, plus Deno for runtime checks.
Run from `backend/`:

```sh
supabase start
supabase db reset
supabase functions serve search-plants
```

`db reset` rebuilds the local development database and erases its local data.
Supabase supplies `SUPABASE_URL` and `SUPABASE_ANON_KEY` to the function. The
public endpoint does not require a user token; send the public publishable key in
the frontend `apikey` header. For Dashboard deployment,
turn off JWT verification for `search-plants`, matching `verify_jwt = false` in config.
For an existing local database, apply the new public access migration without a reset:

```sh
npx supabase migration up --local
```

Restart `functions serve` after changing JWT verification. For existing hosted
tables and search RPC, apply `202610240004_rpc_only_public_search.sql` in the SQL
Editor. It removes anonymous table access and keeps public results available via
the read-only search RPC.

The checked-in config avoids Windows reserved ports in the default 5432x range:
API/functions use `http://127.0.0.1:15421`, Studio uses
`http://127.0.0.1:15423`, and Postgres uses port `15422`.
Run `npx supabase status` for the current endpoints and keys.

To apply to a selected hosted project after reviewing the provisional schema:

```sh
supabase login
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
supabase functions deploy search-plants
```

Tests, from `backend/` (Node 24+):

```sh
npm ci
npm test
```

Tests cover validation and the HTTP handler with a mocked database API, and run
all SQL migrations in PGlite (embedded Postgres), including common-name aliases,
scientific names, enum filters, county joins, pagination, anonymous RPC access,
denial of direct table reads, and denial of public writes.
This does not replace a Supabase Edge runtime check. From the repository root,
with Deno installed:

```sh
deno check backend/supabase/functions/search-plants/index.ts
```

References: [Supabase database functions](https://supabase.com/docs/guides/database/functions)
and [forwarding auth context](https://supabase.com/docs/guides/functions/auth-legacy-jwt).
