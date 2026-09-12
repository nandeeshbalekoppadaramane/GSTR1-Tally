import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List, Tuple
from domain import NormalizedDocument, is_journal_doc
from constants import format_party_ledger, get_state_name, get_party_legal_name, get_party_trade_name, get_name_preference

ADVANCE_LIABILITY_LEDGER = "Advance Receipt - GST Liability A/c"
RCM_LIABILITY_LEDGER = "GST Payable under RCM A/c"

class TallyXMLEngine:
    """Generates Tally-compliant Masters and Entries XML payloads using native ElementTree."""

    @staticmethod
    def generate_masters_xml(documents: List[NormalizedDocument], gstr_type: str) -> str:
        envelope = ET.Element("ENVELOPE")
        header = ET.SubElement(envelope, "HEADER")
        ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
        
        body = ET.SubElement(envelope, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")
        req_desc = ET.SubElement(import_data, "REQUESTDESC")
        ET.SubElement(req_desc, "REPORTNAME").text = "All Masters"
        req_data = ET.SubElement(import_data, "REQUESTDATA")

        is_sales = (gstr_type.upper() == "GSTR1")
        party_parent = "Sundry Debtors" if is_sales else "Sundry Creditors"
        
        created_parties = set()
        created_taxes = set()

        # Voucher Types alteration to ensure Tally preserves doc_number from JSON instead of auto-incrementing
        vch_types = ["Sales", "Credit Note", "Debit Note", "Journal"] if is_sales else ["Purchase", "Debit Note", "Credit Note", "Journal"]
        for vt_name in vch_types:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            vt = ET.SubElement(msg, "VOUCHERTYPE", {"ACTION": "Alter", "NAME": vt_name})
            ET.SubElement(vt, "NAME").text = vt_name
            ET.SubElement(vt, "NUMBERINGMETHOD").text = "Manual"
            series_list = ET.SubElement(vt, "VOUCHERNUMBERSERIES.LIST")
            ET.SubElement(series_list, "NAME").text = "Default"
            ET.SubElement(series_list, "NUMBERINGMETHOD").text = "Manual"

        # Base Accounts required by vouchers
        base_accounts = [
            ("Sales A/c", "Sales Accounts") if is_sales else ("Purchase A/c", "Purchase Accounts"),
            ("Sales Returns A/c", "Sales Accounts") if is_sales else ("Purchase Returns A/c", "Purchase Accounts"),
            ("Round Off A/c", "Indirect Expenses")
        ]
        if is_sales and any(d.doc_type.startswith("EXP") for d in documents):
            base_accounts.append(("Export Sales A/c", "Sales Accounts"))
        if is_sales and any(d.doc_type.startswith("ADVANCE") for d in documents):
            base_accounts.append((ADVANCE_LIABILITY_LEDGER, "Current Liabilities"))
        if not is_sales and any(d.doc_type == "RCM_SELF_INVOICE" for d in documents):
            base_accounts.append((RCM_LIABILITY_LEDGER, "Duties & Taxes"))

        for ac_name, parent_group in base_accounts:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            ledger = ET.SubElement(msg, "LEDGER", {"ACTION": "Create", "NAME": ac_name})
            ET.SubElement(ledger, "NAME").text = ac_name
            ET.SubElement(ledger, "PARENT").text = parent_group

        for doc in documents:
            # Advance-tax entries post only against the liability ledger + tax ledgers
            # (no counterparty is reported for these sections) - skip party master creation.
            if is_journal_doc(doc):
                for tax in doc.tax_lines:
                    prefix = "Output" if is_sales else "Input"
                    tax_ledger_name = f"{prefix} {tax.tax_type} {tax.rate}% A/c"
                    if tax_ledger_name not in created_taxes:
                        msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
                        ledger = ET.SubElement(msg, "LEDGER", {"ACTION": "Create", "NAME": tax_ledger_name})
                        ET.SubElement(ledger, "NAME").text = tax_ledger_name
                        ET.SubElement(ledger, "PARENT").text = "Duties & Taxes"
                        ET.SubElement(ledger, "TAXTYPE").text = "GST"
                        ET.SubElement(ledger, "GSTDUTYHEAD").text = tax.tax_type
                        ET.SubElement(ledger, "PERCENTAGEOFCALCULATION").text = str(tax.rate)
                        created_taxes.add(tax_ledger_name)
                continue

            party_ledger = format_party_ledger(doc.party_name, doc.party_gstin)

            # Party Ledger Master
            if party_ledger not in created_parties:
                msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
                ledger = ET.SubElement(msg, "LEDGER", {"ACTION": "Create", "NAME": party_ledger})
                ET.SubElement(ledger, "NAME").text = party_ledger
                ET.SubElement(ledger, "PARENT").text = party_parent
                if doc.party_gstin:
                    ET.SubElement(ledger, "PARTYGSTIN").text = doc.party_gstin
                    ET.SubElement(ledger, "LEDSTATENAME").text = get_state_name(doc.party_gstin)
                    ET.SubElement(ledger, "ISBILLWISEON").text = "Yes"
                    pref = get_name_preference()
                    mailing_name = get_party_trade_name(doc.party_gstin) if pref == "TRADE" else get_party_legal_name(doc.party_gstin)
                    if mailing_name:
                        mailing_list = ET.SubElement(ledger, "MAILINGNAME.LIST", {"TYPE": "String"})
                        ET.SubElement(mailing_list, "MAILINGNAME").text = mailing_name
                else:
                    ET.SubElement(ledger, "ISBILLWISEON").text = "No"
                created_parties.add(party_ledger)

            # Tax Ledger Masters
            for tax in doc.tax_lines:
                prefix = "Output" if is_sales else "Input"
                tax_ledger_name = f"{prefix} {tax.tax_type} {tax.rate}% A/c"
                
                if tax_ledger_name not in created_taxes:
                    msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
                    ledger = ET.SubElement(msg, "LEDGER", {"ACTION": "Create", "NAME": tax_ledger_name})
                    ET.SubElement(ledger, "NAME").text = tax_ledger_name
                    ET.SubElement(ledger, "PARENT").text = "Duties & Taxes"
                    ET.SubElement(ledger, "TAXTYPE").text = "GST"
                    ET.SubElement(ledger, "GSTDUTYHEAD").text = tax.tax_type
                    ET.SubElement(ledger, "PERCENTAGEOFCALCULATION").text = str(tax.rate)
                    created_taxes.add(tax_ledger_name)

        return TallyXMLEngine._prettify(envelope)

    MAX_ROUNDOFF_TOLERANCE = 2.00

    @staticmethod
    def generate_entries_xml(documents: List[NormalizedDocument], gstr_type: str) -> str:
        envelope = ET.Element("ENVELOPE")
        header = ET.SubElement(envelope, "HEADER")
        ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
        
        body = ET.SubElement(envelope, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")
        req_desc = ET.SubElement(import_data, "REQUESTDESC")
        ET.SubElement(req_desc, "REPORTNAME").text = "Vouchers"
        req_data = ET.SubElement(import_data, "REQUESTDATA")

        is_sales = (gstr_type.upper() == "GSTR1")

        # Configure Voucher Types to Manual numbering before importing vouchers,
        # ensuring that even if transactions are imported standalone, Tally preserves
        # the exact invoice number (doc_number) and never overrides it with auto-increment.
        vch_types = ["Sales", "Credit Note", "Debit Note", "Journal"] if is_sales else ["Purchase", "Debit Note", "Credit Note", "Journal"]
        for vt_name in vch_types:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            vt = ET.SubElement(msg, "VOUCHERTYPE", {"ACTION": "Alter", "NAME": vt_name})
            ET.SubElement(vt, "NAME").text = vt_name
            ET.SubElement(vt, "NUMBERINGMETHOD").text = "Manual"
            series_list = ET.SubElement(vt, "VOUCHERNUMBERSERIES.LIST")
            ET.SubElement(series_list, "NAME").text = "Default"
            ET.SubElement(series_list, "NUMBERINGMETHOD").text = "Manual"

        for doc in documents:
            if doc.doc_type.startswith("ADVANCE"):
                msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
                TallyXMLEngine._build_advance_voucher(msg, doc, gstr_type, is_sales)
                continue

            if doc.doc_type == "RCM_SELF_INVOICE":
                msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
                TallyXMLEngine._build_rcm_voucher(msg, doc, gstr_type)
                continue

            # Calculate discrepancy: |total_val - (taxable_val + tax_sum)|
            tax_sum = sum(t.amount for t in doc.tax_lines)
            discrepancy = round(abs(doc.total_val - (doc.taxable_val + tax_sum)), 2)

            # If discrepancy exceeds normal round-off threshold (> ₹2.00), exclude from XML export
            if discrepancy > TallyXMLEngine.MAX_ROUNDOFF_TOLERANCE:
                continue

            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})

            # Resolve Voucher Type & Accounting Directions
            vch_type, party_is_dr, base_is_dr, base_ledger = TallyXMLEngine._resolve_voucher_rules(doc.doc_type, is_sales)
            party_ledger = format_party_ledger(doc.party_name, doc.party_gstin)

            vch = ET.SubElement(msg, "VOUCHER", {"ACTION": "Create", "VCHTYPE": vch_type})
            ET.SubElement(vch, "DATE").text = doc.doc_date
            ET.SubElement(vch, "VOUCHERTYPENAME").text = vch_type
            ET.SubElement(vch, "VOUCHERNUMBER").text = doc.doc_number
            ET.SubElement(vch, "REFERENCE").text = doc.doc_number
            ET.SubElement(vch, "PARTYLEDGERNAME").text = party_ledger

            # Build Narration String
            narration = f"Auto-imported [{gstr_type}] {doc.doc_type} Doc #{doc.doc_number}"
            if doc.original_doc_num:
                narration += f" (Amended - Original Doc #{doc.original_doc_num}, Dated: {doc.original_doc_date})"
            ET.SubElement(vch, "NARRATION").text = narration

            # 1. Party Line (Tally XML Sign Convention: Debit = Negative, Credit = Positive)
            p_amount = -doc.total_val if party_is_dr else doc.total_val
            p_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(p_entry, "LEDGERNAME").text = party_ledger
            ET.SubElement(p_entry, "ISDEEMEDPOSITIVE").text = "Yes" if party_is_dr else "No"
            ET.SubElement(p_entry, "AMOUNT").text = f"{p_amount:.2f}"
            if doc.party_gstin:
                ba = ET.SubElement(p_entry, "BILLALLOCATIONS.LIST")
                # Amendments (B2BA/CDNRA/...) carry the prior document's number from
                # the portal (original_doc_num) - referencing it as "Agst Ref" nets
                # this voucher against that existing bill instead of opening a second,
                # unlinked one for the same invoice. A plain (non-amended) document has
                # no such reference in the GST data, so it correctly opens a new bill.
                if doc.original_doc_num:
                    ET.SubElement(ba, "NAME").text = doc.original_doc_num
                    ET.SubElement(ba, "BILLTYPE").text = "Agst Ref"
                else:
                    ET.SubElement(ba, "NAME").text = doc.doc_number
                    ET.SubElement(ba, "BILLTYPE").text = "New Ref"
                ET.SubElement(ba, "AMOUNT").text = f"{p_amount:.2f}"

            # 2. Revenue / Expense Line
            b_amount = -doc.taxable_val if base_is_dr else doc.taxable_val
            b_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(b_entry, "LEDGERNAME").text = base_ledger
            ET.SubElement(b_entry, "ISDEEMEDPOSITIVE").text = "Yes" if base_is_dr else "No"
            ET.SubElement(b_entry, "AMOUNT").text = f"{b_amount:.2f}"

            # 3. Tax Lines
            tax_amounts_sum = 0.0
            for tax in doc.tax_lines:
                prefix = "Output" if is_sales else "Input"
                tax_ledger_name = f"{prefix} {tax.tax_type} {tax.rate}% A/c"
                t_amount = -tax.amount if tax.is_debit else tax.amount
                tax_amounts_sum += t_amount
                
                t_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
                ET.SubElement(t_entry, "LEDGERNAME").text = tax_ledger_name
                ET.SubElement(t_entry, "ISDEEMEDPOSITIVE").text = "Yes" if tax.is_debit else "No"
                ET.SubElement(t_entry, "AMOUNT").text = f"{t_amount:.2f}"

            # 4. Round Off Line (Guarantees double-entry balance: Sum of all amounts == 0.0)
            net_balance = round(p_amount + b_amount + tax_amounts_sum, 2)
            round_off = round(-net_balance, 2)
            if abs(round_off) >= 0.01:
                ro_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
                ET.SubElement(ro_entry, "LEDGERNAME").text = "Round Off A/c"
                ET.SubElement(ro_entry, "ISDEEMEDPOSITIVE").text = "Yes" if round_off < 0 else "No"
                ET.SubElement(ro_entry, "AMOUNT").text = f"{round_off:.2f}"

        return TallyXMLEngine._prettify(envelope)

    @staticmethod
    def list_voucher_identities(documents: List[NormalizedDocument], gstr_type: str) -> List[dict]:
        """Mirrors generate_entries_xml's own type-resolution and discrepancy-skip
        logic to report exactly which vouchers a push actually put into Tally -
        {period_label, vch_type, vch_number, vch_date} per voucher. Used to record
        what was pushed, so a later period deletion can cancel precisely those
        vouchers in Tally instead of leaving them there as orphans."""
        is_sales = (gstr_type.upper() == "GSTR1")
        identities = []
        for doc in documents:
            if is_journal_doc(doc):
                vch_type = "Journal"
            else:
                tax_sum = sum(t.amount for t in doc.tax_lines)
                discrepancy = round(abs(doc.total_val - (doc.taxable_val + tax_sum)), 2)
                if discrepancy > TallyXMLEngine.MAX_ROUNDOFF_TOLERANCE:
                    continue
                vch_type, _, _, _ = TallyXMLEngine._resolve_voucher_rules(doc.doc_type, is_sales)
            identities.append({
                "period_label": doc.period_label,
                "vch_type": vch_type,
                "vch_number": doc.doc_number,
                "vch_date": doc.doc_date,
            })
        return identities

    @staticmethod
    def generate_cancel_xml(voucher_entries: List[dict]) -> str:
        """Builds a Tally import XML that cancels each given voucher (identified by
        VOUCHERTYPENAME + VOUCHERNUMBER + DATE, the same triplet Tally already uses to
        match Create/Alter - no GUID tracking exists in this tool). A cancelled voucher
        stays in Tally's register marked void, preserving the audit trail, rather than
        being silently removed."""
        envelope = ET.Element("ENVELOPE")
        header = ET.SubElement(envelope, "HEADER")
        ET.SubElement(header, "TALLYREQUEST").text = "Import Data"

        body = ET.SubElement(envelope, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")
        req_desc = ET.SubElement(import_data, "REQUESTDESC")
        ET.SubElement(req_desc, "REPORTNAME").text = "Vouchers"
        req_data = ET.SubElement(import_data, "REQUESTDATA")

        for v in voucher_entries:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            vch = ET.SubElement(msg, "VOUCHER", {"ACTION": "Cancel"})
            ET.SubElement(vch, "DATE").text = v.get("vch_date") or ""
            ET.SubElement(vch, "VOUCHERTYPENAME").text = v["vch_type"]
            ET.SubElement(vch, "VOUCHERNUMBER").text = v["vch_number"]

        return TallyXMLEngine._prettify(envelope)

    @staticmethod
    def _resolve_voucher_rules(doc_type: str, is_sales: bool) -> Tuple[str, bool, bool, str]:
        """Resolves Tally voucher type, party line polarity, base line polarity, and base ledger."""
        if "CREDIT" in doc_type:
            vch_type = "Credit Note"
            party_is_dr = not is_sales
            base_is_dr = is_sales
            base_ledger = "Sales Returns A/c" if is_sales else "Purchase Returns A/c"
        elif "DEBIT" in doc_type:
            vch_type = "Debit Note"
            party_is_dr = is_sales
            base_is_dr = not is_sales
            base_ledger = "Sales A/c" if is_sales else "Purchase A/c"
        elif doc_type.startswith("EXP"):
            # EXP_WPAY / EXP_WOPAY / EXPA_WPAY / EXPA_WOPAY - zero-rated export sales
            vch_type = "Sales"
            party_is_dr = True
            base_is_dr = False
            base_ledger = "Export Sales A/c"
        else:
            # Standard B2B / B2BA / B2CL / B2CLA / B2CS / B2CSA
            vch_type = "Sales" if is_sales else "Purchase"
            party_is_dr = is_sales
            base_is_dr = not is_sales
            base_ledger = "Sales A/c" if is_sales else "Purchase A/c"

        return vch_type, party_is_dr, base_is_dr, base_ledger

    @staticmethod
    def _build_advance_voucher(msg: ET.Element, doc: NormalizedDocument, gstr_type: str, is_sales: bool):
        """Advance-tax sections (at/ata/txpd/txpda) have no counterparty and no sales
        value of their own - only the GST computed on the advance is posted, as a
        Journal entry against a dedicated liability ledger."""
        is_adjustment = "ADJUSTMENT" in doc.doc_type
        tax_total = round(sum(t.amount for t in doc.tax_lines), 2)

        vch = ET.SubElement(msg, "VOUCHER", {"ACTION": "Create", "VCHTYPE": "Journal"})
        ET.SubElement(vch, "DATE").text = doc.doc_date
        ET.SubElement(vch, "VOUCHERTYPENAME").text = "Journal"
        ET.SubElement(vch, "VOUCHERNUMBER").text = doc.doc_number
        ET.SubElement(vch, "REFERENCE").text = doc.doc_number

        narration = f"Auto-imported [{gstr_type}] {doc.doc_type} - GST on advance receipt"
        if is_adjustment:
            narration = f"Auto-imported [{gstr_type}] {doc.doc_type} - advance tax adjusted against invoice"
        ET.SubElement(vch, "NARRATION").text = narration

        # Liability ledger: Dr when tax is first recognized on the advance, Cr when reversed on adjustment.
        liability_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
        ET.SubElement(liability_entry, "LEDGERNAME").text = ADVANCE_LIABILITY_LEDGER
        ET.SubElement(liability_entry, "ISDEEMEDPOSITIVE").text = "No" if is_adjustment else "Yes"
        liab_amt = tax_total if is_adjustment else -tax_total
        ET.SubElement(liability_entry, "AMOUNT").text = f"{liab_amt:.2f}"

        for tax in doc.tax_lines:
            prefix = "Output" if is_sales else "Input"
            tax_ledger_name = f"{prefix} {tax.tax_type} {tax.rate}% A/c"
            t_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(t_entry, "LEDGERNAME").text = tax_ledger_name
            ET.SubElement(t_entry, "ISDEEMEDPOSITIVE").text = "Yes" if tax.is_debit else "No"
            t_amt = -tax.amount if tax.is_debit else tax.amount
            ET.SubElement(t_entry, "AMOUNT").text = f"{t_amt:.2f}"

    @staticmethod
    def _build_rcm_voucher(msg: ET.Element, doc: NormalizedDocument, gstr_type: str):
        """Reverse-charge purchases (rev='Y' in GSTR-2B) carry no tax from the supplier -
        the recipient self-assesses and claims it. This posts that self-invoice as a
        Journal: Dr Input CGST/SGST/IGST (claiming the credit), Cr the RCM liability
        ledger (the cash/challan payment against that liability is a separate,
        manual Tally entry outside this tool's scope)."""
        tax_total = round(sum(t.amount for t in doc.tax_lines), 2)

        vch = ET.SubElement(msg, "VOUCHER", {"ACTION": "Create", "VCHTYPE": "Journal"})
        ET.SubElement(vch, "DATE").text = doc.doc_date
        ET.SubElement(vch, "VOUCHERTYPENAME").text = "Journal"
        ET.SubElement(vch, "VOUCHERNUMBER").text = doc.doc_number
        ET.SubElement(vch, "REFERENCE").text = doc.doc_number
        ET.SubElement(vch, "NARRATION").text = (
            f"Auto-imported [{gstr_type}] RCM self-invoice - GST self-assessed under reverse charge, Doc #{doc.doc_number}"
        )

        for tax in doc.tax_lines:
            tax_ledger_name = f"Input {tax.tax_type} {tax.rate}% A/c"
            t_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(t_entry, "LEDGERNAME").text = tax_ledger_name
            ET.SubElement(t_entry, "ISDEEMEDPOSITIVE").text = "Yes"
            ET.SubElement(t_entry, "AMOUNT").text = f"{-tax.amount:.2f}"

        liability_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
        ET.SubElement(liability_entry, "LEDGERNAME").text = RCM_LIABILITY_LEDGER
        ET.SubElement(liability_entry, "ISDEEMEDPOSITIVE").text = "No"
        ET.SubElement(liability_entry, "AMOUNT").text = f"{tax_total:.2f}"

    @staticmethod
    def _prettify(elem: ET.Element) -> str:
        ET.indent(elem, space="  ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(elem, encoding="unicode")