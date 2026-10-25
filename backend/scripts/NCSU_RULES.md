# NC State enrichment conversion rules

Selected replacement source: [NC State Extension Gardener Plant Toolbox](https://plants.ces.ncsu.edu/).
Its profiles combine structured dimensions, light, moisture/drainage labels,
USDA zones, life cycle, and flower traits. Bloom time often uses seasons rather
than months, so month completeness will be lower than MOBOT's on some records.
The implementation is `enrich_catalog.py ncstate`; enabling it does not switch
the running MOBOT script. Dimensions, zones, sun, water, bloom, and drainage are
implemented; other-trait rules below guide future extensions without importing
those unrelated fields now.

## Identity and extraction

- Search scientific names. Prefer an exact normalized species/hybrid match;
  normalize whitespace, case, and hybrid sign `×` to `x`.
- Reject cultivar, variety, subspecies, genus-only, or authority suffix matches
  for a bare species. Never apply a related species merely because it ranked first.
- Compare the profile's main scientific-name heading, not common names, cultivar
  lists, related plants, previous-name lists, or plant names in descriptions.
- Resolve synonyms only through a separate, explicitly verified accepted-name
  mapping. Log ambiguous and unmatched names with requested/returned names and URLs.
- Prefer the labeled Dimensions, Light, Soil Drainage, USDA Plant Hardiness Zone,
  Life Cycle, and Flowers sections. Do not parse unrelated sizes from descriptions
  (leaf size, flower size, spacing, fruit size, and available planting space).
- Record source URL, retrieval time, parser version, raw labels, extracted values,
  warnings, and database before/after rows. Unknown labels preserve existing data.

## Sun

Target `sun` must become `sun_type[]`, keeping existing scalar values as singleton
arrays. Search must use overlap/membership, and all writers/readers must support
arrays before activating this importer.

| NC State Light label | Stored value |
|---|---|
| Full sun (6 or more hours of direct sunlight a day) | full sun |
| Partial Shade (Direct sunlight only part of the day, 2–6 hours) | part shade |
| Deep shade (Less than 2 hours to no direct sunlight) | full shade |
| Dappled Sunlight (Shade through upper canopy all day) | part shade, with an approximation warning |

Dappled sunlight describes filtered light, not a measured duration. Its mapping
is an application simplification, not a claim that the source categories are
identical. Preserve the raw label. Do not infer full shade from dappled sunlight.
Union all explicitly listed mapped options with existing sun values; deduplicate.
Separate options do not imply intervening options. Do not infer full shade from
the combination of full sun and partial shade. Missing/unrecognized light is unknown.

## Water and drainage

Use existing `water_level_enum[]`: low, medium, high. This represents suitable
soil-moisture conditions, not a watering schedule or daily water requirement.

| Source moisture label | Stored water value |
|---|---|
| Dry / Occasionally Dry | low |
| Moist | medium |
| Wet / Occasionally Wet | high |
| Frequent Standing Water | high, plus retain the distinct aquatic/standing-water condition |
| Good Drainage / Well-drained | no water inference; retain as drainage information |

These are coarse application mappings. Occasional wet/dry conditions indicate
tolerance and must not be presented as a preferred constant condition. Flood
tolerance or drought resistance in narrative text alone does not set water values.
For explicit moisture ranges, include intervening levels: dry to wet becomes
low/medium/high. For separate listed labels, union only listed values: occasionally
dry plus occasionally wet becomes low/high, without inventing medium.
Merge by union with existing water values; normalize legacy med to medium.
Preserve raw drainage labels separately from water. Missing/unknown never clears data.

## Dimensions

- Read height and width ranges from Dimensions. Convert feet/inches to SMALLINT
  inches: feet * 12 + inches. Existing database measurements are already inches;
  never multiply them again.
- Round source minima down and maxima up. Merge minima with least(existing, source)
  and maxima with greatest(existing, source), ignoring unknown values.
- An unqualified single mature size can supply both range bounds. A qualified
  value such as "up to 4 feet" supplies only the maximum; "at least 4 feet" only
  the minimum. Do not invent a missing bound.
- Negative values, reversed ranges, unsupported units, or values exceeding 32767
  inches are logged and skipped for that field. No silent clipping.

## Zones

Parse USDA zones 1–13 with optional a/b suffixes. Strip the suffix for the existing
SMALLINT columns: 3a–8b becomes min_zone=3, max_zone=8. Preserve original subzones
as raw data; the integer projection intentionally loses subzone precision.
For a list use its lowest/highest numeric zone; merge with existing minimum/maximum
using least/greatest. Ignore heat zones, regional labels, and non-USDA hardiness
scales. Missing/unknown zones preserve current values.

## Bloom time

- Explicit months/ranges become inclusive month_enum arrays, including wrapped
  ranges such as November–February. Merge explicit source months with existing
  months by union, deduplicate, and order January–December.
- Store Spring/Summer/Fall/Winter as separate season metadata, not guessed months.
  NC State defines Flower Bloom Time as a season. Early/late season or frost-based
  wording likewise does not generate exact month values.
- Explicit months may be read from the flower description only when unambiguously
  describing this species' bloom, rather than fruiting, cultivation, or insect activity.
- Missing bloom data never clears existing months. Explicit non-flowering status
  conflicts with existing known months: retain existing data and flag for review.

## Other traits and update behavior

- Map explicit life-cycle and foliage labels only. Multiple life cycles cannot
  fit the current scalar column: preserve the existing value and log the alternatives.
- Species origin is not county presence. Do not update county links from NC regions,
  distribution prose, recommended planting areas, or hardiness zones.
- Missing flags remain unknown. Deer resistance does not mean no animal feeds on
  the plant; wildlife attraction does not prove a specific pollination relationship.
- Use the existing audit/backup/live-batch recovery approach with separate NC State
  source/parser checkpoints. Do not reuse MOBOT completion markers for this source.
- Cache responses, honor robots rules and any larger crawl delay, and stop on
  blocks/rate limits. Verify access and reuse terms before starting a bulk importer.

## Source examples

- [NC State help and glossary](https://plants.ces.ncsu.edu/help/)
- [Purple coneflower: dimensions, sun, moisture, zones, seasonal bloom and explicit bloom description](https://plants.ces.ncsu.edu/plants/echinacea-purpurea/)
- [Umbrella leaf: dappled sunlight, deep shade, and occasionally wet](https://plants.ces.ncsu.edu/plants/podophyllum-cymosum/common-name/pixie-parasol/)
- [Water hyacinth: frequent standing water](https://plants.ces.ncsu.edu/plants/pontederia-crassipes/common-name/water-hyacinth/)
