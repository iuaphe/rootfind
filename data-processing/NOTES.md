# County data extraction

`extract_counties.py` reads the downloaded Census shapefile and produces:

- `output/counties.geojson`: all requested information, including each county's
  Polygon or MultiPolygon boundary in `geometry`.
- `output/counties.csv`: the same county attributes and centers, without boundary
  coordinates, for spreadsheets and table joins.

Both files contain **3,233 counties and county equivalents**, sorted by GEOID.
The download covers the 50 states, District of Columbia, Puerto Rico, American
Samoa, Guam, Northern Mariana Islands, and U.S. Virgin Islands (56 jurisdictions).
All source records are included; no geographic filtering is applied.

## Run

Use Python 3.11 or newer. From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python extract_counties.py
```

The virtual environment and generated outputs have already been created locally.
Rerunning the last command regenerates both output files. Extraction itself uses
only local files; installing dependencies initially requires network access.

Optional paths:

```sh
.venv/bin/python extract_counties.py \
  --input cb_2018_us_county_5m/cb_2018_us_county_5m.shp \
  --output-dir output
```

Default paths are relative to the script's directory. Explicit relative paths
are relative to the working directory. Keep `.shp`, `.shx`, `.dbf`, `.prj`, and
`.cpg` together. The script reads the extracted directory; it does not need the
original ZIP or download more Census data. Other inputs must have the same
`GEOID`, `NAME`, and `STATEFP` attributes and polygon geometry.

## Output fields

Each GeoJSON feature has the following `properties`; these are also the CSV
columns:

| Field | Meaning | Example |
| --- | --- | --- |
| `geoid` | Five-character county FIPS identifier: state + county | `06037` |
| `name` | Census `NAME`, preserved verbatim | `Los Angeles` |
| `state_fips` | Two-character state/territory FIPS code | `06` |
| `state_abbr` | State/territory postal abbreviation | `CA` |
| `state_name` | Full state/territory name | `California` |
| `center_lat` | Calculated center latitude in decimal degrees | `34.3203402` |
| `center_lon` | Calculated center longitude in decimal degrees | `-118.2247081` |

`geoid` is also the GeoJSON feature `id`. Use it as the join key: county names
are not nationally unique. Names retain the source spelling and accents; the
script does not append "County", "Parish", or other legal descriptions.

FIPS identifiers are strings. When opening the CSV in a spreadsheet, import
`geoid` and `state_fips` as text to retain leading zeros. CSV itself cannot force
spreadsheet column types.

### TypeScript types

`types.ts` exports shared types for consumers of these files:

- `CountyFeatureCollection` describes the complete GeoJSON document.
- `CountyFeature` and `CountyProperties` describe each county and its attributes.
- `CountyGeometry` is a discriminated union of Polygon and MultiPolygon shapes;
  `Position` is a `[longitude, latitude]` tuple.
- `CountyCsvRow` describes CSV rows with every field as a string.
  Convert `center_lat` and `center_lon` to numbers to obtain `CountyProperties`.

These are compile-time types; they do not validate data at runtime.

The boundary lives in each GeoJSON feature's `geometry`, with coordinates in
**[longitude, latitude]** order. Center attributes are explicitly named to avoid
ambiguity. Islands and other disconnected pieces remain in the same county
feature as a MultiPolygon, and holes are preserved. No further simplification
is applied. Ring orientation is normalized to counterclockwise exteriors and
clockwise holes.

For example, load a county and its shape in Python:

```python
import json

with open("output/counties.geojson", encoding="utf-8") as source:
    counties = json.load(source)["features"]

los_angeles = next(county for county in counties if county["id"] == "06037")
print(los_angeles["properties"])
boundary = los_angeles["geometry"]
```

## What "center" means

The source does not include Census internal-point latitude/longitude fields, so
the script computes an **area-weighted geometric centroid** of each supplied
boundary. This is neither a population center nor a county seat.

The source `.prj` specifies NAD83 geographic coordinates. The script uses pyproj
to transform boundary coordinates into WGS84 (EPSG:4326), using longitude first.
See the [pyproj Transformer documentation](https://pyproj4.github.io/pyproj/stable/api/transformer.html).

For each county, the script then:

1. Chooses a point inside its largest polygon as the origin of a local Lambert
   azimuthal equal-area projection on the WGS84 ellipsoid.
2. Projects every boundary part and hole into that local coordinate system.
3. Calculates the projected geometry's centroid and converts it back to WGS84
   latitude and longitude, rounded to seven decimal places.

Projecting before calculating the centroid avoids treating degrees as uniform
distances and keeps counties with islands on both sides of the date line near
those islands. Aleutians West (`02016`), for example, has a calculated center
near latitude 52.9491, longitude -173.6793. Shapely documents the distinction
between a [centroid and an interior representative point](https://shapely.readthedocs.io/en/stable/manual.html#object.representative_point).

A centroid can fall outside a concave county or between islands, including in
water. If the application requires a marker guaranteed inside a county, use an
interior representative point instead. The reported decimal places describe
output precision, not positional accuracy; these centers depend on generalized
boundaries and the chosen projection.

## Source and limitations

Source: `cb_2018_us_county_5m/cb_2018_us_county_5m.shp`, with its accompanying
attribute, projection, encoding, and metadata files. Its bundled
`cb_2018_us_county_5m.shp.iso.xml` describes the boundaries as of **January 1,
2018**, generalized for small-scale thematic mapping. The `5m` denotes
1:5,000,000 scale. These are simplified cartographic shapes, suitable for
overview maps; use detailed TIGER/Line boundaries if that level of detail is
needed. This extraction retains the 2018 geography, including its county
equivalents, rather than updating boundaries or names to later vintages.

County equivalents include Louisiana parishes, Alaska boroughs and census
areas, independent cities, DC, and the corresponding territorial divisions.
For territories, the `state_*` columns identify the territory.

The FIPS-to-state-name/abbreviation lookup is embedded in the script. County
names and identifiers come directly from the downloaded attributes. Neither
original input files nor the ZIP are modified.

## Verification

```sh
.venv/bin/python -W error -m unittest -v
```

Eight tests pass, covering symmetric centers, holes, synthetic islands across
the date line, empty input geometry, and the full downloaded dataset. The
dataset checks confirm that all 3,233 records, names, identifiers, boundary
vertices, polygon parts, and holes survive extraction; all exported boundaries
are valid with coordinates in range; the 56 jurisdictions and real Aleutians
West center are sensible; and CSV/GeoJSON attributes agree after serialization.

Dependencies used for this run: Python 3.14.3, pyshp 3.1.6, pyproj 3.8.0,
Shapely 2.2.0. `requirements.txt` records compatible dependency ranges.
