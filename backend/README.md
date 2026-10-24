# Plant name search

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
