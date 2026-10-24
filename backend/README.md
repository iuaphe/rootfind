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
- Only signed-in users can read these tables or call the search RPC. RLS remains
  active because the SQL function uses SECURITY INVOKER and the Edge Function
  forwards the caller's token. No service-role key is used.
- The initial migrations create new tables; reconcile them before applying to an
  existing database. The frontend's sample Drizzle `task` table is unrelated.
  These local migrations have not been deployed. If the supplied tables already
  exist, do not rerun the initial CREATE TABLE migration: ensure the authenticated
  SELECT grants and RLS policies cover all five tables, then apply the search
  function definition. Existing smallint FIPS columns must be widened to integer
  in both county tables before importing codes above 32767.

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
  { headers: { apikey: publishableKey, Authorization: `Bearer ${session.access_token}` } }
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
Supabase supplies `SUPABASE_URL` and `SUPABASE_ANON_KEY` to the function.
Send a signed-in user's access token in `Authorization`.

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
both SQL migrations in PGlite (embedded Postgres), including common-name aliases,
scientific names, enum filters, county joins, pagination, and access permissions.
This does not replace a Supabase Edge runtime check. From the repository root,
with Deno installed:

```sh
deno check backend/supabase/functions/search-plants/index.ts
```

References: [Supabase database functions](https://supabase.com/docs/guides/database/functions)
and [forwarding auth context](https://supabase.com/docs/guides/functions/auth-legacy-jwt).
