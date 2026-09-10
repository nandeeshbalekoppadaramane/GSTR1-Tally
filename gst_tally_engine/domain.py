from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class TaxLine:
    tax_type: str  # CGST, SGST, IGST
    rate: float
    amount: float
    is_debit: bool

@dataclass
class NormalizedDocument:
    doc_type: str  # B2B, CDNR_CREDIT, CDNR_DEBIT, B2BA, CDNRA_CREDIT, CDNRA_DEBIT
    doc_number: str
    doc_date: str
    party_gstin: str
    party_name: str
    total_val: float
    taxable_val: float
    tax_lines: List[TaxLine] = field(default_factory=list)
    original_doc_num: Optional[str] = None
    original_doc_date: Optional[str] = None

def format_tally_date(date_str: str) -> str:
    """Formats DD-MM-YYYY, DD/MM/YYYY, or YYYY-MM-DD into YYYYMMDD for Tally."""
    if not date_str:
        return "20260401"
    clean = str(date_str).strip().replace("/", "-")
    parts = clean.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 4:  # YYYY-MM-DD
            return f"{parts[0]}{parts[1].zfill(2)}{parts[2].zfill(2)}"
        elif len(parts[2]) == 4:  # DD-MM-YYYY
            return f"{parts[2]}{parts[1].zfill(2)}{parts[0].zfill(2)}"
    return clean.replace("-", "")

def unwrap_gst_payload(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """Unwraps nested GST portal envelopes like 'data', 'docdata', 'cpsumm'."""
    curr = raw_json
    if isinstance(curr, dict) and "data" in curr and isinstance(curr["data"], dict):
        curr = curr["data"]
    if isinstance(curr, dict) and "docdata" in curr and isinstance(curr["docdata"], dict):
        curr = curr["docdata"]
    return curr

class GSTDataExtractor:
    """Extracts B2B, CDNR, B2BA, and CDNRA nodes from raw GST portal JSON payloads."""

    @staticmethod
    def extract_documents(raw_json: Dict[str, Any], gstr_type: str) -> List[NormalizedDocument]:
        docs = []
        is_sales = (gstr_type.upper() == "GSTR1")
        
        # Unwrap all outer envelopes (data, docdata, etc.)
        root = unwrap_gst_payload(raw_json)

        # 1. Standard B2B Invoices
        for b2b in root.get("b2b", []):
            gstin = b2b.get("ctin", "")
            party = b2b.get("tradeName") or b2b.get("trdnm") or b2b.get("c_name") or "Party"
            for inv in b2b.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, gstin, party, "B2B", is_sales)
                if doc:
                    docs.append(doc)

        # 2. Credit & Debit Notes (CDNR)
        for cdnr in root.get("cdnr", []):
            gstin = cdnr.get("ctin", "")
            party = cdnr.get("tradeName") or cdnr.get("trdnm") or cdnr.get("c_name") or "Party"
            notes = cdnr.get("nt", []) or cdnr.get("notes", [])
            for note in notes:
                doc = GSTDataExtractor._parse_note(note, gstin, party, "CDNR", is_sales)
                if doc:
                    docs.append(doc)

        # 3. Amended B2B Invoices (B2BA)
        for b2ba in root.get("b2ba", []):
            gstin = b2ba.get("ctin", "")
            party = b2ba.get("tradeName") or b2ba.get("trdnm") or b2ba.get("c_name") or "Party"
            for inv in b2ba.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, gstin, party, "B2BA", is_sales)
                if doc:
                    doc.original_doc_num = inv.get("oinum")
                    doc.original_doc_date = format_tally_date(inv.get("oidt"))
                    docs.append(doc)

        # 4. Amended Credit & Debit Notes (CDNRA)
        for cdnra in root.get("cdnra", []):
            gstin = cdnra.get("ctin", "")
            party = cdnra.get("tradeName") or cdnra.get("trdnm") or cdnra.get("c_name") or "Party"
            notes = cdnra.get("nt", []) or cdnra.get("notes", [])
            for note in notes:
                doc = GSTDataExtractor._parse_note(note, gstin, party, "CDNRA", is_sales)
                if doc:
                    doc.original_doc_num = note.get("ont_num") or note.get("oinum")
                    doc.original_doc_date = format_tally_date(note.get("ont_dt") or note.get("oidt"))
                    docs.append(doc)

        # 5. Consolidated B2C Small Supplies (B2CS)
        if is_sales and "b2cs" in root:
            fp = str(root.get("fp", ""))
            doc_date = "20250430"
            if len(fp) == 6:
                mm = int(fp[:2])
                yyyy = int(fp[2:])
                import calendar
                _, last_day = calendar.monthrange(yyyy, mm)
                doc_date = f"{yyyy}{str(mm).zfill(2)}{str(last_day).zfill(2)}"

            b2c_idx = 1
            for b2c in root.get("b2cs", []):
                txval = float(b2c.get("txval", 0.0))
                if txval == 0:
                    continue
                rt = float(b2c.get("rt", 0.0))
                camt = float(b2c.get("camt", 0.0))
                samt = float(b2c.get("samt", 0.0))
                iamt = float(b2c.get("iamt", 0.0))
                pos = str(b2c.get("pos", "33"))
                
                val = round(txval + camt + samt + iamt)
                tax_lines = []
                if camt > 0:
                    tax_lines.append(TaxLine("CGST", rt / 2, round(camt), is_debit=False))
                if samt > 0:
                    tax_lines.append(TaxLine("SGST", rt / 2, round(samt), is_debit=False))
                if iamt > 0:
                    tax_lines.append(TaxLine("IGST", rt, round(iamt), is_debit=False))

                doc_num = f"B2CS/{fp}/{pos}/{int(rt)}%-{b2c_idx}" if fp else f"B2CS/{pos}/{int(rt)}%-{b2c_idx}"
                b2c_idx += 1

                docs.append(NormalizedDocument(
                    doc_type="B2CS",
                    doc_number=doc_num,
                    doc_date=doc_date,
                    party_gstin="",
                    party_name="B2C Sales",
                    total_val=val,
                    taxable_val=round(txval),
                    tax_lines=tax_lines
                ))

        return docs

    @staticmethod
    def _parse_invoice(inv: dict, gstin: str, party: str, doc_type: str, is_sales: bool) -> Optional[NormalizedDocument]:
        num = inv.get("inum") or inv.get("num")
        dt = format_tally_date(inv.get("idt") or inv.get("dt") or "")
        val = round(float(inv.get("val", 0.0)))
        
        if val == 0:
            return None

        tax_lines = []
        total_txval = 0.0

        for item in inv.get("itms", []):
            det = item.get("itm_det") or item
            txval = float(det.get("txval", 0.0))
            rt = float(det.get("rt", 0.0))
            camt = float(det.get("camt", 0.0))
            samt = float(det.get("samt", 0.0))
            iamt = float(det.get("iamt", 0.0))

            total_txval += txval

            if camt > 0:
                tax_lines.append(TaxLine("CGST", rt / 2, round(camt), is_debit=not is_sales))
            if samt > 0:
                tax_lines.append(TaxLine("SGST", rt / 2, round(samt), is_debit=not is_sales))
            if iamt > 0:
                tax_lines.append(TaxLine("IGST", rt, round(iamt), is_debit=not is_sales))

        return NormalizedDocument(
            doc_type=doc_type,
            doc_number=num,
            doc_date=dt,
            party_gstin=gstin,
            party_name=party,
            total_val=val,
            taxable_val=round(total_txval),
            tax_lines=tax_lines
        )

    @staticmethod
    def _parse_note(note: dict, gstin: str, party: str, base_type: str, is_sales: bool) -> Optional[NormalizedDocument]:
        num = note.get("nt_num") or note.get("inum")
        dt = format_tally_date(note.get("nt_dt") or note.get("dt") or "")
        val = round(float(note.get("val", 0.0)))
        note_type = note.get("ntty", "C")

        if val == 0:
            return None

        doc_type = f"{base_type}_CREDIT" if note_type == "C" else f"{base_type}_DEBIT"
        tax_is_debit = (note_type == "C") if is_sales else (note_type == "D")

        tax_lines = []
        total_txval = 0.0

        for item in note.get("itms", []):
            det = item.get("itm_det") or item
            txval = float(det.get("txval", 0.0))
            rt = float(det.get("rt", 0.0))
            camt = float(det.get("camt", 0.0))
            samt = float(det.get("samt", 0.0))
            iamt = float(det.get("iamt", 0.0))

            total_txval += txval

            if camt > 0:
                tax_lines.append(TaxLine("CGST", rt / 2, round(camt), is_debit=tax_is_debit))
            if samt > 0:
                tax_lines.append(TaxLine("SGST", rt / 2, round(samt), is_debit=tax_is_debit))
            if iamt > 0:
                tax_lines.append(TaxLine("IGST", rt, round(iamt), is_debit=tax_is_debit))

        return NormalizedDocument(
            doc_type=doc_type,
            doc_number=num,
            doc_date=dt,
            party_gstin=gstin,
            party_name=party,
            total_val=val,
            taxable_val=round(total_txval),
            tax_lines=tax_lines
        )