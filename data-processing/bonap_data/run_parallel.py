import subprocess
import sys
import os
import re
import time
import shutil

# ==========================================
# CONFIGURATION
# ==========================================
NUM_WORKERS = 6  # Raised from 4. Watch free commit; drop back to 4-5 if it falls below ~3,000 MB
LAUNCH_STAGGER_SECONDS = 3  # spreads out the startup spike (RAM + server hits)

# Every run writes into a new folder: output_1, output_2, output_3, ...
OUTPUT_PREFIX = "output_"

# To continue a crashed/stopped run instead of starting a new folder, set this to its
# folder name, e.g. "output_3". Must use the SAME NUM_WORKERS the run started with.
RESUME_FOLDER = None

FINAL_FULL_CSV = "all_families_attributes_with_counties.csv"
FINAL_MATRIX_CSV = "species_county_presence_matrix.csv"

def next_output_dir():
    """Return output_N where N is one higher than the largest existing output_# folder."""
    highest = 0
    for name in os.listdir("."):
        m = re.fullmatch(rf"{re.escape(OUTPUT_PREFIX)}(\d+)", name)
        if m and os.path.isdir(name):
            highest = max(highest, int(m.group(1)))
    return f"{OUTPUT_PREFIX}{highest + 1}"

def main():
    # Pick the output folder for this run
    if RESUME_FOLDER:
        output_dir = RESUME_FOLDER
        if not os.path.isdir(output_dir):
            print(f"RESUME_FOLDER '{output_dir}' does not exist.")
            sys.exit(1)
        marker = os.path.join(output_dir, "num_workers.txt")
        if os.path.exists(marker):
            with open(marker) as mf:
                prev = int(mf.read().strip())
            if prev != NUM_WORKERS:
                print(f"'{output_dir}' was started with {prev} workers; set NUM_WORKERS = {prev} to resume it.")
                sys.exit(1)
        print(f"Resuming in existing folder: {output_dir}")
    else:
        output_dir = next_output_dir()
        os.makedirs(output_dir)
        print(f"Created new output folder: {output_dir}")

    with open(os.path.join(output_dir, "num_workers.txt"), "w") as mf:
        mf.write(str(NUM_WORKERS))

    final_full_csv = os.path.join(output_dir, FINAL_FULL_CSV)
    final_matrix_csv = os.path.join(output_dir, FINAL_MATRIX_CSV)

    print(f"=== Starting Parallel Scraper with {NUM_WORKERS} workers ===")

    processes = []

    # 1. Launch all workers using the active Conda/Python executable
    for worker_id in range(NUM_WORKERS):
        cmd = [
            sys.executable,
            "bonap_worker.py",
            "--worker-id", str(worker_id),
            "--total-workers", str(NUM_WORKERS),
            "--output-dir", output_dir
        ]
        print(f"Launching Worker {worker_id}...")
        p = subprocess.Popen(cmd)
        processes.append(p)
        if worker_id < NUM_WORKERS - 1:
            time.sleep(LAUNCH_STAGGER_SECONDS)

    # 2. Wait for all background worker processes to finish safely
    failed = []
    for worker_id, p in enumerate(processes):
        p.wait()
        print(f"Worker {worker_id} has finished (exit code {p.returncode}).")
        if p.returncode != 0:
            failed.append(worker_id)

    full_files = [os.path.join(output_dir, f"all_families_attributes_worker_{i}.csv") for i in range(NUM_WORKERS)]
    matrix_files = [os.path.join(output_dir, f"species_county_presence_matrix_worker_{i}.csv") for i in range(NUM_WORKERS)]

    if failed:
        print(f"\nWorkers {failed} did not exit cleanly. Worker files were KEPT (nothing merged or deleted).")
        print(f'To resume: set RESUME_FOLDER = "{output_dir}" at the top of this script and re-run it (keep NUM_WORKERS = {NUM_WORKERS}).')
        print("Workers will skip species already saved.")
        sys.exit(1)

    print("\nAll workers completed! Merging output files...")

    merge_csvs(full_files, final_full_csv)
    merge_csvs(matrix_files, final_matrix_csv)

    # 3. Clean up temporary worker files (only reached if every worker succeeded)
    print("Cleaning up temporary worker part files...")
    for f in full_files + matrix_files:
        if os.path.exists(f):
            os.remove(f)

    print(f"\nSUCCESS! Final merged files created:")
    print(f" -> {final_full_csv}")
    print(f" -> {final_matrix_csv}")

def merge_csvs(input_files, output_file):
    """Fast byte-level merge: keep the first header, skip every other file's header line."""
    header_written = False
    last_byte_was_newline = True
    with open(output_file, mode="wb") as outfile:
        for filename in input_files:
            if not os.path.exists(filename) or os.path.getsize(filename) == 0:
                continue
            with open(filename, mode="rb") as infile:
                header_line = infile.readline()
                if not header_written:
                    outfile.write(header_line)
                    header_written = True
                    last_byte_was_newline = header_line.endswith(b"\n")
                if not last_byte_was_newline:
                    outfile.write(b"\r\n")
                    last_byte_was_newline = True
                # Copy the rest of the file in large chunks
                shutil.copyfileobj(infile, outfile, length=8 * 1024 * 1024)
            with open(filename, mode="rb") as tail:
                tail.seek(-1, os.SEEK_END)
                last_byte_was_newline = (tail.read(1) == b"\n")

if __name__ == "__main__":
    main()
