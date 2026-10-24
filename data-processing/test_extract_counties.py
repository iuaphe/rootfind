"""Geometry edge cases and end-to-end checks against the downloaded source."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import shapefile
from shapely import get_coordinates
from shapely.geometry import MultiPolygon, Polygon, shape

from extract_counties import ROOT, extract_counties, geographic_center, write_outputs


class CenterTests(unittest.TestCase):
    def test_symmetric_polygon(self):
        lat, lon = geographic_center(Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1)]))
        self.assertAlmostEqual(lat, 0, places=3)
        self.assertAlmostEqual(lon, 0, places=3)

    def test_islands_across_date_line(self):
        geometry = MultiPolygon([
            Polygon([(178, 50), (179, 50), (179, 51), (178, 51)]),
            Polygon([(-179, 50), (-178, 50), (-178, 51), (-179, 51)]),
        ])
        lat, lon = geographic_center(geometry)
        self.assertGreater(abs(lon), 179)
        self.assertTrue(50 < lat < 51)

    def test_hole_changes_center(self):
        outer = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        hole = [(0.1, -1), (1.9, -1), (1.9, 1), (0.1, 1)]
        _, lon = geographic_center(Polygon(outer, [hole]))
        self.assertLess(lon, -0.2)

    def test_empty_geometry_is_rejected(self):
        with self.assertRaises(ValueError):
            geographic_center(Polygon())


class DownloadedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ROOT / "cb_2018_us_county_5m" / "cb_2018_us_county_5m.shp"
        cls.features = extract_counties(cls.source)

    def test_every_source_record_and_boundary_vertex_is_preserved(self):
        indexed = {feature["id"]: feature for feature in self.features}
        with shapefile.Reader(str(self.source), encoding="utf-8") as reader:
            self.assertEqual(len(indexed), len(reader))
            self.assertEqual(len(indexed), 3233)
            for entry in reader.iterShapeRecords():
                record = entry.record.as_dict()
                feature = indexed[record["GEOID"]]
                properties = feature["properties"]
                self.assertEqual(properties["name"], record["NAME"])
                self.assertEqual(properties["state_fips"], record["STATEFP"])
                boundary = shape(feature["geometry"])
                original = shape(entry.shape.__geo_interface__)
                self.assertEqual(boundary.geom_type, original.geom_type)
                self.assertEqual(len(get_coordinates(boundary)), len(entry.shape.points))
                old_parts = original.geoms if original.geom_type == "MultiPolygon" else [original]
                new_parts = boundary.geoms if boundary.geom_type == "MultiPolygon" else [boundary]
                self.assertEqual(len(old_parts), len(new_parts))
                self.assertEqual(
                    [len(part.interiors) for part in old_parts],
                    [len(part.interiors) for part in new_parts],
                )

    def test_coordinate_ranges_and_geometry_validity(self):
        for feature in self.features:
            properties = feature["properties"]
            self.assertTrue(-90 <= properties["center_lat"] <= 90)
            self.assertTrue(-180 <= properties["center_lon"] <= 180)
            boundary = shape(feature["geometry"])
            self.assertTrue(boundary.is_valid, feature["id"])
            coordinates = get_coordinates(boundary)
            self.assertTrue((abs(coordinates[:, 0]) <= 180).all())
            self.assertTrue((abs(coordinates[:, 1]) <= 90).all())

    def test_states_and_real_date_line_county(self):
        indexed = {feature["id"]: feature["properties"] for feature in self.features}
        self.assertEqual(len({p["state_fips"] for p in indexed.values()}), 56)
        self.assertEqual(indexed["01001"]["state_name"], "Alabama")
        self.assertEqual(indexed["06037"]["state_abbr"], "CA")
        self.assertEqual(indexed["72001"]["state_name"], "Puerto Rico")
        self.assertEqual(indexed["02016"]["name"], "Aleutians West")
        self.assertTrue(50 < indexed["02016"]["center_lat"] < 55)
        self.assertGreater(abs(indexed["02016"]["center_lon"]), 170)

    def test_geojson_and_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(self.features, output)
            with (output / "counties.geojson").open(encoding="utf-8") as handle:
                collection = json.load(handle)
            with (output / "counties.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(collection["type"], "FeatureCollection")
            self.assertEqual(len(rows), len(self.features))
            self.assertEqual(rows[0]["geoid"], "01001")
            for feature, row in zip(collection["features"], rows, strict=True):
                for key, value in feature["properties"].items():
                    self.assertEqual(str(value), row[key])


if __name__ == "__main__":
    unittest.main()
