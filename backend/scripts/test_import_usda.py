#!/usr/bin/env python3
"""
test_import_usda.py

Unit tests for USDA PLANTS import script:
  - Botanical identity parsing
  - Hybrid species formatting ('x hybridname')
  - Variety & subspecies exclusion
  - CSV parsing with/without preamble
  - FIPS calculation (2-digit state + 3-digit county)
  - Missing field handling (marked as NULL/None)
  - Enum and boolean attribute mapping
  - SQL generation syntax and joins
"""

import tempfile
import unittest
from pathlib import Path
from import_usda import (
    PlantRecord,
    escape_sql_str,
    generate_sql,
    is_plant_taxon,
    map_plant_attributes,
    parse_botanical_identity,
    parse_csv_file,
    process_csv_files,
    PlantMetadataResolver,
)


class TestBotanicalIdentityParsing(unittest.TestCase):
    def test_standard_species(self):
        res = parse_botanical_identity("<i>Solidago canadensis</i> L.", "Species")
        self.assertEqual(res, ("Solidago", "canadensis"))

        res = parse_botanical_identity("Quercus alba L.", "Species")
        self.assertEqual(res, ("Quercus", "alba"))

        res = parse_botanical_identity("Abies amabilis Douglas ex Forbes")
        self.assertEqual(res, ("Abies", "amabilis"))

    def test_hybrids_formatting(self):
        # With unicode multiplication symbol ×
        res = parse_botanical_identity("Populus ×canadensis Moench", "Species")
        self.assertEqual(res, ("Populus", "x canadensis"))

        # With standard 'x'
        res = parse_botanical_identity("Populus x canadensis")
        self.assertEqual(res, ("Populus", "x canadensis"))

        # Another hybrid
        res = parse_botanical_identity("<i>Quercus ×bebbiana</i> C.K. Schneid.")
        self.assertEqual(res, ("Quercus", "x bebbiana"))

        # Rank explicitly set to hybrid
        res = parse_botanical_identity("Baptisia bicolor Greenm. & Larisey", "Hybrid")
        self.assertEqual(res, ("Baptisia", "x bicolor"))

    def test_varieties_excluded(self):
        # Variety in rank
        res = parse_botanical_identity("Solidago canadensis L.", "Variety")
        self.assertIsNone(res)

        # Variety in name
        res = parse_botanical_identity("Solidago canadensis L. var. canadensis")
        self.assertIsNone(res)

        res = parse_botanical_identity("<i>Fragaria virginiana</i> Duchesne var. <i>glauca</i> S. Watson")
        self.assertIsNone(res)

        res = parse_botanical_identity("Viburnum dentatum L. var. lucidum Aiton", "Variety")
        self.assertIsNone(res)

    def test_subspecies_excluded(self):
        # Subspecies in rank
        res = parse_botanical_identity("Vicia villosa Roth", "Subspecies")
        self.assertIsNone(res)

        # Subspecies in name
        res = parse_botanical_identity("Vicia villosa Roth ssp. varia (Host) Corb.")
        self.assertIsNone(res)

        res = parse_botanical_identity("<i>Vicia villosa</i> Roth subsp. <i>varia</i> (Host) Corb.")
        self.assertIsNone(res)

    def test_forms_and_higher_taxa_excluded(self):
        res = parse_botanical_identity("Quercus alba L. f. alba", "Form")
        self.assertIsNone(res)

        res = parse_botanical_identity("Asteraceae", "Family")
        self.assertIsNone(res)

        res = parse_botanical_identity("Solidago L.", "Genus")
        self.assertIsNone(res)


class TestPlantTaxonFilter(unittest.TestCase):
    def test_plant_accepted(self):
        profile = {
            "Group": "Dicot",
            "Ancestors": [{"Rank": "Kingdom", "CommonName": "Plants", "ScientificName": "<i>Plantae</i>"}]
        }
        self.assertTrue(is_plant_taxon(profile))

        # Bryophyte / Moss
        moss = {
            "Group": "Moss",
            "Ancestors": [{"Rank": "Kingdom", "CommonName": "Plants", "ScientificName": "<i>Plantae</i>"}]
        }
        self.assertTrue(is_plant_taxon(moss))

    def test_non_plant_filtered(self):
        # Lichen (Fungi)
        lichen = {
            "Group": "Lichen",
            "Ancestors": [{"Rank": "Kingdom", "CommonName": "Fungi", "ScientificName": "<i>Fungi</i>"}]
        }
        self.assertFalse(is_plant_taxon(lichen))

        # Direct attribute filter
        self.assertFalse(is_plant_taxon(None, {"group": "lichen"}))
        self.assertFalse(is_plant_taxon(None, {"kingdom": "fungi"}))

    def test_lichen_map_plant_attributes_returns_none(self):
        lichen_profile = {
            "ScientificName": "<i>Arthopyrenia exasperata</i> R.C. Harris",
            "Group": "Lichen",
            "Rank": "Species",
            "Ancestors": [{"Rank": "Kingdom", "CommonName": "Fungi", "ScientificName": "<i>Fungi</i>"}]
        }
        rec = map_plant_attributes("AREX5", lichen_profile, None, {})
        self.assertIsNone(rec, "Lichens must not be imported as plants")


class TestCsvParsing(unittest.TestCase):
    def test_csv_with_preamble(self):
        csv_data = """Distribution Data
Symbol,Country,State,State FIP,County,County FIP
SOCA6,Canada,Alberta,01,,
SOCA6,United States,Alaska,02,,
SOCA6,United States,Alaska,02,Aleutians East,013
SOCA6,United States,Alaska,02,Anchorage,020
SOCA6,United States,Wyoming,56,Albany,001
"""
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            tmp_path = Path(f.name)

        try:
            symbol, counties, direct_attrs = parse_csv_file(tmp_path)
            self.assertEqual(symbol, "SOCA6")
            # Canada and state-level rows (empty county) are excluded
            # FIPS: 02 + 013 = 2013; 02 + 020 = 2020; 56 + 001 = 56001
            expected = [(2013, "Aleutians East"), (2020, "Anchorage"), (56001, "Albany")]
            self.assertEqual(counties, expected)
        finally:
            tmp_path.unlink()

    def test_csv_without_preamble_and_direct_attrs(self):
        csv_data = """Symbol,Country,State,State FIP,County,County FIP,Scientific Name,Common Name,Life Cycle
POXCA,United States,Virginia,51,Fairfax,059,Populus x canadensis,Carolina poplar,perennial
"""
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            tmp_path = Path(f.name)

        try:
            symbol, counties, direct_attrs = parse_csv_file(tmp_path)
            self.assertEqual(symbol, "POXCA")
            self.assertEqual(counties, [(51059, "Fairfax")])
            self.assertEqual(direct_attrs["scientificname"], "Populus x canadensis")
            self.assertEqual(direct_attrs["commonname"], "Carolina poplar")
            self.assertEqual(direct_attrs["lifecycle"], "perennial")
        finally:
            tmp_path.unlink()


class TestAttributeMapping(unittest.TestCase):
    def test_missing_info_marked_as_null(self):
        # A plant with minimal info
        direct_attrs = {
            "scientificname": "Asclepias tuberosa L.",
            "commonname": "Butterfly milkweed",
        }
        record = map_plant_attributes("ASTU", None, None, direct_attrs)
        self.assertIsNotNone(record)
        self.assertEqual(record.genus, "Asclepias")
        self.assertEqual(record.species, "tuberosa")
        self.assertIn("Butterfly milkweed", record.common_names)
        # All missing fields must be None (SQL NULL)
        self.assertIsNone(record.life_cycle)
        self.assertIsNone(record.foliage)
        self.assertIsNone(record.minheight)
        self.assertIsNone(record.maxheight)
        self.assertIsNone(record.minwidth)
        self.assertIsNone(record.maxwidth)
        self.assertIsNone(record.is_woody)
        self.assertIsNone(record.is_tree)
        self.assertIsNone(record.edible)
        self.assertIsNone(record.thorns)
        self.assertIsNone(record.sun)
        self.assertIsNone(record.water)

    def test_characteristics_mapping(self):
        profile = {
            "ScientificName": "<i>Solidago canadensis</i> L.",
            "CommonName": "Canada goldenrod",
            "Rank": "Species",
            "Durations": ["Perennial"],
            "GrowthHabits": ["Forb/herb"],
            "ProfileImageFilename": "soca6.jpg",
        }
        chars = [
            {"PlantCharacteristicName": "Leaf Retention", "PlantCharacteristicValue": "No"},
            {"PlantCharacteristicName": "Height, Mature (feet)", "PlantCharacteristicValue": "3.5"},
            {"PlantCharacteristicName": "Shade Tolerance", "PlantCharacteristicValue": "High"},
            {"PlantCharacteristicName": "Moisture Use", "PlantCharacteristicValue": "Medium"},
            {"PlantCharacteristicName": "Palatable Human", "PlantCharacteristicValue": "No"},
        ]
        record = map_plant_attributes("SOCA6", profile, chars, {})
        self.assertIsNotNone(record)
        self.assertEqual(record.genus, "Solidago")
        self.assertEqual(record.species, "canadensis")
        self.assertEqual(record.life_cycle, "perennial")
        self.assertEqual(record.foliage, "deciduous")
        self.assertEqual(record.sun, "full shade")
        self.assertEqual(record.water, "med")
        self.assertEqual(record.is_tree, False)
        self.assertEqual(record.is_woody, False)
        self.assertEqual(record.edible, False)
        self.assertEqual(record.maxheight, 4)  # 3.5 rounded
        self.assertIn("https://plants.sc.egov.usda.gov/ImageLibrary/standard/soca6.jpg", record.image_link)


class TestSqlGeneration(unittest.TestCase):
    def test_generate_sql_syntax(self):
        plant1 = PlantRecord(
            genus="Solidago",
            species="canadensis",
            life_cycle="perennial",
            foliage="deciduous",
            sun="part shade",
            water="med",
            common_names={"Canada goldenrod", "Canadian goldenrod"},
            counties={(56001, "Albany"), (2013, "Aleutians East")},
        )
        plant2 = PlantRecord(
            genus="Populus",
            species="x canadensis",
            life_cycle="perennial",
            is_tree=True,
            is_woody=True,
            common_names={"Carolina poplar"},
            counties={(51059, "Fairfax")},
        )

        sql = generate_sql([plant1, plant2])
        self.assertTrue(sql.startswith("-- USDA PLANTS Database Import\nBEGIN;"))
        self.assertTrue(sql.strip().endswith("COMMIT;"))

        # Check county info inserts
        self.assertIn("INSERT INTO public.county_info (fips, name) VALUES", sql)
        self.assertIn("(56001, 'Albany')", sql)
        self.assertIn("(2013, 'Aleutians East')", sql)
        self.assertIn("(51059, 'Fairfax')", sql)

        # Check common names
        self.assertIn("INSERT INTO public.common_names (name) VALUES", sql)
        self.assertIn("('Canada goldenrod')", sql)
        self.assertIn("('Carolina poplar')", sql)

        # Check plants insert
        self.assertIn("INSERT INTO public.plants", sql)
        self.assertIn("'Solidago', 'canadensis'", sql)
        self.assertIn("'Populus', 'x canadensis'", sql)

        # Check joins
        self.assertIn("INSERT INTO public.common_plant (name, plant_id)", sql)
        self.assertIn("INSERT INTO public.plant_county (fips, plant_id)", sql)


if __name__ == "__main__":
    unittest.main()
