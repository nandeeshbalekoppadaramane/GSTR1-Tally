import os
import time
import shutil
import json
from agent_engine import GSTTallyEngine

# Folder Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()
WATCH_FOLDER = os.path.join(SCRIPT_DIR, "Data_files")
PROCESSED_FOLDER = os.path.join(WATCH_FOLDER, "Processed")

# Ensure sub-directories exist
os.makedirs(WATCH_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

def process_single_file(filename):
    json_path = os.path.join(WATCH_FOLDER, filename)
    
    # Ignore files in subfolders or non-JSON files
    if not os.path.isfile(json_path) or not filename.endswith('.json'):
        return

    print(f"\n[AUTO-WATCHER] New file detected: {filename}")
    
    try:
        # Load JSON Payload
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_payload = json.load(f)

        # Detect GSTR Type (R2B / 2B -> GSTR2B, otherwise GSTR1)
        fname_upper = filename.upper()
        if "R2B" in fname_upper or "GSTR2B" in fname_upper or "GSTR-2B" in fname_upper:
            gstr_type = "GSTR2B"
        else:
            gstr_type = "GSTR1"

        # Initialize Engine for WATCH_FOLDER
        engine = GSTTallyEngine(output_dir=WATCH_FOLDER)
        result = engine.process_payload(raw_json=raw_payload, gstr_type=gstr_type)

        if result.get("status") == "warning":
            print(f"[AUTO-WATCHER WARNING] {result.get('message')}")
            return

        print(f"[AUTO-WATCHER] Processing complete for {filename}:")
        print(f" -> Records Parsed : {result.get('processed_count')}")
        print(f" -> Breakdown      : {result.get('breakdown')}")

        masters_src = os.path.join(WATCH_FOLDER, "Masters.xml")
        entries_src = os.path.join(WATCH_FOLDER, "Entries.xml")

        # Safely check if XML files were generated before renaming
        if os.path.exists(masters_src) and os.path.exists(entries_src):
            base_name = os.path.splitext(filename)[0]
            masters_dest = os.path.join(WATCH_FOLDER, f"{base_name}_Masters.xml")
            entries_dest = os.path.join(WATCH_FOLDER, f"{base_name}_Entries.xml")

            if os.path.exists(masters_dest):
                os.remove(masters_dest)
            if os.path.exists(entries_dest):
                os.remove(entries_dest)

            os.rename(masters_src, masters_dest)
            os.rename(entries_src, entries_dest)

            # Move the ingested JSON into 'Processed' folder
            shutil.move(json_path, os.path.join(PROCESSED_FOLDER, filename))
            print(f"[AUTO-WATCHER] Moved {filename} -> {PROCESSED_FOLDER}")
        else:
            print(f"[AUTO-WATCHER WARNING] XML files were not generated for {filename}.")

    except Exception as e:
        print(f"[AUTO-WATCHER ERROR] Failed to process {filename}: {str(e)}")

def start_watcher(interval_seconds=5):
    print("=========================================================")
    print("      GST-TO-TALLY AUTOMATED FOLDER WATCHER STARTED      ")
    print("=========================================================")
    print(f"Monitoring folder : {WATCH_FOLDER}")
    print(f"Scan Interval     : Every {interval_seconds} seconds")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            files = [f for f in os.listdir(WATCH_FOLDER) if f.endswith('.json')]
            for file in files:
                process_single_file(file)
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[AUTO-WATCHER] Stopped by user.")
            break

if __name__ == "__main__":
    start_watcher(interval_seconds=5)