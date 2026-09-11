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

from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project roots to sys.path - only meaningful running from source; a PyInstaller
# build already has every module analyzed and bundled, and there is no on-disk
# gst_tally_engine/web_app tree next to the frozen exe to point at.
SRC_ROOT = Path(__file__).resolve().parent.parent
if not getattr(sys, "frozen", False):
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    GST_ENGINE = SRC_ROOT / "gst_tally_engine"
    if str(GST_ENGINE) not in sys.path:
        sys.path.insert(0, str(GST_ENGINE))
    WEB_APP_DIR = SRC_ROOT / "web_app"
    if str(WEB_APP_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_APP_DIR))

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
from app_paths import get_data_root

# Writable data lives next to the .exe in a frozen build, so it survives restarts
# instead of being written into PyInstaller's temp extraction folder.
DATA_ROOT = get_data_root()
INPUT_DIR = DATA_ROOT / "input_json"
OUTPUT_DIR = DATA_ROOT / "output_xml"
CSV_PATH = DATA_ROOT / "party_mappings.csv"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
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

def get_client_input_dir(client_id: Optional[int] = None) -> Optional[Path]:
    if client_id is None:
        client_id = client_db.get_active_client_id()
    if client_id is None:
        return None
    cl = client_db.get_client(client_id)
    if not cl:
        return None
    
    gstin = cl.get("gstin", "").strip().upper()
    code = cl.get("client_code", "").strip()

    # Look for matching directory by GSTIN or code
    if gstin and (INPUT_DIR / gstin).exists():
        return INPUT_DIR / gstin
    if code and (INPUT_DIR / code).exists():
        return INPUT_DIR / code

    # Fallback to creating GSTIN folder
    target = INPUT_DIR / (gstin or code or f"CL-{client_id:03d}")
    target.mkdir(parents=True, exist_ok=True)
    return target

# Global in-memory cache
class DataStore:
    documents: List[NormalizedDocument] = []
    monthly_stats: List[Dict[str, Any]] = []
    financial_totals: Dict[str, Any] = {}
    is_loaded: bool = False
    name_preference: str = "trade"

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
        original_doc_date=row.get("original_doc_date")
    )

def _month_stat(label: str, docs: List[NormalizedDocument], filename: str = "") -> Dict[str, Any]:
    """Builds one row of the monthly summary table from a list of already-extracted documents."""
    b2b_c = sum(1 for d in docs if d.doc_type in ("B2B", "B2BA"))
    b2c_c = sum(1 for d in docs if d.doc_type in ("B2CS", "B2CSA", "B2CL", "B2CLA"))
    cdnr_c = sum(1 for d in docs if "CREDIT" in d.doc_type or "DEBIT" in d.doc_type)
    exp_c = sum(1 for d in docs if d.doc_type.startswith("EXP"))
    advance_c = sum(1 for d in docs if d.doc_type.startswith("ADVANCE"))
    taxable = sum(d.taxable_val for d in docs)
    inv_val = sum(d.total_val for d in docs)
    cgst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "CGST")
    sgst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "SGST")
    igst = sum(t.amount for d in docs for t in d.tax_lines if t.tax_type == "IGST")
    parties = {d.party_gstin for d in docs if d.party_gstin}
    return {
        "label": label,
        "filename": filename,
        "total_docs": len(docs),
        "b2b_count": b2b_c,
        "b2c_count": b2c_c,
        "cdnr_count": cdnr_c,
        "exp_count": exp_c,
        "advance_count": advance_c,
        "parties_count": len(parties),
        "taxable_val": round(taxable, 2),
        "cgst_val": round(cgst, 2),
        "sgst_val": round(sgst, 2),
        "igst_val": round(igst, 2),
        "total_val": round(inv_val, 2)
    }

def _empty_totals() -> Dict[str, Any]:
    return {
        "total_documents": 0, "total_b2b": 0, "total_b2c": 0, "total_cdnr": 0,
        "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
        "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
        "gross_invoiced_value": 0.0, "months_count": 0
    }

def _migrate_legacy_input_folder(client_id: int, client_dir: Path, client_gstin: str):
    """One-time fallback for clients uploaded before raw JSON stopped being persisted to disk:
    parses whatever is still sitting in input_json/<client>/ and writes it into the DB so this
    client never needs its on-disk files read again after this call."""
    seen_labels = set()
    tasks = []  # List of (json_path, label)

    for d in sorted(list(client_dir.iterdir()), key=lambda p: get_month_sort_key(p.name)):
        if d.is_dir():
            jsons = list(d.glob("*.json"))
            if jsons:
                tasks.append((jsons[0], d.name))
                seen_labels.add(d.name)

    for zf in sorted(list(client_dir.glob("*.zip")), key=lambda p: get_month_sort_key(p.stem)):
        if zf.stem not in seen_labels:
            extract_dir = client_dir / zf.stem
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(zf, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                jsons = list(extract_dir.glob("*.json"))
                if jsons:
                    tasks.append((jsons[0], zf.stem))
                    seen_labels.add(zf.stem)
            except Exception as ze:
                print(f"[WARN] Error extracting legacy zip {zf}: {ze}")

    for jf in client_dir.glob("*.json"):
        if jf.stem not in seen_labels:
            tasks.append((jf, jf.stem))
            seen_labels.add(jf.stem)

    for fpath, label in tasks:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        root_payload = domain.unwrap_gst_payload(payload)
        file_gstin = (root_payload.get("gstin") or payload.get("gstin") or "").strip().upper()
        if file_gstin and file_gstin != client_gstin:
            continue

        docs = GSTDataExtractor.extract_documents(payload, "GSTR1")
        if docs:
            client_db.save_invoice_documents(client_id, label, [_doc_to_row(d) for d in docs])

def reload_dataset(client_id: Optional[int] = None) -> Dict[str, Any]:
    """Rebuilds the in-memory store for the active client strictly from persisted DB records.
    Raw uploaded JSON is never read from disk here - it was parsed once at upload time and the
    extracted documents are what's stored. The only exception is a one-time migration for
    clients that still have pre-existing files under input_json/ from before this behavior."""
    if client_id is None:
        client_id = client_db.get_active_client_id()

    cl = client_db.get_client(client_id) if client_id else None
    if not cl:
        store.documents = []
        store.monthly_stats = []
        store.financial_totals = _empty_totals()
        store.is_loaded = True
        return store.financial_totals

    client_gstin = cl["gstin"].strip().upper()
    reload_party_mappings()

    stored_rows = client_db.get_invoice_documents(client_id)

    if not stored_rows:
        client_dir = get_client_input_dir(client_id)
        if client_dir and client_dir.exists() and any(client_dir.iterdir()):
            _migrate_legacy_input_folder(client_id, client_dir, client_gstin)
            stored_rows = client_db.get_invoice_documents(client_id)

    by_period: Dict[str, List[Dict[str, Any]]] = {}
    for row in stored_rows:
        by_period.setdefault(row["period_label"], []).append(row)

    all_docs: List[NormalizedDocument] = []
    month_stats = []
    for label in sorted(by_period.keys(), key=get_month_sort_key):
        docs = [_row_to_doc(r) for r in by_period[label]]
        all_docs.extend(docs)
        month_stats.append(_month_stat(label, docs))

    all_docs.sort(key=lambda d: d.doc_date)

    tot_taxable = sum(d.taxable_val for d in all_docs)
    tot_val = sum(d.total_val for d in all_docs)
    tot_cgst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "CGST")
    tot_sgst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "SGST")
    tot_igst = sum(t.amount for d in all_docs for t in d.tax_lines if t.tax_type == "IGST")
    all_parties = {d.party_gstin for d in all_docs if d.party_gstin}

    store.documents = all_docs
    store.monthly_stats = month_stats
    store.financial_totals = {
        "total_documents": len(all_docs),
        "total_b2b": sum(m["b2b_count"] for m in month_stats),
        "total_b2c": sum(m["b2c_count"] for m in month_stats),
        "total_cdnr": sum(m["cdnr_count"] for m in month_stats),
        "total_exp": sum(m["exp_count"] for m in month_stats),
        "total_advance": sum(m["advance_count"] for m in month_stats),
        "unique_parties": len(all_parties),
        "taxable_turnover": round(tot_taxable, 2),
        "total_cgst": round(tot_cgst, 2),
        "total_sgst": round(tot_sgst, 2),
        "total_igst": round(tot_igst, 2),
        "total_tax": round(tot_cgst + tot_sgst + tot_igst, 2),
        "gross_invoiced_value": round(tot_val, 2),
        "months_count": len(month_stats)
    }
    store.is_loaded = True

    # Sync into Client DB
    try:
        if client_id and client_db.get_client(client_id):
            client_db.sync_return_periods_for_client(client_id, month_stats)
    except Exception as e:
        print(f"[WARN] Could not sync return periods: {e}")

    return store.financial_totals

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
def delete_client_record(client_id: int, delete_files: bool = True):
    """Deletes a client profile, return period records, and optionally uploaded return files."""
    cl = client_db.get_client(client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client not found.")

    was_active = (client_id == client_db.get_active_client_id())
    gstin = cl.get("gstin", "").strip().upper()
    code = cl.get("client_code", "").strip()

    # 1. Optionally delete uploaded files for this client
    if delete_files and gstin:
        client_folders = []
        if (INPUT_DIR / gstin).exists():
            client_folders.append(INPUT_DIR / gstin)
        if code and (INPUT_DIR / code).exists():
            client_folders.append(INPUT_DIR / code)
        
        for folder in client_folders:
            try:
                shutil.rmtree(folder)
            except Exception as e:
                print(f"[WARN] Failed to delete client folder {folder}: {e}")

    # 2. Delete from database (cascades return periods)
    ok = client_db.delete_client(client_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete client from database.")

    # 3. If active client was deleted, switch context or clear store
    new_active_id = client_db.get_active_client_id()
    if was_active:
        if new_active_id:
            store.is_loaded = False
            reload_dataset(new_active_id)
        else:
            store.documents.clear()
            store.monthly_stats.clear()
            store.financial_totals = {
                "total_documents": 0, "total_b2b": 0, "total_b2c": 0, "total_cdnr": 0,
                "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
                "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
                "gross_invoiced_value": 0.0, "months_count": 0
            }
            store.is_loaded = True

    new_active_client = client_db.get_client(new_active_id) if new_active_id else None

    return {
        "status": "success",
        "message": f"Client '{cl['name']}' ({gstin}) deleted successfully.",
        "deleted_client_id": client_id,
        "files_removed": delete_files,
        "new_active_client": new_active_client
    }

@app.post("/api/clients/deselect")
@app.post("/api/clients/active/clear")
def clear_active_client_selection():
    """Clears the active client session and unloads client dataset."""
    client_db.set_active_client_id(None)
    store.documents.clear()
    store.monthly_stats.clear()
    store.financial_totals = {
        "total_documents": 0, "total_b2b": 0, "total_b2c": 0, "total_cdnr": 0,
        "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
        "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
        "gross_invoiced_value": 0.0, "months_count": 0
    }
    store.is_loaded = True
    return {"status": "success", "message": "Active client session cleared."}

@app.post("/api/clients/{client_id}/select")
def set_active_client(client_id: int):
    """Sets the active client context and reloads only that client's data."""
    try:
        cl = client_db.set_active_client_id(client_id)
        if cl.get("default_name_preference"):
            set_name_preference(cl["default_name_preference"])
            store.name_preference = cl["default_name_preference"]
        store.is_loaded = False
        totals = reload_dataset(client_id)
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
    store.documents.clear()
    store.monthly_stats.clear()
    store.financial_totals = {
        "total_documents": 0, "total_b2b": 0, "total_b2c": 0, "total_cdnr": 0,
        "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
        "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
        "gross_invoiced_value": 0.0, "months_count": 0
    }
    store.is_loaded = True
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
        "data_loaded": store.is_loaded,
        "total_docs": len(store.documents),
        "name_preference": store.name_preference
    }

@app.get("/api/overview")
def get_overview():
    """Returns overall financial KPIs, active client, and monthly breakdown."""
    active_id = client_db.get_active_client_id()
    if not active_id:
        empty_totals = {
            "total_documents": 0, "total_b2b": 0, "total_b2c": 0, "total_cdnr": 0,
            "unique_parties": 0, "taxable_turnover": 0.0, "total_cgst": 0.0,
            "total_sgst": 0.0, "total_igst": 0.0, "total_tax": 0.0,
            "gross_invoiced_value": 0.0, "months_count": 0
        }
        return {
            "active_client": None,
            "totals": empty_totals,
            "monthly_stats": [],
            "name_preference": store.name_preference
        }

    if not store.is_loaded:
        reload_dataset(active_id)
    active_cl = client_db.get_client(active_id)
    return {
        "active_client": active_cl,
        "totals": store.financial_totals,
        "monthly_stats": store.monthly_stats,
        "name_preference": store.name_preference
    }

@app.get("/api/parties")
def get_parties(search: Optional[str] = None):
    """Returns list of counterparties for the active client."""
    active_id = client_db.get_active_client_id()
    if not active_id:
        return {
            "total_parties": 0,
            "name_preference": store.name_preference,
            "parties": []
        }

    if not store.is_loaded:
        reload_dataset(active_id)

    # Derive counterparties strictly from the active client's documents
    party_docs: Dict[str, List[NormalizedDocument]] = {}
    for d in store.documents:
        if d.party_gstin:
            party_docs.setdefault(d.party_gstin, []).append(d)

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
            "has_custom_mapping": bool(db_map)
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
            reload_dataset(active_id)
        all_gstins = sorted(list({d.party_gstin for d in store.documents if d.party_gstin}))
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
            reload_dataset(active_id)
        gstin_list = sorted(list({d.party_gstin for d in store.documents if d.party_gstin}))

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
        store.is_loaded = False
        reload_dataset(active_id)
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

@app.get("/api/invoices")
def get_invoices(
    month: Optional[str] = None,
    doc_type: Optional[str] = None,
    search: Optional[str] = None,
    validation_status: Optional[str] = None,
    page: int = 1,
    limit: Optional[int] = None,
    page_size: Optional[int] = None
):
    """Returns paginated, searchable invoices list for active client."""
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

    if not store.is_loaded:
        reload_dataset(active_id)

    filtered = store.documents

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
        else:
            filtered = [d for d in filtered if dt_upper in d.doc_type]

    if validation_status:
        vs = validation_status.lower().strip()
        if vs == "balanced":
            filtered = [d for d in filtered if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) < 0.01]
        elif vs == "rounded":
            filtered = [d for d in filtered if 0.01 <= abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) <= 2.0]
        elif vs == "discrepant":
            filtered = [d for d in filtered if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) > 2.0]

    if search:
        s = search.lower().strip()
        filtered = [
            d for d in filtered
            if s in d.doc_number.lower() or (d.party_name and s in d.party_name.lower()) or (d.party_gstin and s in d.party_gstin.lower())
        ]

    total_count = len(filtered)
    start_idx = (page - 1) * ps
    end_idx = start_idx + ps
    paged_docs = filtered[start_idx:end_idx]

    inv_list = []
    pref = store.name_preference
    for d in paged_docs:
        cgst = sum(t.amount for t in d.tax_lines if t.tax_type == "CGST")
        sgst = sum(t.amount for t in d.tax_lines if t.tax_type == "SGST")
        igst = sum(t.amount for t in d.tax_lines if t.tax_type == "IGST")
        tot_tax = cgst + sgst + igst
        tax_rate = d.tax_lines[0].rate if d.tax_lines else 18.0
        
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

        # Double-entry balance calculation
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

        inv_list.append({
            "doc_num": d.doc_number,
            "doc_number": d.doc_number,
            "doc_date": display_dt,
            "raw_date": d.doc_date,
            "doc_type": d.doc_type,
            "party_gstin": d.party_gstin or "Unregistered",
            "party_name": d.party_name,
            "party_ledger": formatted_name,
            "ledger_name": formatted_name,
            "tax_rate": tax_rate,
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
        })

    # Summary across all loaded documents
    all_docs = store.documents
    balanced_cnt = sum(1 for d in all_docs if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) < 0.01)
    rounded_cnt = sum(1 for d in all_docs if 0.01 <= abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) <= 2.0)
    discrepant_cnt = sum(1 for d in all_docs if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) > 2.0)

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

@app.post("/api/generate-xml")
def generate_xml(preference: Optional[str] = None):
    """Generates Consolidated XML files for the active client."""
    active_id = client_db.get_active_client_id()
    if not active_id:
        raise HTTPException(status_code=400, detail="No active client selected. Please select a client first.")

    if not store.is_loaded:
        reload_dataset(active_id)

    if not store.documents:
        raise HTTPException(status_code=400, detail="No documents found for this client to export.")

    pref = (preference or store.name_preference or "trade").strip().lower()
    set_name_preference(pref)
    store.name_preference = pref

    masters_xml = TallyXMLEngine.generate_masters_xml(store.documents, "GSTR1")
    entries_xml = TallyXMLEngine.generate_entries_xml(store.documents, "GSTR1")

    masters_path = OUTPUT_DIR / "Consolidated_Masters.xml"
    entries_path = OUTPUT_DIR / "Consolidated_Entries.xml"
    masters_path.write_text(masters_xml, encoding="utf-8")
    entries_path.write_text(entries_xml, encoding="utf-8")

    m_count = masters_xml.count('<LEDGER ACTION="Create"')
    v_count = entries_xml.count('<VOUCHER ACTION="Create"')

    all_docs = store.documents
    discrepant_cnt = sum(1 for d in all_docs if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) > 2.0)
    rounded_cnt = sum(1 for d in all_docs if 0.01 <= abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) <= 2.0)
    balanced_cnt = sum(1 for d in all_docs if abs(round(d.total_val - (d.taxable_val + sum(t.amount for t in d.tax_lines)), 2)) < 0.01)

    return {
        "status": "success",
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

@app.post("/api/import-tally")
def import_to_tally(req: ImportRequest):
    """Posts Consolidated XML directly to TallyPrime via HTTP."""
    masters_file = OUTPUT_DIR / "Consolidated_Masters.xml"
    entries_file = OUTPUT_DIR / "Consolidated_Entries.xml"

    if not masters_file.exists() or not entries_file.exists():
        generate_xml(store.name_preference)

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

        return {"status": "success", "results": results}
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

import io

@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    client_id: Optional[int] = None,
    clear_existing: bool = Query(False)
):
    """Uploads GSTR-1 files, automatically routes to client folder by GSTIN, and activates client."""
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

    # If detected_gstin differs from passed client_id's GSTIN, route to matching or new client
    if detected_gstin:
        if resolved_client and resolved_client.get("gstin", "").upper() == detected_gstin:
            pass  # Matches correctly
        else:
            # Look for existing client with this GSTIN
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

    # If user selected to clear existing return data for this client
    if clear_existing:
        client_db.clear_client_documents(client_id)

    client_gstin = (resolved_client or {}).get("gstin", "").strip().upper()

    # Parse each upload in-memory and persist only the EXTRACTED documents - the raw
    # JSON/zip bytes are never written to disk.
    for filename, content in file_bytes_list:
        label = Path(filename).stem
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

        root_payload = domain.unwrap_gst_payload(payload)
        file_gstin = (root_payload.get("gstin") or payload.get("gstin") or "").strip().upper()
        if client_gstin and file_gstin and file_gstin != client_gstin:
            print(f"[WARN] Skipped '{filename}': GSTIN {file_gstin} does not match client {client_gstin}.")
            continue

        docs = GSTDataExtractor.extract_documents(payload, "GSTR1")
        if not docs:
            continue

        client_db.save_invoice_documents(client_id, label, [_doc_to_row(d) for d in docs])
        saved_files.append(filename)

    # Reload dataset for this client only
    store.is_loaded = False
    totals = reload_dataset(client_id)
    active_cl = client_db.get_client(client_id) if client_id else None

    return {
        "status": "success",
        "active_client": active_cl,
        "uploaded_files": saved_files,
        "new_totals": totals
    }

@app.delete("/api/clients/{client_id}/periods/{period_label}")
@app.post("/api/clients/{client_id}/periods/{period_label}/delete")
def delete_client_return_period(client_id: int, period_label: str):
    """Deletes one stored return period for a client and reloads the dataset."""
    cl = client_db.get_client(client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client not found.")

    clean_target = period_label.strip()
    deleted = client_db.delete_period_documents(client_id, clean_target)

    # Best-effort cleanup of any leftover on-disk copy from before uploads stopped
    # writing to input_json/ (legacy clients only; new uploads never create these).
    client_dir = get_client_input_dir(client_id)
    if client_dir and client_dir.exists():
        for item in list(client_dir.iterdir()):
            if item.name == clean_target or item.stem == clean_target or clean_target in item.name:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    deleted = True
                elif item.is_file():
                    item.unlink(missing_ok=True)
                    deleted = True

    if deleted:
        store.is_loaded = False
        reload_dataset(client_id)
        return {"status": "success", "message": f"Period '{period_label}' deleted successfully."}
    raise HTTPException(status_code=404, detail=f"Period '{period_label}' not found.")

@app.delete("/api/clients/{client_id}/clear-returns")
@app.post("/api/clients/{client_id}/clear-returns")
def clear_client_returns(client_id: int):
    """Clears all stored return data for a client."""
    client_db.clear_client_documents(client_id)

    # Best-effort cleanup of any leftover on-disk copy from before uploads stopped
    # writing to input_json/ (legacy clients only; new uploads never create these).
    client_dir = get_client_input_dir(client_id)
    if client_dir and client_dir.exists():
        for item in list(client_dir.iterdir()):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    store.is_loaded = False
    reload_dataset(client_id)
    return {"status": "success", "message": "All return data cleared for client."}

# Mount static frontend
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
