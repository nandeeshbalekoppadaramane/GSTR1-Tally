"""
Client Management Database Module
---------------------------------
Handles SQLite persistence for multi-client taxpayer management.
Tracks Client Master details, GSTIN, PAN, State, Tally mappings, and GSTR-1 return periods.
"""

import sqlite3
import re
import csv
import json
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Optional, Any

from app_paths import get_data_root

DB_PATH = get_data_root() / "client_memory.db"
CSV_PATH = get_data_root() / "party_mappings.csv"

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh"
}

_ACTIVE_CLIENT_ID = 1

@contextmanager
def get_connection():
    """Every call site uses `with get_connection() as conn:`. sqlite3.Connection's own
    context-manager protocol only commits/rolls back on exit - it never closes the
    connection, so that pattern was leaking one open connection per call. Wrapping it
    here fixes every call site at once: still commits (or rolls back on error) exactly
    as before, and now actually closes the connection too."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_client_db():
    """Initializes tables and seeds the primary client if none exists."""
    with get_connection() as conn:
        c = conn.cursor()
        
        # 1. Clients Master Table
        c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            trade_name TEXT,
            gstin TEXT UNIQUE NOT NULL,
            pan TEXT,
            state_code TEXT,
            state_name TEXT,
            contact_person TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            tally_company_name TEXT,
            tally_company_id TEXT,
            default_name_preference TEXT DEFAULT 'trade',
            status TEXT DEFAULT 'active',
            notes TEXT,
            gst_username TEXT,
            gst_password TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Migration: Ensure gst_username & gst_password exist on clients table
        c.execute("PRAGMA table_info(clients)")
        existing_cl_cols = {row[1] for row in c.fetchall()}
        if "gst_username" not in existing_cl_cols:
            c.execute("ALTER TABLE clients ADD COLUMN gst_username TEXT")
        if "gst_password" not in existing_cl_cols:
            c.execute("ALTER TABLE clients ADD COLUMN gst_password TEXT")

        # 2. Client Return Periods Table
        c.execute("""
        CREATE TABLE IF NOT EXISTS client_return_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            financial_year TEXT NOT NULL,
            return_period TEXT NOT NULL,
            doc_count INTEGER DEFAULT 0,
            b2b_count INTEGER DEFAULT 0,
            b2c_count INTEGER DEFAULT 0,
            cdnr_count INTEGER DEFAULT 0,
            parties_count INTEGER DEFAULT 0,
            taxable_val REAL DEFAULT 0.0,
            cgst_val REAL DEFAULT 0.0,
            sgst_val REAL DEFAULT 0.0,
            igst_val REAL DEFAULT 0.0,
            total_val REAL DEFAULT 0.0,
            file_path TEXT,
            status TEXT DEFAULT 'Imported',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE,
            UNIQUE(client_id, return_period)
        )
        """)

        # 3. Party Mappings (Permanent storage of verified trade and legal names against GSTIN)
        c.execute("""
        CREATE TABLE IF NOT EXISTS party_mappings (
            gstin TEXT PRIMARY KEY,
            trade_name TEXT,
            legal_name TEXT,
            state_code TEXT,
            state_name TEXT,
            party_name TEXT,
            status TEXT DEFAULT 'Active',
            taxpayer_type TEXT,
            reg_date TEXT,
            constitution TEXT,
            center_jurisdiction TEXT,
            state_jurisdiction TEXT,
            source TEXT DEFAULT 'portal_verified',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Ensure all columns exist in party_mappings even if table existed previously
        c.execute("PRAGMA table_info(party_mappings)")
        existing_cols = {row[1] for row in c.fetchall()}
        cols_to_add = {
            "state_code": "TEXT",
            "trade_name": "TEXT",
            "legal_name": "TEXT",
            "state_name": "TEXT",
            "party_name": "TEXT",
            "status": "TEXT DEFAULT 'Active'",
            "taxpayer_type": "TEXT",
            "reg_date": "TEXT",
            "constitution": "TEXT",
            "center_jurisdiction": "TEXT",
            "state_jurisdiction": "TEXT",
            "source": "TEXT DEFAULT 'portal_verified'",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        }
        for col_name, col_def in cols_to_add.items():
            if col_name not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE party_mappings ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass

        # 4. Invoice Documents (extracted GSTR-1/2B records, replaces retaining raw uploaded JSON)
        c.execute("""
        CREATE TABLE IF NOT EXISTS client_invoice_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            period_label TEXT NOT NULL,
            doc_type TEXT,
            doc_number TEXT,
            doc_date TEXT,
            party_gstin TEXT,
            party_name TEXT,
            total_val REAL DEFAULT 0.0,
            taxable_val REAL DEFAULT 0.0,
            tax_lines_json TEXT,
            original_doc_num TEXT,
            original_doc_date TEXT,
            FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_invoice_docs_client ON client_invoice_documents(client_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_invoice_docs_period ON client_invoice_documents(client_id, period_label)")

        conn.commit()

def extract_pan_from_gstin(gstin: str) -> str:
    """Extracts 10-digit PAN (characters 3 to 12) from 15-digit GSTIN."""
    clean = re.sub(r'[^A-Za-z0-9]', '', gstin or "").upper()
    if len(clean) >= 12:
        return clean[2:12]
    return ""

def extract_state_from_gstin(gstin: str) -> tuple:
    """Extracts state code and resolved state name from GSTIN."""
    clean = re.sub(r'[^A-Za-z0-9]', '', gstin or "").upper()
    if len(clean) >= 2:
        code = clean[:2]
        return code, STATE_CODES.get(code, "Unknown State")
    return "", "Unknown State"

def list_clients(search: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists all clients with aggregated return period statistics."""
    init_client_db()
    query = """
    SELECT 
        c.*,
        COUNT(DISTINCT r.id) AS total_periods,
        COALESCE(SUM(r.doc_count), 0) AS total_docs,
        COALESCE(SUM(r.taxable_val), 0) AS total_taxable,
        COALESCE(SUM(r.total_val), 0) AS total_gross
    FROM clients c
    LEFT JOIN client_return_periods r ON c.id = r.client_id
    """
    conditions = []
    params = []

    if status:
        conditions.append("c.status = ?")
        params.append(status.lower())

    if search:
        s = f"%{search.strip().lower()}%"
        conditions.append("(LOWER(c.name) LIKE ? OR LOWER(c.trade_name) LIKE ? OR LOWER(c.gstin) LIKE ? OR LOWER(c.pan) LIKE ? OR LOWER(c.state_name) LIKE ?)")
        params.extend([s, s, s, s, s])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " GROUP BY c.id ORDER BY c.id ASC"

    with get_connection() as conn:
        c = conn.cursor()
        c.execute(query, params)
        rows = c.fetchall()
        
        clients = []
        for r in rows:
            item = dict(r)
            item["is_active_session"] = (item["id"] == _ACTIVE_CLIENT_ID)
            clients.append(item)
        return clients

def get_client(client_id: int) -> Optional[Dict[str, Any]]:
    """Returns single client with detailed summary."""
    init_client_db()
    query = """
    SELECT 
        c.*,
        COUNT(DISTINCT r.id) AS total_periods,
        COALESCE(SUM(r.doc_count), 0) AS total_docs,
        COALESCE(SUM(r.taxable_val), 0) AS total_taxable,
        COALESCE(SUM(r.cgst_val), 0) AS total_cgst,
        COALESCE(SUM(r.sgst_val), 0) AS total_sgst,
        COALESCE(SUM(r.igst_val), 0) AS total_igst,
        COALESCE(SUM(r.total_val), 0) AS total_gross
    FROM clients c
    LEFT JOIN client_return_periods r ON c.id = r.client_id
    WHERE c.id = ?
    GROUP BY c.id
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(query, (client_id,))
        row = c.fetchone()
        if not row:
            return None
        res = dict(row)
        res["is_active_session"] = (res["id"] == _ACTIVE_CLIENT_ID)
        return res

def _clean(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()

def create_client(data: Dict[str, Any]) -> Dict[str, Any]:
    """Creates a new client record with automatic PAN and State derivation."""
    init_client_db()
    gstin = re.sub(r'[^A-Za-z0-9]', '', data.get("gstin") or "").upper()
    if len(gstin) != 15:
        raise ValueError("GSTIN must be exactly 15 alphanumeric characters.")

    name = _clean(data.get("name"))
    if not name:
        raise ValueError("Business/Legal Name is required.")

    trade_name = _clean(data.get("trade_name")) or name
    pan = _clean(data.get("pan")).upper() or extract_pan_from_gstin(gstin)
    state_code, state_name = extract_state_from_gstin(gstin)

    # Generate next client code
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT max(id) FROM clients")
            max_id = c.fetchone()[0] or 0
            client_code = _clean(data.get("client_code")) or f"CL-{max_id + 1:03d}"

            c.execute("""
            INSERT INTO clients (
                client_code, name, trade_name, gstin, pan, state_code, state_name,
                contact_person, email, phone, address, tally_company_name, tally_company_id,
                default_name_preference, status, notes, gst_username, gst_password
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_code,
                name,
                trade_name,
                gstin,
                pan,
                state_code,
                state_name,
                _clean(data.get("contact_person")),
                _clean(data.get("email")),
                _clean(data.get("phone")),
                _clean(data.get("address")),
                _clean(data.get("tally_company_name")) or name,
                _clean(data.get("tally_company_id")),
                _clean(data.get("default_name_preference"), "trade").lower(),
                _clean(data.get("status"), "active").lower(),
                _clean(data.get("notes")),
                _clean(data.get("gst_username")),
                _clean(data.get("gst_password"))
            ))
            conn.commit()
            new_id = c.lastrowid
            return get_client(new_id)
    except sqlite3.IntegrityError as ie:
        if "gstin" in str(ie).lower():
            raise ValueError(f"A client with GSTIN '{gstin}' already exists.")
        if "client_code" in str(ie).lower():
            raise ValueError(f"Client code '{client_code}' already exists.")
        raise ValueError(f"Database constraint error: {ie}")

def update_client(client_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Updates an existing client."""
    init_client_db()
    existing = get_client(client_id)
    if not existing:
        raise ValueError(f"Client ID {client_id} not found.")

    gstin = data.get("gstin", existing["gstin"])
    gstin = re.sub(r'[^A-Za-z0-9]', '', gstin).upper()
    pan = data.get("pan", existing["pan"]) or extract_pan_from_gstin(gstin)
    state_code, state_name = extract_state_from_gstin(gstin)

    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("""
            UPDATE clients SET
                name = ?,
                trade_name = ?,
                gstin = ?,
                pan = ?,
                state_code = ?,
                state_name = ?,
                contact_person = ?,
                email = ?,
                phone = ?,
                address = ?,
                tally_company_name = ?,
                tally_company_id = ?,
                default_name_preference = ?,
                status = ?,
                notes = ?,
                gst_username = ?,
                gst_password = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (
                _clean(data.get("name", existing["name"])),
                _clean(data.get("trade_name", existing["trade_name"])),
                gstin,
                pan,
                state_code,
                state_name,
                _clean(data.get("contact_person", existing["contact_person"])),
                _clean(data.get("email", existing["email"])),
                _clean(data.get("phone", existing["phone"])),
                _clean(data.get("address", existing["address"])),
                _clean(data.get("tally_company_name", existing["tally_company_name"])),
                _clean(data.get("tally_company_id", existing["tally_company_id"])),
                _clean(data.get("default_name_preference", existing["default_name_preference"]), "trade").lower(),
                _clean(data.get("status", existing["status"]), "active").lower(),
                _clean(data.get("notes", existing["notes"])),
                _clean(data.get("gst_username", existing.get("gst_username", ""))),
                _clean(data.get("gst_password", existing.get("gst_password", ""))),
                client_id
            ))
            conn.commit()
            return get_client(client_id)
    except sqlite3.IntegrityError as ie:
        if "gstin" in str(ie).lower():
            raise ValueError(f"Another client already uses GSTIN '{gstin}'.")
        raise ValueError(f"Database constraint error: {ie}")

def delete_client(client_id: int) -> bool:
    """Deletes a client record and any associated return periods."""
    global _ACTIVE_CLIENT_ID
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM client_return_periods WHERE client_id = ?", (client_id,))
        c.execute("DELETE FROM client_invoice_documents WHERE client_id = ?", (client_id,))
        c.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
        deleted = c.rowcount > 0

    if deleted and _ACTIVE_CLIENT_ID == client_id:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM clients ORDER BY id ASC LIMIT 1")
            row = c.fetchone()
            _ACTIVE_CLIENT_ID = row[0] if row else None

    return deleted

def clear_active_client():
    global _ACTIVE_CLIENT_ID
    _ACTIVE_CLIENT_ID = None

def get_active_client_id() -> Optional[int]:
    global _ACTIVE_CLIENT_ID
    if _ACTIVE_CLIENT_ID is None:
        return None
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM clients WHERE id = ?", (_ACTIVE_CLIENT_ID,))
        row = c.fetchone()
        if row:
            return row[0]
    _ACTIVE_CLIENT_ID = None
    return None

def set_active_client_id(client_id: Optional[int]) -> Optional[Dict[str, Any]]:
    global _ACTIVE_CLIENT_ID
    if client_id is None:
        _ACTIVE_CLIENT_ID = None
        return None
    cl = get_client(client_id)
    if not cl:
        raise ValueError(f"Client {client_id} not found.")
    _ACTIVE_CLIENT_ID = client_id
    return cl

def clear_all_db_data() -> Dict[str, int]:
    """Truncates all data across all tables in client_memory.db and resets autoincrement."""
    init_client_db()
    deleted_counts = {}
    with get_connection() as conn:
        c = conn.cursor()
        # Find all user tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in c.fetchall()]
        for t in tables:
            c.execute(f"SELECT count(*) FROM {t}")
            cnt = c.fetchone()[0]
            c.execute(f"DELETE FROM {t}")
            deleted_counts[t] = cnt
        
        # Reset sqlite autoincrement sequence
        try:
            c.execute("DELETE FROM sqlite_sequence")
        except Exception:
            pass
        conn.commit()
    
    global _ACTIVE_CLIENT_ID
    _ACTIVE_CLIENT_ID = None
    return deleted_counts

def sync_return_periods_for_client(client_id: int, month_stats: List[Dict[str, Any]]):
    """Syncs extracted monthly GSTR-1 stats into client_return_periods."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        for m in month_stats:
            c.execute("""
            INSERT OR REPLACE INTO client_return_periods (
                client_id, financial_year, return_period, doc_count,
                b2b_count, b2c_count, cdnr_count, parties_count,
                taxable_val, cgst_val, sgst_val, igst_val, total_val, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_id,
                "2025-26",
                m["label"],
                m["total_docs"],
                m["b2b_count"],
                m["b2c_count"],
                m["cdnr_count"],
                m["parties_count"],
                m["taxable_val"],
                m["cgst_val"],
                m["sgst_val"],
                m["igst_val"],
                m["total_val"],
                m.get("filename", "")
            ))
        conn.commit()

# ---------------------------------------------------------------------------
# Invoice Documents (persisted extraction results - replaces retaining raw JSON)
# ---------------------------------------------------------------------------
def save_invoice_documents(client_id: int, period_label: str, documents: List[Dict[str, Any]]):
    """Replaces all stored documents for one client+period with the freshly extracted set."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM client_invoice_documents WHERE client_id = ? AND period_label = ?",
            (client_id, period_label)
        )
        for doc in documents:
            c.execute("""
            INSERT INTO client_invoice_documents (
                client_id, period_label, doc_type, doc_number, doc_date,
                party_gstin, party_name, total_val, taxable_val, tax_lines_json,
                original_doc_num, original_doc_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                client_id, period_label,
                doc["doc_type"], doc["doc_number"], doc["doc_date"],
                doc["party_gstin"], doc["party_name"],
                doc["total_val"], doc["taxable_val"],
                json.dumps(doc["tax_lines"]),
                doc.get("original_doc_num"), doc.get("original_doc_date")
            ))
        conn.commit()

def get_invoice_documents(client_id: int) -> List[Dict[str, Any]]:
    """Returns every stored document for a client, grouped implicitly by period_label."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM client_invoice_documents WHERE client_id = ? ORDER BY period_label",
            (client_id,)
        )
        rows = []
        for row in c.fetchall():
            d = dict(row)
            try:
                d["tax_lines"] = json.loads(d.pop("tax_lines_json") or "[]")
            except Exception:
                d["tax_lines"] = []
                d.pop("tax_lines_json", None)
            rows.append(d)
        return rows

def list_invoice_periods(client_id: int) -> List[str]:
    """Returns the distinct period labels already stored for a client."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT DISTINCT period_label FROM client_invoice_documents WHERE client_id = ?",
            (client_id,)
        )
        return [row[0] for row in c.fetchall()]

def delete_period_documents(client_id: int, period_label: str) -> bool:
    """Deletes all documents for one client+period. Returns True if anything was deleted."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM client_invoice_documents WHERE client_id = ? AND period_label = ?",
            (client_id, period_label)
        )
        c.execute(
            "DELETE FROM client_return_periods WHERE client_id = ? AND return_period = ?",
            (client_id, period_label)
        )
        conn.commit()
        return c.rowcount > 0

def clear_client_documents(client_id: int):
    """Deletes all stored documents and return-period records for a client."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM client_invoice_documents WHERE client_id = ?", (client_id,))
        c.execute("DELETE FROM client_return_periods WHERE client_id = ?", (client_id,))
        conn.commit()

# ---------------------------------------------------------------------------
# Party Mappings (Permanent storage of verified trade & legal names)
# ---------------------------------------------------------------------------
def save_party_mapping(data: Dict[str, Any]) -> Dict[str, Any]:
    """Permanently saves trade and legal name against GSTIN in database and CSV."""
    init_client_db()
    gstin = (data.get("gstin") or "").strip().upper()
    if not gstin:
        raise ValueError("GSTIN is required.")

    trade_name = (data.get("trade_name") or "").strip()
    legal_name = (data.get("legal_name") or "").strip()
    if not trade_name and not legal_name:
        raise ValueError("At least Trade Name or Legal Name is required.")

    trade_val = trade_name or legal_name
    legal_val = legal_name or trade_name

    state_code, state_name = extract_state_from_gstin(gstin)
    if data.get("state_name"):
        state_name = data["state_name"]
    if data.get("state_code"):
        state_code = data["state_code"]

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
        INSERT INTO party_mappings (
            gstin, trade_name, legal_name, state_code, state_name,
            party_name, status, taxpayer_type, reg_date, constitution,
            center_jurisdiction, state_jurisdiction, source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(gstin) DO UPDATE SET
            trade_name = excluded.trade_name,
            legal_name = excluded.legal_name,
            state_code = excluded.state_code,
            state_name = excluded.state_name,
            party_name = excluded.party_name,
            status = excluded.status,
            taxpayer_type = excluded.taxpayer_type,
            reg_date = excluded.reg_date,
            constitution = excluded.constitution,
            center_jurisdiction = excluded.center_jurisdiction,
            state_jurisdiction = excluded.state_jurisdiction,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """, (
            gstin,
            trade_val,
            legal_val,
            state_code,
            state_name,
            trade_val,
            data.get("status") or data.get("gstin_status") or "Active",
            data.get("taxpayer_type", ""),
            data.get("reg_date", ""),
            data.get("constitution", ""),
            data.get("center_jurisdiction", ""),
            data.get("state_jurisdiction", ""),
            data.get("source", "portal_verified")
        ))
        conn.commit()

    # Sync to CSV
    _sync_party_to_csv(gstin, trade_val, legal_val, state_name)

    # Reload memory cache in constants
    try:
        from constants import reload_party_mappings
        reload_party_mappings()
    except Exception:
        try:
            from gst_tally_engine.constants import reload_party_mappings
            reload_party_mappings()
        except Exception:
            pass

    # If matches a registered client, update client profile
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("""
            UPDATE clients SET 
                trade_name = ?, 
                name = CASE WHEN name LIKE 'Taxpayer %' THEN ? ELSE name END,
                updated_at = CURRENT_TIMESTAMP 
            WHERE gstin = ?
            """, (trade_val, legal_val, gstin))
            conn.commit()
    except Exception:
        pass

    return get_party_mapping(gstin)

def get_party_mapping(gstin: str) -> Optional[Dict[str, Any]]:
    init_client_db()
    clean = gstin.strip().upper()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM party_mappings WHERE gstin = ?", (clean,))
        row = c.fetchone()
        return dict(row) if row else None

def get_party_mappings_bulk(gstins: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetches multiple party mappings in one query, keyed by GSTIN. Use this instead
    of calling get_party_mapping() in a loop - each call opens its own DB connection,
    so looping it is an N+1 query pattern that gets slower the more parties there are."""
    init_client_db()
    clean = list({g.strip().upper() for g in gstins if g})
    if not clean:
        return {}
    with get_connection() as conn:
        c = conn.cursor()
        placeholders = ",".join("?" * len(clean))
        c.execute(f"SELECT * FROM party_mappings WHERE gstin IN ({placeholders})", clean)
        return {row["gstin"]: dict(row) for row in c.fetchall()}

def list_party_mappings() -> List[Dict[str, Any]]:
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM party_mappings ORDER BY updated_at DESC")
        rows = c.fetchall()
        return [dict(r) for r in rows]

def delete_party_mapping(gstin: str) -> bool:
    init_client_db()
    clean = gstin.strip().upper()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM party_mappings WHERE gstin = ?", (clean,))
        conn.commit()
        deleted = c.rowcount > 0

    # Also remove from CSV
    csv_path = CSV_PATH
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [r for r in reader if (r.get("gstin") or "").strip().upper() != clean]
            fieldnames = ["gstin", "trade_name", "state", "total_docs", "legal_name"]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except Exception:
            pass

    try:
        from constants import reload_party_mappings
        reload_party_mappings()
    except Exception:
        try:
            from gst_tally_engine.constants import reload_party_mappings
            reload_party_mappings()
        except Exception:
            pass

    return deleted

def _sync_party_to_csv(gstin: str, trade_name: str, legal_name: str, state_name: str):
    csv_path = CSV_PATH
    rows = []
    fieldnames = ["gstin", "trade_name", "state", "total_docs", "legal_name"]
    found = False
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    if (r.get("gstin") or "").strip().upper() == gstin:
                        r["trade_name"] = trade_name
                        r["legal_name"] = legal_name
                        r["state"] = state_name or r.get("state", "")
                        found = True
                    rows.append(r)
        except Exception:
            pass

    if not found:
        rows.append({
            "gstin": gstin,
            "trade_name": trade_name,
            "state": state_name,
            "total_docs": "1",
            "legal_name": legal_name
        })

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"[WARN] Failed updating party_mappings.csv: {e}")

def clear_all_party_mappings() -> int:
    """Wipes all saved party mappings from SQLite database and resets party_mappings.csv."""
    init_client_db()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) FROM party_mappings")
        count = c.fetchone()[0] or 0
        c.execute("DELETE FROM party_mappings")
        conn.commit()

    csv_path = CSV_PATH
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["gstin", "trade_name", "state", "total_docs", "legal_name"])
            writer.writeheader()
    except Exception as e:
        print(f"[WARN] Failed resetting party_mappings.csv: {e}")

    try:
        from constants import reload_party_mappings
        reload_party_mappings()
    except Exception:
        try:
            from gst_tally_engine.constants import reload_party_mappings
            reload_party_mappings()
        except Exception:
            pass

    return count
