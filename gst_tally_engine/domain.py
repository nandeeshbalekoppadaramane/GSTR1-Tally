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
    doc_type: str  # B2B, B2BA, B2CL, B2CLA, B2CS, B2CSA, CDNR_CREDIT/DEBIT, CDNRA_CREDIT/DEBIT,
                   # CDNUR_CREDIT/DEBIT, CDNURA_CREDIT/DEBIT, EXP_WPAY/WOPAY, EXPA_WPAY/WOPAY,
                   # ADVANCE, ADVANCE_AMENDED, ADVANCE_ADJUSTMENT, ADVANCE_ADJUSTMENT_AMENDED
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

def fy_end_date(fp: str) -> str:
    """Resolves a GST filing period 'MMYYYY' to that month's last calendar day, YYYYMMDD."""
    fp = str(fp or "")
    if len(fp) == 6:
        import calendar
        mm = int(fp[:2])
        yyyy = int(fp[2:])
        _, last_day = calendar.monthrange(yyyy, mm)
        return f"{yyyy}{str(mm).zfill(2)}{str(last_day).zfill(2)}"
    return "20260430"

def unwrap_gst_payload(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """Unwraps nested GST portal envelopes like 'data', 'docdata', 'cpsumm'."""
    curr = raw_json
    if isinstance(curr, dict) and "data" in curr and isinstance(curr["data"], dict):
        curr = curr["data"]
    if isinstance(curr, dict) and "docdata" in curr and isinstance(curr["docdata"], dict):
        curr = curr["docdata"]
    return curr

def _extract_tax_lines(det: dict, is_debit: bool) -> List[TaxLine]:
    """Reads camt/samt/iamt off a rate-detail node and builds non-zero TaxLine entries."""
    rt = float(det.get("rt", 0.0))
    camt = float(det.get("camt", 0.0))
    samt = float(det.get("samt", 0.0))
    iamt = float(det.get("iamt", 0.0))

    lines = []
    if camt > 0:
        lines.append(TaxLine("CGST", rt / 2, round(camt, 2), is_debit=is_debit))
    if samt > 0:
        lines.append(TaxLine("SGST", rt / 2, round(samt, 2), is_debit=is_debit))
    if iamt > 0:
        lines.append(TaxLine("IGST", rt, round(iamt, 2), is_debit=is_debit))
    return lines

class GSTDataExtractor:
    """Extracts every voucher-relevant node (standard, large/unregistered, exports, and
    advance-tax sections, plus their amendments) from raw GSTR-1 JSON payloads."""

    @staticmethod
    def extract_documents(raw_json: Dict[str, Any], gstr_type: str) -> List[NormalizedDocument]:
        docs = []
        is_sales = (gstr_type.upper() == "GSTR1")

        # Unwrap all outer envelopes (data, docdata, etc.)
        root = unwrap_gst_payload(raw_json)
        fp = str(root.get("fp", ""))

        # 1. Standard B2B Invoices
        for b2b in root.get("b2b", []):
            gstin = b2b.get("ctin", "")
            party = b2b.get("tradeName") or b2b.get("trdnm") or b2b.get("c_name") or "Party"
            for inv in b2b.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, gstin, party, "B2B", is_sales)
                if doc:
                    docs.append(doc)

        # 2. Amended B2B Invoices (B2BA)
        for b2ba in root.get("b2ba", []):
            gstin = b2ba.get("ctin", "")
            party = b2ba.get("tradeName") or b2ba.get("trdnm") or b2ba.get("c_name") or "Party"
            for inv in b2ba.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, gstin, party, "B2BA", is_sales)
                if doc:
                    doc.original_doc_num = inv.get("oinum")
                    doc.original_doc_date = format_tally_date(inv.get("oidt"))
                    docs.append(doc)

        # 3. Credit & Debit Notes (CDNR) - registered counterparties
        for cdnr in root.get("cdnr", []):
            gstin = cdnr.get("ctin", "")
            party = cdnr.get("tradeName") or cdnr.get("trdnm") or cdnr.get("c_name") or "Party"
            notes = cdnr.get("nt", []) or cdnr.get("notes", [])
            for note in notes:
                doc = GSTDataExtractor._parse_note(note, gstin, party, "CDNR", is_sales)
                if doc:
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

        # 5. B2C Large - single interstate invoice >2.5L to an unregistered buyer
        for b2cl in root.get("b2cl", []):
            pos = str(b2cl.get("pos", ""))
            for inv in b2cl.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, "", "B2C Large Sales", "B2CL", is_sales)
                if doc:
                    docs.append(doc)

        # 6. Amended B2C Large (B2CLA)
        for b2cla in root.get("b2cla", []):
            for inv in b2cla.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, "", "B2C Large Sales", "B2CLA", is_sales)
                if doc:
                    doc.original_doc_num = inv.get("oinum")
                    doc.original_doc_date = format_tally_date(inv.get("oidt"))
                    docs.append(doc)

        # 7. Credit/Debit Notes against Unregistered buyers (B2CL / export reversals) -
        # unlike CDNR, each cdnur row IS a note directly (no nested "nt" wrapper).
        for note in root.get("cdnur", []):
            doc = GSTDataExtractor._parse_note(note, "", "B2C Large Sales", "CDNUR", is_sales)
            if doc:
                docs.append(doc)

        # 8. Amended CDNUR
        for note in root.get("cdnura", []):
            doc = GSTDataExtractor._parse_note(note, "", "B2C Large Sales", "CDNURA", is_sales)
            if doc:
                doc.original_doc_num = note.get("ont_num") or note.get("oinum")
                doc.original_doc_date = format_tally_date(note.get("ont_dt") or note.get("oidt"))
                docs.append(doc)

        # 9. Exports (zero-rated; IGST only present when filed "with payment of tax")
        for exp in root.get("exp", []):
            exp_typ = (exp.get("exp_typ") or "").upper()  # WPAY or WOPAY
            for inv in exp.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, "", "Export Sales", f"EXP_{exp_typ or 'WOPAY'}", is_sales)
                if doc:
                    docs.append(doc)

        # 10. Amended Exports
        for expa in root.get("expa", []):
            exp_typ = (expa.get("exp_typ") or "").upper()
            for inv in expa.get("inv", []):
                doc = GSTDataExtractor._parse_invoice(inv, "", "Export Sales", f"EXPA_{exp_typ or 'WOPAY'}", is_sales)
                if doc:
                    doc.original_doc_num = inv.get("oinum")
                    doc.original_doc_date = format_tally_date(inv.get("oidt"))
                    docs.append(doc)

        # 11. Consolidated B2C Small Supplies (B2CS)
        if is_sales and "b2cs" in root:
            docs.extend(GSTDataExtractor._parse_b2cs_slabs(root.get("b2cs", []), fp, "B2CS"))

        # 12. Amended B2C Small Supplies (B2CSA)
        if is_sales and "b2csa" in root:
            docs.extend(GSTDataExtractor._parse_b2cs_slabs(root.get("b2csa", []), fp, "B2CSA"))

        # 13. Advances Received (tax paid on advance before the invoice is raised)
        if is_sales and "at" in root:
            docs.extend(GSTDataExtractor._parse_advance_slabs(root.get("at", []), fp, "ADVANCE"))

        # 14. Amended Advances Received
        if is_sales and "ata" in root:
            docs.extend(GSTDataExtractor._parse_advance_slabs(root.get("ata", []), fp, "ADVANCE_AMENDED"))

        # 15. Advance Adjusted against a later invoice (reverses the ADVANCE liability)
        if is_sales and "txpd" in root:
            docs.extend(GSTDataExtractor._parse_advance_slabs(root.get("txpd", []), fp, "ADVANCE_ADJUSTMENT"))

        # 16. Amended Advance Adjustment
        if is_sales and "txpda" in root:
            docs.extend(GSTDataExtractor._parse_advance_slabs(root.get("txpda", []), fp, "ADVANCE_ADJUSTMENT_AMENDED"))

        return docs

    @staticmethod
    def _parse_invoice(inv: dict, gstin: str, party: str, doc_type: str, is_sales: bool) -> Optional[NormalizedDocument]:
        num = inv.get("inum") or inv.get("num")
        dt = format_tally_date(inv.get("idt") or inv.get("dt") or "")
        val = round(float(inv.get("val", 0.0)), 2)

        if val == 0:
            return None

        tax_lines = []
        total_txval = 0.0

        for item in inv.get("itms", []):
            det = item.get("itm_det") or item
            txval = float(det.get("txval", 0.0))
            total_txval += txval
            tax_lines.extend(_extract_tax_lines(det, is_debit=not is_sales))

        return NormalizedDocument(
            doc_type=doc_type,
            doc_number=num,
            doc_date=dt,
            party_gstin=gstin,
            party_name=party,
            total_val=val,
            taxable_val=round(total_txval, 2),
            tax_lines=tax_lines
        )

    @staticmethod
    def _parse_note(note: dict, gstin: str, party: str, base_type: str, is_sales: bool) -> Optional[NormalizedDocument]:
        num = note.get("nt_num") or note.get("inum")
        dt = format_tally_date(note.get("nt_dt") or note.get("dt") or "")
        val = round(float(note.get("val", 0.0)), 2)
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
            total_txval += txval
            tax_lines.extend(_extract_tax_lines(det, is_debit=tax_is_debit))

        return NormalizedDocument(
            doc_type=doc_type,
            doc_number=num,
            doc_date=dt,
            party_gstin=gstin,
            party_name=party,
            total_val=val,
            taxable_val=round(total_txval, 2),
            tax_lines=tax_lines
        )

    @staticmethod
    def _parse_b2cs_slabs(entries: list, fp: str, doc_type: str) -> List[NormalizedDocument]:
        """B2CS/B2CSA carry one row per (state, rate) slab rather than per invoice -
        each row becomes its own consolidated document."""
        doc_date = fy_end_date(fp)
        docs = []
        idx = 1
        for row in entries:
            det = row.get("itm_det") or row
            txval = float(det.get("txval", 0.0))
            if txval == 0:
                continue
            pos = str(row.get("pos", "33"))
            rt = float(det.get("rt", 0.0))
            tax_lines = _extract_tax_lines(det, is_debit=False)
            val = round(txval + sum(t.amount for t in tax_lines), 2)

            doc_num = f"{doc_type}/{fp}/{pos}/{int(rt)}%-{idx}" if fp else f"{doc_type}/{pos}/{int(rt)}%-{idx}"
            idx += 1

            docs.append(NormalizedDocument(
                doc_type=doc_type,
                doc_number=doc_num,
                doc_date=doc_date,
                party_gstin="",
                party_name="B2C Sales",
                total_val=val,
                taxable_val=round(txval, 2),
                tax_lines=tax_lines
            ))
        return docs

    @staticmethod
    def _parse_advance_slabs(entries: list, fp: str, doc_type: str) -> List[NormalizedDocument]:
        """at/ata carry one row per (state, rate) slab of advance tax received - the GST
        is a new liability (tax lines credited). txpd/txpda carry the same shape for tax
        adjusted back out once the real invoice is raised - there the liability reverses
        (tax lines debited). Neither section reports an invoice number or counterparty."""
        doc_date = fy_end_date(fp)
        is_adjustment = "ADJUSTMENT" in doc_type
        docs = []
        idx = 1
        for row in entries:
            det = row.get("itm_det") or row
            base_amt = float(det.get("ad_amt", det.get("txval", 0.0)))
            if base_amt == 0:
                continue
            pos = str(row.get("pos", "33"))
            rt = float(det.get("rt", 0.0))
            tax_lines = _extract_tax_lines(det, is_debit=is_adjustment)
            val = round(base_amt + sum(t.amount for t in tax_lines), 2)

            doc_num = f"{doc_type}/{fp}/{pos}/{int(rt)}%-{idx}" if fp else f"{doc_type}/{pos}/{int(rt)}%-{idx}"
            idx += 1

            docs.append(NormalizedDocument(
                doc_type=doc_type,
                doc_number=doc_num,
                doc_date=doc_date,
                party_gstin="",
                party_name="Advance Receipt",
                total_val=val,
                taxable_val=round(base_amt, 2),
                tax_lines=tax_lines
            ))
        return docs
