import os
import sys
import json
import re
import csv
import zipfile
import tempfile
import shutil
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Request, UploadFile, File, Form, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add this app's own folders to sys.path - only meaningful running from source; a
# PyInstaller build already has every module analyzed and bundled, and there is no
# on-disk TaxFlowPro/engine tree next to the frozen exe to point at.
SRC_ROOT = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    ENGINE_DIR = SRC_ROOT / "engine"
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))

import client_db
import domain
from domain import GSTDataExtractor, NormalizedDocument, TaxLine
from xml_engine import TallyXMLEngine
from constants import (
    reload_party_mappings,
    set_name_preference,
    get_name_preference,
    get_party_trade_name,
    get_party_legal_name,
    get_state_name,
    format_party_ledger
)
from app_paths import get_data_root, get_bundle_dir

# Writable data lives next to the .exe in a frozen build, so it survives restarts
# instead of being written into PyInstaller's temp extraction folder.
DATA_ROOT = get_data_root()
OUTPUT_DIR = DATA_ROOT / "output_xml"
CSV_PATH = DATA_ROOT / "party_mappings.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client_db.init_client_db()

app = FastAPI(
    title="GSTR-1 to TallyPrime Automation Hub",
    description="Full-stack web application with Client Management, GSTR-1 Ingestion, and TallyPrime XML Exporter.",
    version="2.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": f"Internal Server Error: {str(exc)}"}
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

# Global in-memory cache. GSTR-1 (sales) and GSTR-2B (purchases) are two independent
# datasets for the same client - same GSTIN, opposite sides of the ledger, different
# document types - so each return type gets its own slot rather than sharing one list.
class ReturnStore:
    def __init__(self):
        self.documents: List[NormalizedDocument] = []
        self.monthly_stats: List[Dict[str, Any]] = []
        self.financial_totals: Dict[str, Any] = {}
        self.is_loaded: bool = False

class DataStore:
    def __init__(self):
        self._stores = {"GSTR1": ReturnStore(), "GSTR2B": ReturnStore()}
        self.name_preference: str = "trade"

    def get(self, return_type: Optional[str]) -> ReturnStore:
        return self._stores.get((return_type or "GSTR1").upper(), self._stores["GSTR1"])

store = DataStore()

def _doc_to_row(doc: NormalizedDocument) -> Dict[str, Any]:
    """Serializes a NormalizedDocument for persistence in client_invoice_documents."""
    return {
        "doc_type": doc.doc_type,
        "doc_number": doc.doc_number,
        "doc_date": doc.doc_date,
        "party_gstin": doc.party_gstin,
        "party_name": doc.party_name,
        "total_val": doc.total_val,
        "taxable_val": doc.taxable_val,
        "tax_lines": [
            {"tax_type": t.tax_type, "rate": t.rate, "amount": t.amount, "is_debit": t.is_debit}
            for t in doc.tax_lines
        ],
        "original_doc_num": doc.original_doc_num,
        "original_doc_date": doc.original_doc_date
    }

def _row_to_doc(row: Dict[str, Any]) -> NormalizedDocument:
    """Reconstructs a NormalizedDocument from a stored DB row."""
    return NormalizedDocument(
        doc_type=row["doc_type"],
        doc_number=row["doc_number"],
        doc_date=row["doc_date"],
        party_gstin=row["party_gstin"],
        party_name=row["party_name"],
        total_val=row["total_val"],
        taxable_val=row["taxable_val"],
        tax_lines=[TaxLine(**tl) for tl in (row.get("tax_lines") or [])],
        original_doc_num=row.get("original_doc_num"),
        original_doc_date=row.get("original_doc_date"),
        period_label=row.get("period_label")
    )

def _doc_discrepancy(d: NormalizedDocument) -> float:
    """abs(total_val - (taxable_val + tax)) for a real invoice/note; always 0 for a
    journal-only document, which isn't held to that formula."""
    if domain.is_journal_doc(d):
        return 0.0
    return round(abs(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines))), 2)

def _month_stat(label: str, docs: List[NormalizedDocument], return_type: str = "GSTR1", filename: str = "") -> Dict[str, Any]:
    """Builds one row of the monthly summary table from a list of already-extracted documents.
    Sales (GSTR1) and purchases (GSTR2B) get different document-type breakdown columns -
    B2C/Exports/Advances only exist on the sales side; RCM/Ineligible-ITC only on purchases."""
    is_sales = (return_type or "GSTR1").upper() == "GSTR1"
    taxable = sum(d.taxable_val for d in docs)
    inv_val = sum(d.total_val for d in docs)
    cgst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "CGST")
    sgst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "SGST")
    igst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "IGST")
    parties = {d.party_gstin for d in docs if d.party_gstin}

    stat = {
        "label": label,
        "filename": filename,
        "total_docs": len(docs),
        "b2b_count": sum(1 for d in docs if d.doc_type in ("B2B", "B2BA")),
        "cdnr_count": sum(1 for d in docs if "CREDIT" in d.doc_type or "DEBIT" in d.doc_type),
        "parties_count": len(parties),
        "taxable_val": round(taxable, 2),
        "cgst_val": round(cgst, 2),
        "sgst_val": round(sgst, 2),
        "igst_val": round(igst, 2),
        "total_val": round(inv_val, 2)
    }
    if is_sales:
        stat["b2c_count"] = sum(1 for d in docs if d.doc_type in ("B2CS", "B2CSA", "B2CL", "B2CLA"))
        stat["exp_count"] = sum(1 for d in docs if d.doc_type.startswith("EXP"))
        stat["advance_count"] = sum(1 for d in docs if d.doc_type.startswith("ADVANCE"))
    else:
        stat["rcm_count"] = sum(1 for d in docs if d.doc_type in ("B2B_RCM", "B2BA_RCM", "RCM_SELF_INVOICE"))
        stat["ineligible_count"] = sum(1 for d in docs if d.doc_type in ("B2B_INELIGIBLE", "B2BA_INELIGIBLE"))
    return stat

def _empty_totals(return_type: str = "GSTR1") -> Dict[str, Any]:
    is_sales = (return_type or "GSTR1").upper() == "GSTR1"
    totals = {
        "total_documents": 0, "total_b2b": 0, "total_cdnr": 0,
        "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
        "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
        "gross_invoiced_value": 0.0, "months_count": 0
    }
    if is_sales:
        totals.update({"total_b2c": 0, "total_exp": 0, "total_advance": 0})
    else:
        totals.update({"total_rcm": 0, "total_ineligible": 0})
    return totals

def reload_dataset(client_id: Optional[int] = None, return_type: str = "GSTR1") -> Dict[str, Any]:
    """Rebuilds the in-memory store for one return type (GSTR1=sales, GSTR2B=purchases)
    of the active client, strictly from persisted DB records. Raw uploaded JSON is never
    read from or written to disk - it's parsed once at upload time and only the extracted
    documents are stored."""
    return_type = (return_type or "GSTR1").upper()
    rstore = store.get(return_type)

    if client_id is None:
        client_id = client_db.get_active_client_id()

    cl = client_db.get_client(client_id) if client_id else None
    if not cl:
        rstore.documents = []
        rstore.monthly_stats = []
        rstore.financial_totals = _empty_totals(return_type)
        rstore.is_loaded = True
        return rstore.financial_totals

    reload_party_mappings()

    stored_rows = client_db.get_invoice_documents(client_id, return_type)

    by_period: Dict[str, List[Dict[str, Any]]] = {}
    for row in stored_rows:
        by_period.setdefault(row["period_label"], []).append(row)

    all_docs: List[NormalizedDocument] = []
    month_stats = []
    for label in sorted(by_period.keys(), key=get_month_sort_key):
        docs = [_row_to_doc(r) for r in by_period[label]]
        all_docs.extend(docs)
        month_stats.append(_month_stat(label, docs, return_type))

    all_docs.sort(key=lambda d: d.doc_date)

    tot_taxable = sum(d.taxable_val for d in all_docs)
    tot_val = sum(d.total_val for d in all_docs)
    tot_cgst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "CGST")
    tot_sgst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "SGST")
    tot_igst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "IGST")
    all_parties = {d.party_gstin for d in all_docs if d.party_gstin}

    totals = {
        "total_documents": len(all_docs),
        "total_b2b": sum(m["b2b_count"] for m in month_stats),
        "total_cdnr": sum(m["cdnr_count"] for m in month_stats),
        "unique_parties": len(all_parties),
        "taxable_turnover": round(tot_taxable, 2),
        "total_cgst": round(tot_cgst, 2),
        "total_sgst": round(tot_sgst, 2),
        "total_igst": round(tot_igst, 2),
        "total_tax": round(tot_cgst + tot_sgst + tot_igst, 2),
        "gross_invoiced_value": round(tot_val, 2),
        "months_count": len(month_stats)
    }
    if return_type == "GSTR1":
        totals["total_b2c"] = sum(m.get("b2c_count", 0) for m in month_stats)
        totals["total_exp"] = sum(m.get("exp_count", 0) for m in month_stats)
        totals["total_advance"] = sum(m.get("advance_count", 0) for m in month_stats)
    else:
        totals["total_rcm"] = sum(m.get("rcm_count", 0) for m in month_stats)
        totals["total_ineligible"] = sum(m.get("ineligible_count", 0) for m in month_stats)

    rstore.documents = all_docs
    rstore.monthly_stats = month_stats
    rstore.financial_totals = totals
    rstore.is_loaded = True

    # Sync into Client DB
    try:
        if client_id and client_db.get_client(client_id):
            client_db.sync_return_periods_for_client(client_id, month_stats, return_type)
    except Exception as e:
        print(f"[WARN] Could not sync return periods: {e}")

    return rstore.financial_totals

def _reload_both(client_id: Optional[int] = None) -> Dict[str, Any]:
    """Reloads both the sales (GSTR1) and purchase (GSTR2B) datasets for a client -
    used whenever the active client changes, since either tab could be viewed next."""
    sales_totals = reload_dataset(client_id, "GSTR1")
    reload_dataset(client_id, "GSTR2B")
    return sales_totals

def _clear_both_stores():
    """Resets both in-memory stores to an empty, fully-loaded state (no active client)."""
    for rt in ("GSTR1", "GSTR2B"):
        rstore = store.get(rt)
        rstore.documents = []
        rstore.monthly_stats = []
        rstore.financial_totals = _empty_totals(rt)
        rstore.is_loaded = True

def query_tally_company(endpoint: str = "http://127.0.0.1:9000") -> dict:
    """Checks TallyPrime connection and active company name."""
    xml_req = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Companies</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
    </DESC>
  </BODY>
</ENVELOPE>"""
    try:
        resp = requests.post(endpoint, data=xml_req.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=1.5)
        if resp.status_code == 200 and "<COMPANY" in resp.text:
            m = re.search(r'<COMPANY\s+NAME="([^"]+)"', resp.text)
            comp_name = m.group(1) if m else "Open Company"
            return {"online": True, "company": comp_name, "endpoint": endpoint}
        return {"online": False, "company": None, "endpoint": endpoint, "error": "No open company detected."}
    except Exception as e:
        return {"online": False, "company": None, "endpoint": endpoint, "error": str(e)}

# ---------------------------------------------------------------------------
# Client Management API Endpoints
# ---------------------------------------------------------------------------
class ClientCreateRequest(BaseModel):
    name: str
    trade_name: Optional[str] = None
    gstin: str
    pan: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tally_company_name: Optional[str] = None
    tally_company_id: Optional[str] = None
    default_name_preference: Optional[str] = "trade"
    status: Optional[str] = "active"
    notes: Optional[str] = None
    gst_username: Optional[str] = None
    gst_password: Optional[str] = None
    financial_year: Optional[str] = None

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    trade_name: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    tally_company_name: Optional[str] = None
    tally_company_id: Optional[str] = None
    default_name_preference: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    gst_username: Optional[str] = None
    gst_password: Optional[str] = None
    financial_year: Optional[str] = None

@app.get("/api/clients")
def get_clients_list(search: Optional[str] = None, status: Optional[str] = None):
    """Returns list of all clients with return statistics."""
    return {
        "clients": client_db.list_clients(search, status),
        "active_client_id": client_db.get_active_client_id()
    }

@app.post("/api/clients")
def create_new_client(req: ClientCreateRequest):
    """Creates a new client in the database with auto PAN and State extraction."""
    try:
        new_cl = client_db.create_client(req.dict())
        # If this is the only client, set as active
        if client_db.get_active_client_id() is None:
            client_db.set_active_client_id(new_cl["id"])
            reload_dataset(new_cl["id"])
        return {"status": "success", "client": new_cl}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}")
def get_client_detail(client_id: int):
    """Fetches details for a single client."""
    cl = client_db.get_client(client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client not found.")
    return cl

@app.put("/api/clients/{client_id}")
def update_client_detail(client_id: int, req: ClientUpdateRequest):
    """Updates client record."""
    try:
        clean_data = {k: v for k, v in req.dict().items() if v is not None}
        updated = client_db.update_client(client_id, clean_data)
        return {"status": "success", "client": updated}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/clients/{client_id}")
@app.post("/api/clients/{client_id}/delete")
def delete_client_record(client_id: int):
    """Deletes a client profile and its return period records."""
    cl = client_db.get_client(client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client not found.")

    was_active = (client_id == client_db.get_active_client_id())
    gstin = cl.get("gstin", "").strip().upper()

    # Delete from database (cascades return periods)
    ok = client_db.delete_client(client_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete client from database.")

    # If active client was deleted, switch context or clear store
    new_active_id = client_db.get_active_client_id()
    if was_active:
        if new_active_id:
            _reload_both(new_active_id)
        else:
            _clear_both_stores()

    new_active_client = client_db.get_client(new_active_id) if new_active_id else None

    return {
        "status": "success",
        "message": f"Client '{cl['name']}' ({gstin}) deleted successfully.",
        "deleted_client_id": client_id,
        "new_active_client": new_active_client
    }

@app.post("/api/clients/deselect")
@app.post("/api/clients/active/clear")
def clear_active_client_selection():
    """Clears the active client session and unloads client dataset."""
    client_db.set_active_client_id(None)
    _clear_both_stores()
    return {"status": "success", "message": "Active client session cleared."}

@app.post("/api/clients/{client_id}/select")
def set_active_client(client_id: int):
    """Sets the active client context and reloads both sales and purchase data."""
    try:
        cl = client_db.set_active_client_id(client_id)
        if cl.get("default_name_preference"):
            set_name_preference(cl["default_name_preference"])
            store.name_preference = cl["default_name_preference"]
        totals = _reload_both(client_id)
        return {"status": "success", "active_client": cl, "totals": totals}
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))

@app.get("/api/clients/active/current")
def get_active_client_info():
    """Returns currently selected active client."""
    active_id = client_db.get_active_client_id()
    if not active_id:
        return {}
    cl = client_db.get_client(active_id)
    return cl or {}

@app.get("/api/clients/lookup/gstin/{gstin}")
def lookup_gstin_details(gstin: str):
    """Utility endpoint: validates 15-char GSTIN and extracts PAN & State."""
    clean = re.sub(r'[^A-Za-z0-9]', '', gstin or "").upper()
    pan = client_db.extract_pan_from_gstin(clean)
    state_code, state_name = client_db.extract_state_from_gstin(clean)
    valid_format = bool(re.match(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$', clean))
    return {
        "gstin": clean,
        "is_valid_format": valid_format,
        "pan": pan,
        "state_code": state_code,
        "state_name": state_name
    }

@app.post("/api/db/clear")
def clear_all_db_records():
    """Wipes all data across all tables in SQLite database and resets autoincrement."""
    counts = client_db.clear_all_db_data()
    _clear_both_stores()
    return {
        "status": "success",
        "message": "All database records have been deleted.",
        "deleted_records": counts
    }

# ---------------------------------------------------------------------------
# General GSTR-1 & Tally API Routes
# ---------------------------------------------------------------------------
@app.get("/api/status")
def get_status(endpoint: str = "http://127.0.0.1:9000"):
    """Returns Tally connection status, active client, and dataset status."""
    tally_info = query_tally_company(endpoint)
    active_id = client_db.get_active_client_id()
    active_cl = client_db.get_client(active_id) if active_id else None
    return {
        "tally": tally_info,
        "active_client": active_cl,
        "data_loaded": store.get("GSTR1").is_loaded and store.get("GSTR2B").is_loaded,
        "total_docs": len(store.get("GSTR1").documents),
        "total_purchase_docs": len(store.get("GSTR2B").documents),
        "name_preference": store.name_preference
    }

@app.get("/api/overview")
def get_overview(return_type: str = "GSTR1"):
    """Returns overall financial KPIs, active client, and monthly breakdown for one
    return type: GSTR1 (sales) or GSTR2B (purchases)."""
    return_type = (return_type or "GSTR1").upper()
    active_id = client_db.get_active_client_id()
    if not active_id:
        return {
            "active_client": None,
            "totals": _empty_totals(return_type),
            "monthly_stats": [],
            "name_preference": store.name_preference,
            "return_type": return_type
        }

    rstore = store.get(return_type)
    if not rstore.is_loaded:
        reload_dataset(active_id, return_type)
    active_cl = client_db.get_client(active_id)
    return {
        "active_client": active_cl,
        "totals": rstore.financial_totals,
        "monthly_stats": rstore.monthly_stats,
        "name_preference": store.name_preference,
        "return_type": return_type
    }

@app.get("/api/parties")
def get_parties(search: Optional[str] = None):
    """Returns the union of counterparties from BOTH sales and purchases for the active
    client - a GSTIN the client sells to and also buys from shows up once, tagged as both."""
    active_id = client_db.get_active_client_id()
    if not active_id:
        return {
            "total_parties": 0,
            "name_preference": store.name_preference,
            "parties": []
        }

    for rt in ("GSTR1", "GSTR2B"):
        if not store.get(rt).is_loaded:
            reload_dataset(active_id, rt)

    # Derive counterparties from both datasets, tagging each GSTIN as customer/supplier/both
    party_docs: Dict[str, List[NormalizedDocument]] = {}
    party_relation: Dict[str, set] = {}
    for rt, relation in (("GSTR1", "Customer"), ("GSTR2B", "Supplier")):
        for d in store.get(rt).documents:
            if d.party_gstin:
                party_docs.setdefault(d.party_gstin, []).append(d)
                party_relation.setdefault(d.party_gstin, set()).add(relation)

    # One bulk query for every party's mapping instead of one query per party (N+1).
    all_mappings = client_db.get_party_mappings_bulk(list(party_docs.keys()))

    parties_list = []
    pref = store.name_preference
    for idx, (gstin, docs) in enumerate(sorted(party_docs.items())):
        sample_doc = docs[0]
        db_map = all_mappings.get(gstin)
        if db_map:
            trade_name = db_map.get("trade_name") or "NA"
            legal_name = db_map.get("legal_name") or trade_name
            status = db_map.get("status") or "Active"
            center_jur = db_map.get("center_jurisdiction") or ""
            state_jur = db_map.get("state_jurisdiction") or ""
            is_verified = True
        else:
            csv_trade = get_party_trade_name(gstin)
            csv_legal = get_party_legal_name(gstin)
            doc_name = sample_doc.party_name if (sample_doc.party_name and sample_doc.party_name.upper() not in ("PARTY", "NA", "N/A", "NONE", "-")) else ""
            trade_name = csv_trade or doc_name or "NA"
            legal_name = csv_legal or doc_name or (trade_name if trade_name != "NA" else "NA")
            status = "Unverified"
            center_jur = ""
            state_jur = ""
            is_verified = bool(csv_trade)

        state_name = get_state_name(gstin)
        state_code = gstin[:2] if len(gstin) >= 2 else ""
        formatted_name = format_party_ledger(trade_name, gstin, pref)
        relations = party_relation.get(gstin, set())
        relation_label = "Both" if len(relations) > 1 else (next(iter(relations)) if relations else "Customer")

        parties_list.append({
            "gstin": gstin,
            "trade_name": trade_name,
            "legal_name": legal_name,
            "state_code": state_code,
            "state_name": state_name,
            "ledger_name": formatted_name,
            "total_docs": len(docs),
            "is_verified": is_verified,
            "status": status,
            "center_jurisdiction": center_jur,
            "state_jurisdiction": state_jur,
            "has_custom_mapping": bool(db_map),
            "relation": relation_label
        })

    if search:
        s = search.lower().strip()
        parties_list = [
            p for p in parties_list 
            if s in p["gstin"].lower() or s in p["trade_name"].lower() or s in p["legal_name"].lower() or s in p["state_name"].lower()
        ]

    return {
        "total_parties": len(parties_list),
        "name_preference": store.name_preference,
        "parties": parties_list
    }

class PortalVerifyStartRequest(BaseModel):
    gstins: Optional[List[str]] = None
    mode: Optional[str] = "counterparties"
    gst_username: Optional[str] = None
    gst_password: Optional[str] = None
    save_credentials: Optional[bool] = True

@app.post("/api/gstin/verify-portal/start")
def start_portal_verification(req: Optional[PortalVerifyStartRequest] = None):
    """Launches Chrome to GST login, waits for user login, and verifies GSTINs permanently."""
    import portal_verifier
    gstin_list = []
    active_id = client_db.get_active_client_id()
    active_cl = client_db.get_client(active_id) if active_id else None

    # Resolve credentials
    req_username = (req.gst_username.strip() if req and req.gst_username else None)
    req_password = (req.gst_password.strip() if req and req.gst_password else None)

    # If save_credentials requested and active client exists, persist to client profile
    if req and req.save_credentials and active_cl:
        cred_update = {}
        if req_username is not None:
            cred_update["gst_username"] = req_username
        if req_password is not None:
            cred_update["gst_password"] = req_password
        if cred_update:
            client_db.update_client(active_cl["id"], cred_update)
            active_cl = client_db.get_client(active_id)

    # Use supplied or saved credentials
    final_username = req_username or (active_cl.get("gst_username") if active_cl else None)
    final_password = req_password or (active_cl.get("gst_password") if active_cl else None)

    if req and req.gstins:
        gstin_list = req.gstins
    elif req and req.mode == "active_client":
        if active_cl and active_cl.get("gstin"):
            gstin_list = [active_cl["gstin"]]
    elif req and req.mode == "unverified":
        if active_id:
            _reload_both(active_id)
        all_gstins = sorted(list({d.party_gstin for rt in ("GSTR1", "GSTR2B") for d in store.get(rt).documents if d.party_gstin}))
        unverified = []
        for g in all_gstins:
            mp = client_db.get_party_mapping(g)
            if not mp or mp.get("status") != "Active" or not mp.get("trade_name"):
                unverified.append(g)
        gstin_list = unverified
        if not gstin_list:
            raise HTTPException(
                status_code=400,
                detail="All counterparties for this client are already verified! Choose 'All Counterparties' if you wish to re-verify them."
            )
    else:  # "counterparties", "all", or default
        if active_id:
            _reload_both(active_id)
        gstin_list = sorted(list({d.party_gstin for rt in ("GSTR1", "GSTR2B") for d in store.get(rt).documents if d.party_gstin}))

    if not gstin_list:
        raise HTTPException(status_code=400, detail="No GSTINs found to verify. Please upload returns or specify GSTIN.")

    started = portal_verifier.verifier_service.start(
        gstin_list,
        gst_username=final_username,
        gst_password=final_password
    )
    if not started:
        state = portal_verifier.verifier_service.get_state()
        detail = state.get("error") or "A verification session is already in progress."
        raise HTTPException(status_code=409, detail=detail)

    return {
        "status": "started",
        "total": len(gstin_list),
        "gstins": gstin_list,
        "auto_credentials": bool(final_username and final_password)
    }

@app.get("/api/gstin/verify-portal/status")
def get_portal_verification_status():
    """Polls real-time verification progress, terminal logs, and results."""
    import portal_verifier
    return portal_verifier.verifier_service.get_state()

class CaptchaSubmitRequest(BaseModel):
    answer: str

@app.post("/api/gstin/verify-portal/submit-captcha")
def submit_portal_captcha(req: CaptchaSubmitRequest):
    """Relays the CAPTCHA answer the user typed on our own page into the headless
    browser's login form."""
    import portal_verifier
    answer = (req.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="CAPTCHA answer is required.")
    portal_verifier.verifier_service.submit_captcha(answer)
    return {"status": "success"}

@app.post("/api/gstin/verify-portal/cancel")
def cancel_portal_verification():
    """Aborts portal verification session and closes Chrome."""
    import portal_verifier
    portal_verifier.verifier_service.cancel()
    return {"status": "success", "message": "Verification session cancelled."}

@app.post("/api/gstin/verify-portal/reset")
def reset_portal_verification():
    """Resets verification session to idle, terminates Chrome, and clears logs & results."""
    import portal_verifier
    portal_verifier.verifier_service.reset()
    return {"status": "success", "message": "Verification session reset to idle."}

class GSTR2BDownloadStartRequest(BaseModel):
    months: List[int]
    gst_username: Optional[str] = None
    gst_password: Optional[str] = None
    debug: Optional[bool] = False

@app.post("/api/gstr2b/download/start")
def start_gstr2b_download(req: GSTR2BDownloadStartRequest):
    """Launches Chrome to the GST portal and downloads GSTR-2B JSON for the active
    client's selected months (restricted to their configured Financial Year),
    importing each one straight into the Purchases dataset - never saved as a file."""
    import gstr2b_portal_downloader as g2b

    active_id = client_db.get_active_client_id()
    active_cl = client_db.get_client(active_id) if active_id else None
    if not active_cl:
        raise HTTPException(status_code=400, detail="Select an active taxpayer client first.")

    fy = (active_cl.get("financial_year") or "").strip()
    if not fy:
        raise HTTPException(
            status_code=400,
            detail="Set this client's Financial Year on their profile before downloading GSTR-2B from the portal."
        )

    req_username = (req.gst_username or "").strip() or None
    req_password = (req.gst_password or "").strip() or None
    if req_username or req_password:
        client_db.update_client(active_cl["id"], {
            k: v for k, v in {"gst_username": req_username, "gst_password": req_password}.items() if v
        })
        active_cl = client_db.get_client(active_id)

    final_username = req_username or active_cl.get("gst_username")
    final_password = req_password or active_cl.get("gst_password")
    if not final_username or not final_password:
        raise HTTPException(status_code=400, detail="GST Portal username & password are required.")

    client_id = active_cl["id"]
    client_gstin = active_cl.get("gstin", "")

    def _ingest(filename: str, payload: dict):
        return _ingest_return_payload(client_id, filename, payload, client_gstin)

    started = g2b.downloader_service.start(
        fy=fy,
        months=req.months,
        gst_username=final_username,
        gst_password=final_password,
        ingest_fn=_ingest,
        on_month_done=lambda: _reload_both(client_id),
        debug=bool(req.debug),
    )
    if not started:
        state = g2b.downloader_service.get_state()
        detail = state.get("error") or "A GSTR-2B download session is already in progress."
        raise HTTPException(status_code=409, detail=detail)

    return {"status": "started", "fy": fy, "total": len(req.months)}

@app.get("/api/gstr2b/download/status")
def get_gstr2b_download_status():
    """Polls real-time GSTR-2B download progress, terminal logs, and per-month results."""
    import gstr2b_portal_downloader as g2b
    return g2b.downloader_service.get_state()

@app.post("/api/gstr2b/download/submit-captcha")
def submit_gstr2b_download_captcha(req: CaptchaSubmitRequest):
    """Relays the CAPTCHA answer the user typed on our own page into the headless
    browser's login form for the GSTR-2B downloader."""
    import gstr2b_portal_downloader as g2b
    answer = (req.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="CAPTCHA answer is required.")
    g2b.downloader_service.submit_captcha(answer)
    return {"status": "success"}

@app.post("/api/gstr2b/download/cancel")
def cancel_gstr2b_download():
    """Aborts the GSTR-2B download session and closes Chrome."""
    import gstr2b_portal_downloader as g2b
    g2b.downloader_service.cancel()
    return {"status": "success", "message": "GSTR-2B download session cancelled."}

@app.post("/api/gstr2b/download/reset")
def reset_gstr2b_download():
    """Resets the GSTR-2B download session to idle and clears logs & results."""
    import gstr2b_portal_downloader as g2b
    g2b.downloader_service.reset()
    return {"status": "success", "message": "GSTR-2B download session reset to idle."}

class PartySaveRequest(BaseModel):
    gstin: str
    trade_name: str
    legal_name: Optional[str] = None
    state_name: Optional[str] = None

@app.post("/api/parties/save")
def save_party_custom_mapping(req: PartySaveRequest):
    """Manually saves or updates trade and legal name against GSTIN permanently."""
    saved = client_db.save_party_mapping(req.dict())
    return {"status": "success", "mapping": saved}

@app.delete("/api/parties/{gstin}")
@app.post("/api/parties/{gstin}/delete")
def delete_party_mapping_record(gstin: str):
    """Deletes permanently saved party mapping for a GSTIN."""
    deleted = client_db.delete_party_mapping(gstin)
    return {"status": "success", "deleted": deleted, "gstin": gstin.upper()}

@app.delete("/api/parties/clear-all")
@app.post("/api/parties/clear-all")
def clear_all_party_mappings_endpoint():
    """Wipes all saved party mappings from SQLite database and party_mappings.csv."""
    count = client_db.clear_all_party_mappings()
    active_id = client_db.get_active_client_id()
    if active_id:
        _reload_both(active_id)
    return {
        "status": "success",
        "message": "All saved GSTIN party mappings cleared successfully.",
        "cleared_count": count
    }

class PreferenceRequest(BaseModel):
    preference: str  # "trade" or "legal"

@app.post("/api/parties/preference")
def update_preference(req: PreferenceRequest):
    """Switches ledger name preference between Trade Name and Legal Name."""
    pref = req.preference.lower().strip()
    if pref not in ("trade", "legal"):
        raise HTTPException(status_code=400, detail="Preference must be 'trade' or 'legal'")
    set_name_preference(pref)
    store.name_preference = pref
    return {"status": "success", "name_preference": pref}

@app.post("/api/preferences/name")
def set_preference_name(pref: str = Query(...)):
    """Switches ledger name preference via query param."""
    clean = pref.lower().strip()
    if clean not in ("trade", "legal"):
        raise HTTPException(status_code=400, detail="Preference must be 'trade' or 'legal'")
    set_name_preference(clean)
    store.name_preference = clean
    return {"status": "success", "name_preference": clean}

def _filter_and_sort_documents(
    documents: List[NormalizedDocument],
    month: Optional[str] = None,
    doc_type: Optional[str] = None,
    validation_status: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc"
) -> List[NormalizedDocument]:
    """Shared filter+sort chain for the Transactions Explorer - used by both the
    paginated /api/invoices endpoint and the full Excel export, so the export
    always reflects exactly what the on-screen filters currently show."""
    filtered = documents

    if month:
        m_lower = month.lower()
        filtered = [d for d in filtered if m_lower in d.doc_date]

    if doc_type:
        dt_upper = doc_type.upper().strip()
        if dt_upper in ("B2B", "B2BA"):
            filtered = [d for d in filtered if d.doc_type in ("B2B", "B2BA")]
        elif dt_upper in ("CDNR", "CREDIT", "CDNRA", "CDNUR", "CDNURA"):
            filtered = [d for d in filtered if "CREDIT" in d.doc_type or "DEBIT" in d.doc_type]
        elif dt_upper in ("B2CS", "B2CSA"):
            filtered = [d for d in filtered if d.doc_type in ("B2CS", "B2CSA")]
        elif dt_upper in ("B2CL", "B2CLA"):
            filtered = [d for d in filtered if d.doc_type in ("B2CL", "B2CLA")]
        elif dt_upper == "EXP":
            filtered = [d for d in filtered if d.doc_type.startswith("EXP")]
        elif dt_upper == "ADVANCE":
            filtered = [d for d in filtered if d.doc_type.startswith("ADVANCE")]
        elif dt_upper == "RCM":
            filtered = [d for d in filtered if d.doc_type in ("B2B_RCM", "B2BA_RCM", "RCM_SELF_INVOICE")]
        elif dt_upper == "INELIGIBLE":
            filtered = [d for d in filtered if d.doc_type in ("B2B_INELIGIBLE", "B2BA_INELIGIBLE")]
        else:
            filtered = [d for d in filtered if dt_upper in d.doc_type]

    if validation_status:
        vs = validation_status.lower().strip()
        if vs == "balanced":
            filtered = [d for d in filtered if _doc_discrepancy(d) < 0.01]
        elif vs == "rounded":
            filtered = [d for d in filtered if 0.01 <= _doc_discrepancy(d) <= 2.0]
        elif vs == "discrepant":
            filtered = [d for d in filtered if _doc_discrepancy(d) > 2.0]

    if search:
        s = search.lower().strip()
        filtered = [
            d for d in filtered
            if s in d.doc_number.lower() or (d.party_name and s in d.party_name.lower()) or (d.party_gstin and s in d.party_gstin.lower())
        ]

    if sort_by:
        sort_keys = {
            "doc_type": lambda d: d.doc_type or "",
            "doc_number": lambda d: d.doc_number or "",
            "date": lambda d: d.doc_date or "",
            "party": lambda d: (d.party_name or "").lower(),
            "gstin": lambda d: d.party_gstin or "",
            "rate": lambda d: (_effective_rates(d.tax_lines) or [0.0])[-1],
            "taxable_val": lambda d: d.taxable_val,
            "cgst": lambda d: sum(t.amount for t in d.tax_lines if t.tax_type == "CGST"),
            "sgst": lambda d: sum(t.amount for t in d.tax_lines if t.tax_type == "SGST"),
            "igst": lambda d: sum(t.amount for t in d.tax_lines if t.tax_type == "IGST"),
            "tax": lambda d: sum(t.amount for t in d.tax_lines),
            "total_val": lambda d: d.total_val,
        }
        key_fn = sort_keys.get(sort_by)
        if key_fn:
            filtered = sorted(filtered, key=key_fn, reverse=(sort_dir == "desc"))

    return filtered

def _effective_rates(tax_lines: List[TaxLine]) -> List[float]:
    """Recovers the actual GST rate(s) charged on a document. CGST/SGST are each
    stored at HALF the invoice rate (e.g. 9 for an 18% intrastate supply) while
    IGST is stored at the full rate - reading either one directly as "the rate"
    is wrong for intrastate invoices specifically. A single invoice can also carry
    more than one rate if its line items differ, which is reported as-is rather
    than silently picking one item's rate and hiding the rest."""
    rates = set()
    for t in tax_lines:
        if t.tax_type == "CGST":
            rates.add(round(t.rate * 2, 2))
        elif t.tax_type == "IGST":
            rates.add(round(t.rate, 2))
    return sorted(rates)

def _format_rate_display(rates: List[float]) -> str:
    if not rates:
        return "Nil-Rated"
    if len(rates) == 1:
        r = rates[0]
        return f"{r:g}%"
    return "Multiple (" + ", ".join(f"{r:g}%" for r in rates) + ")"

def _build_invoice_row(d: NormalizedDocument, pref: str) -> Dict[str, Any]:
    """Builds one Transactions Explorer row (JSON API and Excel export both use this,
    so the export's columns are always the same numbers shown on screen)."""
    cgst = sum(t.amount for t in d.tax_lines if t.tax_type == "CGST")
    sgst = sum(t.amount for t in d.tax_lines if t.tax_type == "SGST")
    igst = sum(t.amount for t in d.tax_lines if t.tax_type == "IGST")
    tot_tax = cgst + sgst + igst
    eff_rates = _effective_rates(d.tax_lines)

    formatted_name = format_party_ledger(d.party_name, d.party_gstin, pref)

    # Format date as DD/MM/YYYY for UI display (e.g. 20250501 -> 01/05/2025)
    raw_dt = str(d.doc_date or "").strip()
    if len(raw_dt) == 8 and raw_dt.isdigit():
        display_dt = f"{raw_dt[6:8]}/{raw_dt[4:6]}/{raw_dt[0:4]}"
    elif len(raw_dt) == 10 and "-" in raw_dt:
        dp = raw_dt.split("-")
        display_dt = f"{dp[2].zfill(2)}/{dp[1].zfill(2)}/{dp[0]}" if len(dp) == 3 and len(dp[0]) == 4 else raw_dt
    else:
        display_dt = raw_dt or "-"

    # Double-entry balance calculation (journal-only docs like RCM self-invoices
    # and advance-tax entries don't follow taxable_val+tax=total_val, so they're
    # always reported as balanced rather than run through this formula)
    if domain.is_journal_doc(d):
        diff = 0.0
        val_status = "balanced"
        val_label = "Balanced (Journal)"
    else:
        diff = round(d.total_val - (d.taxable_val + tot_tax), 2)
        abs_diff = abs(diff)
        if abs_diff < 0.01:
            val_status = "balanced"
            val_label = "Balanced"
        elif abs_diff <= 2.0:
            val_status = "rounded"
            val_label = f"Round Off ({diff:+.2f})"
        else:
            val_status = "discrepant"
            val_label = f"Mismatch ({diff:+.2f})"

    return {
        "doc_num": d.doc_number,
        "doc_number": d.doc_number,
        "doc_date": display_dt,
        "raw_date": d.doc_date,
        "doc_type": d.doc_type,
        "party_gstin": d.party_gstin or "Unregistered",
        "party_name": d.party_name,
        "party_ledger": formatted_name,
        "ledger_name": formatted_name,
        "tax_rates": eff_rates,
        "rate_display": _format_rate_display(eff_rates),
        "taxable_val": round(d.taxable_val, 2),
        "cgst": round(cgst, 2),
        "sgst": round(sgst, 2),
        "igst": round(igst, 2),
        "total_tax": round(tot_tax, 2),
        "total_val": round(d.total_val, 2),
        "diff": diff,
        "round_off": round(-diff, 2) if val_status == "rounded" else 0.0,
        "validation_status": val_status,
        "validation_label": val_label
    }

@app.get("/api/invoices")
def get_invoices(
    return_type: str = "GSTR1",
    month: Optional[str] = None,
    doc_type: Optional[str] = None,
    search: Optional[str] = None,
    validation_status: Optional[str] = None,
    page: int = 1,
    limit: Optional[int] = None,
    page_size: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc"
):
    """Returns paginated, searchable invoices list for the active client's sales
    (GSTR1) or purchases (GSTR2B)."""
    return_type = (return_type or "GSTR1").upper()
    rstore = store.get(return_type)
    try:
        p = int(page)
    except Exception:
        p = 1
    try:
        ps = int(limit or page_size or 50)
    except Exception:
        ps = 50

    active_id = client_db.get_active_client_id()
    if not active_id:
        return {
            "total": 0,
            "total_records": 0,
            "page": p,
            "page_size": ps,
            "limit": ps,
            "total_pages": 0,
            "items": [],
            "invoices": [],
            "validation_summary": {"total": 0, "balanced": 0, "rounded": 0, "discrepant": 0}
        }

    if not rstore.is_loaded:
        reload_dataset(active_id, return_type)

    filtered = _filter_and_sort_documents(rstore.documents, month, doc_type, validation_status, search, sort_by, sort_dir)

    total_count = len(filtered)
    start_idx = (page - 1) * ps
    end_idx = start_idx + ps
    paged_docs = filtered[start_idx:end_idx]

    pref = store.name_preference
    inv_list = [_build_invoice_row(d, pref) for d in paged_docs]

    # Summary across all loaded documents
    all_docs = rstore.documents
    balanced_cnt = sum(1 for d in all_docs if _doc_discrepancy(d) < 0.01)
    rounded_cnt = sum(1 for d in all_docs if 0.01 <= _doc_discrepancy(d) <= 2.0)
    discrepant_cnt = sum(1 for d in all_docs if _doc_discrepancy(d) > 2.0)

    return {
        "total": total_count,
        "total_records": total_count,
        "page": page,
        "page_size": ps,
        "limit": ps,
        "total_pages": (total_count + ps - 1) // ps,
        "items": inv_list,
        "invoices": inv_list,
        "validation_summary": {
            "total": len(all_docs),
            "balanced": balanced_cnt,
            "rounded": rounded_cnt,
            "discrepant": discrepant_cnt
        }
    }

@app.get("/api/export-invoices-xlsx")
def export_invoices_xlsx(
    return_type: str = "GSTR1",
    month: Optional[str] = None,
    doc_type: Optional[str] = None,
    search: Optional[str] = None,
    validation_status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc"
):
    """Exports every transaction matching the current Transactions Explorer filters
    (not just the current page) as an Excel workbook - built in memory, never
    written to disk, matching the rest of the app's no-stored-file policy."""
    return_type = (return_type or "GSTR1").upper()
    rstore = store.get(return_type)

    active_id = client_db.get_active_client_id()
    if not active_id:
        raise HTTPException(status_code=400, detail="No active client selected.")

    if not rstore.is_loaded:
        reload_dataset(active_id, return_type)

    filtered = _filter_and_sort_documents(rstore.documents, month, doc_type, validation_status, search, sort_by, sort_dir)
    pref = store.name_preference
    rows = [_build_invoice_row(d, pref) for d in filtered]

    import openpyxl
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"

    headers = [
        "Type", "Doc Number", "Date", "Party Ledger / Customer", "GSTIN", "Rate",
        "Taxable Value", "CGST", "SGST", "IGST", "Total GST", "Invoice Total", "Balance Status"
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for r in rows:
        ws.append([
            r["doc_type"], r["doc_number"], r["doc_date"], r["party_ledger"], r["party_gstin"],
            r["rate_display"], r["taxable_val"], r["cgst"], r["sgst"], r["igst"], r["total_tax"],
            r["total_val"], r["validation_label"]
        ])

    for i, width in enumerate([12, 22, 12, 32, 18, 8, 14, 12, 12, 12, 14, 14, 18], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    active_cl = client_db.get_client(active_id)
    gstin = (active_cl or {}).get("gstin") or "export"
    suffix = "Sales" if return_type == "GSTR1" else "Purchases"
    filename = f"{gstin}_{suffix}_Transactions.xlsx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

def _xml_filenames(return_type: str) -> tuple:
    """Sales and purchases each get their own Masters/Entries files so generating
    one never clobbers the other."""
    suffix = "Sales" if (return_type or "GSTR1").upper() == "GSTR1" else "Purchases"
    return (OUTPUT_DIR / f"Consolidated_Masters_{suffix}.xml", OUTPUT_DIR / f"Consolidated_Entries_{suffix}.xml")

@app.post("/api/generate-xml")
def generate_xml(preference: Optional[str] = None, return_type: str = "GSTR1"):
    """Generates Consolidated Masters/Entries XML for the active client's sales
    (GSTR1) or purchases (GSTR2B)."""
    return_type = (return_type or "GSTR1").upper()
    active_id = client_db.get_active_client_id()
    if not active_id:
        raise HTTPException(status_code=400, detail="No active client selected. Please select a client first.")

    reload_dataset(active_id, return_type)
    rstore = store.get(return_type)

    if not rstore.documents:
        detail = "No documents found for this client to export." if return_type == "GSTR1" else \
            "No purchase documents found for this client to export."
        raise HTTPException(status_code=400, detail=detail)

    pref = (preference or store.name_preference or "trade").strip().lower()
    set_name_preference(pref)
    store.name_preference = pref

    import importlib
    import xml_engine
    importlib.reload(xml_engine)

    masters_xml = xml_engine.TallyXMLEngine.generate_masters_xml(rstore.documents, return_type)
    entries_xml = xml_engine.TallyXMLEngine.generate_entries_xml(rstore.documents, return_type)

    masters_path, entries_path = _xml_filenames(return_type)
    masters_path.write_text(masters_xml, encoding="utf-8")
    entries_path.write_text(entries_xml, encoding="utf-8")

    m_count = masters_xml.count('<LEDGER ACTION="Create"')
    v_count = entries_xml.count('<VOUCHER ACTION="Create"')

    all_docs = rstore.documents
    discrepant_cnt = sum(1 for d in all_docs if _doc_discrepancy(d) > 2.0)
    rounded_cnt = sum(1 for d in all_docs if 0.01 <= _doc_discrepancy(d) <= 2.0)
    balanced_cnt = sum(1 for d in all_docs if _doc_discrepancy(d) < 0.01)

    return {
        "status": "success",
        "return_type": return_type,
        "name_preference": store.name_preference,
        "masters_file": masters_path.name,
        "masters_size": masters_path.stat().st_size,
        "master_ledgers_count": m_count,
        "entries_file": entries_path.name,
        "entries_size": entries_path.stat().st_size,
        "vouchers_count": v_count,
        "balanced_count": balanced_cnt,
        "rounded_count": rounded_cnt,
        "excluded_discrepant_count": discrepant_cnt
    }

class ImportRequest(BaseModel):
    target: str = "both"  # "both", "masters", "vouchers"
    endpoint: str = "http://127.0.0.1:9000"
    return_type: str = "GSTR1"

@app.post("/api/import-tally")
def import_to_tally(req: ImportRequest):
    """Posts Consolidated XML directly to TallyPrime via HTTP."""
    return_type = (req.return_type or "GSTR1").upper()
    active_id = client_db.get_active_client_id()
    if not active_id:
        raise HTTPException(status_code=400, detail="No active client selected. Please select a client first.")

    # Always reload and regenerate XML before importing so that:
    # 1. Any deleted months/periods are immediately removed.
    # 2. Freshly uploaded returns and decimal values are used.
    # 3. No stale file on disk is ever posted to Tally.
    reload_dataset(active_id, return_type)
    rstore = store.get(return_type)

    if not rstore.documents:
        raise HTTPException(status_code=400, detail="No documents found for this client to export/import.")

    generate_xml(store.name_preference, return_type)

    masters_file, entries_file = _xml_filenames(return_type)

    def post_file(fpath: Path):
        with open(fpath, "r", encoding="utf-8") as f:
            data = f.read()
        resp = requests.post(req.endpoint, data=data.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=180)
        
        created = re.search(r'<CREATED>(\d+)</CREATED>', resp.text)
        altered = re.search(r'<ALTERED>(\d+)</ALTERED>', resp.text)
        errors = re.search(r'<ERRORS>(\d+)</ERRORS>', resp.text)
        exceptions = re.search(r'<EXCEPTIONS>(\d+)</EXCEPTIONS>', resp.text)
        line_err = re.findall(r'<LINEERROR>(.*?)</LINEERROR>', resp.text)

        return {
            "file": fpath.name,
            "status_code": resp.status_code,
            "created": int(created.group(1)) if created else 0,
            "altered": int(altered.group(1)) if altered else 0,
            "errors": int(errors.group(1)) if errors else 0,
            "exceptions": int(exceptions.group(1)) if exceptions else 0,
            "line_errors": line_err[:5],
            "raw_response": resp.text[:1000]
        }

    results = {}
    try:
        if req.target in ("both", "masters"):
            results["masters"] = post_file(masters_file)
        if req.target in ("both", "vouchers"):
            results["vouchers"] = post_file(entries_file)

        # Record exactly which vouchers this push put into Tally, so a later period
        # deletion in this tool can cancel those same vouchers in Tally instead of
        # leaving them there as orphans. Best-effort: Tally only reports aggregate
        # counts, not which specific vouchers failed, so a push that partially errors
        # still records all attempted vouchers - a stale "Cancel" for one Tally never
        # actually created is harmless (Tally just can't find it).
        if "vouchers" in results:
            import xml_engine
            identities = xml_engine.TallyXMLEngine.list_voucher_identities(rstore.documents, return_type)
            client_db.record_tally_pushes(active_id, return_type, identities)

        # Tally responding is not the same as Tally accepting the vouchers - a
        # blanket "success" here regardless of <ERRORS>/<EXCEPTIONS> in its response
        # would misrepresent a partially/fully rejected import as clean.
        had_issues = any(r.get("errors") or r.get("exceptions") for r in results.values())
        return {"status": "completed_with_issues" if had_issues else "success", "results": results}
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to TallyPrime at {req.endpoint}. Ensure Tally is running and port 9000 is open."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{filename}")
def download_file(filename: str):
    """Downloads generated XML or templates."""
    fpath = OUTPUT_DIR / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=fpath,
        filename=filename,
        media_type="application/xml"
    )

def extract_gstin_from_file_bytes(filename: str, content: bytes) -> Optional[str]:
    """Detects 15-char GSTIN from filename or JSON content."""
    # 1. Check filename
    m = re.search(r'([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})', filename.upper())
    if m:
        return m.group(1)

    # 2. Check zip
    if filename.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for name in z.namelist():
                    m = re.search(r'([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})', name.upper())
                    if m:
                        return m.group(1)
                    if name.endswith(".json"):
                        text = z.read(name).decode("utf-8", errors="ignore")
                        m = re.search(r'"gstin"\s*:\s*"([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})"', text, re.I)
                        if m:
                            return m.group(1).upper()
        except Exception:
            pass

    # 3. Check json
    if filename.lower().endswith(".json"):
        try:
            text = content.decode("utf-8", errors="ignore")
            m = re.search(r'"gstin"\s*:\s*"([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})"', text, re.I)
            if m:
                return m.group(1).upper()
        except Exception:
            pass

    return None

def _detect_return_type(filename: str, payload: dict) -> str:
    """Best-effort GSTR1 (sales) vs GSTR2B (purchases) detection so the user doesn't have
    to say which they're uploading. Filename hint first (GSTN's own downloads are named
    accordingly), then payload shape: GSTR-2B always carries an 'itcsumm' summary block
    and per-invoice itcavl/imsStatus flags that GSTR-1 never has."""
    fname_upper = (filename or "").upper()
    if "2B" in fname_upper:
        return "GSTR2B"
    if "GSTR1" in fname_upper or "GSTR-1" in fname_upper:
        return "GSTR1"

    def _has_itcsumm(node, depth=0):
        if not isinstance(node, dict) or depth > 3:
            return False
        if "itcsumm" in node:
            return True
        return any(_has_itcsumm(v, depth + 1) for v in node.values() if isinstance(v, dict))

    if _has_itcsumm(payload):
        return "GSTR2B"

    root = domain.unwrap_gst_payload(payload)
    for b2b in (root.get("b2b") or []):
        for inv in (b2b.get("inv") or []):
            return "GSTR2B" if ("itcavl" in inv or "imsStatus" in inv) else "GSTR1"

    return "GSTR1"

def _ingest_return_payload(client_id: int, filename: str, payload: dict, client_gstin: str = "") -> Optional[tuple]:
    """Extracts documents from one parsed GSTR-1/GSTR-2B payload and persists them for
    the client. Returns (return_type, doc_count), or None if nothing was ingested
    (GSTIN mismatch or no documents). Shared by /api/upload and the GSTR-2B portal
    downloader so both go through the exact same parsing/persistence path."""
    root_payload = domain.unwrap_gst_payload(payload)
    file_gstin = (root_payload.get("gstin") or payload.get("gstin") or "").strip().upper()
    if client_gstin and file_gstin and file_gstin != client_gstin.strip().upper():
        print(f"[WARN] Skipped '{filename}': GSTIN {file_gstin} does not match client {client_gstin}.")
        return None

    file_return_type = _detect_return_type(filename, payload)
    docs = GSTDataExtractor.extract_documents(payload, file_return_type)
    if not docs:
        return None

    label = Path(filename).stem
    client_db.save_invoice_documents(client_id, label, [_doc_to_row(d) for d in docs], file_return_type)
    return file_return_type, len(docs)

import io

@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    client_id: Optional[int] = None,
    clear_existing: bool = Query(False),
    confirm_new_client: bool = Query(False),
    confirm_duplicate_periods: bool = Query(False)
):
    """Uploads GSTR-1 (sales) or GSTR-2B (purchases) files - auto-detected per file -
    routes to client folder by GSTIN, and activates client."""
    saved_files = []
    detected_gstin = None
    resolved_client = None

    if client_id:
        resolved_client = client_db.get_client(client_id)

    # First inspect files to detect GSTIN if present
    file_bytes_list = []
    for file in files:
        content = await file.read()
        file_bytes_list.append((file.filename, content))
        gstin = extract_gstin_from_file_bytes(file.filename, content)
        if gstin:
            detected_gstin = gstin

    # If detected_gstin differs from an already-active client's GSTIN, this could be a
    # misclick (wrong client's file), so it stops for explicit confirmation instead of
    # silently switching the active client or spawning a new one. With no active client
    # resolved yet (fresh onboarding, nothing to conflict with) it proceeds as before.
    if detected_gstin:
        if resolved_client and resolved_client.get("gstin", "").upper() == detected_gstin:
            pass  # Matches correctly
        elif resolved_client and not confirm_new_client:
            clients = client_db.list_clients()
            match = next((c for c in clients if c["gstin"].upper() == detected_gstin), None)
            raise HTTPException(status_code=409, detail={
                "error": "gstin_mismatch",
                "message": (
                    f"This file's GSTIN ({detected_gstin}) does not match the active client "
                    f"'{resolved_client['name']}' ({resolved_client['gstin']})."
                ),
                "detected_gstin": detected_gstin,
                "active_client": {
                    "id": resolved_client["id"], "name": resolved_client["name"], "gstin": resolved_client["gstin"]
                },
                "existing_client_match": (
                    {"id": match["id"], "name": match["name"], "gstin": match["gstin"]} if match else None
                )
            })
        else:
            # No active client to conflict with, or the mismatch was already confirmed -
            # route to a matching existing client, or create one for this GSTIN.
            clients = client_db.list_clients()
            match = next((c for c in clients if c["gstin"].upper() == detected_gstin), None)
            if match:
                resolved_client = match
            else:
                state_code, state_name = client_db.extract_state_from_gstin(detected_gstin)
                pan = client_db.extract_pan_from_gstin(detected_gstin)
                resolved_client = client_db.create_client({
                    "name": f"Taxpayer {detected_gstin}",
                    "trade_name": f"Taxpayer {detected_gstin}",
                    "gstin": detected_gstin,
                    "pan": pan,
                    "tally_company_name": f"Taxpayer {detected_gstin}"
                })

    if resolved_client:
        client_id = resolved_client["id"]
        client_db.set_active_client_id(client_id)

    if not client_id:
        raise HTTPException(status_code=400, detail="Could not determine a target client for this upload.")

    client_gstin = (resolved_client or {}).get("gstin", "").strip().upper()

    # Parse every file up front - needed both to check for duplicate periods before
    # touching the database, and to actually ingest afterward, without parsing twice.
    parsed_files = []  # (filename, payload, file_return_type, period_label)
    for filename, content in file_bytes_list:
        payload = None

        if filename.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
                    if json_names:
                        with zf.open(json_names[0]) as jf:
                            payload = json.load(jf)
            except Exception as e:
                print(f"[WARN] Failed reading uploaded zip '{filename}': {e}")
        elif filename.lower().endswith(".json"):
            try:
                payload = json.loads(content.decode("utf-8"))
            except Exception as e:
                print(f"[WARN] Failed parsing uploaded JSON '{filename}': {e}")

        if payload is None:
            continue

        file_return_type = _detect_return_type(filename, payload)
        parsed_files.append((filename, payload, file_return_type, Path(filename).stem))

    # A period already holding data will get MORE added to it, not replaced, unless
    # "Clear Existing" is checked - easy to not notice when re-uploading a period you
    # already loaded. Stop and ask, once, rather than silently duplicating documents.
    if not clear_existing and not confirm_duplicate_periods:
        existing_periods_by_type: Dict[str, set] = {}
        duplicates = []
        for filename, payload, file_return_type, label in parsed_files:
            if file_return_type not in existing_periods_by_type:
                existing_periods_by_type[file_return_type] = set(
                    client_db.list_invoice_periods(client_id, file_return_type)
                )
            if label in existing_periods_by_type[file_return_type]:
                duplicates.append({"filename": filename, "period_label": label, "return_type": file_return_type})
        if duplicates:
            raise HTTPException(status_code=409, detail={
                "error": "duplicate_periods",
                "message": (
                    f"{len(duplicates)} file(s) match a period that already has data for this client. "
                    "Uploading will add to the existing data, not replace it, unless you clear it first."
                ),
                "duplicates": duplicates
            })

    # Parse each upload in-memory and persist only the EXTRACTED documents - the raw
    # JSON/zip bytes are never written to disk. Each file's return type (sales GSTR-1
    # vs purchases GSTR-2B) is auto-detected independently, so one batch can mix both.
    detected_types: Dict[str, str] = {}
    cleared_types = set()
    for filename, payload, file_return_type, label in parsed_files:
        detected_types[filename] = file_return_type

        # If user selected to clear existing return data, clear each detected type once
        if clear_existing and file_return_type not in cleared_types:
            client_db.clear_client_documents(client_id, file_return_type)
            cleared_types.add(file_return_type)

        if _ingest_return_payload(client_id, filename, payload, client_gstin):
            saved_files.append(filename)

    # Reload both datasets for this client - a batch may have touched either or both
    _reload_both(client_id)
    active_cl = client_db.get_client(client_id) if client_id else None

    return {
        "status": "success",
        "active_client": active_cl,
        "uploaded_files": saved_files,
        "detected_types": detected_types,
        "new_totals": {
            "GSTR1": store.get("GSTR1").financial_totals,
            "GSTR2B": store.get("GSTR2B").financial_totals
        }
    }

DEFAULT_TALLY_ENDPOINT = "http://127.0.0.1:9000"

def _cancel_vouchers_in_tally(vouchers: List[Dict[str, Any]], endpoint: str = DEFAULT_TALLY_ENDPOINT) -> Dict[str, Any]:
    """Best-effort: posts a Cancel request to Tally for every given voucher. Never
    raises - deleting data from this tool must succeed even if Tally is offline or
    the cancel is rejected; the caller just reports whether it reached Tally."""
    if not vouchers:
        return {"attempted": 0, "reached_tally": False}
    import xml_engine
    cancel_xml = xml_engine.TallyXMLEngine.generate_cancel_xml(vouchers)
    try:
        resp = requests.post(endpoint, data=cancel_xml.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=30)
        return {"attempted": len(vouchers), "reached_tally": True, "status_code": resp.status_code}
    except requests.exceptions.RequestException as e:
        return {"attempted": len(vouchers), "reached_tally": False, "error": str(e)}

@app.delete("/api/clients/{client_id}/periods/{period_label}")
@app.post("/api/clients/{client_id}/periods/{period_label}/delete")
def delete_client_return_period(client_id: int, period_label: str, return_type: str = "GSTR1", tally_endpoint: str = DEFAULT_TALLY_ENDPOINT):
    """Deletes one stored return period (sales or purchases) for a client, cancels
    any vouchers this tool previously pushed to Tally for that period, and
    regenerates that return type's XML."""
    return_type = (return_type or "GSTR1").upper()
    cl = client_db.get_client(client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client not found.")

    clean_target = period_label.strip()
    pushed = client_db.get_pushed_vouchers_for_period(client_id, return_type, clean_target)
    cancel_result = _cancel_vouchers_in_tally(pushed, tally_endpoint)

    deleted = client_db.delete_period_documents(client_id, clean_target, return_type)
    client_db.delete_pushed_vouchers_for_period(client_id, return_type, clean_target)

    if deleted:
        reload_dataset(client_id, return_type)
        rstore = store.get(return_type)
        masters_path, entries_path = _xml_filenames(return_type)
        if rstore.documents:
            generate_xml(store.name_preference, return_type)
        else:
            masters_path.unlink(missing_ok=True)
            entries_path.unlink(missing_ok=True)

        message = f"Period '{period_label}' deleted successfully."
        if cancel_result["attempted"]:
            message += (
                f" Cancelled {cancel_result['attempted']} voucher(s) in Tally."
                if cancel_result["reached_tally"]
                else f" Could not reach Tally to cancel {cancel_result['attempted']} previously-pushed voucher(s) - cancel them manually if needed."
            )
        return {"status": "success", "message": message, "tally_cancel": cancel_result}
    raise HTTPException(status_code=404, detail=f"Period '{period_label}' not found.")

@app.delete("/api/clients/{client_id}/clear-returns")
@app.post("/api/clients/{client_id}/clear-returns")
def clear_client_returns(client_id: int, return_type: str = "GSTR1", tally_endpoint: str = DEFAULT_TALLY_ENDPOINT):
    """Clears all stored return data (sales or purchases) for a client, cancelling
    any vouchers this tool previously pushed to Tally for it."""
    return_type = (return_type or "GSTR1").upper()

    pushed = client_db.get_pushed_vouchers_for_client(client_id, return_type)
    cancel_result = _cancel_vouchers_in_tally(pushed, tally_endpoint)

    client_db.clear_client_documents(client_id, return_type)
    client_db.delete_pushed_vouchers_for_client(client_id, return_type)

    reload_dataset(client_id, return_type)
    masters_path, entries_path = _xml_filenames(return_type)
    masters_path.unlink(missing_ok=True)
    entries_path.unlink(missing_ok=True)

    message = "All return data cleared for client."
    if cancel_result["attempted"]:
        message += (
            f" Cancelled {cancel_result['attempted']} voucher(s) in Tally."
            if cancel_result["reached_tally"]
            else f" Could not reach Tally to cancel {cancel_result['attempted']} previously-pushed voucher(s) - cancel them manually if needed."
        )
    return {"status": "success", "message": message, "tally_cancel": cancel_result}

# Mount static frontend
STATIC_DIR = get_bundle_dir() / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
