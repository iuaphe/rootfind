from playwright.sync_api import sync_playwright
import csv
import time
import requests
from PIL import Image
import io
import os

# ==========================================
# CONFIGURATION
# ==========================================
MAP_CSV_FILENAME = "bonap_county_map.csv" 

# Output files requested
FULL_OUTPUT_CSV = "all_families_attributes_with_counties.csv"
MATRIX_OUTPUT_CSV = "species_county_presence_matrix.csv"

# Load county coordinates dynamically from your CSV matching your exact columns
US_COUNTY_PIXELS = {}

if not os.path.exists(MAP_CSV_FILENAME):
    print(f"Error: Could not find '{MAP_CSV_FILENAME}'. Make sure your coordinate CSV is in the same folder!")
    exit(1)

print(f"Loading county coordinates from '{MAP_CSV_FILENAME}'...")
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
            
    print(f"Successfully loaded {len(US_COUNTY_PIXELS)} counties to check for every species.")
except Exception as e:
    print(f"Error parsing '{MAP_CSV_FILENAME}': {e}")
    exit(1)

def run_dual_output_scraper():
    with sync_playwright() as p:
        # headless=False keeps the browser visible so you can watch it navigate live
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        print("Navigating to BONAP TDC...")
        page.goto("https://bonap.net/tdc")
        
        # Wait for the family list to load
        page.wait_for_selector("#family-list", timeout=15000)
        time.sleep(2)
        
        # Extract ALL families from the page
        families = page.eval_on_selector_all(
            "#family-list li.tl-a",
            """elements => elements.map(el => ({
                id: el.getAttribute('data-id'),
                name: el.querySelector('.t-n') ? el.querySelector('.t-n').innerText.trim() : el.innerText.trim()
            }))"""
        )
        
        print(f"Found {len(families)} total families to process.")
        
        # Define headers
        base_headers = [
            "Family", "Genus", "Species", 
            "Major Plant Group", "Habit", "Duration", 
            "Nativity", "Flower Color", "Flowering Season", 
            "Habitat", "Global Rank", "Rarity & Endemism", "Special Locations"
        ]
        county_keys = list(US_COUNTY_PIXELS.keys())
        
        full_headers = base_headers + county_keys
        matrix_headers = ["Family", "Genus", "Species"] + county_keys
        
        # Open BOTH output CSV files for writing simultaneously
        with open(FULL_OUTPUT_CSV, mode="w", newline="", encoding="utf-8") as f_full, \
             open(MATRIX_OUTPUT_CSV, mode="w", newline="", encoding="utf-8") as f_matrix:
            
            writer_full = csv.writer(f_full)
            writer_matrix = csv.writer(f_matrix)
            
            # Write headers to both files
            writer_full.writerow(full_headers)
            writer_matrix.writerow(matrix_headers)
            
            # Loop through every family
            for fam_index, family in enumerate(families):
                family_id = family['id']
                family_name = family['name']
                print(f"\n[{fam_index+1}/{len(families)}] Processing Family: {family_name} (ID: {family_id})")
                
                try:
                    # Click family to load genera list via AJAX
                    page.click(f"#family-list li.tl-a[data-id='{family_id}']")
                    time.sleep(2.5)
                    
                    genera = page.eval_on_selector_all(
                        "#genus-list li.tl-a",
                        """elements => elements.map(el => ({
                            id: el.getAttribute('data-id'),
                            name: el.querySelector('.t-n') ? el.querySelector('.t-n').innerText.trim() : el.innerText.trim()
                        }))"""
                    )
                    
                    print(f"  -> Found {len(genera)} genera under {family_name}")
                    
                    for genus in genera:
                        genus_id = genus['id']
                        genus_name = genus['name']
                        
                        try:
                            # Click genus to load species list via AJAX
                            page.click(f"#genus-list li.tl-a[data-id='{genus_id}']")
                            time.sleep(2)
                            
                            species_elements = page.query_selector_all("#species-list li.tl-a")
                            
                            for i in range(len(species_elements)):
                                species_elements = page.query_selector_all("#species-list li.tl-a")
                                if i >= len(species_elements):
                                    break
                                
                                spec_el = species_elements[i]
                                spec_name_el = spec_el.query_selector('.t-n')
                                spec_name = spec_name_el.inner_text().strip() if spec_name_el else spec_el.inner_text().strip()
                                
                                try:
                                    # Click species to update UI attributes and map image
                                    spec_el.click()
                                    time.sleep(1.2) # Visual pause to let you watch the screen update
                                    
                                    # 1. Parse text attributes via JavaScript
                                    attributes = page.evaluate("""() => {
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
                                    }""")
                                    
                                    # 2. Download raw native map image via requests and check pixel colors against #ad8e00 / rgb(173, 142, 0)
                                    county_results = {}
                                    map_img_elem = page.query_selector("img[alt*='County distribution map']")
                                    
                                    if map_img_elem:
                                        img_src = map_img_elem.get_attribute("src")
                                        if img_src:
                                            full_img_url = f"https://bonap.net{img_src}" if img_src.startswith("/") else img_src
                                            img_resp = requests.get(full_img_url, timeout=10)
                                            
                                            if img_resp.status_code == 200:
                                                img_raw = Image.open(io.BytesIO(img_resp.content))
                                                
                                                # Composite transparency onto white background so native dimensions match CSV coordinates
                                                img = Image.new("RGB", img_raw.size, (255, 255, 255))
                                                if img_raw.mode in ('RGBA', 'LA') or (img_raw.mode == 'P' and 'transparency' in img_raw.info):
                                                    img.paste(img_raw, mask=img_raw.convert('RGBA').split()[3])
                                                else:
                                                    img = img_raw.convert("RGB")
                                                
                                                # Reference absent color: #ad8e00 -> rgb(173, 142, 0)
                                                absent_color = (173, 142, 0)
                                                
                                                for county_name, (px, py) in US_COUNTY_PIXELS.items():
                                                    try:
                                                        pixel_color = img.getpixel((px, py))
                                                        # If pixel IS #ad8e00 -> 0 (negative), otherwise -> 1 (positive)
                                                        county_results[county_name] = 0 if pixel_color == absent_color else 1
                                                    except Exception:
                                                        county_results[county_name] = 0
                                    
                                    # Clean, single-line terminal progress output per species
                                    present_count = sum(county_results.values())
                                    print(f"    [Logged] {genus_name} {spec_name} (Counties present: {present_count})")
                                    
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
                                    ]
                                    for county_name in county_keys:
                                        full_row.append(county_results.get(county_name, 0))
                                        
                                    matrix_row = [
                                        family_name,
                                        genus_name,
                                        spec_name
                                    ]
                                    for county_name in county_keys:
                                        matrix_row.append(county_results.get(county_name, 0))
                                        
                                    # Write immediately to both files and flush to disk
                                    writer_full.writerow(full_row)
                                    writer_matrix.writerow(matrix_row)
                                    f_full.flush()
                                    f_matrix.flush()
                                    
                                except Exception as spec_err:
                                    print(f"      [Error] Failed on species {spec_name}: {spec_err}")
                                    
                        except Exception as genus_err:
                            print(f"    [Error] Failed processing genus {genus_name}: {genus_err}")
                            
                except Exception as fam_err:
                    print(f"  [Error] Failed processing family {family_name}: {fam_err}")
                    
        print(f"\nFull scraping complete!")
        print(f" -> Full Attributes + Counties saved to: '{FULL_OUTPUT_CSV}'")
        print(f" -> Species County Matrix saved to: '{MATRIX_OUTPUT_CSV}'")
        browser.close()

if __name__ == "__main__":
    run_dual_output_scraper()