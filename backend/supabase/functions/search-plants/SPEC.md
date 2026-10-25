# search-plants

Search or browse plants and return one page of matching plant IDs. This contract
describes the handler and the `search_plants` RPC after migration
`202610240011_search_county.sql`.

## Request

`GET /functions/v1/search-plants`

No request body is used. All query parameters are optional, case-sensitive, and
may appear at most once. Unknown parameters are rejected. The entire request URL
must be at most 8192 characters.

| Parameter | Accepted value | Default / meaning |
| --- | --- | --- |
| `search` | String, at most 200 characters after trimming and collapsing whitespace | Empty; browse without a name restriction |
| `lifeCycle` | Comma-separated `perennial`, `biennial`, `annual` | No life-cycle restriction |
| `foliage` | Comma-separated `deciduous`, `evergreen` | No foliage restriction |
| `sun` | Comma-separated `full sun`, `part shade`, `full shade` | No sun restriction |
| `water` | Comma-separated `low`, `medium`, `high`; `med` aliases `medium` | No water restriction |
| `isWoody` | Literal `true` or `false` | No woody-trait restriction |
| `isTree` | Literal `true` or `false` | No tree-trait restriction |
| `edible` | Literal `true` or `false` | No edibility restriction |
| `thorns` | Literal `true` or `false` | No thorn restriction |
| `minHeight` | Integer 0–32767, in inches | Plant's recorded maximum height must be at least this value |
| `maxHeight` | Integer 0–32767, in inches | Plant's recorded minimum height must be at most this value |
| `minWidth` | Integer 0–32767, in inches | Plant's recorded maximum width must be at least this value |
| `maxWidth` | Integer 0–32767, in inches | Plant's recorded minimum width must be at most this value |
| `page` | Integer 1–1000 | `1` |
| `pageSize` | Integer 1–100 | `50` |
| `county` | One FIPS code, 1–99999; up to five decimal digits, including leading zeros | No county restriction |

Numeric parameters use decimal digits without signs, spaces, or decimals. Leading
zeros are allowed only for `county`; measurements also accept `0`. Each minimum must not exceed its
corresponding maximum when both are supplied. Enum values must match exactly;
spaces around comma-separated entries are not trimmed. Repeated selections
within a single enum parameter are deduplicated.

The name search is a case-insensitive literal substring of `genus + ' ' + species`
or any linked common name. SQL wildcard characters have no special meaning.
Selections within one enum filter use OR; separate filters use AND. A water
filter matches any selected level in the plant's water array.

The optional county filter requires a `plant_county` link for that FIPS and applies
together with all name and trait filters before pagination. Unknown counties and
counties with no matching plants return `[]`. Direct RPC clients pass `county`
as a JSON integer, for example `{"p_filters":{"county":1001,"search":"oak"}}`.
County counts remain nationwide and reject the `county` parameter.

Measurement filters test inclusive interval overlap, rather than requiring the plant's range
to fit inside the requested range. An active filter excludes a plant when the
field it compares is null; omitted filters impose no restriction.

No user login is required. Hosted clients send the project's publishable key in
`apikey`. JWT verification is disabled in `supabase/config.toml`. The handler
uses its own `SUPABASE_ANON_KEY` for the database request, regardless of caller
credentials.

## Response

HTTP `200`, `Content-Type: application/json`, with a bare array of unique integer
plant IDs in ascending order:

```json
[12, 45, 81]
```

There are no plant fields, counts, or pagination metadata. The offset is
`(page - 1) * pageSize`; a page has at most `pageSize` IDs. No matches or an offset
beyond the results returns `[]`. Ordering is by ID, without relevance ranking.

Example request:

```http
GET /functions/v1/search-plants?search=milkweed&sun=full%20sun,part%20shade&water=low,medium&minHeight=12&maxHeight=72&page=1&pageSize=50
apikey: <publishable-key>
```

## Errors

Errors are JSON objects of the form `{"error":"message"}`.

| Status | Condition / message |
| --- | --- |
| `400` | Invalid, repeated, or unknown query parameter; invalid range; URL too long. Message identifies the validation failure, such as `Invalid water option`, `Provide page exactly once`, or `Query is too long`. |
| `401`, `403` | Matching status from the RPC; `Search access denied` |
| `405` | Unsupported method; `Use GET`. Includes `Allow: GET, OPTIONS`. |
| `429` | Runtime rate limit exceeded; `Too many searches; try again shortly`. Includes `Retry-After: 60`. |
| `502` | Other non-success RPC response; `Plant search failed` |
| `500` | Missing environment, fetch failure/timeout, invalid RPC JSON, or other unexpected error; `Plant search unavailable` |

## HTTP behavior and dependencies

- `OPTIONS` returns `204` with no body and bypasses validation and throttling.
- CORS allows origin `*`, methods `GET, OPTIONS`, and request headers
  `authorization, apikey, content-type, x-client-info`.
- Successful responses use `Cache-Control: public, max-age=30, s-maxage=60`;
  other responses use `Cache-Control: no-store`.
- GET requests within the URL limit are throttled before parameter parsing or
  database access: 120 requests per 60-second window per forwarded IP per
  runtime. IP precedence is `cf-connecting-ip`, `x-real-ip`, then the first
  `x-forwarded-for` entry. Requests without an IP bypass the throttle. Counters
  hold at most 5000 IPs, can be evicted, and reset with the runtime; this is a
  best-effort limit, not a distributed one. Direct RPC calls bypass it.
- Requires `SUPABASE_URL` and `SUPABASE_ANON_KEY`. Calls
  `POST /rest/v1/rpc/search_plants` with `{"p_filters": <parsed filters>}` and a
  10-second fetch timeout. The RPC is read-only and available to `anon` and
  `authenticated`.
