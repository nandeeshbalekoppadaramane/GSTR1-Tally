import glob
import json
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "gst_tally_engine"))
from constants import get_state_name

counts = {}
for f in glob.glob(str(_ROOT / "input_json/**/*.json"), recursive=True):
    with open(f, encoding="utf-8") as jf:
        data = json.load(jf)
        for b in data.get("b2b", []):
            ctin = b.get("ctin")
            inv_count = len(b.get("inv", []))
            counts[ctin] = counts.get(ctin, 0) + inv_count
        for c in data.get("cdnr", []):
            ctin = c.get("ctin")
            notes = len(c.get("nt", []) or c.get("notes", []))
            counts[ctin] = counts.get(ctin, 0) + notes

sorted_gstins = sorted(counts.items(), key=lambda x: -x[1])

csv_path = _ROOT / "party_mappings.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["gstin", "trade_name", "state", "total_docs"])
    for gstin, cnt in sorted_gstins:
        writer.writerow([gstin, "", get_state_name(gstin), cnt])

print(f"Generated {csv_path} with {len(sorted_gstins)} unique counterparties.")
