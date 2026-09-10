import json
import os
import random
from datetime import datetime, timedelta

# Master State Codes for random GSTIN generation
STATE_CODES = {
    "27": "Maharashtra", "07": "Delhi", "33": "Tamil Nadu",
    "29": "Karnataka", "09": "Uttar Pradesh", "24": "Gujarat",
    "19": "West Bengal", "36": "Telangana"
}

TRADE_NAMES = [
    "Apex Logistics Solutions Pvt Ltd", "Global Tech Services Enterprise",
    "RK Traders & Distributors", "Precision Components Corp",
    "Synergy Electricals & Engineering", "Vanguard Industrial Supplies",
    "National Supply Chain Infra", "Horizon Electronics India",
    "Metro Commercial Agencies", "Trident Manufacturing Works"
]

TAX_RATES = [5.0, 12.0, 18.0, 28.0]

def get_random_fy2026_date():
    """Generates a random date within FY 2026-27 (01-Apr-2026 to 31-Mar-2027)."""
    start_date = datetime(2026, 4, 1)
    end_date = datetime(2027, 3, 31)
    delta_days = (end_date - start_date).days
    random_day = start_date + timedelta(days=random.randint(0, delta_days))
    return random_day.strftime("%d-%m-%Y")

def generate_gstin(state_code: str) -> str:
    chars = "ABCDE"
    digits = f"{random.randint(1000, 9999)}"
    char_end = "F1Z"
    random_check = str(random.randint(1, 9))
    return f"{state_code}{chars}{digits}{char_end}{random_check}"

def generate_line_item(item_num: int, is_interstate: bool):
    """Generates a single line item with tax allocations."""
    rt = random.choice(TAX_RATES)
    txval = round(random.uniform(500, 50000), 2)
    
    if is_interstate:
        iamt = round(txval * (rt / 100.0), 2)
        camt = 0.0
        samt = 0.0
    else:
        iamt = 0.0
        camt = round(txval * (rt / 200.0), 2)
        samt = round(txval * (rt / 200.0), 2)

    return {
        "num": item_num,
        "itm_det": {
            "txval": txval,
            "rt": rt,
            "camt": camt,
            "samt": samt,
            "iamt": iamt,
            "csamt": 0.0
        }
    }


def generate_large_gstr1(supplier_gstin="33AABCC1234Q1ZR", min_items=2000):
    """Generates GSTR-1 payload with at least min_items total line items."""
    supplier_state = supplier_gstin[:2]
    total_line_items = 0
    
    b2b_records = []
    inv_counter = 1000

    # 1. B2B Invoices (approx 85% of items)
    while total_line_items < (min_items * 0.85):
        buyer_state = random.choice(list(STATE_CODES.keys()))
        buyer_gstin = generate_gstin(buyer_state)
        trade_name = random.choice(TRADE_NAMES)
        is_interstate = (buyer_state != supplier_state)

        invoices = []
        # Generate 2 to 5 invoices per buyer
        for _ in range(random.randint(2, 5)):
            inv_counter += 1
            inum = f"INV-2627-{inv_counter}"
            idt = get_random_fy2026_date()

            itms = []
            num_items = random.randint(1, 4)
            for item_idx in range(1, num_items + 1):
                itms.append(generate_line_item(item_idx, is_interstate))
                total_line_items += 1

            total_val = sum(
                item["itm_det"]["txval"] + item["itm_det"]["camt"] + 
                item["itm_det"]["samt"] + item["itm_det"]["iamt"] 
                for item in itms
            )

            invoices.append({
                "inum": inum,
                "idt": idt,
                "val": round(total_val, 2),
                "pos": buyer_state,
                "rchrg": "N",
                "inv_typ": "R",
                "itms": itms
            })

        b2b_records.append({
            "ctin": buyer_gstin,
            "c_name": trade_name,
            "inv": invoices
        })

    # 2. CDNR Credit / Debit Notes (remaining items to reach min_items)
    cdnr_records = []
    note_counter = 100
    while total_line_items < min_items:
        buyer_state = random.choice(list(STATE_CODES.keys()))
        buyer_gstin = generate_gstin(buyer_state)
        trade_name = random.choice(TRADE_NAMES)
        is_interstate = (buyer_state != supplier_state)

        notes = []
        for _ in range(random.randint(1, 3)):
            note_counter += 1
            nttype = random.choice(["C", "D"])
            nt_num = f"{nttype}N-2627-{note_counter}"
            nt_dt = get_random_fy2026_date()

            itms = []
            num_items = random.randint(1, 3)
            for item_idx in range(1, num_items + 1):
                itms.append(generate_line_item(item_idx, is_interstate))
                total_line_items += 1

            total_val = sum(
                item["itm_det"]["txval"] + item["itm_det"]["camt"] + 
                item["itm_det"]["samt"] + item["itm_det"]["iamt"] 
                for item in itms
            )

            notes.append({
                "nt_num": nt_num,
                "nt_dt": nt_dt,
                "ntty": nttype,
                "val": round(total_val, 2),
                "itms": itms
            })

        cdnr_records.append({
            "ctin": buyer_gstin,
            "c_name": trade_name,
            "nt": notes
        })

    return {
        "gstin": supplier_gstin,
        "fp": "032027",
        "version": "GST3.2",
        "hash": "large_dataset_hash",
        "b2b": b2b_records,
        "cdnr": cdnr_records
    }, total_line_items


def generate_large_gstr2b(recipient_gstin="33AABCC1234Q1ZR", min_items=4000):
    """Generates GSTR-2B payload with at least min_items total line items."""
    recipient_state = recipient_gstin[:2]
    total_line_items = 0
    
    b2b_records = []
    pur_counter = 5000

    while total_line_items < min_items:
        supplier_state = random.choice(list(STATE_CODES.keys()))
        supplier_gstin = generate_gstin(supplier_state)
        trade_name = random.choice(TRADE_NAMES)
        is_interstate = (supplier_state != recipient_state)

        invoices = []
        for _ in range(random.randint(2, 6)):
            pur_counter += 1
            inum = f"PUR-2627-{pur_counter}"
            idt = get_random_fy2026_date()

            itms = []
            num_items = random.randint(1, 4)
            for item_idx in range(1, num_items + 1):
                rt = random.choice(TAX_RATES)
                txval = round(random.uniform(500, 75000), 2)
                
                if is_interstate:
                    iamt = round(txval * (rt / 100.0), 2)
                    camt, samt = 0.0, 0.0
                else:
                    iamt = 0.0
                    camt = round(txval * (rt / 200.0), 2)
                    samt = round(txval * (rt / 200.0), 2)

                itms.append({
                    "num": item_idx,
                    "txval": txval,
                    "rt": rt,
                    "camt": camt,
                    "samt": samt,
                    "iamt": iamt
                })
                total_line_items += 1

            total_val = sum(item["txval"] + item["camt"] + item["samt"] + item["iamt"] for item in itms)

            invoices.append({
                "inum": inum,
                "dt": idt,
                "val": round(total_val, 2),
                "pos": recipient_state,
                "itms": itms
            })

        b2b_records.append({
            "ctin": supplier_gstin,
            "tradeName": trade_name,
            "inv": invoices
        })

    return {
        "gendt": "31-03-2027",
        "gstin": recipient_gstin,
        "version": "1.0",
        "docdata": {
            "b2b": b2b_records
        }
    }, total_line_items


# =====================================================================
# SCRIPT EXECUTION & SUB-FOLDER SAVING
# =====================================================================
if __name__ == "__main__":
    # 1. Determine script directory
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()
    
    # 2. Define sub-folder target: <main_folder>/Data_files
    data_files_dir = os.path.join(script_dir, "Data_files")
    os.makedirs(data_files_dir, exist_ok=True)

    print(f"Generating datasets in target folder: {data_files_dir}\n")

    # 3. Generate GSTR-1 dataset (>= 2000 line items)
    print("Generating GSTR-1 dataset (>= 2,000 line items)...")
    gstr1_payload, gstr1_items = generate_large_gstr1(min_items=2000)
    gstr1_file = os.path.join(data_files_dir, "GSTR1_2026-2027_Large.json")
    with open(gstr1_file, "w", encoding="utf-8") as f:
        json.dump(gstr1_payload, f, indent=2)
    print(f" -> Saved: GSTR1_2026-2027_Large.json (Total Line Items: {gstr1_items})")

    # 4. Generate GSTR-2B dataset (>= 4000 line items)
    print("\nGenerating GSTR-2B dataset (>= 4,000 line items)...")
    gstr2b_payload, gstr2b_items = generate_large_gstr2b(min_items=4000)
    gstr2b_file = os.path.join(data_files_dir, "GSTR2B_2026-2027_Large.json")
    with open(gstr2b_file, "w", encoding="utf-8") as f:
        json.dump(gstr2b_payload, f, indent=2)
    print(f" -> Saved: GSTR2B_2026-2027_Large.json (Total Line Items: {gstr2b_items})")

    print("\n=====================================================")
    print("          SUCCESSFUL GENERATION COMPLETE             ")
    print("=====================================================")
    print(f"Sub-folder Path: {data_files_dir}")