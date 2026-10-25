# count-plants-by-county

Count all matching plants per recorded county in one database query. Requires
`202610240014_count_plants_by_county.sql`, following migrations 008–013.

## Request

`GET /functions/v1/count-plants-by-county`

Accepts all taxonomy parameters plus the name and trait parameters described in
[search-plants](../search-plants/SPEC.md#request): `search`, `lifeCycle`, `foliage`,
`sun`, `water`, `isWoody`, `isTree`, `edible`, `thorns`, `minHeight`, `maxHeight`,
`minWidth`, and `maxWidth`. All are optional. Empty filters count every linked
plant. `page` and `pageSize` are rejected; counts always cover all matches.
Taxonomy support requires `202610250001_taxonomy_search.sql` and uses the same
case-insensitive exact matches as plant search. `county` is also rejected here.

Search and count share query parsing, SQL validation, and matching predicates.
Enum selections within a filter use OR; separate filters use AND. Name search
uses a case-insensitive literal substring of the scientific name or any common
name. `med` aliases `medium` in water filters. Measurements use inclusive interval
overlap in inches. Active filters exclude unknown field values. Duplicate and
unknown query parameters, invalid values, and reversed ranges return 400.
The URL must not exceed 8192 characters. There is no request body.

No login or caller bearer token is required. Hosted clients send the project's
publishable key in `apikey`. The function uses its own `SUPABASE_ANON_KEY` for
the RPC regardless of caller credentials. JWT verification is disabled in config.

## Response

HTTP 200, `Content-Type: application/json`:

```json
[{"fips":1001,"plant_count":12},{"fips":1003,"plant_count":0}]
```

The bare JSON array contains one object per row of `county_info`, sorted by
numeric `fips`, including counties with zero matches. Both fields are integers.
FIPS values omit leading zeros; map clients using string keys can format them as
five digits. No counties returns `[]`; no matching plants returns zero for every
recorded county. Counties absent from `county_info` are not synthesized.

Each plant counts once per county, even if several common names match. A plant
linked to several counties counts in each. Plants without county links make no
contribution. The response contains no plant IDs, plant records, or pagination.
The RPC returns one JSON value so PostgREST's row limit does not truncate the list
of counties.

## Errors

Errors have the form `{"error":"message"}`.

| Status | Condition |
| --- | --- |
| 400 | Invalid, repeated, or unknown query parameter, pagination parameter, invalid range, or URL too long; message identifies the error |
| 401, 403 | Matching upstream status; `County count access denied` |
| 405 | Unsupported method; `Use GET`; `Allow: GET, OPTIONS` |
| 429 | Throttled; `Too many county counts; try again shortly`; `Retry-After: 60` |
| 502 | Other non-success RPC response; `County count failed` |
| 500 | Missing environment, fetch failure/timeout, invalid RPC JSON, or unexpected error; `County count unavailable` |

## HTTP behavior and database access

- OPTIONS returns 204 without querying or consuming rate quota.
- CORS allows origin `*`, methods `GET, OPTIONS`, and headers
  `authorization, apikey, content-type, x-client-info`.
- Successful responses use `Cache-Control: public, max-age=30, s-maxage=60`.
  All errors use `Cache-Control: no-store`.
- GET requests within the URL limit are throttled before parsing or querying:
  120 requests per 60-second window per forwarded IP per runtime. IP precedence
  is `cf-connecting-ip`, `x-real-ip`, then the first `x-forwarded-for` entry.
  Missing IPs bypass the throttle. Counters are shared by handlers in the same
  runtime, bounded to 5000 IPs, may be evicted, and reset with the runtime.
  This limit is best effort; direct RPC calls bypass it.
- Requires `SUPABASE_URL` and `SUPABASE_ANON_KEY`. Calls
  `POST /rest/v1/rpc/count_plants_by_county` with `{"p_filters": <parsed filters>}`
  and a 10-second fetch timeout.
- The RPC accepts JSON filter objects using enum arrays, booleans, and numeric
  measurements. It validates direct callers too, including rejecting pagination.
  It defaults `p_filters` to `{}`.
- Only RPC execution is granted to `anon` and `authenticated`. The RPC is stable,
  read-only SECURITY DEFINER with an empty search path. Matching and validation
  helpers stay in `rootfind_private`; no additional table grants are introduced.
