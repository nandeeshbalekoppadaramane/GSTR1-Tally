import os
import sys
import json
import argparse
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_GST_ENGINE = _ROOT / "gst_tally_engine"
if str(_GST_ENGINE) not in sys.path:
    sys.path.insert(0, str(_GST_ENGINE))

from contexts.states import MappingMemoryContext
from harnesses.wrappers import AuditHarness
from loops.agent_loops import AgentLoopEngine
from domain import GSTDataExtractor, NormalizedDocument
from xml_engine import TallyXMLEngine
from constants import (
    reload_party_mappings,
    set_name_preference,
    get_name_preference,
    format_party_ledger
)

MONTH_ORDER = {
    "APRIL": 1, "MAY": 2, "JUNE": 3, "JULY": 4,
    "AUGUST": 5, "SEPTEMBER": 6, "OCTOBER": 7, "NOVEMBER": 8,
    "DECEMBER": 9, "JANUARY": 10, "FEBRUARY": 11, "MARCH": 12
}

def get_month_sort_key(name: str) -> int:
    upper = name.upper()
    for month, idx in MONTH_ORDER.items():
        if month in upper:
            return idx
    return 99

def process_file_entries(
    json_file_path: Path,
    output_dir: Path,
    file_label: str,
    memory_context: MappingMemoryContext,
    save_individual_masters: bool = False
) -> tuple:
    print(f"\n{'=' * 65}")
    print(f" PROCESSING GSTR-1: {file_label}")
    print(f"{'=' * 65}")

    with open(json_file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    audit_harness = AuditHarness()
    loop_engine = AgentLoopEngine(
        memory=memory_context,
        audit=audit_harness,
        tally_harness=None
    )

    batch_id = f"BATCH-{file_label}"
    result = loop_engine.run_sequential_loop(
        raw_json=payload,
        gstr_type="GSTR1",
        batch_id=batch_id,
        resume_from_breakpoint=False,
        push_to_tally=False
    )

    total_processed = result["total_processed"]
    masters_xml = result["masters_xml"]
    entries_xml = result["entries_xml"]

    # Validate Entries XML
    try:
        ET.fromstring(entries_xml)
        entries_valid = True
    except ET.ParseError as pe:
        entries_valid = False
        print(f"[XML ERROR] Entries XML Parse Failed: {pe}")

    # Write Entries XML
    output_dir.mkdir(parents=True, exist_ok=True)
    entries_file = output_dir / f"{file_label}_Entries.xml"
    with open(entries_file, "w", encoding="utf-8") as f:
        f.write(entries_xml)

    # Optional individual monthly masters
    masters_file = None
    if save_individual_masters:
        masters_file = output_dir / f"{file_label}_Masters.xml"
        with open(masters_file, "w", encoding="utf-8") as f:
            f.write(masters_xml)

    # Compute financial summary from parsed documents
    docs = GSTDataExtractor.extract_documents(payload, "GSTR1")
    b2b_count = sum(1 for d in docs if d.doc_type in ("B2B", "B2BA"))
    b2c_count = sum(1 for d in docs if d.doc_type == "B2CS")
    cdnr_count = sum(1 for d in docs if "CREDIT" in d.doc_type or "DEBIT" in d.doc_type)
    total_taxable = sum(d.taxable_val for d in docs)
    total_inv_val = sum(d.total_val for d in docs)
    cgst_tot = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "CGST")
    sgst_tot = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "SGST")
    igst_tot = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "IGST")
    parties = {d.party_gstin for d in docs if d.party_gstin}

    print(f"[MONTHLY STATS]")
    print(f" -> Total Documents           : {total_processed} (B2B: {b2b_count}, B2C: {b2c_count}, CDNR: {cdnr_count})")
    print(f" -> Unique Counterparties     : {len(parties)}")
    print(f" -> Taxable Amount (INR)      : {total_taxable:,.2f}")
    print(f" -> Central Tax / CGST (INR)  : {cgst_tot:,.2f}")
    print(f" -> State Tax / SGST (INR)    : {sgst_tot:,.2f}")
    print(f" -> Integrated Tax / IGST(INR): {igst_tot:,.2f}")
    print(f" -> Total Invoice Value (INR) : {total_inv_val:,.2f}")
    print(f"\n[GENERATED XML]")
    print(f" -> Entries XML               : {entries_file.name} ({entries_file.stat().st_size:,} bytes)")
    print(f" -> XML Schema Valid          : {'YES' if entries_valid else 'NO'}")

    summary = {
        "label": file_label,
        "docs": total_processed,
        "parties": len(parties),
        "taxable": total_taxable,
        "cgst": cgst_tot,
        "sgst": sgst_tot,
        "igst": igst_tot,
        "total_val": total_inv_val,
        "entries_file": str(entries_file)
    }

    return summary, docs

def generate_consolidated_masters(
    all_documents: List[NormalizedDocument],
    output_dir: Path
) -> Path:
    """
    Generates a single consolidated Masters.xml containing all unique parties,
    tax ledgers, base accounts, and respective parent groups across the entire year.
    """
    print(f"\n{'=' * 65}")
    print(" GENERATING CONSOLIDATED ANNUAL MASTERS XML")
    print(f"{'=' * 65}")

    consolidated_xml = TallyXMLEngine.generate_masters_xml(all_documents, "GSTR1")

    # Validate XML Syntax
    try:
        root = ET.fromstring(consolidated_xml)
        valid = True
    except ET.ParseError as pe:
        valid = False
        print(f"[ERROR] Consolidated Masters XML Parse Failed: {pe}")

    output_dir.mkdir(parents=True, exist_ok=True)
    master_file = output_dir / "Consolidated_Masters.xml"
    master_alias = output_dir / "Masters.xml"

    with open(master_file, "w", encoding="utf-8") as f:
        f.write(consolidated_xml)

    with open(master_alias, "w", encoding="utf-8") as f:
        f.write(consolidated_xml)

    # Count distinct ledgers inside the consolidated XML
    ledgers = root.findall(".//LEDGER") if valid else []
    debtors = [l for l in ledgers if l.find("PARENT") is not None and l.find("PARENT").text == "Sundry Debtors"]
    taxes = [l for l in ledgers if l.find("PARENT") is not None and l.find("PARENT").text == "Duties & Taxes"]
    sales_ac = [l for l in ledgers if l.find("PARENT") is not None and l.find("PARENT").text in ("Sales Accounts", "Indirect Expenses")]

    print(f" -> Total Master Ledgers Created: {len(ledgers)}")
    print(f"    - Party Ledgers (Sundry Debtors): {len(debtors)}")
    print(f"    - Tax Accounts (Duties & Taxes) : {len(taxes)}")
    print(f"    - Base Accounts (Sales & Round) : {len(sales_ac)}")
    print(f" -> Artifact File Saved         : {master_file.name} ({master_file.stat().st_size:,} bytes)")
    print(f" -> Ready for TallyPrime Import : {'YES' if valid else 'NO'}")

    return master_file

def generate_consolidated_entries(
    all_documents: List[NormalizedDocument],
    output_dir: Path
) -> Path:
    """
    Generates a single consolidated Entries.xml containing all vouchers
    across all months (the entire financial year), sorted chronologically.
    """
    print(f"\n{'=' * 65}")
    print(" GENERATING CONSOLIDATED ANNUAL ENTRIES (VOUCHERS) XML")
    print(f"{'=' * 65}")

    # Sort all documents chronologically by doc_date
    sorted_docs = sorted(all_documents, key=lambda d: d.doc_date)

    consolidated_xml = TallyXMLEngine.generate_entries_xml(sorted_docs, "GSTR1")

    # Validate XML Syntax
    try:
        root = ET.fromstring(consolidated_xml)
        valid = True
    except ET.ParseError as pe:
        valid = False
        print(f"[ERROR] Consolidated Entries XML Parse Failed: {pe}")

    output_dir.mkdir(parents=True, exist_ok=True)
    entries_file = output_dir / "Consolidated_Entries.xml"
    entries_alias = output_dir / "Entries.xml"

    with open(entries_file, "w", encoding="utf-8") as f:
        f.write(consolidated_xml)

    with open(entries_alias, "w", encoding="utf-8") as f:
        f.write(consolidated_xml)

    vouchers = root.findall(".//VOUCHER") if valid else []
    print(f" -> Total Vouchers in Single XML: {len(vouchers):,}")
    if sorted_docs:
        print(f" -> Date Range Covered         : {sorted_docs[0].doc_date} to {sorted_docs[-1].doc_date}")
    print(f" -> Artifact File Saved        : {entries_file.name} ({entries_file.stat().st_size:,} bytes)")
    print(f" -> Ready for TallyPrime Import: {'YES' if valid else 'NO'}")

    return entries_file

def main():
    parser = argparse.ArgumentParser(description="Process GSTR-1 JSON files and generate Tally XML files.")
    parser.add_argument(
        "--input", "-i",
        default=str(_ROOT / "input_json"),
        help="Path to a GSTR-1 JSON/ZIP file or directory (default: ./input_json)"
    )
    parser.add_argument(
        "--output", "-o",
        default=str(_ROOT / "output_xml"),
        help="Path to directory where XML files should be saved (default: ./output_xml)"
    )
    parser.add_argument(
        "--name-preference", "-p",
        choices=["trade", "legal"],
        default="trade",
        help="Ledger name preference: 'trade' (default) uses Trade Name (fallback to Legal); 'legal' uses Legal Name."
    )
    parser.add_argument(
        "--save-monthly-masters",
        action="store_true",
        default=False,
        help="Also output individual monthly *_Masters.xml alongside Consolidated_Masters.xml"
    )
    args = parser.parse_args()

    # Set user naming preference and refresh database
    set_name_preference(args.name_preference)
    reload_party_mappings()
    print(f"[CONFIG] Party Ledger Name Preference: {get_name_preference().upper()}")
    print("         (Format: 'Name (GSTIN)' - state suffix removed)")

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    tasks = []  # List of (json_path, label)

    if input_path.is_file():
        if input_path.suffix.lower() == ".zip":
            print(f"[INFO] Unpacking ZIP archive: {input_path.name}")
            extract_dir = input_path.parent / input_path.stem
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(input_path, 'r') as zf:
                zf.extractall(extract_dir)
            for jf in extract_dir.glob("**/*.json"):
                tasks.append((jf, input_path.stem))
        elif input_path.suffix.lower() == ".json":
            tasks.append((input_path, input_path.stem))
        else:
            print(f"Error: Unsupported file format '{input_path.suffix}'.")
            return
    elif input_path.is_dir():
        # First, unpack any .zip files into subdirectories named after the zip
        zip_files = sorted(list(input_path.glob("*.zip")), key=lambda z: get_month_sort_key(z.stem))
        for zf_path in zip_files:
            extract_dir = input_path / zf_path.stem
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zf_path, 'r') as zf:
                zf.extractall(extract_dir)
            for jf in extract_dir.glob("*.json"):
                tasks.append((jf, zf_path.stem))

        # Also check for any standalone .json files in input_json root
        for jf in input_path.glob("*.json"):
            tasks.append((jf, jf.stem))
    else:
        print(f"Error: Path does not exist: {input_path}")
        return

    if not tasks:
        print(f"No .json or .zip files found in {input_path}")
        return

    # Sort tasks chronologically by month
    tasks.sort(key=lambda t: get_month_sort_key(t[1]))

    print(f"Ready to process {len(tasks)} GSTR-1 dataset(s).")

    memory_context = MappingMemoryContext("client_memory.db")
    all_summaries = []
    all_annual_documents: List[NormalizedDocument] = []

    for jf, label in tasks:
        summary, docs = process_file_entries(
            jf, output_path, label, memory_context,
            save_individual_masters=args.save_monthly_masters
        )
        all_summaries.append(summary)
        all_annual_documents.extend(docs)

    # Generate Single Consolidated Masters XML for the entire year
    generate_consolidated_masters(all_annual_documents, output_path)

    # Generate Single Consolidated Entries (Vouchers) XML for all 11 months
    generate_consolidated_entries(all_annual_documents, output_path)

    # Print Grand Annual Reconciliation Summary
    print("\n" + "#" * 65)
    print("       ANNUAL FINANCIAL RECONCILIATION SUMMARY (ALL MONTHS)    ")
    print("#" * 65)
    total_docs = sum(s["docs"] for s in all_summaries)
    total_taxable = sum(s["taxable"] for s in all_summaries)
    total_cgst = sum(s["cgst"] for s in all_summaries)
    total_sgst = sum(s["sgst"] for s in all_summaries)
    total_igst = sum(s["igst"] for s in all_summaries)
    grand_total_val = sum(s["total_val"] for s in all_summaries)

    print(f" Total Months Processed       : {len(all_summaries)}")
    print(f" Total Documents (Vouchers)   : {total_docs:,}")
    print(f" Grand Total Taxable (INR)    : {total_taxable:,.2f}")
    print(f" Grand Total Central Tax/CGST : {total_cgst:,.2f}")
    print(f" Grand Total State Tax/SGST   : {total_sgst:,.2f}")
    print(f" Grand Total Integrated Tax   : {total_igst:,.2f}")
    print(f" Grand Total Value (INR)      : {grand_total_val:,.2f}")
    print(f" Output Directory             : {output_path.resolve()}")
    print("#" * 65 + "\n")

if __name__ == "__main__":
    main()
