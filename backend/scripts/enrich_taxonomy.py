#!/usr/bin/env python3
"""
enrich_taxonomy.py

High-performance, multi-threaded taxonomy enrichment pipeline for Rootfind.
Iterates through records in public.plants, resolves their USDA symbols,
fetches their taxonomic lineage concurrently using connection pooling,
and batch-upserts the normalized data into public.plant_taxonomy in real-time.

Features:
  - Concurrent fetching using ThreadPoolExecutor and connection-pooled HTTP sessions (20-30 req/s).
  - Batch upserts to Supabase (50-100 rows per HTTP POST) using PostgREST resolution=merge-duplicates.
  - Resumable: automatically checks existing plant_ids in plant_taxonomy and skips them.
  - Fallback checklist lookup via plantlst.txt if symbol is missing from plants.misc.
  - Cleans botanical names (strips author citations and HTML tags).
  - Clean progress bar / status logging with ETA and throughput metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger("enrich_taxonomy")
USDA_API_BASE = "https://plantsservices.sc.egov.usda.gov/api"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; RootfindTaxonomyEnricher/2.0)"
CHECKLIST_FILE = Path(__file__).resolve().parents[2] / "plantlst.txt"


def strip_html(text: Optional[str]) -> str:
    """Removes HTML tags and normalizes whitespace."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    return " ".join(clean.split())


def extract_canonical_taxon_name(raw_sci_name: Optional[str]) -> str:
    """
    Extracts the canonical botanical taxon name.
    USDA profile scientific names frequently wrap the taxon in <i>...</i>
    followed by the botanical authority, e.g.:
      '<i>Aceraceae</i> Juss.' -> 'Aceraceae'
      '<i>Pinaceae</i> Spreng. ex Rudolphi' -> 'Pinaceae'
      '<i>Sapindales</i>' -> 'Sapindales'
    """
    if not raw_sci_name:
        return ""
    match = re.search(r"<i>(.*?)</i>", raw_sci_name)
    if match:
        return strip_html(match.group(1))
    
    clean = strip_html(raw_sci_name)
    parts = clean.split()
    return parts[0] if parts else clean


def extract_usda_symbol(misc: Any) -> Optional[str]:
    """Extracts USDA symbol from plants.misc column."""
    if not misc:
        return None
    if isinstance(misc, list):
        for item in misc:
            if isinstance(item, str) and item.startswith("usda_symbol:"):
                return item.split(":", 1)[1].strip()
    if isinstance(misc, str):
        match = re.search(r"usda_symbol:([A-Za-z0-9]+)", misc)
        if match:
            return match.group(1)
        try:
            parsed = json.loads(misc)
            if isinstance(parsed, list):
                return extract_usda_symbol(parsed)
        except Exception:
            pass
    return None


class USDAChecklistIndex:
    """In-memory index mapping (genus, species) -> USDA symbol from plantlst.txt."""
    def __init__(self, checklist_path: Path = CHECKLIST_FILE):
        self._map: Dict[Tuple[str, str], str] = {}
        self._loaded = False
        self._path = checklist_path

    def load(self) -> None:
        if self._loaded or not self._path.exists():
            return
        logger.info("Indexing fallback USDA checklist from %s...", self._path)
        try:
            with open(self._path, mode="r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) >= 3:
                        symbol = row[0].strip()
                        sci_name = row[2].strip()
                        words = sci_name.split()
                        if len(words) >= 2:
                            key = (words[0].capitalize(), words[1].lower())
                            if key not in self._map:
                                self._map[key] = symbol
            self._loaded = True
            logger.info("Indexed %d scientific names from USDA checklist.", len(self._map))
        except Exception as e:
            logger.warning("Could not load USDA checklist: %s", e)

    def lookup(self, genus: str, species: str) -> Optional[str]:
        if not self._loaded:
            self.load()
        return self._map.get((genus.capitalize(), species.lower()))


class HTTPSessionPool:
    """Thread-safe HTTP client with connection pooling and retries."""
    def __init__(self, max_connections: int = 32):
        self.session = None
        if HAS_REQUESTS:
            self.session = requests.Session()
            adapter = HTTPAdapter(pool_connections=max_connections, pool_maxsize=max_connections, max_retries=2)
            self.session.mount("https://", adapter)
            self.session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"})

    def get_json(self, url: str, timeout: int = 12) -> Optional[Dict[str, Any]]:
        if self.session:
            try:
                r = self.session.get(url, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 404:
                    return None
                elif r.status_code == 429:
                    time.sleep(1.0)
                    r = self.session.get(url, timeout=timeout)
                    if r.status_code == 200:
                        return r.json()
            except Exception:
                return None
        else:
            # Fallback to urllib
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception:
                return None
        return None


def parse_usda_taxonomy(profile_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parses Ancestors from USDA PlantProfile into normalized taxonomy table fields."""
    ancestors = profile_data.get("Ancestors")
    if not ancestors or not isinstance(ancestors, list):
        return None

    lineage: List[Dict[str, Any]] = []
    ranks: Dict[str, Dict[str, Any]] = {}

    for a in ancestors:
        rank_name = a.get("Rank")
        if not rank_name:
            continue
        raw_sci = a.get("ScientificName") or ""
        canonical_name = extract_canonical_taxon_name(raw_sci)
        full_sci = strip_html(raw_sci)
        common_name = a.get("CommonName")
        symbol = a.get("Symbol")

        lineage.append({
            "rank": rank_name,
            "name": canonical_name or full_sci,
            "full_name": full_sci,
            "common": common_name,
            "symbol": symbol,
        })
        ranks[rank_name.lower()] = {
            "name": canonical_name or full_sci,
            "common": common_name,
            "symbol": symbol,
        }

    family_name = ranks.get("family", {}).get("name") or profile_data.get("Family") or "Unknown"

    return {
        "kingdom": ranks.get("kingdom", {}).get("name") or "Plantae",
        "subkingdom": ranks.get("subkingdom", {}).get("name"),
        "superdivision": ranks.get("superdivision", {}).get("name"),
        "division": ranks.get("division", {}).get("name"),
        "division_common": ranks.get("division", {}).get("common"),
        "class": ranks.get("class", {}).get("name"),
        "class_common": ranks.get("class", {}).get("common"),
        "subclass": ranks.get("subclass", {}).get("name"),
        "order_name": ranks.get("order", {}).get("name"),
        "family": family_name,
        "family_common": ranks.get("family", {}).get("common"),
        "lineage": lineage,
    }


def batch_upsert_taxonomy(
    supabase_url: str,
    service_key: str,
    batch_rows: List[Dict[str, Any]],
    timeout: int = 30,
) -> bool:
    """Batch upserts multiple taxonomy rows in a single HTTP request to Supabase."""
    if not batch_rows:
        return True
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/plant_taxonomy"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    payload = json.dumps(batch_rows).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        logger.error("Supabase batch upsert failed (HTTP %d): %s", e.code, err)
        return False
    except Exception as e:
        logger.error("Network error on Supabase batch upsert: %s", e)
        return False


def fetch_plants_needing_taxonomy(
    supabase_url: str,
    service_key: str,
    limit: Optional[int] = None,
    page_size: int = 1000,
) -> List[Dict[str, Any]]:
    """Fetches public.plants that do not yet have an entry in public.plant_taxonomy."""
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }
    logger.info("Querying already enriched plants from plant_taxonomy...")
    existing_ids: Set[int] = set()
    offset = 0
    while True:
        url = f"{supabase_url.rstrip('/')}/rest/v1/plant_taxonomy?select=plant_id&order=plant_id.asc&offset={offset}&limit={page_size}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data:
                break
            for row in data:
                existing_ids.add(row["plant_id"])
            if len(data) < page_size:
                break
            offset += page_size

    logger.info("Found %d already enriched plants in database.", len(existing_ids))

    logger.info("Fetching plants to process...")
    plants: List[Dict[str, Any]] = []
    offset = 0
    while True:
        url = f"{supabase_url.rstrip('/')}/rest/v1/plants?select=plant_id,genus,species,misc&order=plant_id.asc&offset={offset}&limit={page_size}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.loads(resp.read().decode("utf-8"))
            if not batch:
                break
            for p in batch:
                if p["plant_id"] not in existing_ids:
                    plants.append(p)
                    if limit and len(plants) >= limit:
                        return plants
            if len(batch) < page_size:
                break
            offset += page_size

    return plants


def run_fast_taxonomy_pipeline(
    supabase_url: str,
    service_key: str,
    workers: int = 20,
    batch_size: int = 50,
    limit: Optional[int] = None,
    delay: float = 0.0,
) -> Dict[str, int]:
    """
    Executes concurrent fetching and batch upserting for maximum speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    plants = fetch_plants_needing_taxonomy(supabase_url, service_key, limit=limit)
    total_count = len(plants)
    if not total_count:
        logger.info("All plants are already enriched! Nothing to do.")
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    logger.info("Processing %d plants with %d worker threads (batch size: %d)...", total_count, workers, batch_size)

    checklist = USDAChecklistIndex()
    http_pool = HTTPSessionPool(max_connections=workers * 2)

    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}
    stats_lock = threading.Lock()
    batch_buffer: List[Dict[str, Any]] = []
    buffer_lock = threading.Lock()

    def flush_batch() -> None:
        nonlocal batch_buffer
        with buffer_lock:
            if not batch_buffer:
                return
            to_send = batch_buffer
            batch_buffer = []
        ok = batch_upsert_taxonomy(supabase_url, service_key, to_send)
        with stats_lock:
            if ok:
                stats["success"] += len(to_send)
            else:
                stats["failed"] += len(to_send)

    def worker_fetch(plant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        plant_id = plant["plant_id"]
        genus = plant.get("genus", "").strip()
        species = plant.get("species", "").strip()
        misc = plant.get("misc")

        symbol = extract_usda_symbol(misc)
        if not symbol:
            symbol = checklist.lookup(genus, species)

        if not symbol:
            with stats_lock:
                stats["skipped"] += 1
            return None

        url = f"{USDA_API_BASE}/PlantProfile?symbol={urllib.parse.quote(symbol)}"
        profile = http_pool.get_json(url)
        if delay > 0:
            time.sleep(delay)

        if not profile:
            with stats_lock:
                stats["failed"] += 1
            return None

        tax = parse_usda_taxonomy(profile)
        if not tax:
            with stats_lock:
                stats["failed"] += 1
            return None

        tax["plant_id"] = plant_id
        return tax

    start_time = time.time()
    last_log_time = start_time

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(worker_fetch, p): p for p in plants}
        for future in as_completed(futures):
            tax_record = future.result()
            with stats_lock:
                stats["processed"] += 1
                current_done = stats["processed"]

            if tax_record:
                with buffer_lock:
                    batch_buffer.append(tax_record)
                    should_flush = len(batch_buffer) >= batch_size
                if should_flush:
                    flush_batch()

            # Logging progress every 2 seconds or at finish
            now = time.time()
            if now - last_log_time >= 2.0 or current_done == total_count:
                elapsed = now - start_time
                rate = current_done / elapsed if elapsed > 0 else 0
                remaining = (total_count - current_done) / rate if rate > 0 else 0
                logger.info(
                    "Progress: %d/%d (%.1f%%) | Speed: %.1f plants/sec | ETA: %.1f min | Success: %d",
                    current_done, total_count, (current_done / total_count) * 100,
                    rate, remaining / 60.0, stats["success"]
                )
                last_log_time = now

    # Final flush
    flush_batch()
    total_elapsed = time.time() - start_time
    logger.info(
        "Complete! Processed %d plants in %.1f seconds (avg %.1f plants/sec). Final stats: %s",
        total_count, total_elapsed, total_count / total_elapsed if total_elapsed > 0 else 0, stats
    )
    return stats


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Loads environment variables from .env files if present."""
    candidates = [
        env_path,
        Path(".env"),
        Path("backend/.env"),
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for p in candidates:
        if p and p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="High-speed concurrent USDA taxonomy enrichment.")
    parser.add_argument("--workers", type=int, default=20, help="Number of concurrent worker threads (default: 20).")
    parser.add_argument("--batch-size", type=int, default=50, help="Supabase upsert batch size (default: 50).")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay in seconds between requests per thread (default: 0.0).")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of plants to process.")
    parser.add_argument("--supabase-url", default=os.environ.get("SUPABASE_URL"), help="Supabase Project URL.")
    parser.add_argument("--supabase-key", default=os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY"), help="Supabase service role key.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not args.supabase_url or not args.supabase_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. Check your backend/.env file.")
        return 1

    stats = run_fast_taxonomy_pipeline(
        supabase_url=args.supabase_url,
        service_key=args.supabase_key,
        workers=args.workers,
        batch_size=args.batch_size,
        limit=args.limit,
        delay=args.delay,
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
