# suggest-plants

`GET /functions/v1/suggest-plants?q=acer` returns up to 12 unique suggestions.
Requires `202610250001_taxonomy_search.sql`.

`q` is required exactly once, limited to 200 characters after whitespace
normalization. Empty text returns `[]`. Text is matched as a case-insensitive
literal substring; `%` and `_` are not wildcards. A query such as `family=Ac`
restricts results to that taxonomy field; `family=` lists its available values.
Without `=`, taxonomy values and field-name prefixes can match. All 11 taxonomy
columns except `plant_id` and `lineage` are supported, including common-name ranks.

Optional context parameters are the taxonomy and trait filters from
[search-plants](../search-plants/SPEC.md). `search`, `county`, `page`, and `pageSize`
are rejected. If `q` names a taxonomy field already in the context, that one filter
is excluded so users can replace it; other filters still apply.

```json
[
  {"kind":"taxonomy","field":"family","value":"Aceraceae"},
  {"kind":"common","field":null,"value":"Acer maple"},
  {"kind":"scientific","field":null,"value":"Acer rubrum"}
]
```

Priority is taxonomy, common names, scientific names. Within each group: exact
matches, then prefixes, then other substrings, followed by frequency among
matching plants and alphabetical order. Duplicate values within a kind/field are
collapsed without regard to case. Clicking a taxonomy suggestion creates a chip;
name suggestions fill the editable name query.

The public handler uses its own `SUPABASE_ANON_KEY`, never caller credentials,
to call `suggest_plants(p_query text, p_filters jsonb)`. The RPC validates direct
callers, uses a fixed empty search path, and grants only execution to `anon` and
`authenticated`. It performs no writes and introduces no table grants.

CORS allows `GET, OPTIONS`, origin `*`, and the same headers as search. OPTIONS
returns 204. Successful results use `Cache-Control: public, max-age=30, s-maxage=60`;
errors are not cached. URLs are limited to 8192 characters. There is a best-effort
240 requests/minute/IP throttle with at most 5000 buckets; direct RPC calls bypass
it. The upstream request times out after 10 seconds.

Errors are JSON `{ "error": "message" }`: 400 for validation, 405 for unsupported
methods, 429 with `Retry-After: 60` for throttling, upstream 401/403 preserved,
502 for other RPC failures, and 500 for unexpected errors. Upstream details are
not returned. No sign-in is required; JWT verification is disabled in config.
