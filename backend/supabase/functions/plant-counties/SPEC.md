# plant-counties

Look up county FIPS codes for a batch of plant IDs. Each response position
corresponds to the same position in the input. This contract describes the
handler and the RPC in `202610240006_plant_counties.sql`.

## Request

`POST /functions/v1/plant-counties`

Send `Content-Type: application/json`; media-type casing and parameters such as
`; charset=utf-8` are accepted. The body must be at most 16384 bytes (16 KiB),
including JSON formatting, and must contain exactly one property:

| Property | Required | Accepted value |
| --- | --- | --- |
| `plantIds` | Yes | Array of 0–1000 JSON numbers, each an integer from 1 to 2147483647 |

Duplicate IDs are accepted. Strings, null IDs, fractional numbers, nonpositive
IDs, nested arrays, extra properties, and a non-object top-level body are
rejected. Query parameters are not read or validated.

No user login is required. Hosted clients send the project's publishable key in
`apikey`. JWT verification is disabled in `supabase/config.toml`. The handler
uses its own `SUPABASE_ANON_KEY`, regardless of caller credentials.

Example request:

```http
POST /functions/v1/plant-counties
apikey: <publishable-key>
Content-Type: application/json

{"plantIds":[12,45,81,12]}
```

## Response

HTTP `200`, `Content-Type: application/json`, with a bare array of integer arrays:

```json
[[12069,12095],[51059],[],[12069,12095]]
```

The outer array has exactly as many elements as `plantIds`, preserving input
order and duplicate IDs. Each inner array contains that plant's county FIPS codes
sorted numerically. Missing plants and plants with no linked counties both
produce `[]` at their position. An empty input array returns `[]`. FIPS codes are
numbers, so leading zeros are not retained. No county names or plant fields are
returned.

## Errors

Errors are JSON objects of the form `{"error":"message"}`.

| Status | Condition / message |
| --- | --- |
| `400` | No body stream: `JSON body required`; malformed or empty JSON: `Invalid JSON body`; oversized body: `JSON body must not exceed 16 KiB` |
| `400` | Wrong object shape: `Expected { "plantIds": [1, 2, 3] }`; invalid IDs or too many IDs: `plantIds must contain up to 1000 positive 32-bit integers` |
| `401`, `403` | Matching status from the RPC; `County lookup access denied` |
| `405` | Unsupported method; `Use POST`. Includes `Allow: POST, OPTIONS`. |
| `415` | Missing or unsupported media type; `Use Content-Type: application/json` |
| `429` | Runtime rate limit exceeded; `Too many county lookups; try again shortly`. Includes `Retry-After: 60`. |
| `502` | Other non-success RPC response; `County lookup failed` |
| `500` | Missing environment, fetch failure/timeout, invalid RPC JSON, or other unexpected error; `County lookup unavailable` |

## HTTP behavior and dependencies

- `OPTIONS` returns `204` with no body and bypasses validation and throttling.
- CORS allows origin `*`, methods `POST, OPTIONS`, and request headers
  `authorization, apikey, content-type, x-client-info`.
- All responses use `Cache-Control: no-store`.
- POST requests are throttled before checking the content type, reading the
  body, or accessing the database: 120 requests per 60-second window per
  forwarded IP per runtime. IP precedence is `cf-connecting-ip`, `x-real-ip`,
  then the first `x-forwarded-for` entry. Requests without an IP bypass the
  throttle. Counters hold at most 5000 IPs, can be evicted, and reset with the
  runtime; this is a best-effort limit, not a distributed one. Direct RPC calls
  bypass it.
- Requires `SUPABASE_URL` and `SUPABASE_ANON_KEY`. Calls
  `POST /rest/v1/rpc/plant_counties` with `{"p_plant_ids": <plantIds>}` and a
  10-second fetch timeout. The RPC is read-only and available to `anon` and
  `authenticated`.
