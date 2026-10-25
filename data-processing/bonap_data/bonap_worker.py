from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import csv
import time
import requests
from requests.adapters import HTTPAdapter
from PIL import Image
import numpy as np
import io
import os
import sys
import argparse

# Parse command line arguments for parallel slicing
parser = argparse.ArgumentParser()
parser.add_argument("--worker-id", type=int, default=0)
parser.add_argument("--total-workers", type=int, default=1)
parser.add_argument("--output-dir", type=str, default=".",
                    help="Folder where this worker's CSVs and failure log are written")
parser.add_argument("--fresh", action="store_true",
                    help="Ignore any existing worker files and start over (default: resume)")
args = parser.parse_args()

worker_id = args.worker_id
total_workers = args.total_workers

# ==========================================
# CONFIGURATION
# ==========================================
MAP_CSV_FILENAME = "bonap_county_map.csv"

# Worker-specific temporary output files (written inside the run's output folder)
OUTPUT_DIR = args.output_dir
os.makedirs(OUTPUT_DIR, exist_ok=True)
FULL_OUTPUT_CSV = os.path.join(OUTPUT_DIR, f"all_families_attributes_worker_{worker_id}.csv")
MATRIX_OUTPUT_CSV = os.path.join(OUTPUT_DIR, f"species_county_presence_matrix_worker_{worker_id}.csv")
FAILURES_LOG = os.path.join(OUTPUT_DIR, f"failures_worker_{worker_id}.txt")

# Efficiency settings
BLOCK_BROWSER_IMAGES = True   # browser doesn't download images/fonts/media (src attribute is still read; the map is fetched via requests)
LIST_WAIT_MS = 5000           # max wait for family/genus/species lists to update
MAP_WAIT_MS = 8000            # max wait for the species map to update
ABSENT_COLOR = (173, 142, 0)  # #ad8e00 -> county absent

# Load county coordinates dynamically from your CSV matching your exact columns
US_COUNTY_PIXELS = {}

if not os.path.exists(MAP_CSV_FILENAME):
    print(f"[Worker {worker_id}] Error: Could not find '{MAP_CSV_FILENAME}'. Make sure your coordinate CSV is in the same folder!")
    sys.exit(1)

print(f"[Worker {worker_id}] Loading county coordinates from '{MAP_CSV_FILENAME}'...")
try:
    with open(MAP_CSV_FILENAME, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            c_name = row['county'].strip()
            s_name = row['state'].strip()
            px = int(row['pixel_x'])
            py = int(row['pixel_y'])

            county_label = f"{c_name}, {s_name}"
            US_COUNTY_PIXELS[county_label] = (px, py)

    print(f"[Worker {worker_id}] Successfully loaded {len(US_COUNTY_PIXELS)} counties to check for every species.")
except Exception as e:
    print(f"[Worker {worker_id}] Error parsing '{MAP_CSV_FILENAME}': {e}")
    sys.exit(1)

# Coordinate arrays built ONCE (same order as county_keys) for vectorized pixel lookup
COUNTY_KEYS = list(US_COUNTY_PIXELS.keys())
XS = np.array([US_COUNTY_PIXELS[k][0] for k in COUNTY_KEYS], dtype=np.int64)
YS = np.array([US_COUNTY_PIXELS[k][1] for k in COUNTY_KEYS], dtype=np.int64)
ABSENT_ARR = np.array(ABSENT_COLOR, dtype=np.int32)
NO_MAP_ROW = [0] * len(COUNTY_KEYS)

# ==========================================
# JAVASCRIPT SNIPPETS
# ==========================================
LIST_JS = """elements => elements.map(el => ({
    id: el.getAttribute('data-id'),
    name: el.querySelector('.t-n') ? el.querySelector('.t-n').innerText.trim() : el.innerText.trim()
}))"""

LIST_SIG_JS = """sel => Array.from(document.querySelectorAll(sel)).map(e => (e.getAttribute('data-id') || '') + ':' + e.innerText.trim()).join('|')"""

LIST_CHANGED_JS = """([sel, prev]) => {
    const sig = Array.from(document.querySelectorAll(sel)).map(e => (e.getAttribute('data-id') || '') + ':' + e.innerText.trim()).join('|');
    return sig !== '' && sig !== prev;
}"""

MAP_SRC_JS = """() => {
    const i = document.querySelector("img[alt*='County distribution map']");
    return i ? (i.getAttribute('src') || '') : '';
}"""

MAP_CHANGED_JS = """prev => {
    const i = document.querySelector("img[alt*='County distribution map']");
    const s = i ? (i.getAttribute('src') || '') : '';
    return s !== '' && s !== prev;
}"""

ATTR_JS = """() => {
    const container = document.querySelector('div.attributes');
    if (!container) return {};

    const divs = Array.from(container.querySelectorAll('div'));
    let subItems = {};
    let activeHeader = null;

    divs.forEach(div => {
        const text = div.innerText.trim();
        if (!text || text.includes("Floristic Synthesis")) return;

        const style = div.getAttribute('style') || "";
        if (style.includes("text-decoration: underline")) {
            activeHeader = text;
            if (!subItems[activeHeader]) {
                subItems[activeHeader] = [];
            }
        } else if (activeHeader) {
            subItems[activeHeader].push(text);
        }
    });

    let result = {
        "Major Plant Group": "",
        "Habit": "",
        "Duration": "",
        "Nativity": "",
        "Flower Color": "",
        "Flowering Season": "",
        "Habitat": "",
        "Global Rank": "",
        "Rarity & Endemism": "",
        "Special Locations": ""
    };

    if (subItems["Major plant group"]) result["Major Plant Group"] = subItems["Major plant group"].join(" > ");
    if (subItems["Habit"]) result["Habit"] = subItems["Habit"].join(", ");
    if (subItems["Duration"]) result["Duration"] = subItems["Duration"].join(", ");
    if (subItems["Nativity"]) result["Nativity"] = subItems["Nativity"].join(" > ");

    if (subItems["Morphology"]) {
        let morphText = subItems["Morphology"];
        let colors = morphText.filter(t => t.includes("Main flower color"));
        let seasons = morphText.filter(t => t.includes("flowering") && !t.includes("color"));
        result["Flower Color"] = colors.join("; ");
        result["Flowering Season"] = seasons.join("; ");
    }

    if (subItems["Habitat"]) result["Habitat"] = subItems["Habitat"].join(" | ");

    if (subItems["Rarity and endemism"]) {
        let rarityLines = subItems["Rarity and endemism"];
        let globalRank = rarityLines.find(t => t.startsWith("G") || t.includes("rank") || t.includes("Secure"));
        let others = rarityLines.filter(t => t !== globalRank);
        if (globalRank) result["Global Rank"] = globalRank;
        result["Rarity & Endemism"] = others.join(" | ");
    }

    let locs = [];
    if (subItems["Horticultural novelty plants"]) locs = locs.concat(subItems["Horticultural novelty plants"]);
    if (subItems["Plant of U.S. federal lands and other specific locations"]) {
        locs = locs.concat(subItems["Plant of U.S. federal lands and other specific locations"]);
    }
    result["Special Locations"] = locs.join(" | ");

    return result;
}"""

# ==========================================
# HELPERS
# ==========================================
def make_session():
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def trim_partial_last_line(path):
    """If a previous run died mid-write, cut the file back to its last complete line."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "rb+") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        pos = size
        block = 65536
        while pos > 0:
            start = max(0, pos - block)
            f.seek(start)
            data = f.read(pos - start)
            idx = data.rfind(b"\n")
            if idx != -1:
                f.truncate(start + idx + 1)
                return
            pos = start
        f.truncate(0)


def load_done_keys(path, ncols):
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, mode="r", newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) == ncols:
                keys.add((row[0], row[1], row[2]))
    return keys


def wait_list_change(page, selector, prev_sig):
    """Wait until a list's contents change (and are non-empty) instead of a fixed sleep."""
    try:
        page.wait_for_function(LIST_CHANGED_JS, arg=[selector, prev_sig], timeout=LIST_WAIT_MS)
    except PWTimeout:
        pass  # fall through, same as the old behavior of just proceeding


def wait_map_change(page, prev_src):
    try:
        page.wait_for_function(MAP_CHANGED_JS, arg=prev_src, timeout=MAP_WAIT_MS)
    except PWTimeout:
        pass


def wait_idle(page, inflight, timeout=5.0):
    """Wait until in-page AJAX (xhr/fetch) requests have settled."""
    deadline = time.time() + timeout
    quiet = 0
    while time.time() < deadline:
        if inflight["n"] <= 0:
            quiet += 1
            if quiet >= 3:
                return
        else:
            quiet = 0
        page.wait_for_timeout(30)


def compute_presence(content):
    """Vectorized equivalent of the per-county getpixel loop (transparency composited on white)."""
    img_raw = Image.open(io.BytesIO(content))
    rgba = np.asarray(img_raw.convert("RGBA"))
    h, w = rgba.shape[:2]

    valid = (XS >= 0) & (XS < w) & (YS >= 0) & (YS < h)
    xs = np.where(valid, XS, 0)
    ys = np.where(valid, YS, 0)

    px = rgba[ys, xs].astype(np.int32)
    rgb = px[:, :3]
    a = px[:, 3:4]

    if not (a == 255).all():
        # Same rounding PIL uses when pasting with an alpha mask onto white
        t = rgb * a + 255 * (255 - a) + 128
        rgb = (t + (t >> 8)) >> 8

    absent = (rgb == ABSENT_ARR).all(axis=1)
    presence = np.where(valid & ~absent, 1, 0)
    return presence.tolist()


def fetch_map_presence(session, page):
    """Returns (presence_list, found_map_bool)."""
    map_img_elem = page.query_selector("img[alt*='County distribution map']")
    if not map_img_elem:
        return NO_MAP_ROW, False
    img_src = map_img_elem.get_attribute("src")
    if not img_src:
        return NO_MAP_ROW, False

    full_img_url = f"https://bonap.net{img_src}" if img_src.startswith("/") else img_src

    img_resp = None
    for attempt in range(3):
        try:
            img_resp = session.get(full_img_url, timeout=10)
            if img_resp.status_code == 200:
                break
        except requests.RequestException:
            img_resp = None
        time.sleep(0.5 * (attempt + 1))

    if img_resp is None or img_resp.status_code != 200:
        return NO_MAP_ROW, False

    return compute_presence(img_resp.content), True


# ==========================================
# WORKER
# ==========================================
def run_worker():
    base_headers = [
        "Family", "Genus", "Species",
        "Major Plant Group", "Habit", "Duration",
        "Nativity", "Flower Color", "Flowering Season",
        "Habitat", "Global Rank", "Rarity & Endemism", "Special Locations"
    ]
    county_keys = COUNTY_KEYS

    full_headers = base_headers + county_keys
    matrix_headers = ["Family", "Genus", "Species"] + county_keys

    # ---- Resume support ----
    if args.fresh:
        for pth in (FULL_OUTPUT_CSV, MATRIX_OUTPUT_CSV):
            if os.path.exists(pth):
                os.remove(pth)
    trim_partial_last_line(FULL_OUTPUT_CSV)
    trim_partial_last_line(MATRIX_OUTPUT_CSV)

    done_full = load_done_keys(FULL_OUTPUT_CSV, len(full_headers))
    done_matrix = load_done_keys(MATRIX_OUTPUT_CSV, len(matrix_headers))
    if done_full != done_matrix:
        print(f"[Worker {worker_id}] Warning: worker files out of sync "
              f"({len(done_full)} vs {len(done_matrix)} rows); resuming from the intersection.")
    done = done_full & done_matrix
    if done:
        print(f"[Worker {worker_id}] Resuming: {len(done)} species already saved, they will be skipped.")

    need_full_header = (not os.path.exists(FULL_OUTPUT_CSV)) or os.path.getsize(FULL_OUTPUT_CSV) == 0
    need_matrix_header = (not os.path.exists(MATRIX_OUTPUT_CSV)) or os.path.getsize(MATRIX_OUTPUT_CSV) == 0

    session = make_session()

    with sync_playwright() as p:
        # headless=True is faster and recommended for parallel background workers
        browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
        context = browser.new_context()

        if BLOCK_BROWSER_IMAGES:
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "font", "media")
                else route.continue_()
            )

        page = context.new_page()

        # Track in-flight AJAX so we know when the page has finished updating
        inflight = {"n": 0}

        def _on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                inflight["n"] += 1

        def _on_done(req):
            if req.resource_type in ("xhr", "fetch"):
                inflight["n"] = max(0, inflight["n"] - 1)

        page.on("request", _on_request)
        page.on("requestfinished", _on_done)
        page.on("requestfailed", _on_done)

        print(f"[Worker {worker_id}] Navigating to BONAP TDC...")
        page.goto("https://bonap.net/tdc")

        # Wait for the family list to load
        page.wait_for_selector("#family-list", timeout=15000)
        page.wait_for_selector("#family-list li.tl-a", timeout=15000)
        wait_idle(page, inflight)

        # Extract ALL families from the page
        families = page.eval_on_selector_all("#family-list li.tl-a", LIST_JS)

        # Slice families across workers (interleaved so big and small families are spread evenly)
        assigned_idx = [i for i in range(len(families)) if i % total_workers == worker_id]
        families = [families[i] for i in assigned_idx]

        print(f"[Worker {worker_id}] Assigned {len(families)} families (interleaved, every {total_workers}th starting at index {worker_id}).")

        # Open BOTH output CSV files for writing simultaneously
        with open(FULL_OUTPUT_CSV, mode="a", newline="", encoding="utf-8") as f_full, \
             open(MATRIX_OUTPUT_CSV, mode="a", newline="", encoding="utf-8") as f_matrix, \
             open(FAILURES_LOG, mode="a", encoding="utf-8") as f_fail:

            writer_full = csv.writer(f_full)
            writer_matrix = csv.writer(f_matrix)

            # Write headers to both files (only if new)
            if need_full_header:
                writer_full.writerow(full_headers)
            if need_matrix_header:
                writer_matrix.writerow(matrix_headers)

            def log_failure(level, family_name, genus_name, spec_name, err):
                f_fail.write(f"{level}\t{family_name}\t{genus_name}\t{spec_name}\t{err}\n")
                f_fail.flush()

            # Loop through assigned slice of families
            for fam_index, family in enumerate(families):
                family_id = family['id']
                family_name = family['name']
                print(f"\n[Worker {worker_id}] [{fam_index+1}/{len(families)}] Processing Family: {family_name} (ID: {family_id})")

                try:
                    # Click family to load genera list via AJAX
                    prev_sig = page.evaluate(LIST_SIG_JS, "#genus-list li.tl-a")
                    page.click(f"#family-list li.tl-a[data-id='{family_id}']")
                    wait_list_change(page, "#genus-list li.tl-a", prev_sig)
                    wait_idle(page, inflight)

                    genera = page.eval_on_selector_all("#genus-list li.tl-a", LIST_JS)

                    print(f"[Worker {worker_id}]   -> Found {len(genera)} genera under {family_name}")

                    for genus in genera:
                        genus_id = genus['id']
                        genus_name = genus['name']

                        try:
                            # Click genus to load species list via AJAX
                            prev_sig = page.evaluate(LIST_SIG_JS, "#species-list li.tl-a")
                            page.click(f"#genus-list li.tl-a[data-id='{genus_id}']")
                            wait_list_change(page, "#species-list li.tl-a", prev_sig)
                            wait_idle(page, inflight)

                            # Read the species list ONCE instead of re-querying every iteration
                            species = page.eval_on_selector_all("#species-list li.tl-a", LIST_JS)

                            for i, spec in enumerate(species):
                                spec_name = spec['name']

                                if (family_name, genus_name, spec_name) in done:
                                    continue

                                try:
                                    # Click species to update UI attributes and map image
                                    prev_src = page.evaluate(MAP_SRC_JS)
                                    page.locator("#species-list li.tl-a").nth(i).click()
                                    wait_map_change(page, prev_src)
                                    wait_idle(page, inflight)

                                    # 1. Parse text attributes via JavaScript
                                    attributes = page.evaluate(ATTR_JS)

                                    # 2. Download raw native map image and check pixel colors against #ad8e00 / rgb(173, 142, 0)
                                    presence, found_map = fetch_map_presence(session, page)
                                    if not found_map:
                                        print(f"      [Worker {worker_id}] [NoMap] {genus_name} {spec_name}: no map retrieved, counties recorded as 0")
                                        log_failure("nomap", family_name, genus_name, spec_name, "map not retrieved")

                                    present_count = sum(presence)
                                    print(f"    [Worker {worker_id}] [Logged] {genus_name} {spec_name} (Counties present: {present_count})")

                                    # 3. Assemble rows
                                    full_row = [
                                        family_name,
                                        genus_name,
                                        spec_name,
                                        attributes.get("Major Plant Group", ""),
                                        attributes.get("Habit", ""),
                                        attributes.get("Duration", ""),
                                        attributes.get("Nativity", ""),
                                        attributes.get("Flower Color", ""),
                                        attributes.get("Flowering Season", ""),
                                        attributes.get("Habitat", ""),
                                        attributes.get("Global Rank", ""),
                                        attributes.get("Rarity & Endemism", ""),
                                        attributes.get("Special Locations", "")
                                    ] + presence

                                    matrix_row = [family_name, genus_name, spec_name] + presence

                                    # Write immediately to worker files and flush to disk
                                    writer_full.writerow(full_row)
                                    writer_matrix.writerow(matrix_row)
                                    f_full.flush()
                                    f_matrix.flush()

                                except Exception as spec_err:
                                    print(f"      [Worker {worker_id}] [Error] Failed on species {spec_name}: {spec_err}")
                                    log_failure("species", family_name, genus_name, spec_name, spec_err)

                        except Exception as genus_err:
                            print(f"    [Worker {worker_id}] [Error] Failed processing genus {genus_name}: {genus_err}")
                            log_failure("genus", family_name, genus_name, "", genus_err)

                except Exception as fam_err:
                    print(f"  [Worker {worker_id}] [Error] Failed processing family {family_name}: {fam_err}")
                    log_failure("family", family_name, "", "", fam_err)

        print(f"\n[Worker {worker_id}] Complete! Saved to worker files.")
        browser.close()
        session.close()


if __name__ == "__main__":
    run_worker()
