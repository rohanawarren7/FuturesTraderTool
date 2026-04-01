"""Check Databento data availability and cost for MES."""
import os, sys
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("DATABENTO_API_KEY")
if not key:
    print("ERROR: DATABENTO_API_KEY not set")
    sys.exit(1)

import databento as db

client = db.Historical(key)

schemas = ["ohlcv-1m", "ohlcv-1s"]
ranges = [
    ("2024-01-01", "2025-01-01", "1 year"),
    ("2023-01-01", "2025-01-01", "2 years"),
]

for schema in schemas:
    for start, end, label in ranges:
        try:
            cost = client.metadata.get_cost(
                dataset="GLBX.MDP3",
                symbols=["MES.c.0"],
                schema=schema,
                stype_in="continuous",
                start=start,
                end=end,
            )
            print(f"{label} {schema}: ${cost:.4f}")
        except Exception as e:
            print(f"{label} {schema}: ERROR - {e}")
