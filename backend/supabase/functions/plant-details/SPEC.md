# plant-details

Return one plant record with its common names and county FIPS codes. This
contract describes the handler, the `plant_details` RPC, and the plant schema
through migration `202610240008_mobot_enrichment.sql`.

## Request

`GET /functions/v1/plant-details?plantId=<id>`

| Parameter | Location | Required | Accepted value |
| --- | --- | --- | --- |
| `plantId` | Query string | Yes, exactly once | Decimal digits representing an integer from 1 to 2147483647 |

Leading zeros are accepted. Signs, whitespace, decimals, missing/empty values,
duplicate parameters, and unknown query parameters are rejected. The entire
request URL must be at most 8192 characters. No request body is used.

No user login is required. Hosted clients send the project's publishable key in
`apikey`. JWT verification is disabled in `supabase/config.toml`. The handler
uses its own `SUPABASE_ANON_KEY`, regardless of caller credentials.

## Response

HTTP `200`, `Content-Type: application/json`, with one object containing all
columns of `public.plants`, plus `common_names` and `county_fips`:

| Field | JSON type / values |
| --- | --- |
| `plant_id` | Integer |
| `genus`, `species` | Strings |
| `life_cycle` | `perennial`, `biennial`, `annual`, or null |
| `foliage` | `deciduous`, `evergreen`, or null |
| `minheight`, `maxheight`, `minwidth`, `maxwidth` | Integer inches, or null |
| `is_woody`, `is_tree`, `edible`, `thorns` | Booleans, or null |
| `sun` | `full sun`, `part shade`, `full shade`, or null |
| `water` | Array of `low`, `medium`, `high`, or null |
| `image_link` | String, or null |
| `misc` | Array of strings, or null |
| `min_zone`, `max_zone` | Integer hardiness-zone bounds, or null |
| `bloom_months` | Array of full English month names (`January` through `December`), or null |
| `common_names` | Linked common-name strings, sorted by name using database ordering |
| `county_fips` | Linked integer county FIPS codes, sorted numerically |

Unpopulated plant fields remain null. Missing links produce empty arrays for
`common_names` and `county_fips`. FIPS codes are numbers, so leading zeros are
not retained. The response uses all plant columns dynamically: future columns
added to `public.plants` will also be returned. Taxonomy fields from the separate
`plant_taxonomy` table/view are not joined into this response.

Example request:

```http
GET /functions/v1/plant-details?plantId=6
apikey: <publishable-key>
```

Example response:

```json
{
  "plant_id": 6,
  "genus": "Asclepias",
  "species": "tuberosa",
  "life_cycle": "perennial",
  "foliage": "deciduous",
  "minheight": 12,
  "maxheight": 36,
  "minwidth": null,
  "maxwidth": null,
  "is_woody": false,
  "is_tree": false,
  "edible": null,
  "thorns": false,
  "sun": "full sun",
  "water": ["low", "medium"],
  "image_link": null,
  "misc": null,
  "min_zone": null,
  "max_zone": null,
  "bloom_months": null,
  "common_names": ["Butterfly milkweed", "Butterfly weed"],
  "county_fips": [12095, 51059]
}
```

## Errors

Errors are JSON objects of the form `{"error":"message"}`.

| Status | Condition / message |
| --- | --- |
| `400` | Missing/invalid/repeated ID: `Provide plantId exactly once as a positive 32-bit integer`; unknown query key: `Unknown parameter: <name>`; oversized URL: `Query is too long` |
| `401`, `403` | Matching status from the RPC; `Plant lookup access denied` |
| `404` | Unknown plant ID; `Plant not found` |
| `405` | Unsupported method; `Use GET`. Includes `Allow: GET, OPTIONS`. |
| `429` | Runtime rate limit exceeded; `Too many plant lookups; try again shortly`. Includes `Retry-After: 60`. |
| `502` | Other non-success RPC response; `Plant lookup failed` |
| `500` | Missing environment, fetch failure/timeout, invalid RPC JSON, or other unexpected error; `Plant lookup unavailable` |

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
  `POST /rest/v1/rpc/plant_details` with `{"p_plant_id": <plantId>}` and a
  10-second fetch timeout. The RPC is read-only and available to `anon` and
  `authenticated`.
