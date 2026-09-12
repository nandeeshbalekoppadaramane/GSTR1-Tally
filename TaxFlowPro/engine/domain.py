from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

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
    period_label: Optional[str] = None  # which stored return period this came from -
                                         # needed to cancel exactly this period's vouchers
                                         # in Tally if the period is later deleted here.

def is_journal_doc(doc: "NormalizedDocument") -> bool:
    """ADVANCE* and RCM_SELF_INVOICE are synthetic Journal entries whose total_val is
    just the tax movement being recorded, not taxable_val + tax like a real invoice -
    the generic double-entry balance check doesn't apply to them, and they always net
    to zero by construction. Shared by server.py (discrepancy flagging) and
    xml_engine.py (XML generation) so the two can't silently disagree about which
    doc types are journal-only."""
    return doc.doc_type.startswith("ADVANCE") or doc.doc_type == "RCM_SELF_INVOICE"

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

def _extract_2b_tax_lines(inv: dict, is_debit: bool) -> List[TaxLine]:
    """GSTR-2B invoices/notes carry cgst/sgst/igst directly on the record (no itemized
    rate breakdown, and no 'rt' field at all) - the per-component rate is derived from
    each amount's ratio to the taxable value instead of being read off the JSON."""
    txval = float(inv.get("txval", 0.0))
    cgst = float(inv.get("cgst", 0.0))
    sgst = float(inv.get("sgst", 0.0))
    igst = float(inv.get("igst", 0.0))

    lines = []
    if cgst > 0:
        rt = round(cgst / txval * 100, 2) if txval else 0.0
        lines.append(TaxLine("CGST", rt, round(cgst, 2), is_debit=is_debit))
    if sgst > 0:
        rt = round(sgst / txval * 100, 2) if txval else 0.0
        lines.append(TaxLine("SGST", rt, round(sgst, 2), is_debit=is_debit))
    if igst > 0:
        rt = round(igst / txval * 100, 2) if txval else 0.0
        lines.append(TaxLine("IGST", rt, round(igst, 2), is_debit=is_debit))
    return lines

def _merge_same_invoice_documents(docs: List["NormalizedDocument"]) -> List["NormalizedDocument"]:
    """The GST portal occasionally reports one multi-rate invoice as several separate
    JSON entries under the same invoice number and GSTIN - one entry per rate slab -
    instead of one entry carrying multiple line items. Left alone, each becomes its
    own NormalizedDocument and therefore its own Tally voucher sharing the same
    VOUCHERNUMBER + VOUCHERTYPE - Tally would then silently ALTER (overwrite) the
    first with the second on import instead of combining them, quietly losing one
    rate's worth of tax.

    This merges any documents that share (doc_type, doc_number, party_gstin) - once
    both the invoice number AND the GSTIN match - into a single document, combining
    their tax lines and totals, so a multi-rate invoice always becomes exactly one
    voucher no matter how the portal chose to split it up. Only applies where GSTIN
    is a real identifier (registered-party sections): B2C/export/advance/RCM-journal
    rows all use an empty party_gstin by design and must never be merged just
    because two of them happen to share a synthetically-built document number."""
    result: List[NormalizedDocument] = []
    seen: Dict[Tuple[str, str, str], NormalizedDocument] = {}
    for doc in docs:
        if not doc.party_gstin:
            result.append(doc)
            continue
        key = (doc.doc_type, doc.doc_number, doc.party_gstin)
        existing = seen.get(key)
        if existing:
            existing.tax_lines.extend(doc.tax_lines)
            existing.taxable_val = round(existing.taxable_val + doc.taxable_val, 2)
            existing.total_val = round(existing.total_val + doc.total_val, 2)
        else:
            seen[key] = doc
            result.append(doc)
    return result

class GSTDataExtractor:
    """Extracts every voucher-relevant node (standard, large/unregistered, exports, and
    advance-tax sections, plus their amendments) from raw GSTR-1 JSON payloads."""

    @staticmethod
    def extract_documents(raw_json: Dict[str, Any], gstr_type: str) -> List[NormalizedDocument]:
        if gstr_type.upper() == "GSTR2B":
            return _merge_same_invoice_documents(GSTDataExtractor._extract_gstr2b_documents(raw_json))

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

        return _merge_same_invoice_documents(docs)

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

    # -----------------------------------------------------------------------
    # GSTR-2B (purchase-side ITC statement) - structurally different from
    # GSTR-1: invoices carry cgst/sgst/igst flat (no itemized rate breakdown),
    # field names differ (ntnum not nt_num, typ not ntty for notes), and three
    # GSTR-2B-only flags change how a record should post: imsStatus (Invoice
    # Management System action), itcavl (is ITC actually claimable), and rev
    # (reverse charge - the supplier charged no tax, recipient self-assesses it).
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_gstr2b_documents(raw_json: Dict[str, Any]) -> List[NormalizedDocument]:
        docs = []
        root = unwrap_gst_payload(raw_json)

        # 1. Standard B2B purchase invoices
        for b2b in root.get("b2b", []):
            gstin = b2b.get("ctin", "")
            party = b2b.get("trdnm") or "Party"
            for inv in b2b.get("inv", []):
                docs.extend(GSTDataExtractor._parse_2b_invoice(inv, gstin, party, "B2B"))

        # 2. Amended B2B purchase invoices
        for b2ba in root.get("b2ba", []):
            gstin = b2ba.get("ctin", "")
            party = b2ba.get("trdnm") or "Party"
            for inv in b2ba.get("inv", []):
                docs.extend(GSTDataExtractor._parse_2b_invoice(inv, gstin, party, "B2BA"))

        # 3. Credit & Debit Notes from registered suppliers
        for cdnr in root.get("cdnr", []):
            gstin = cdnr.get("ctin", "")
            party = cdnr.get("trdnm") or "Party"
            for note in cdnr.get("nt", []):
                docs.extend(GSTDataExtractor._parse_2b_note(note, gstin, party))

        return docs

    @staticmethod
    def _parse_2b_invoice(inv: dict, gstin: str, party: str, doc_type: str) -> List[NormalizedDocument]:
        """Returns 0, 1, or 2 documents for one GSTR-2B invoice line:
        - Rejected via IMS -> nothing, it isn't a valid purchase.
        - Reverse charge -> a plain purchase at taxable value (supplier charged no tax)
          plus a separate RCM self-invoice journal recognizing the self-assessed tax.
        - ITC not available -> a single purchase voucher for the FULL invoice value
          (tax folded into cost, since it isn't recoverable).
        - Otherwise -> a standard purchase voucher with claimable Input Tax lines."""
        if (inv.get("imsStatus") or "").upper() == "R":
            return []

        num = inv.get("inum")
        dt = format_tally_date(inv.get("dt") or "")
        val = round(float(inv.get("val", 0.0)), 2)
        if val == 0:
            return []

        txval = round(float(inv.get("txval", 0.0)), 2)
        is_rcm = (inv.get("rev") or "").upper() == "Y"
        itc_available = (inv.get("itcavl") or "").upper() != "N"

        results = []

        if is_rcm:
            purchase = NormalizedDocument(
                doc_type=f"{doc_type}_RCM", doc_number=num, doc_date=dt,
                party_gstin=gstin, party_name=party,
                total_val=txval, taxable_val=txval, tax_lines=[]
            )
            results.append(purchase)
            rcm_tax_lines = _extract_2b_tax_lines(inv, is_debit=True)
            if rcm_tax_lines:
                results.append(NormalizedDocument(
                    doc_type="RCM_SELF_INVOICE", doc_number=num, doc_date=dt,
                    party_gstin="", party_name="RCM Tax Self-Assessment",
                    total_val=round(sum(t.amount for t in rcm_tax_lines), 2),
                    taxable_val=txval, tax_lines=rcm_tax_lines
                ))
        elif not itc_available:
            results.append(NormalizedDocument(
                doc_type=f"{doc_type}_INELIGIBLE", doc_number=num, doc_date=dt,
                party_gstin=gstin, party_name=party,
                total_val=val, taxable_val=val, tax_lines=[]
            ))
        else:
            tax_lines = _extract_2b_tax_lines(inv, is_debit=True)
            results.append(NormalizedDocument(
                doc_type=doc_type, doc_number=num, doc_date=dt,
                party_gstin=gstin, party_name=party,
                total_val=val, taxable_val=txval, tax_lines=tax_lines
            ))

        if doc_type == "B2BA" and inv.get("oinum"):
            for doc in results:
                doc.original_doc_num = inv.get("oinum")
                doc.original_doc_date = format_tally_date(inv.get("oidt"))

        return results

    @staticmethod
    def _parse_2b_note(note: dict, gstin: str, party: str) -> List[NormalizedDocument]:
        """Purchase-side credit/debit note. A credit note reduces the purchase and
        reverses (debits) the ITC already claimed on the original invoice; a debit
        note increases both. Reverse-charge and ITC-ineligible notes fold their tax
        into the note value instead of touching an Input Tax ledger, matching how
        the originating invoice would have been posted."""
        if (note.get("imsStatus") or "").upper() == "R":
            return []

        num = note.get("ntnum")
        dt = format_tally_date(note.get("dt") or "")
        val = round(float(note.get("val", 0.0)), 2)
        if val == 0:
            return []

        note_type = (note.get("typ") or "C").upper()  # C = Credit, D = Debit
        doc_type = "CDNR_CREDIT" if note_type == "C" else "CDNR_DEBIT"
        # Purchase-side: a credit note reverses (credits) previously-claimed ITC; a debit
        # note increases (debits) it - opposite of the sales-side convention.
        tax_is_debit = (note_type == "D")

        is_rcm = (note.get("rev") or "").upper() == "Y"
        itc_available = (note.get("itcavl") or "").upper() != "N"

        if is_rcm or not itc_available:
            return [NormalizedDocument(
                doc_type=doc_type, doc_number=num, doc_date=dt,
                party_gstin=gstin, party_name=party,
                total_val=val, taxable_val=val, tax_lines=[]
            )]

        txval = round(float(note.get("txval", 0.0)), 2)
        tax_lines = _extract_2b_tax_lines(note, is_debit=tax_is_debit)
        return [NormalizedDocument(
            doc_type=doc_type, doc_number=num, doc_date=dt,
            party_gstin=gstin, party_name=party,
            total_val=val, taxable_val=txval, tax_lines=tax_lines
        )]
