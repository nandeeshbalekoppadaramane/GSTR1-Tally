import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List, Tuple
from domain import NormalizedDocument
from constants import format_party_ledger, get_state_name, get_party_legal_name, get_party_trade_name, get_name_preference

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

        # Base Accounts required by vouchers
        base_accounts = [
            ("Sales A/c", "Sales Accounts") if is_sales else ("Purchase A/c", "Purchase Accounts"),
            ("Sales Returns A/c", "Sales Accounts") if is_sales else ("Purchase Returns A/c", "Purchase Accounts"),
            ("Round Off A/c", "Indirect Expenses")
        ]
        for ac_name, parent_group in base_accounts:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            ledger = ET.SubElement(msg, "LEDGER", {"ACTION": "Create", "NAME": ac_name})
            ET.SubElement(ledger, "NAME").text = ac_name
            ET.SubElement(ledger, "PARENT").text = parent_group

        for doc in documents:
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
                    mailing_name = get_party_legal_name(doc.party_gstin) if pref == "TRADE" else get_party_trade_name(doc.party_gstin)
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

        for doc in documents:
            msg = ET.SubElement(req_data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
            
            # Resolve Voucher Type & Accounting Directions
            vch_type, party_is_dr, base_is_dr, base_ledger = TallyXMLEngine._resolve_voucher_rules(doc.doc_type, is_sales)
            party_ledger = format_party_ledger(doc.party_name, doc.party_gstin)

            vch = ET.SubElement(msg, "VOUCHER", {"ACTION": "Create", "VCHTYPE": vch_type})
            ET.SubElement(vch, "DATE").text = doc.doc_date
            ET.SubElement(vch, "VOUCHERTYPENAME").text = vch_type
            ET.SubElement(vch, "VOUCHERNUMBER").text = doc.doc_number
            ET.SubElement(vch, "PARTYLEDGERNAME").text = party_ledger

            # Build Narration String
            narration = f"Auto-imported [{gstr_type}] {doc.doc_type} Doc #{doc.doc_number}"
            if doc.original_doc_num:
                narration += f" (Amended - Original Doc #{doc.original_doc_num}, Dated: {doc.original_doc_date})"
            ET.SubElement(vch, "NARRATION").text = narration

            # 1. Party Line (Tally XML Sign Convention: Debit = Negative, Credit = Positive)
            p_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(p_entry, "LEDGERNAME").text = party_ledger
            ET.SubElement(p_entry, "ISDEEMEDPOSITIVE").text = "Yes" if party_is_dr else "No"
            ET.SubElement(p_entry, "AMOUNT").text = str(-doc.total_val if party_is_dr else doc.total_val)

            # 2. Revenue / Expense Line
            b_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(b_entry, "LEDGERNAME").text = base_ledger
            ET.SubElement(b_entry, "ISDEEMEDPOSITIVE").text = "Yes" if base_is_dr else "No"
            ET.SubElement(b_entry, "AMOUNT").text = str(-doc.taxable_val if base_is_dr else doc.taxable_val)

            # 3. Tax Lines
            for tax in doc.tax_lines:
                prefix = "Output" if is_sales else "Input"
                tax_ledger_name = f"{prefix} {tax.tax_type} {tax.rate}% A/c"
                
                t_entry = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")
                ET.SubElement(t_entry, "LEDGERNAME").text = tax_ledger_name
                ET.SubElement(t_entry, "ISDEEMEDPOSITIVE").text = "Yes" if tax.is_debit else "No"
                ET.SubElement(t_entry, "AMOUNT").text = str(-tax.amount if tax.is_debit else tax.amount)

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
        else:
            # Standard B2B / B2BA
            vch_type = "Sales" if is_sales else "Purchase"
            party_is_dr = is_sales
            base_is_dr = not is_sales
            base_ledger = "Sales A/c" if is_sales else "Purchase A/c"

        return vch_type, party_is_dr, base_is_dr, base_ledger

    @staticmethod
    def _prettify(elem: ET.Element) -> str:
        ET.indent(elem, space="  ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(elem, encoding="unicode")