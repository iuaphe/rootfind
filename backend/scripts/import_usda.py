#!/usr/bin/env python3
"""
import_usda.py

Imports data from USDA PLANTS database into the PostgreSQL/Supabase database schema
defined in backend/supabase/migrations/.

Modes of operation:
  1. Stream Online (Zero Disk Space for CSVs):
     Reads each plant's profile and distribution CSV directly from the USDA API into memory,
     extracts all plant attributes, counties, and joins, generates/executes SQL, and immediately
     discards the CSV. Zero CSV files are stored on disk!
     Usage:
       python3 backend/scripts/import_usda.py --stream --checklist plantlst.txt --sql-out import.sql

  2. Local File Import:
     Imports existing .csv files from disk. If --delete-after is passed, deletes each .csv
     file immediately after processing to conserve disk storage.
     Usage:
       python3 backend/scripts/import_usda.py /path/to/csvs/ --sql-out import.sql --delete-after

Tables populated:
  - public.plants
  - public.county_info
  - public.plant_county
  - public.common_names
  - public.common_plant

Key Rules:
  - Non-plants (Lichens, Fungi) are excluded.
  - Missing information is marked as NULL.
  - Varieties and subspecies are excluded (skipped).
  - Hybrids are included, but their species epithet is formatted as "x hybridname".
  - All plant-county relations are added to the plant_county join table.
  - Idempotent: safe to run multiple times, pause, or resume.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import itertools
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# USDA API & Checklist
USDA_API_BASE = "https://plantsservices.sc.egov.usda.gov/api"
DEFAULT_CHECKLIST_URL = "https://plants.sc.egov.usda.gov/DocumentLibrary/Txt/plantlst.txt"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; RootfindUSDAImport/1.0)"
DEFAULT_PROGRESS_FILE = "import_progress.txt"

# Database enum allowed values
VALID_LIFE_CYCLES = {"perennial", "biennial", "annual"}
VALID_FOLIAGE = {"deciduous", "evergreen"}
VALID_SUN = {"full sun", "part shade", "full shade"}
VALID_WATER = {"high", "med", "low"}


@dataclass
class PlantRecord:
    genus: str
    species: str
    life_cycle: Optional[str] = None
    foliage: Optional[str] = None
    minheight: Optional[int] = None
    maxheight: Optional[int] = None
    minwidth: Optional[int] = None
    maxwidth: Optional[int] = None
    is_woody: Optional[bool] = None
    is_tree: Optional[bool] = None
    edible: Optional[bool] = None
    thorns: Optional[bool] = None
    sun: Optional[str] = None
    water: Optional[str] = None
    image_link: Optional[str] = None
    misc: Optional[List[str]] = None
    common_names: Set[str] = field(default_factory=set)
    counties: Set[Tuple[int, str]] = field(default_factory=set)  # (fips, name)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes plant record, counties, and common names to a dictionary for Supabase."""
        return {
            "plant": {
                "genus": self.genus,
                "species": self.species,
                "life_cycle": self.life_cycle,
                "foliage": self.foliage,
                "minheight": self.minheight,
                "maxheight": self.maxheight,
                "minwidth": self.minwidth,
                "maxwidth": self.maxwidth,
                "is_woody": self.is_woody,
                "is_tree": self.is_tree,
                "edible": self.edible,
                "thorns": self.thorns,
                "sun": self.sun,
                "water": self.water,
                "image_link": self.image_link,
                "misc": self.misc or [],
            },
            "counties": [{"fips": fips, "name": name} for fips, name in self.counties],
            "common_names": sorted(list(self.common_names)),
        }


def strip_html(text: str) -> str:
    """Removes HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def is_variety_or_subspecies(sci_name: str) -> bool:
    """Checks if a scientific name represents a variety, subspecies, or form."""
    if not sci_name:
        return False
    return bool(
        re.search(
            r"(?:\bvar\.?|\bssp\.?|\bsubsp\.?|\bsubvar\.?|\bforma\b|\bf\.)",
            sci_name,
            re.IGNORECASE,
        )
    )


def is_plant_taxon(
    profile: Optional[Dict[str, Any]] = None,
    direct_attrs: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Verifies that the taxon is actually a plant (Kingdom Plantae),
    filtering out non-plants such as lichens or fungi that are also in the USDA database.
    """
    if direct_attrs:
        group_val = (direct_attrs.get("group") or direct_attrs.get("plant_group") or "").strip().lower()
        if group_val in ("lichen", "fungi", "fungus"):
            return False
        kingdom_val = (direct_attrs.get("kingdom") or "").strip().lower()
        if kingdom_val in ("fungi", "fungus", "animalia", "bacteria"):
            return False

    if profile:
        group = (profile.get("Group") or profile.get("GroupName") or "").strip().lower()
        if group in ("lichen", "fungi", "fungus"):
            return False

        ancestors = profile.get("Ancestors") or []
        for anc in ancestors:
            if anc.get("Rank") == "Kingdom":
                sci_k = strip_html(anc.get("ScientificName", "")).lower()
                com_k = (anc.get("CommonName") or "").lower()
                sym_k = (anc.get("Symbol") or "").lower()
                if any(k in ("fungi", "fungus", "animalia", "bacteria") for k in (sci_k, com_k, sym_k)):
                    return False
                if any(k in ("plantae", "plants") for k in (sci_k, com_k, sym_k)):
                    return True

        if group in (
            "dicot",
            "monocot",
            "gymnosperm",
            "fern",
            "horsetail",
            "lycopod",
            "bryophyte",
            "moss",
            "liverwort",
            "hornwort",
            "green alga",
        ):
            return True

    return True


def parse_botanical_identity(
    scientific_name: str,
    rank: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    Parses a scientific name into (genus, species).
    Returns None if the taxon is a variety, subspecies, non-species taxon, or invalid.

    Hybrid rule:
      hybrids are included with species formatted as 'x hybridname' (lowercase).
    Variety/subspecies rule:
      do not include varieties or subspecies.
    """
    if not scientific_name:
        return None

    clean_name = strip_html(scientific_name)

    # 1. Filter out varieties, subspecies, forms, or non-species ranks
    if rank:
        normalized_rank = rank.strip().lower()
        if normalized_rank not in ("species", "hybrid"):
            return None

    # Check scientific name text for infraspecific markers
    if is_variety_or_subspecies(clean_name):
        return None

    # Replace multiplication sign (\u00d7) with standard 'x'
    normalized_name = clean_name.replace("\u00d7", " x ").replace("\u00D7", " x ")
    normalized_name = re.sub(r"\s+", " ", normalized_name).strip()

    is_hybrid = False
    if rank and rank.strip().lower() == "hybrid":
        is_hybrid = True

    # 2. Check for hybrid format:
    # e.g., "Populus x canadensis", "Populus ×canadensis", "Quercus x bebbiana"
    m_hybrid = re.match(r"^([A-Z][a-zA-Z\-]+)\s+[xX]\s*([a-z\-]+)", normalized_name)
    if m_hybrid:
        genus = m_hybrid.group(1).capitalize()
        species_epithet = m_hybrid.group(2).lower()
        return genus, f"x {species_epithet}"

    # Check if scientific name starts with 'x ' (intergeneric hybrid)
    m_intergeneric = re.match(r"^[xX]\s+([A-Z][a-zA-Z\-]+)\s+([a-z\-]+)", normalized_name)
    if m_intergeneric:
        genus = m_intergeneric.group(1).capitalize()
        species_epithet = m_intergeneric.group(2).lower()
        return genus, f"x {species_epithet}"

    # 3. Regular species match:
    # Epithet must be lowercase letters/hyphen (botanical epithet, not author citation)
    m_species = re.match(r"^([A-Z][a-zA-Z\-]+)\s+([a-z\-]+)", normalized_name)
    if m_species:
        genus = m_species.group(1).capitalize()
        species_epithet = m_species.group(2).lower()
        if is_hybrid:
            return genus, f"x {species_epithet}"
        return genus, species_epithet

    return None


class PlantMetadataResolver:
    """
    Resolves plant metadata (scientific name, common name, characteristics)
    via local offline file, disk cache, or USDA PLANTS API.
    """

    def __init__(
        self,
        offline: bool = False,
        cache_dir: Optional[str] = None,
        metadata_file: Optional[str] = None,
    ):
        self.offline = offline
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir and not self.offline:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.offline_metadata: Dict[str, Dict[str, Any]] = {}
        if metadata_file:
            self._load_metadata_file(metadata_file)

    def _load_metadata_file(self, filepath: str) -> None:
        p = Path(filepath)
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.offline_metadata = data
                elif isinstance(data, list):
                    for item in data:
                        sym = item.get("Symbol") or item.get("symbol")
                        if sym:
                            self.offline_metadata[sym.upper()] = item
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load metadata file {filepath}: {e}\n")

    def _fetch_json(self, url: str) -> Optional[Any]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def get_plant_data(self, symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        sym = symbol.strip().upper()
        if not sym:
            return None, None

        # 1. Check pre-loaded offline metadata
        if sym in self.offline_metadata:
            meta = self.offline_metadata[sym]
            chars = meta.get("Characteristics") or meta.get("characteristics") or []
            return meta, chars

        # 2. Check disk cache if enabled
        cached_profile = None
        cached_chars = None
        if self.cache_dir:
            prof_file = self.cache_dir / f"{sym}_profile.json"
            char_file = self.cache_dir / f"{sym}_chars.json"
            if prof_file.exists():
                try:
                    with open(prof_file, "r", encoding="utf-8") as f:
                        cached_profile = json.load(f)
                except Exception:
                    pass
            if char_file.exists():
                try:
                    with open(char_file, "r", encoding="utf-8") as f:
                        cached_chars = json.load(f)
                except Exception:
                    pass

        if cached_profile is not None:
            return cached_profile, cached_chars

        if self.offline:
            return None, None

        # 3. Fetch from USDA API
        prof_url = f"{USDA_API_BASE}/PlantProfile?symbol={urllib.parse.quote(sym)}"
        profile = self._fetch_json(prof_url)
        chars = None

        if profile and isinstance(profile, dict):
            master_id = profile.get("Id")
            has_chars = profile.get("HasCharacteristics", False)
            if master_id and has_chars:
                char_url = f"{USDA_API_BASE}/PlantCharacteristics/{master_id}"
                chars = self._fetch_json(char_url)

            # Save to disk cache if cache_dir configured
            if self.cache_dir:
                try:
                    with open(self.cache_dir / f"{sym}_profile.json", "w", encoding="utf-8") as f:
                        json.dump(profile, f)
                    if chars:
                        with open(self.cache_dir / f"{sym}_chars.json", "w", encoding="utf-8") as f:
                            json.dump(chars, f)
                except Exception:
                    pass

        return profile, chars


def download_distribution_csv_in_memory(symbol: str, master_id: int, timeout: int = 20) -> Optional[str]:
    """
    Downloads the plant distribution CSV directly into memory via HTTP POST.
    Does NOT save to disk.
    """
    post_url = f"{USDA_API_BASE}/PlantProfile/getDownloadDistributionDocumentation"
    payload = json.dumps({
        "Field": "Symbol",
        "Text": symbol,
        "SortBy": "sortSciName",
        "Offset": None,
        "MasterId": master_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        post_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/csv",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8-sig", errors="replace")
            return content
    except Exception as e:
        sys.stderr.write(f"Warning: Failed downloading distribution CSV for {symbol}: {e}\n")
        return None


def parse_csv_content(content: str) -> Tuple[Optional[str], List[Tuple[int, str]], Dict[str, Any]]:
    """
    Parses CSV text directly from memory.
    Returns:
      (symbol, list_of_counties, direct_plant_attrs)
    """
    lines = content.splitlines()
    if not lines:
        return None, [], {}

    # Check for optional "Distribution Data" title line
    start_line_idx = 0
    if lines[0].strip().lower() in ("distribution data", '"distribution data"'):
        start_line_idx = 1

    reader = csv.reader(lines[start_line_idx:])
    try:
        raw_header = next(reader)
    except StopIteration:
        return None, [], {}

    header_map: Dict[str, int] = {}
    for i, col in enumerate(raw_header):
        normalized_col = re.sub(r"[_\s\-]+", "", col.strip().lower())
        header_map[normalized_col] = i

    def get_val(row: List[str], *aliases: str) -> Optional[str]:
        for alias in aliases:
            norm_alias = re.sub(r"[_\s\-]+", "", alias.lower())
            if norm_alias in header_map:
                idx = header_map[norm_alias]
                if idx < len(row):
                    val = row[idx].strip()
                    if val != "":
                        return val
        return None

    symbol: Optional[str] = None
    counties: List[Tuple[int, str]] = []
    direct_attrs: Dict[str, Any] = {}

    for row in reader:
        if not row:
            continue

        row_sym = get_val(row, "symbol", "plantsymbol", "accepted_symbol", "acceptedSymbol")
        if row_sym and not symbol:
            symbol = row_sym.strip().upper()

        for key in [
            "scientificname",
            "scientific_name",
            "commonname",
            "common_name",
            "genus",
            "species",
            "rank",
            "lifecycle",
            "life_cycle",
            "duration",
            "foliage",
            "sun",
            "water",
            "minheight",
            "maxheight",
            "minwidth",
            "maxwidth",
            "iswoody",
            "is_woody",
            "istree",
            "is_tree",
            "edible",
            "thorns",
            "imagelink",
            "image_link",
        ]:
            val = get_val(row, key)
            if val is not None and key not in direct_attrs:
                direct_attrs[key] = val

        county_name = get_val(row, "county", "countyname", "county_name")
        state_fip_str = get_val(row, "statefip", "statefips", "state_fip", "state_fips")
        county_fip_str = get_val(row, "countyfip", "countyfips", "county_fip", "county_fips")
        country = get_val(row, "country")

        if country and country.strip().lower() not in ("united states", "usa", "us"):
            continue

        if county_name and state_fip_str and county_fip_str:
            try:
                state_fip_int = int(state_fip_str.strip())
                county_fip_int = int(county_fip_str.strip())
                full_fips = state_fip_int * 1000 + county_fip_int
                if 0 <= full_fips <= 99999:
                    cleaned_name = county_name.strip()[:100]
                    counties.append((full_fips, cleaned_name))
            except ValueError:
                pass

    return symbol, counties, direct_attrs


def parse_csv_file(
    filepath: Path,
    delete_after: bool = False,
) -> Tuple[Optional[str], List[Tuple[int, str]], Dict[str, Any]]:
    """
    Parses a single USDA CSV file from disk, optionally deleting it immediately when done.
    """
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    except Exception as e:
        sys.stderr.write(f"Error reading {filepath}: {e}\n")
        return None, [], {}

    res = parse_csv_content(content)
    del content

    if delete_after:
        try:
            filepath.unlink()
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to delete {filepath}: {e}\n")

    return res


def map_plant_attributes(
    symbol: str,
    profile: Optional[Dict[str, Any]],
    chars: Optional[List[Dict[str, Any]]],
    direct_attrs: Dict[str, Any],
) -> Optional[PlantRecord]:
    """
    Combines profile, characteristics, and direct CSV attributes into a validated PlantRecord.
    Enforces plant verification, variety/subspecies filtering, and hybrid formatting.
    Marks missing values as None.
    """
    # 0. Check that taxon is a true plant (Kingdom Plantae, not Lichen or Fungus)
    if not is_plant_taxon(profile, direct_attrs):
        return None

    # 1. Botanical Identity (Genus & Species)
    raw_sci_name = direct_attrs.get("scientificname") or direct_attrs.get("scientific_name")
    raw_rank = direct_attrs.get("rank")

    if profile:
        if not raw_sci_name:
            raw_sci_name = profile.get("ScientificName")
        if not raw_rank:
            raw_rank = profile.get("Rank")

    genus_species = None
    if raw_sci_name:
        genus_species = parse_botanical_identity(raw_sci_name, raw_rank)

    # If genus and species were directly provided in CSV columns
    if not genus_species and "genus" in direct_attrs and "species" in direct_attrs:
        g = direct_attrs["genus"].strip().capitalize()
        s = direct_attrs["species"].strip()
        if not is_variety_or_subspecies(s):
            if s.startswith("x ") or s.startswith("×") or (raw_rank and raw_rank.lower() == "hybrid"):
                clean_s = re.sub(r"^[xX×]\s*", "", s).lower()
                genus_species = (g, f"x {clean_s}")
            else:
                genus_species = (g, s.lower())

    if not genus_species:
        return None

    genus, species = genus_species
    genus = genus[:100]
    species = species[:100]

    record = PlantRecord(genus=genus, species=species)

    # 2. Common Names
    common_names: Set[str] = set()
    direct_common = direct_attrs.get("commonname") or direct_attrs.get("common_name")
    if direct_common:
        for name in re.split(r"[,;]", direct_common):
            cleaned = name.strip()
            if cleaned:
                common_names.add(cleaned[:100])

    if profile:
        profile_common = profile.get("CommonName")
        if profile_common:
            cleaned = profile_common.strip()
            if cleaned:
                common_names.add(cleaned[:100])
        for other in profile.get("OtherCommonNames", []) or []:
            if isinstance(other, str) and other.strip():
                common_names.add(other.strip()[:100])

    record.common_names = common_names

    # Convert characteristics to a lookup dict
    char_map: Dict[str, str] = {}
    if chars and isinstance(chars, list):
        for c in chars:
            name = c.get("PlantCharacteristicName")
            val = c.get("PlantCharacteristicValue")
            if name and val:
                char_map[name.strip().lower()] = str(val).strip()

    # 3. Life Cycle
    life_cycle_str = direct_attrs.get("lifecycle") or direct_attrs.get("life_cycle") or direct_attrs.get("duration")
    durations = (profile.get("Durations") or []) if profile else []
    dur_combined = " ".join(durations).lower() if durations else ""

    if life_cycle_str and life_cycle_str.lower() in VALID_LIFE_CYCLES:
        record.life_cycle = life_cycle_str.lower()
    elif "perennial" in dur_combined:
        record.life_cycle = "perennial"
    elif "biennial" in dur_combined:
        record.life_cycle = "biennial"
    elif "annual" in dur_combined:
        record.life_cycle = "annual"
    else:
        lifespan = char_map.get("lifespan", "").lower()
        if lifespan in ("long", "moderate", "short"):
            record.life_cycle = "perennial"
        else:
            record.life_cycle = None

    # 4. Foliage (deciduous, evergreen)
    foliage_str = direct_attrs.get("foliage")
    if foliage_str and foliage_str.lower() in VALID_FOLIAGE:
        record.foliage = foliage_str.lower()
    else:
        leaf_retention = char_map.get("leaf retention", "").lower()
        if leaf_retention == "yes":
            record.foliage = "evergreen"
        elif leaf_retention == "no":
            record.foliage = "deciduous"
        else:
            record.foliage = None

    # 5. Sun (full sun, part shade, full shade)
    sun_str = direct_attrs.get("sun")
    if sun_str and sun_str.lower() in VALID_SUN:
        record.sun = sun_str.lower()
    else:
        shade_tol = char_map.get("shade tolerance", "").lower()
        if shade_tol in ("tolerant", "high"):
            record.sun = "full shade"
        elif shade_tol in ("intermediate", "medium", "moderate"):
            record.sun = "part shade"
        elif shade_tol in ("intolerant", "low"):
            record.sun = "full sun"
        else:
            record.sun = None

    # 6. Water (high, med, low)
    water_str = direct_attrs.get("water")
    if water_str and water_str.lower() in VALID_WATER:
        record.water = water_str.lower()
    else:
        moisture_use = char_map.get("moisture use", "").lower()
        if moisture_use == "high":
            record.water = "high"
        elif moisture_use in ("medium", "med", "moderate"):
            record.water = "med"
        elif moisture_use == "low":
            record.water = "low"
        else:
            record.water = None

    # 7. Measurements: minheight, maxheight, minwidth, maxwidth
    def parse_smallint(val: Any) -> Optional[int]:
        if val is None or val == "":
            return None
        try:
            num = int(round(float(val)))
            if 0 <= num <= 32767:
                return num
        except (ValueError, TypeError):
            pass
        return None

    record.minheight = parse_smallint(direct_attrs.get("minheight"))
    record.maxheight = parse_smallint(direct_attrs.get("maxheight"))
    record.minwidth = parse_smallint(direct_attrs.get("minwidth"))
    record.maxwidth = parse_smallint(direct_attrs.get("maxwidth"))

    if record.maxheight is None:
        mature_height = char_map.get("height, mature (feet)")
        if mature_height:
            record.maxheight = parse_smallint(mature_height)

    # 8. Booleans: is_woody, is_tree, edible, thorns
    growth_habits = [g.lower() for g in (profile.get("GrowthHabits") or [])] if profile else []
    gh_combined = " ".join(growth_habits)

    def parse_bool(val: Any) -> Optional[bool]:
        if val is None or val == "":
            return None
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("true", "t", "yes", "y", "1"):
            return True
        if s in ("false", "f", "no", "n", "0"):
            return False
        return None

    is_tree_direct = parse_bool(direct_attrs.get("istree") or direct_attrs.get("is_tree"))
    if is_tree_direct is not None:
        record.is_tree = is_tree_direct
    elif "tree" in gh_combined:
        record.is_tree = True
    elif growth_habits:
        record.is_tree = False
    else:
        record.is_tree = None

    is_woody_direct = parse_bool(direct_attrs.get("iswoody") or direct_attrs.get("is_woody"))
    if is_woody_direct is not None:
        record.is_woody = is_woody_direct
    elif any(w in gh_combined for w in ("tree", "shrub", "subshrub")):
        record.is_woody = True
    elif any(h in gh_combined for h in ("forb/herb", "graminoid")):
        record.is_woody = False
    else:
        record.is_woody = None

    edible_direct = parse_bool(direct_attrs.get("edible"))
    if edible_direct is not None:
        record.edible = edible_direct
    else:
        palatable_human = char_map.get("palatable human", "").lower()
        if palatable_human in ("high", "medium", "low", "yes"):
            record.edible = True
        elif palatable_human == "no":
            record.edible = False
        else:
            record.edible = None

    thorns_direct = parse_bool(direct_attrs.get("thorns"))
    if thorns_direct is not None:
        record.thorns = thorns_direct
    else:
        record.thorns = None

    # 9. Image Link
    img_direct = direct_attrs.get("imagelink") or direct_attrs.get("image_link")
    if img_direct:
        record.image_link = img_direct.strip()
    elif profile and profile.get("ProfileImageFilename"):
        fn = profile["ProfileImageFilename"].strip()
        record.image_link = f"https://plants.sc.egov.usda.gov/ImageLibrary/standard/{fn}"
    else:
        record.image_link = None

    # 10. Misc tags
    misc_tags: List[str] = []
    if symbol:
        misc_tags.append(f"usda_symbol:{symbol}")
    record.misc = misc_tags if misc_tags else None

    return record


def escape_sql_str(val: str) -> str:
    """Escapes string literal for PostgreSQL."""
    return "'" + val.replace("'", "''") + "'"


def append_plant_sql(out: io.TextIOBase, p: PlantRecord) -> None:
    """Appends SQL statements for a single plant and its relations to an open stream."""
    genus_sql = escape_sql_str(p.genus)
    species_sql = escape_sql_str(p.species)
    life_cycle_sql = escape_sql_str(p.life_cycle) if p.life_cycle else "NULL"
    foliage_sql = escape_sql_str(p.foliage) if p.foliage else "NULL"
    minheight_sql = str(p.minheight) if p.minheight is not None else "NULL"
    maxheight_sql = str(p.maxheight) if p.maxheight is not None else "NULL"
    minwidth_sql = str(p.minwidth) if p.minwidth is not None else "NULL"
    maxwidth_sql = str(p.maxwidth) if p.maxwidth is not None else "NULL"
    is_woody_sql = "true" if p.is_woody is True else ("false" if p.is_woody is False else "NULL")
    is_tree_sql = "true" if p.is_tree is True else ("false" if p.is_tree is False else "NULL")
    edible_sql = "true" if p.edible is True else ("false" if p.edible is False else "NULL")
    thorns_sql = "true" if p.thorns is True else ("false" if p.thorns is False else "NULL")
    sun_sql = escape_sql_str(p.sun) if p.sun else "NULL"
    water_sql = escape_sql_str(p.water) if p.water else "NULL"
    image_link_sql = escape_sql_str(p.image_link) if p.image_link else "NULL"

    if p.misc:
        elements = ", ".join(escape_sql_str(m) for m in p.misc)
        misc_sql = f"ARRAY[{elements}]"
    else:
        misc_sql = "NULL"

    # 1. Counties
    if p.counties:
        county_values = [
            f"({fips}, {escape_sql_str(name)})"
            for fips, name in sorted(p.counties)
        ]
        out.write("INSERT INTO public.county_info (fips, name) VALUES\n  ")
        out.write(",\n  ".join(county_values))
        out.write("\nON CONFLICT (fips) DO UPDATE SET name = EXCLUDED.name;\n")

    # 2. Common Names
    if p.common_names:
        name_values = [f"({escape_sql_str(name)})" for name in sorted(p.common_names)]
        out.write("INSERT INTO public.common_names (name) VALUES\n  ")
        out.write(",\n  ".join(name_values))
        out.write("\nON CONFLICT (name) DO NOTHING;\n")

    # 3. Plant
    out.write(
        f"INSERT INTO public.plants (\n"
        f"  genus, species, life_cycle, foliage, minheight, maxheight, minwidth, maxwidth,\n"
        f"  is_woody, is_tree, edible, thorns, sun, water, image_link, misc\n"
        f") VALUES (\n"
        f"  {genus_sql}, {species_sql}, {life_cycle_sql}, {foliage_sql},\n"
        f"  {minheight_sql}, {maxheight_sql}, {minwidth_sql}, {maxwidth_sql},\n"
        f"  {is_woody_sql}, {is_tree_sql}, {edible_sql}, {thorns_sql},\n"
        f"  {sun_sql}, {water_sql}, {image_link_sql}, {misc_sql}\n"
        f")\n"
        f"ON CONFLICT (genus, species) DO UPDATE SET\n"
        f"  life_cycle = COALESCE(EXCLUDED.life_cycle, public.plants.life_cycle),\n"
        f"  foliage = COALESCE(EXCLUDED.foliage, public.plants.foliage),\n"
        f"  minheight = COALESCE(EXCLUDED.minheight, public.plants.minheight),\n"
        f"  maxheight = COALESCE(EXCLUDED.maxheight, public.plants.maxheight),\n"
        f"  minwidth = COALESCE(EXCLUDED.minwidth, public.plants.minwidth),\n"
        f"  maxwidth = COALESCE(EXCLUDED.maxwidth, public.plants.maxwidth),\n"
        f"  is_woody = COALESCE(EXCLUDED.is_woody, public.plants.is_woody),\n"
        f"  is_tree = COALESCE(EXCLUDED.is_tree, public.plants.is_tree),\n"
        f"  edible = COALESCE(EXCLUDED.edible, public.plants.edible),\n"
        f"  thorns = COALESCE(EXCLUDED.thorns, public.plants.thorns),\n"
        f"  sun = COALESCE(EXCLUDED.sun, public.plants.sun),\n"
        f"  water = COALESCE(EXCLUDED.water, public.plants.water),\n"
        f"  image_link = COALESCE(EXCLUDED.image_link, public.plants.image_link),\n"
        f"  misc = COALESCE(EXCLUDED.misc, public.plants.misc);\n"
    )

    # 4. Common Plant Joins
    if p.common_names:
        cname_vals = ", ".join(f"({escape_sql_str(cn)})" for cn in sorted(p.common_names))
        out.write(
            f"INSERT INTO public.common_plant (name, plant_id)\n"
            f"SELECT v.name, p.plant_id\n"
            f"FROM public.plants p\n"
            f"CROSS JOIN (VALUES {cname_vals}) AS v(name)\n"
            f"WHERE p.genus = {genus_sql} AND p.species = {species_sql}\n"
            f"ON CONFLICT (name, plant_id) DO NOTHING;\n"
        )

    # 5. Plant County Joins
    if p.counties:
        county_fips_list = sorted({fips for fips, _ in p.counties})
        for j in range(0, len(county_fips_list), 500):
            fips_chunk = county_fips_list[j : j + 500]
            fips_vals = ", ".join(f"({f})" for f in fips_chunk)
            out.write(
                f"INSERT INTO public.plant_county (fips, plant_id)\n"
                f"SELECT v.fips, p.plant_id\n"
                f"FROM public.plants p\n"
                f"CROSS JOIN (VALUES {fips_vals}) AS v(fips)\n"
                f"WHERE p.genus = {genus_sql} AND p.species = {species_sql}\n"
                f"ON CONFLICT (fips, plant_id) DO NOTHING;\n"
            )

    out.write("\n")


def generate_sql(records: List[PlantRecord]) -> str:
    """Generates complete SQL script for a batch of PlantRecords."""
    out = io.StringIO()
    out.write("-- USDA PLANTS Database Import\nBEGIN;\n\n")
    for r in records:
        append_plant_sql(out, r)
    out.write("COMMIT;\n")
    return out.getvalue()


def execute_sql_against_db(sql_content: str, db_url: str) -> bool:
    """Executes SQL against database using psycopg, psycopg2, or psql."""
    try:
        import psycopg  # type: ignore

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_content)
            conn.commit()
        return True
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"psycopg error: {e}\n")
        return False

    try:
        import psycopg2  # type: ignore

        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_content)
            conn.commit()
        return True
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"psycopg2 error: {e}\n")
        return False

    try:
        proc = subprocess.run(
            ["psql", db_url],
            input=sql_content,
            text=True,
            capture_output=True,
            check=True,
        )
        return True
    except FileNotFoundError:
        sys.stderr.write("Error: psql CLI not found and neither psycopg nor psycopg2 is installed.\n")
        return False
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"psql error: {e.stderr}\n")
        return False


def upsert_plant_to_supabase(
    record: PlantRecord,
    supabase_url: str,
    service_key: str,
    use_edge_function: bool = True,
    timeout: int = 15,
) -> bool:
    """Upserts a single plant record in real time to Supabase via Edge Function or PostgREST RPC."""
    url = (
        f"{supabase_url.rstrip('/')}/functions/v1/upsert-plant"
        if use_edge_function
        else f"{supabase_url.rstrip('/')}/rest/v1/rpc/upsert_plant"
    )
    payload = record.to_dict() if use_edge_function else {"p_payload": record.to_dict()}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"Supabase upsert HTTP {e.code} for {record.genus} {record.species}: {err_msg}\n")
        return False
    except Exception as e:
        sys.stderr.write(f"Failed to upsert {record.genus} {record.species} to Supabase: {e}\n")
        return False


def upsert_plants_batch_to_supabase(
    records: List[PlantRecord],
    supabase_url: str,
    service_key: str,
    use_edge_function: bool = True,
    timeout: int = 30,
) -> bool:
    """Upserts a batch of plant records in real time to Supabase."""
    if not records:
        return True
    url = (
        f"{supabase_url.rstrip('/')}/functions/v1/upsert-plant"
        if use_edge_function
        else f"{supabase_url.rstrip('/')}/rest/v1/rpc/upsert_plants_batch"
    )
    payload = [r.to_dict() for r in records] if use_edge_function else {"p_payload": [r.to_dict() for r in records]}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"Supabase batch upsert HTTP {e.code}: {err_msg}\n")
        return False
    except Exception as e:
        sys.stderr.write(f"Failed to upsert batch to Supabase: {e}\n")
        return False


def fetch_accepted_checklist(
    checklist_path_or_url: str,
    limit: Optional[int] = None,
    verbose: bool = True,
) -> List[Tuple[str, str, str]]:
    """Loads accepted species & hybrids from USDA plantlst.txt."""
    if os.path.exists(checklist_path_or_url):
        if verbose:
            print(f"Reading checklist from local file: {checklist_path_or_url}")
        with open(checklist_path_or_url, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    else:
        if verbose:
            print(f"Downloading checklist from {checklist_path_or_url}...")
        req = urllib.request.Request(checklist_path_or_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8-sig", errors="replace")

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        return []

    col_map = {col.strip().lower(): idx for idx, col in enumerate(header)}
    sym_idx = col_map.get("symbol", 0)
    syn_idx = col_map.get("synonym symbol", 1)
    sci_idx = col_map.get("scientific name with author", 2)
    com_idx = col_map.get("common name", 3)

    taxa: List[Tuple[str, str, str]] = []
    seen = set()

    for row in reader:
        if not row or len(row) <= sym_idx:
            continue
        symbol = row[sym_idx].strip().upper()
        synonym = row[syn_idx].strip() if len(row) > syn_idx else ""
        sci_name = row[sci_idx].strip() if len(row) > sci_idx else ""
        com_name = row[com_idx].strip() if len(row) > com_idx else ""

        if synonym != "":
            continue
        if is_variety_or_subspecies(sci_name):
            continue

        clean_sci = sci_name.replace("×", " x ").strip()
        words = clean_sci.split()
        if len(words) < 2 or not (words[1][0].islower() or words[1].lower() == "x"):
            continue

        if symbol not in seen:
            seen.add(symbol)
            taxa.append((symbol, sci_name, com_name))

        if limit and len(taxa) >= limit:
            break

    if verbose:
        print(f"Checklist loaded: {len(taxa)} accepted species/hybrids.")
    return taxa


def process_plant_online_in_memory(
    symbol: str,
    resolver: PlantMetadataResolver,
    delay: float = 0.0,
) -> Optional[PlantRecord]:
    """
    Downloads profile, characteristics, and distribution CSV directly into memory.
    Parses attributes and counties, discards raw CSV text from RAM, and returns PlantRecord.
    Zero disk files created!
    """
    if delay > 0:
        time.sleep(delay)

    profile, chars = resolver.get_plant_data(symbol)
    if not profile or not isinstance(profile, dict):
        return None

    # Check plant kingdom and infraspecific rank
    if not is_plant_taxon(profile):
        return None

    rank = profile.get("Rank", "")
    sci_name = profile.get("ScientificName", "")
    if rank and rank.lower() in ("variety", "subspecies", "subtaxa", "form"):
        return None
    if is_variety_or_subspecies(sci_name):
        return None

    master_id = profile.get("Id")
    if not master_id:
        return None

    # Download distribution CSV directly into memory
    csv_text = download_distribution_csv_in_memory(symbol, master_id)
    if not csv_text:
        counties = []
        direct_attrs = {}
    else:
        _, counties, direct_attrs = parse_csv_content(csv_text)
        del csv_text  # Free memory immediately!

    record = map_plant_attributes(symbol, profile, chars, direct_attrs)
    if record:
        record.counties.update(counties)

    return record


def stream_online_import(
    taxa_list: List[Tuple[str, str, str]],
    resolver: PlantMetadataResolver,
    sql_out_path: Optional[Path],
    db_url: Optional[str],
    progress_file_path: Optional[Path],
    workers: int = 4,
    delay: float = 0.0,
    verbose: bool = True,
    supabase_url: Optional[str] = None,
    supabase_key: Optional[str] = None,
    use_edge_function: bool = True,
) -> None:
    """
    Streams online data plant-by-plant in memory, writes SQL / inserts into DB,
    and discards data immediately. Zero CSV files stored on disk!
    """
    completed_symbols: Set[str] = set()
    if progress_file_path and progress_file_path.exists():
        try:
            with open(progress_file_path, "r", encoding="utf-8") as f:
                completed_symbols = {line.strip().upper() for line in f if line.strip()}
            if verbose:
                print(f"Resuming: {len(completed_symbols)} plants already processed previously.")
        except Exception:
            pass

    pending_taxa = [item for item in taxa_list if item[0] not in completed_symbols]
    if verbose:
        print(f"Pending plants to process: {len(pending_taxa)} (out of {len(taxa_list)} total).")

    if not pending_taxa:
        print("All plants have already been processed!")
        return

    # Open SQL output file
    sql_file = None
    if sql_out_path:
        sql_out_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if completed_symbols and sql_out_path.exists() else "w"
        is_new = mode == "w" or sql_out_path.stat().st_size == 0
        sql_file = open(sql_out_path, mode, encoding="utf-8")
        if is_new:
            sql_file.write("-- USDA PLANTS Database Import (Streamed Online)\nBEGIN;\n\n")
        else:
            sql_file.write("\n-- Resumed Import\nBEGIN;\n\n")

    progress_log = None
    if progress_file_path:
        progress_file_path.parent.mkdir(parents=True, exist_ok=True)
        progress_log = open(progress_file_path, "a", encoding="utf-8")

    total = len(pending_taxa)
    count = 0
    success = 0
    skipped = 0

    try:
        def task_fn(item: Tuple[str, str, str]) -> Tuple[str, Optional[PlantRecord]]:
            sym = item[0]
            rec = process_plant_online_in_memory(sym, resolver, delay=delay)
            return sym, rec

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            taxa_iter = iter(pending_taxa)
            futures: Dict[concurrent.futures.Future, Tuple[str, str, str]] = {}

            # Prime the pool with bounded in-flight tasks
            max_in_flight = max(workers * 4, 16)
            for item in itertools.islice(taxa_iter, max_in_flight):
                futures[executor.submit(task_fn, item)] = item

            while futures:
                done, _ = concurrent.futures.wait(
                    futures.keys(), return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    count += 1
                    sym = futures[future][0]
                    del futures[future]

                    # Submit next item if available
                    next_item = next(taxa_iter, None)
                    if next_item:
                        futures[executor.submit(task_fn, next_item)] = next_item

                    try:
                        _, record = future.result()
                    except Exception as e:
                        if verbose:
                            sys.stderr.write(f"Error streaming {sym}: {e}\n")
                        record = None

                    if record:
                        success += 1
                        # 1. Output SQL
                        if sql_file:
                            append_plant_sql(sql_file, record)
                            sql_file.flush()

                        # 2. Direct DB execution if URL provided
                        if db_url:
                            plant_sql = io.StringIO()
                            plant_sql.write("BEGIN;\n")
                            append_plant_sql(plant_sql, record)
                            plant_sql.write("COMMIT;\n")
                            execute_sql_against_db(plant_sql.getvalue(), db_url)

                        # 3. Real-time Supabase Edge Function / RPC execution
                        if supabase_url and supabase_key:
                            upsert_plant_to_supabase(
                                record,
                                supabase_url,
                                supabase_key,
                                use_edge_function=use_edge_function,
                            )

                        # Discard record from memory!
                        del record
                    else:
                        skipped += 1

                    # Record in progress file
                    if progress_log:
                        progress_log.write(f"{sym}\n")
                        progress_log.flush()

                    if verbose and (count % 25 == 0 or count == total):
                        print(f"[{count}/{total}] {success} plants imported, {skipped} non-plants/varieties skipped.")

    finally:
        if sql_file:
            sql_file.write("COMMIT;\n")
            sql_file.close()
            if verbose:
                print(f"\nSQL script written to: {sql_out_path.resolve()}")
        if progress_log:
            progress_log.close()

    print("\nStreaming import complete!")
    print(f"Total processed: {count}")
    print(f"Plants imported: {success}")
    print(f"Skipped:        {skipped}")
    print("Zero CSV files left on disk.")


def collect_csv_files(paths: List[str]) -> List[Path]:
    """Collects and deduplicates CSV files from paths/globs."""
    collected: List[Path] = []
    for p_str in paths:
        path = Path(p_str)
        if path.is_file() and path.suffix.lower() == ".csv":
            collected.append(path)
        elif path.is_dir():
            for f in sorted(path.glob("**/*.csv")):
                if f.is_file():
                    collected.append(f)
        else:
            parent = path.parent if str(path.parent) != "" else Path(".")
            pattern = path.name
            for m in list(parent.glob(pattern)):
                if m.is_file() and m.suffix.lower() == ".csv":
                    collected.append(m)

    seen: Set[Path] = set()
    result: List[Path] = []
    for p in collected:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(p)
    return result


def process_local_csv_files(
    csv_files: List[Path],
    resolver: PlantMetadataResolver,
    delete_after: bool = False,
    verbose: bool = False,
) -> List[PlantRecord]:
    """Processes local CSV files, optionally deleting each file after parsing."""
    plants_by_key: Dict[Tuple[str, str], PlantRecord] = {}
    skipped_varieties = 0
    skipped_no_identity = 0

    for idx, csv_path in enumerate(csv_files, start=1):
        if verbose or idx % 50 == 0 or idx == len(csv_files):
            print(f"[{idx}/{len(csv_files)}] Parsing {csv_path.name}...")

        symbol, counties, direct_attrs = parse_csv_file(csv_path, delete_after=delete_after)

        if not symbol:
            base_name = csv_path.stem.split(".")[0].split("_")[0].split("(")[0].strip().upper()
            if 3 <= len(base_name) <= 10 and re.match(r"^[A-Z0-9]+$", base_name):
                symbol = base_name

        profile = None
        chars = None
        if symbol:
            profile, chars = resolver.get_plant_data(symbol)

        record = map_plant_attributes(symbol or "", profile, chars, direct_attrs)
        if not record:
            skipped_varieties += 1
            continue

        key = (record.genus, record.species)
        if key not in plants_by_key:
            plants_by_key[key] = record
        else:
            existing = plants_by_key[key]
            existing.common_names.update(record.common_names)
            for attr in [
                "life_cycle",
                "foliage",
                "minheight",
                "maxheight",
                "minwidth",
                "maxwidth",
                "is_woody",
                "is_tree",
                "edible",
                "thorns",
                "sun",
                "water",
                "image_link",
            ]:
                if getattr(existing, attr) is None and getattr(record, attr) is not None:
                    setattr(existing, attr, getattr(record, attr))

        plants_by_key[key].counties.update(counties)

    records = list(plants_by_key.values())
    total_counties = sum(len(r.counties) for r in records)
    total_unique_counties = len({fips for r in records for fips, _ in r.counties})
    total_hybrids = sum(1 for r in records if r.species.startswith("x "))

    print("\n--- Summary ---")
    print(f"CSV files processed:        {len(csv_files)}")
    if delete_after:
        print(f"CSV files deleted from disk:{len(csv_files)}")
    print(f"Distinct plants extracted:   {len(records)}")
    print(f"  Standard species:          {len(records) - total_hybrids}")
    print(f"  Hybrids formatted ('x '):  {total_hybrids}")
    print(f"Varieties/subspecies skipped:{skipped_varieties}")
    print(f"Unique counties resolved:    {total_unique_counties}")
    print(f"Total plant-county relations:{total_counties}")

    return records


# Backwards compatibility alias
process_csv_files = process_local_csv_files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import USDA PLANTS data into Postgres database with zero or minimal disk usage."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Optional local CSV file paths or directories. If omitted or --stream is used, reads from online.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream mode: reads each CSV directly from online into memory and discards it. Zero CSVs on disk!",
    )
    parser.add_argument(
        "--checklist",
        default="plantlst.txt",
        help="Path or URL to USDA checklist plantlst.txt (default: plantlst.txt).",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated symbols to process (e.g. SOCA6,ACSA3,POXCA).",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        help="Limit number of plants to process (useful for testing, e.g. --limit 50).",
    )
    parser.add_argument(
        "--sql-out",
        "-o",
        dest="sql_out",
        help="Path to write the generated SQL script (e.g. import.sql).",
    )
    parser.add_argument(
        "--db-url",
        dest="db_url",
        help="PostgreSQL connection URL (e.g. postgresql://user:pass@host:5432/db).",
    )
    parser.add_argument(
        "--delete-after",
        action="store_true",
        help="In local file mode: delete each CSV file immediately after reading it to save storage.",
    )
    parser.add_argument(
        "--progress-file",
        default=DEFAULT_PROGRESS_FILE,
        help="File to record completed symbols for resuming (default: import_progress.txt).",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=4,
        help="Number of concurrent worker threads (default: 4).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay in seconds between requests per thread (default: 0.05).",
    )
    parser.add_argument(
        "--metadata",
        dest="metadata_file",
        help="Optional local JSON file containing plant profiles/characteristics for offline resolution.",
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        default=None,
        help="Optional directory to cache USDA API responses.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Offline mode: do not attempt to contact the USDA PLANTS API.",
    )
    parser.add_argument(
        "--supabase-url",
        default=os.environ.get("SUPABASE_URL"),
        help="Supabase Project URL (e.g. https://xyz.supabase.co), defaults to SUPABASE_URL env var.",
    )
    parser.add_argument(
        "--supabase-key",
        default=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        help="Supabase service_role secret key for real-time DB edit access, defaults to SUPABASE_SERVICE_ROLE_KEY env var.",
    )
    parser.add_argument(
        "--use-rpc",
        action="store_true",
        help="Call PostgREST RPC directly instead of the Edge Function.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output.",
    )

    args = parser.parse_args()

    resolver = PlantMetadataResolver(
        offline=args.offline,
        cache_dir=args.cache_dir,
        metadata_file=args.metadata_file,
    )

    # 1. Stream Mode (Reads from online, parses in-memory, discards CSV)
    if args.stream or not args.inputs:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            taxa_list = [(s, s, "") for s in symbols]
            if args.limit:
                taxa_list = taxa_list[: args.limit]
        else:
            taxa_list = fetch_accepted_checklist(
                checklist_path_or_url=args.checklist,
                limit=args.limit,
                verbose=True,
            )

        sql_out_path = Path(args.sql_out) if args.sql_out else None
        progress_path = Path(args.progress_file) if args.progress_file else None

        stream_online_import(
            taxa_list=taxa_list,
            resolver=resolver,
            sql_out_path=sql_out_path,
            db_url=args.db_url,
            progress_file_path=progress_path,
            workers=args.workers,
            delay=args.delay,
            verbose=args.verbose or True,
            supabase_url=args.supabase_url,
            supabase_key=args.supabase_key,
            use_edge_function=not args.use_rpc,
        )
        return

    # 2. Local File Mode
    csv_files = collect_csv_files(args.inputs)
    if not csv_files:
        print("No CSV files found matching inputs.")
        sys.exit(1)

    records = process_local_csv_files(
        csv_files=csv_files,
        resolver=resolver,
        delete_after=args.delete_after,
        verbose=args.verbose,
    )

    sql_content = generate_sql(records)

    if args.sql_out:
        sql_path = Path(args.sql_out)
        sql_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sql_path, "w", encoding="utf-8") as f:
            f.write(sql_content)
        print(f"Generated SQL written to: {sql_path.resolve()}")

    if args.db_url:
        print(f"Applying SQL to database at {args.db_url}...")
        success = execute_sql_against_db(sql_content, args.db_url)
        if not success:
            sys.exit(1)

    if not args.sql_out and not args.db_url:
        print("\nGenerated SQL preview (use --sql-out <file> or --db-url <url> to save/apply):")
        preview = "\n".join(sql_content.splitlines()[:30])
        print(preview)
        print("...")


if __name__ == "__main__":
    main()
