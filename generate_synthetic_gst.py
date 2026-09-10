import json
import os
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List

# Master State Codes and Sample Trade Names
STATE_CODES = {
    "27": "Maharashtra",
    "07": "Delhi",
    "33": "Tamil Nadu",
    "29": "Karnataka",
    "09": "Uttar Pradesh",
    "24": "Gujarat"
}

TRADE_NAMES = [
    "RK Traders Private Limited",
    "Apex Industrial Logistics",
    "Global Tech Services",
    "Precision Components Corp",
    "National Supply Chain Pvt Ltd",
    "Synergy Electricals",
    "Vanguard Enterprises"
]

TAX_RATES = [5.0, 12.0, 18.0, 28.0]


def generate_gstin(state_code: str) -> str:
    """Generates a pseudo-valid GSTIN for a given state code."""
    chars = "ABCDE"
    digits = f"{random.randint(1000, 9999)}"
    char_end = "F1Z"
    random_check = str(random.randint(1, 9))
    return f"{state_code}{chars}{digits}{char_end}{random_check}"


def generate_item_detail(rt: float, txval: float, is_interstate: bool) -> Dict[str, Any]:
    """Calculates CGST/SGST or IGST based on place of supply rules."""
    txval = round(txval, 2)
    if is_interstate:
        iamt = round(txval * (rt / 100.0), 2)
        camt = 0.0
        samt = 0.0
    else:
        iamt = 0.0
        camt = round(txval * (rt / 200.0), 2)
        samt = round(txval * (rt / 200.0), 2)

    return {
        "txval": txval,
        "rt": rt,
        "camt": camt,
        "samt": samt,
        "iamt": iamt,
        "csamt": 0.0
    }


class SyntheticGSTGenerator:
    """Generates schema-valid GSTR-1 and GSTR-2B synthetic JSON payloads."""

    def __init__(self, supplier_gstin: str = "33AABCC1234Q1ZR"):
        self.supplier_gstin = supplier_gstin
        self.supplier_state = supplier_gstin[:2]

    def generate_gstr1(self, fp: str = "072026") -> Dict[str, Any]:
        """Generates a complete GSTR-1 JSON payload with B2B, CDNR, B2BA, and CDNRA."""
        
        # 1. Standard B2B Invoices
        b2b_records = []
        for i in range(4):
            buyer_state = random.choice(list(STATE_CODES.keys()))
            buyer_gstin = generate_gstin(buyer_state)
            trade_name = TRADE_NAMES[i % len(TRADE_NAMES)]
            is_interstate = (buyer_state != self.supplier_state)

            invoices = []
            for j in range(2):
                inum = f"INV-2026-{100 + i*10 + j}"
                idt = (datetime(2026, 7, 1) + timedelta(days=j*5)).strftime("%d-%m-%Y")
                
                # Alternate single vs multi-item invoices
                itms = []
                total_inv_val = 0.0
                
                if j == 0:
                    # Single 18% item
                    txval = round(random.uniform(5000, 25000) + random.random(), 2)
                    item_det = generate_item_detail(18.0, txval, is_interstate)
                    itms.append({"num": 1, "itm_det": item_det})
                    total_inv_val = txval + item_det["camt"] + item_det["samt"] + item_det["iamt"]
                else:
                    # Multi-item invoice (12% and 18%)
                    txval1 = round(random.uniform(2000, 8000), 2)
                    item_det1 = generate_item_detail(12.0, txval1, is_interstate)
                    itms.append({"num": 1, "itm_det": item_det1})

                    txval2 = round(random.uniform(4000, 12000), 2)
                    item_det2 = generate_item_detail(18.0, txval2, is_interstate)
                    itms.append({"num": 2, "itm_det": item_det2})

                    total_inv_val = txval1 + item_det1["camt"] + item_det1["samt"] + item_det1["iamt"] + \
                                    txval2 + item_det2["camt"] + item_det2["samt"] + item_det2["iamt"]

                invoices.append({
                    "inum": inum,
                    "idt": idt,
                    "val": round(total_inv_val, 2),
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

        # Edge Case Addition: Add 1 Zero-Value Invoice to test filtering logic
        b2b_records[0]["inv"].append({
            "inum": "INV-ZERO-999",
            "idt": "20-07-2026",
            "val": 0.00,
            "pos": self.supplier_state,
            "rchrg": "N",
            "inv_typ": "R",
            "itms": [{"num": 1, "itm_det": {"txval": 0.00, "rt": 18.0, "camt": 0.0, "samt": 0.0, "iamt": 0.0, "csamt": 0.0}}]
        })

        # 2. Credit and Debit Notes (CDNR)
        cdnr_records = []
        target_buyer_gstin = b2b_records[0]["ctin"]
        target_buyer_state = target_buyer_gstin[:2]
        is_interstate = (target_buyer_state != self.supplier_state)

        cdnr_records.append({
            "ctin": target_buyer_gstin,
            "c_name": b2b_records[0]["c_name"],
            "nt": [
                {
                    "nt_num": "CN-2026-001",
                    "nt_dt": "15-07-2026",
                    "ntty": "C",  # Credit Note
                    "val": 1180.00,
                    "itms": [{"num": 1, "itm_det": generate_item_detail(18.0, 1000.00, is_interstate)}]
                },
                {
                    "nt_num": "DN-2026-001",
                    "nt_dt": "18-07-2026",
                    "ntty": "D",  # Debit Note
                    "val": 2360.00,
                    "itms": [{"num": 1, "itm_det": generate_item_detail(18.0, 2000.00, is_interstate)}]
                }
            ]
        })

        # 3. Amended B2B Invoices (B2BA)
        b2ba_records = [{
            "ctin": generate_gstin("07"),
            "c_name": "Global Tech Services",
            "inv": [{
                "inum": "INV-2026-050-REV",
                "idt": "25-07-2026",
                "oinum": "INV-2026-050",
                "oidt": "05-07-2026",
                "val": 5900.00,
                "pos": "07",
                "rchrg": "N",
                "inv_typ": "R",
                "itms": [{"num": 1, "itm_det": generate_item_detail(18.0, 5000.00, True)}]
            }]
        }]

        # 4. Amended Credit/Debit Notes (CDNRA)
        cdnra_records = [{
            "ctin": target_buyer_gstin,
            "c_name": b2b_records[0]["c_name"],
            "nt": [{
                "nt_num": "CN-2026-001-REV",
                "nt_dt": "28-07-2026",
                "ont_num": "CN-2026-001",
                "ont_dt": "15-07-2026",
                "ntty": "C",
                "val": 1416.00,
                "itms": [{"num": 1, "itm_det": generate_item_detail(18.0, 1200.00, is_interstate)}]
            }]
        }]

        return {
            "gstin": self.supplier_gstin,
            "fp": fp,
            "version": "GST3.2",
            "hash": "synth_hash_987654321",
            "b2b": b2b_records,
            "cdnr": cdnr_records,
            "b2ba": b2ba_records,
            "cdnra": cdnra_records
        }

    def generate_gstr2b(self, gendt: str = "14-07-2026") -> Dict[str, Any]:
        """Generates a complete GSTR-2B JSON payload."""
        
        b2b_records = []
        for i in range(3):
            supplier_state = random.choice(list(STATE_CODES.keys()))
            supplier_gstin = generate_gstin(supplier_state)
            trade_name = TRADE_NAMES[i % len(TRADE_NAMES)]
            is_interstate = (supplier_state != self.supplier_state)

            txval = round(random.uniform(8000, 35000) + random.random(), 2)
            item_det = generate_item_detail(18.0, txval, is_interstate)
            total_val = round(txval + item_det["camt"] + item_det["samt"] + item_det["iamt"], 2)

            b2b_records.append({
                "ctin": supplier_gstin,
                "tradeName": trade_name,
                "inv": [{
                    "inum": f"PUR-2026-{500 + i}",
                    "dt": "05-07-2026",
                    "val": total_val,
                    "pos": self.supplier_state,
                    "itms": [{
                        "num": 1,
                        "txval": item_det["txval"],
                        "rt": item_det["rt"],
                        "camt": item_det["camt"],
                        "samt": item_det["samt"],
                        "iamt": item_det["iamt"]
                    }]
                }]
            })

        return {
            "gendt": gendt,
            "gstin": self.supplier_gstin,
            "version": "1.0",
            "docdata": {
                "b2b": b2b_records
            }
        }


# =====================================================================
# CLI EXECUTION & FILE GENERATION
# =====================================================================
if __name__ == "__main__":
    output_dir = "./synthetic_data"
    os.makedirs(output_dir, exist_ok=True)

    generator = SyntheticGSTGenerator(supplier_gstin="33AABCC1234Q1ZR")

    # Generate GSTR-1 File
    gstr1_data = generator.generate_gstr1(fp="072026")
    gstr1_path = os.path.join(output_dir, "GSTR1_2026-2027_072026.json")
    with open(gstr1_path, "w", encoding="utf-8") as f:
        json.dump(gstr1_data, f, indent=2)

    # Generate GSTR-2B File
    gstr2b_data = generator.generate_gstr2b(gendt="14-07-2026")
    gstr2b_path = os.path.join(output_dir, "GSTR2B_2026-2027_072026.json")
    with open(gstr2b_path, "w", encoding="utf-8") as f:
        json.dump(gstr2b_data, f, indent=2)

    print("=====================================================")
    print("      SYNTHETIC GST DATA GENERATION COMPLETE         ")
    print("=====================================================")
    print(f"1. Generated GSTR-1 File : {os.path.abspath(gstr1_path)}")
    print(f"2. Generated GSTR-2B File : {os.path.abspath(gstr2b_path)}")
    print("\nData Preview:")
    print(f" - GSTR-1 Invoices Parsed : {len(gstr1_data['b2b'])} B2B entities, {len(gstr1_data['cdnr'])} CDNR entities.")
    print(f" - GSTR-2B Invoices Parsed: {len(gstr2b_data['docdata']['b2b'])} B2B Purchase entities.")