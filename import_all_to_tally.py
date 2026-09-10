import argparse
import requests
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = _ROOT / "output_xml"
MASTERS_XML = OUTPUT_DIR / "Consolidated_Masters.xml"
ENTRIES_XML = OUTPUT_DIR / "Consolidated_Entries.xml"

def post_to_tally(xml_file: Path, endpoint: str = "http://localhost:9000") -> bool:
    if not xml_file.exists():
        print(f"Error: {xml_file} not found.")
        return False

    print(f"\nPosting {xml_file.name} ({xml_file.stat().st_size:,} bytes) to TallyPrime at {endpoint}...")
    with open(xml_file, "r", encoding="utf-8") as f:
        xml_data = f.read()

    try:
        response = requests.post(
            endpoint,
            data=xml_data.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
            timeout=180
        )
        print("\n--- TallyPrime Response ---")
        print(response.text.strip())
        print("---------------------------")
        if "<ERRORS>0</ERRORS>" in response.text and "<EXCEPTIONS>0</EXCEPTIONS>" in response.text:
            print(f"[SUCCESS] {xml_file.name} imported into TallyPrime with 0 errors, 0 exceptions!")
            return True
        else:
            print(f"[WARNING] Tally returned non-zero errors or exceptions. See response above.")
            return False
    except requests.exceptions.ConnectionError:
        print(f"[ERROR] Could not connect to TallyPrime at {endpoint}. Ensure Tally is running and port 9000 is open.")
        return False
    except Exception as e:
        print(f"[ERROR] Import failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Import consolidated Masters and Entries XML files directly into TallyPrime.")
    parser.add_argument(
        "--endpoint", "-e",
        default="http://localhost:9000",
        help="TallyPrime HTTP server endpoint (default: http://localhost:9000)"
    )
    parser.add_argument(
        "--masters-only",
        action="store_true",
        help="Import only Consolidated_Masters.xml"
    )
    parser.add_argument(
        "--vouchers-only",
        action="store_true",
        help="Import only Consolidated_Entries.xml"
    )
    args = parser.parse_args()

    print("=" * 65)
    print(" TALLYPRIME LIVE XML IMPORTER ")
    print("=" * 65)

    if args.masters_only:
        post_to_tally(MASTERS_XML, args.endpoint)
    elif args.vouchers_only:
        post_to_tally(ENTRIES_XML, args.endpoint)
    else:
        # Default: import Masters first, then Entries
        print("\n[Step 1/2] Importing Master Ledgers...")
        m_ok = post_to_tally(MASTERS_XML, args.endpoint)
        if m_ok:
            print("\n[Step 2/2] Importing Accounting Vouchers...")
            post_to_tally(ENTRIES_XML, args.endpoint)
        else:
            print("[ABORT] Skipping voucher import because masters encountered an issue.")

if __name__ == "__main__":
    main()
