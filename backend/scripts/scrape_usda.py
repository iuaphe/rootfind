#!/usr/bin/env python3
"""
scrape_usda.py

Scrapes USDA PLANTS profile data and downloads the distribution CSV files
(the exact output of the "Download Data" button on species pages e.g. https://plants.sc.egov.usda.gov/plant-profile/SOCA6).

Features:
  - Fetches the official complete PLANTS checklist from USDA.
  - Automatically skips varieties and subspecies based on taxonomic rank and name.
  - Includes standard species and hybrids.
  - Downloads each plant's distribution CSV into an output directory (default: usda_csvs/).
  - Saves all plant profile attributes and characteristics into a unified metadata JSON file
    (e.g. usda_metadata.json) for 100% offline database imports.
  - Supports concurrency (--workers), resume/skip existing files, and limits (--limit N).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

CHECKLIST_URL = "https://plants.sc.egov.usda.gov/DocumentLibrary/Txt/plantlst.txt"
USDA_API_BASE = "https://plantsservices.sc.egov.usda.gov/api"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; RootfindUSDAScraper/1.0)"


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


def is_plant_taxon(profile: Optional[Dict[str, Any]]) -> bool:
    """
    Verifies that the profile represents a true plant (Kingdom Plantae),
    filtering out non-plants such as lichens or fungi that are also in the USDA database.
    """
    if not profile or not isinstance(profile, dict):
        return True

    # 1. Check Group
    group = (profile.get("Group") or profile.get("GroupName") or "").strip().lower()
    if group in ("lichen", "fungi", "fungus"):
        return False

    # 2. Check Ancestors Kingdom
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


def fetch_plant_checklist(
    checklist_path_or_url: Optional[str] = None,
    limit: Optional[int] = None,
    verbose: bool = True,
) -> List[Tuple[str, str, str]]:
    """
    Fetches and filters the complete USDA checklist.
    Returns a list of (symbol, scientific_name, common_name) for accepted species and hybrids.
    """
    raw_content = ""
    source = checklist_path_or_url or CHECKLIST_URL

    if os.path.exists(source):
        if verbose:
            print(f"Reading plant checklist from local file: {source}")
        with open(source, "r", encoding="utf-8-sig", errors="replace") as f:
            raw_content = f.read()
    else:
        if verbose:
            print(f"Downloading plant checklist from {source}...")
        req = urllib.request.Request(source, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_content = resp.read().decode("utf-8-sig", errors="replace")

    reader = csv.reader(io.StringIO(raw_content))
    try:
        header = next(reader)
    except StopIteration:
        return []

    # Identify column indices
    col_map = {col.strip().lower(): idx for idx, col in enumerate(header)}
    sym_idx = col_map.get("symbol", 0)
    syn_idx = col_map.get("synonym symbol", 1)
    sci_idx = col_map.get("scientific name with author", 2)
    com_idx = col_map.get("common name", 3)

    accepted_taxa: List[Tuple[str, str, str]] = []
    seen_symbols: Set[str] = set()

    for row in reader:
        if not row or len(row) <= sym_idx:
            continue

        symbol = row[sym_idx].strip().upper()
        synonym = row[syn_idx].strip() if len(row) > syn_idx else ""
        sci_name = row[sci_idx].strip() if len(row) > sci_idx else ""
        com_name = row[com_idx].strip() if len(row) > com_idx else ""

        # Skip synonyms (only keep accepted taxa)
        if synonym != "":
            continue

        # Skip varieties and subspecies
        if is_variety_or_subspecies(sci_name):
            continue

        # Check for valid botanical species or hybrid format
        # Must have at least two parts (Genus and epithet)
        clean_sci = sci_name.replace("×", " x ").strip()
        words = clean_sci.split()
        if len(words) < 2:
            continue

        # Epithet should start with lowercase letter or 'x'
        if not (words[1][0].islower() or words[1].lower() == "x"):
            continue

        if symbol not in seen_symbols:
            seen_symbols.add(symbol)
            accepted_taxa.append((symbol, sci_name, com_name))

        if limit and len(accepted_taxa) >= limit:
            break

    if verbose:
        print(f"Checklist loaded: {len(accepted_taxa)} accepted species/hybrids selected (varieties/subspecies excluded).")

    return accepted_taxa


def fetch_json(url: str, timeout: int = 15) -> Optional[Any]:
    """Helper to fetch JSON from USDA API."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def download_plant_distribution_csv(symbol: str, master_id: int, timeout: int = 20) -> Optional[str]:
    """
    Downloads the distribution CSV from USDA PLANTS API
    (corresponds to the "Download Data" button on the plant profile page).
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
        sys.stderr.write(f"Warning: Failed downloading CSV for {symbol} (id {master_id}): {e}\n")
        return None


def scrape_single_plant(
    symbol: str,
    output_dir: Path,
    force: bool = False,
    delay: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Scrapes a single plant:
      1. Fetches PlantProfile JSON (to get Id, Rank, CommonName, etc.)
      2. Validates not a variety/subspecies
      3. Downloads distribution CSV to {output_dir}/{symbol}.csv
      4. Fetches PlantCharacteristics if available
      5. Returns compiled metadata dict
    """
    if delay > 0:
        time.sleep(delay)

    csv_dest = output_dir / f"{symbol}.csv"

    # 1. Fetch Profile
    prof_url = f"{USDA_API_BASE}/PlantProfile?symbol={urllib.parse.quote(symbol)}"
    profile = fetch_json(prof_url)
    if not profile or not isinstance(profile, dict):
        return None

    # Check rank and scientific name
    if rank and rank.lower() in ("variety", "subspecies", "subtaxa", "form"):
        return None
    if is_variety_or_subspecies(sci_name):
        return None

    # Check that taxon is a plant (Kingdom Plantae, not Lichen or Fungus)
    if not is_plant_taxon(profile):
        return None

    master_id = profile.get("Id")
    if not master_id:
        return None

    # 2. Download Distribution CSV if not already present or forced
    if force or not csv_dest.exists():
        csv_text = download_plant_distribution_csv(symbol, master_id)
        if csv_text:
            try:
                with open(csv_dest, "w", encoding="utf-8") as f:
                    f.write(csv_text)
            except Exception as e:
                sys.stderr.write(f"Error saving {csv_dest}: {e}\n")

    # 3. Fetch Characteristics if available
    chars = None
    if profile.get("HasCharacteristics", False):
        char_url = f"{USDA_API_BASE}/PlantCharacteristics/{master_id}"
        chars = fetch_json(char_url)

    metadata_entry: Dict[str, Any] = {
        "Symbol": symbol,
        "Id": master_id,
        "ScientificName": sci_name,
        "CommonName": profile.get("CommonName"),
        "OtherCommonNames": profile.get("OtherCommonNames", []),
        "Rank": rank,
        "Durations": profile.get("Durations", []),
        "GrowthHabits": profile.get("GrowthHabits", []),
        "ProfileImageFilename": profile.get("ProfileImageFilename"),
        "Characteristics": chars or [],
    }

    return metadata_entry


def scrape_all_plants(
    taxa_list: List[Tuple[str, str, str]],
    output_dir: Path,
    metadata_out_path: Optional[Path] = None,
    workers: int = 4,
    force: bool = False,
    delay: float = 0.0,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Scrapes plants concurrently and writes metadata file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_map: Dict[str, Any] = {}

    total = len(taxa_list)
    completed = 0
    success = 0
    skipped = 0

    if verbose:
        print(f"Beginning download of {total} plants with {workers} worker thread(s)...")

    def worker_task(item: Tuple[str, str, str]) -> Optional[Dict[str, Any]]:
        sym, _, _ = item
        return scrape_single_plant(sym, output_dir, force=force, delay=delay)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(worker_task, item): item for item in taxa_list}
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            item = futures[future]
            sym = item[0]
            try:
                meta = future.result()
                if meta:
                    metadata_map[sym] = meta
                    success += 1
                else:
                    skipped += 1
            except Exception as e:
                skipped += 1
                if verbose:
                    sys.stderr.write(f"Error scraping {sym}: {e}\n")

            if verbose and (completed % 25 == 0 or completed == total):
                print(f"[{completed}/{total}] {success} downloaded, {skipped} skipped/failed.")

    # Save metadata JSON file
    if metadata_out_path:
        metadata_out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_out_path, "w", encoding="utf-8") as f:
            json.dump(metadata_map, f, indent=2)
        if verbose:
            print(f"Saved metadata for {len(metadata_map)} plants to: {metadata_out_path.resolve()}")

    return metadata_map


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape USDA PLANTS profile data and download distribution CSVs."
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        dest="output_dir",
        default="usda_csvs",
        help="Directory to save downloaded CSV files (default: usda_csvs).",
    )
    parser.add_argument(
        "--metadata-out",
        "-m",
        dest="metadata_out",
        default="usda_metadata.json",
        help="Path to save metadata JSON file (default: usda_metadata.json).",
    )
    parser.add_argument(
        "--checklist",
        dest="checklist",
        help="Local path or URL to plantlst.txt (default: fetches from USDA).",
    )
    parser.add_argument(
        "--symbols",
        dest="symbols",
        help="Comma-separated list of symbols to scrape (e.g. SOCA6,ACSA3,POXCA).",
    )
    parser.add_argument(
        "--limit",
        "-n",
        dest="limit",
        type=int,
        help="Limit number of plants to scrape (useful for testing, e.g. --limit 10).",
    )
    parser.add_argument(
        "--workers",
        "-w",
        dest="workers",
        type=int,
        default=4,
        help="Number of concurrent download threads (default: 4).",
    )
    parser.add_argument(
        "--delay",
        dest="delay",
        type=float,
        default=0.05,
        help="Delay in seconds between requests per thread (default: 0.05).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download CSVs even if they already exist in output directory.",
    )

    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    meta_path = Path(args.metadata_out) if args.metadata_out else None

    # Determine list of plants to scrape
    taxa_list: List[Tuple[str, str, str]] = []
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        taxa_list = [(s, s, "") for s in symbols]
        if args.limit:
            taxa_list = taxa_list[: args.limit]
    else:
        taxa_list = fetch_plant_checklist(
            checklist_path_or_url=args.checklist,
            limit=args.limit,
            verbose=True,
        )

    if not taxa_list:
        print("No plants selected to scrape.")
        sys.exit(1)

    scrape_all_plants(
        taxa_list=taxa_list,
        output_dir=out_dir,
        metadata_out_path=meta_path,
        workers=args.workers,
        force=args.force,
        delay=args.delay,
        verbose=True,
    )

    print("\nScraping complete!")
    print(f"CSVs saved to:     {out_dir.resolve()}")
    if meta_path:
        print(f"Metadata saved to: {meta_path.resolve()}")
    print("\nNext step (run offline database import):")
    print(f"  python3 backend/scripts/import_usda.py {out_dir}/ --sql-out import.sql --metadata {meta_path} --offline")


if __name__ == "__main__":
    main()
