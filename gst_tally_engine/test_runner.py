import os
import json
from agent_engine import GSTTallyEngine

if __name__ == "__main__":
    print("=== GST-to-Tally Batch Processing Engine ===")

    # 1. Locate the Data_files sub-folder
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()
    data_folder = os.path.join(script_dir, "Data_files")

    if not os.path.exists(data_folder):
        print(f"Error: Sub-folder '{data_folder}' not found!")
        print("Please run 'generate_large_datasets.py' first to create the data files.")
        exit(1)

    # 2. Find all JSON files inside the Data_files folder
    json_files = [f for f in os.listdir(data_folder) if f.endswith(".json")]

    if not json_files:
        print(f"No JSON files found in '{data_folder}'.")
        exit(0)

    print(f"Found {len(json_files)} file(s) in '{data_folder}' to process.\n")

    # 3. Initialize the Engine targeting the Data_files sub-folder for output XMLs
    engine = GSTTallyEngine(output_dir=data_folder)

    # 4. Process each JSON file
    for json_file in json_files:
        full_json_path = os.path.join(data_folder, json_file)
        
        # Determine GSTR type automatically from filename
        gstr_type = "GSTR2B" if "GSTR2B" in json_file.upper() else "GSTR1"

        print(f"--------------------------------------------------")
        print(f"Processing File : {json_file}")
        print(f"Detected Type   : {gstr_type}")

        with open(full_json_path, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)

        # Execute conversion engine
        result = engine.process_payload(raw_json=raw_payload, gstr_type=gstr_type)

        print(f"Status          : {result.get('status')}")
        print(f"Records Parsed  : {result.get('processed_count')}")
        print(f"Breakdown       : {result.get('breakdown')}")

    print("\n==================================================")
    print("          BATCH PROCESSING COMPLETE               ")
    print("==================================================")
    print(f"All XML files generated in: {data_folder}")