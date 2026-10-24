#!/usr/bin/env python3
"""Extract county boundaries and calculate geographic centers. See NOTES.md."""

import argparse
import csv
import json
import math
from pathlib import Path

import shapefile
from pyproj import CRS, Proj, Transformer
from shapely import transform
from shapely.geometry import mapping, shape
from shapely.geometry.polygon import orient


ROOT = Path(__file__).resolve().parent

# Census state/territory FIPS codes, kept as strings to preserve leading zeros.
STATES = {
    "01": ("AL", "Alabama"),
    "02": ("AK", "Alaska"),
    "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"),
    "06": ("CA", "California"),
    "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"),
    "10": ("DE", "Delaware"),
    "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"),
    "13": ("GA", "Georgia"),
    "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"),
    "17": ("IL", "Illinois"),
    "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"),
    "20": ("KS", "Kansas"),
    "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"),
    "23": ("ME", "Maine"),
    "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"),
    "26": ("MI", "Michigan"),
    "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"),
    "29": ("MO", "Missouri"),
    "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"),
    "32": ("NV", "Nevada"),
    "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"),
    "35": ("NM", "New Mexico"),
    "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"),
    "38": ("ND", "North Dakota"),
    "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"),
    "41": ("OR", "Oregon"),
    "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"),
    "45": ("SC", "South Carolina"),
    "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"),
    "48": ("TX", "Texas"),
    "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"),
    "51": ("VA", "Virginia"),
    "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"),
    "55": ("WI", "Wisconsin"),
    "56": ("WY", "Wyoming"),
    "60": ("AS", "American Samoa"),
    "66": ("GU", "Guam"),
    "69": ("MP", "Northern Mariana Islands"),
    "72": ("PR", "Puerto Rico"),
    "74": ("UM", "U.S. Minor Outlying Islands"),
    "78": ("VI", "U.S. Virgin Islands"),
}

FIELDS = [
    "geoid", "name", "state_fips", "state_abbr", "state_name",
    "center_lat", "center_lon",
]


def geographic_center(geometry):
    """Return (latitude, longitude) of a local equal-area centroid in WGS84.

    Project before calculating the centroid so that latitude and the date line
    do not distort the area weights. A point on the largest polygon provides a
    nearby projection origin even for island counties that cross the date line.
    """
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Expected a nonempty Polygon or MultiPolygon")
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
    origin = max(polygons, key=lambda polygon: polygon.area).representative_point()
    projection = Proj(
        proj="laea", lat_0=origin.y, lon_0=origin.x, datum="WGS84", units="m"
    )
    center = transform(geometry, projection, interleaved=False).centroid
    lon, lat = projection(center.x, center.y, inverse=True, errcheck=True)
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("Center calculation produced non-finite coordinates")
    return round(lat, 7), round(lon, 7)


def orient_boundary(geometry):
    """Use GeoJSON's counterclockwise exteriors and clockwise holes."""
    if geometry.geom_type == "Polygon":
        return orient(geometry, sign=1.0)
    return type(geometry)([orient(polygon, sign=1.0) for polygon in geometry.geoms])


def extract_counties(source):
    """Read one feature per county; retain the supplied boundary vertices."""
    source = Path(source)
    for suffix in (".shp", ".shx", ".dbf", ".prj"):
        if not source.with_suffix(suffix).is_file():
            raise FileNotFoundError(f"Missing shapefile component: {source.with_suffix(suffix)}")
    source_crs = CRS.from_wkt(source.with_suffix(".prj").read_text())
    to_wgs84 = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    encoding_file = source.with_suffix(".cpg")
    encoding = encoding_file.read_text().strip() if encoding_file.exists() else "utf-8"
    features = []
    seen = set()
    with shapefile.Reader(str(source), encoding=encoding) as reader:
        for entry in reader.iterShapeRecords():
            record = entry.record.as_dict()
            geoid = record["GEOID"]
            if geoid in seen:
                raise ValueError(f"Duplicate county GEOID: {geoid}")
            seen.add(geoid)
            state_fips = record["STATEFP"]
            if state_fips not in STATES:
                raise ValueError(f"Unknown state FIPS code {state_fips!r} for {geoid}")
            abbreviation, state_name = STATES[state_fips]
            geometry = transform(
                shape(entry.shape.__geo_interface__), to_wgs84.transform, interleaved=False
            )
            latitude, longitude = geographic_center(geometry)
            features.append({
                "type": "Feature",
                "id": geoid,
                "properties": {
                    "geoid": geoid,
                    "name": record["NAME"],
                    "state_fips": state_fips,
                    "state_abbr": abbreviation,
                    "state_name": state_name,
                    "center_lat": latitude,
                    "center_lon": longitude,
                },
                "geometry": mapping(orient_boundary(geometry)),
            })
    if not features:
        raise ValueError(f"No county records found in {source}")
    return sorted(features, key=lambda feature: feature["id"])


def write_outputs(features, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "counties.geojson").open("w", encoding="utf-8") as output:
        json.dump(
            {"type": "FeatureCollection", "features": features}, output,
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        )
        output.write("\n")
    with (output_dir / "counties.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(feature["properties"] for feature in features)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path,
        default=ROOT / "cb_2018_us_county_5m" / "cb_2018_us_county_5m.shp",
        help="Input county shapefile (keep its companion files beside it)",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    features = extract_counties(args.input)
    write_outputs(features, args.output_dir)
    print(f"Wrote {len(features):,} counties to {args.output_dir / 'counties.geojson'}")
    print(f"Wrote county attributes and centers to {args.output_dir / 'counties.csv'}")


if __name__ == "__main__":
    main()
