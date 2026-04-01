"""
Download MES 1-minute OHLCV data from Databento (stdlib only — no C extensions).
Saves to data/databento/MES_1min_2023_2025.csv
"""
import os
import io
import sys
import csv
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

KEY = os.getenv("DATABENTO_API_KEY")
if not KEY:
    print("ERROR: DATABENTO_API_KEY not set in .env")
    sys.exit(1)

OUT_DIR = Path("data/databento")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "MES_1min_2023_2025.csv"


def main():
    if OUT_FILE.exists():
        # Count rows without pandas
        with open(OUT_FILE) as f:
            rows = sum(1 for _ in f) - 1  # minus header
        print(f"Already exists: {OUT_FILE} ({rows:,} rows) — skipping download.")
        return

    print("Submitting batch job to Databento (uncompressed CSV)...")
    r = requests.post(
        "https://hist.databento.com/v0/batch.submit_job",
        auth=(KEY, ""),
        data={
            "dataset":     "GLBX.MDP3",
            "symbols":     "MES.c.0",
            "schema":      "ohlcv-1m",
            "stype_in":    "continuous",
            "start":       "2023-01-01",
            "end":         "2025-01-01",
            "encoding":    "csv",
            "compression": "none",
            "delivery":    "download",
        },
    )
    if not r.ok:
        print(f"ERROR submitting job: {r.status_code} {r.text}")
        sys.exit(1)

    job_id = r.json()["id"]
    print(f"Job submitted: {job_id}")

    # Poll until done
    while True:
        s = requests.get(
            "https://hist.databento.com/v0/batch.get_job",
            auth=(KEY, ""),
            params={"job_id": job_id},
        )
        state = s.json().get("state")
        print(f"  Job state: {state}")
        if state == "done":
            break
        if state in ("failed", "expired"):
            print(f"ERROR: job {state}")
            sys.exit(1)
        time.sleep(5)

    # Get file list
    fl = requests.get(
        "https://hist.databento.com/v0/batch.list_files",
        auth=(KEY, ""),
        params={"job_id": job_id},
    )
    files = fl.json()
    csv_file = next((f for f in files if f["filename"].endswith(".csv")), None)
    if not csv_file:
        # May also be a .dbn.zst file — list what we got
        print(f"Files available: {[f['filename'] for f in files]}")
        # Try any file
        csv_file = files[0] if files else None
    if not csv_file:
        print("ERROR: no files in job output")
        sys.exit(1)

    url = csv_file["https_url"]
    size_mb = csv_file.get("size", 0) / 1e6
    print(f"Downloading {csv_file['filename']} ({size_mb:.1f} MB)...")

    with requests.get(url, auth=(KEY, ""), stream=True) as dl:
        dl.raise_for_status()
        downloaded = 0
        with open(OUT_FILE, "wb") as f:
            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                print(f"  {downloaded / 1e6:.1f} MB", end="\r")

    print(f"\nDownload complete.")
    with open(OUT_FILE) as f:
        rows = sum(1 for _ in f) - 1
        f.seek(0)
        reader = csv.reader(f)
        header = next(reader)
    print(f"Rows: {rows:,}  Columns: {header}")


if __name__ == "__main__":
    main()
