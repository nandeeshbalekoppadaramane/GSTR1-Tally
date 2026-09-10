import sqlite3
import os
import csv
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from pathlib import Path

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

@dataclass
class ExecutionContext:
    """Tracks runtime state across agent processing loops."""
    batch_id: str
    gstr_type: str
    fy: str
    current_index: int = 0
    total_records: int = 0
    status: str = "INITIALIZED"
    metadata: Dict[str, Any] = field(default_factory=dict)

class MappingMemoryContext:
    """SQLite-backed memory store for persisting party Trade Name, Legal Name, and ledger mappings."""

    def __init__(self, db_path: str = "client_memory.db"):
        self.db_path = db_path
        self._init_db()
        self.sync_from_csv()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS party_mappings 
                         (gstin TEXT PRIMARY KEY, 
                          trade_name TEXT, 
                          legal_name TEXT, 
                          party_name TEXT, 
                          state_name TEXT)''')
            # Add trade_name and legal_name columns if upgrading existing table
            c.execute("PRAGMA table_info(party_mappings)")
            existing_cols = [row[1] for row in c.fetchall()]
            if "trade_name" not in existing_cols:
                c.execute("ALTER TABLE party_mappings ADD COLUMN trade_name TEXT")
            if "legal_name" not in existing_cols:
                c.execute("ALTER TABLE party_mappings ADD COLUMN legal_name TEXT")

            c.execute('''CREATE TABLE IF NOT EXISTS execution_checkpoints 
                         (batch_id TEXT PRIMARY KEY, last_processed_index INTEGER, status TEXT)''')
            conn.commit()

    def sync_from_csv(self, csv_path: str = "party_mappings.csv"):
        """Syncs all party records from CSV directly into SQLite."""
        p = Path(csv_path)
        if not p.exists():
            return
        try:
            with open(p, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                with sqlite3.connect(self.db_path) as conn:
                    c = conn.cursor()
                    for row in reader:
                        gstin = (row.get("gstin") or "").strip()
                        if not gstin:
                            continue
                        trade = (row.get("trade_name") or "").strip()
                        legal = (row.get("legal_name") or "").strip()
                        state = (row.get("state") or STATE_CODES.get(gstin[:2], "Unknown State")).strip()
                        primary_name = trade if trade and trade.upper() not in ("N/A", "-", "--", "NONE") else legal
                        c.execute('''INSERT OR REPLACE INTO party_mappings 
                                     (gstin, trade_name, legal_name, party_name, state_name) 
                                     VALUES (?, ?, ?, ?, ?)''',
                                  (gstin, trade, legal, primary_name, state))
                    conn.commit()
        except Exception:
            pass

    def save_party_mapping(self, gstin: str, trade_name: str = "", legal_name: str = ""):
        state_name = STATE_CODES.get(gstin[:2], "Unknown State") if len(gstin) >= 2 else "Unknown State"
        primary_name = trade_name if trade_name and trade_name.upper() not in ("N/A", "-", "--", "NONE") else legal_name
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO party_mappings 
                         (gstin, trade_name, legal_name, party_name, state_name) 
                         VALUES (?, ?, ?, ?, ?)''',
                      (gstin, trade_name, legal_name, primary_name, state_name))
            conn.commit()

    def get_party_mapping(self, gstin: str, preference: str = "TRADE") -> Optional[str]:
        """
        Returns standardized ledger name: 'Name (GSTIN)' without state suffix.
        - If preference is 'TRADE': uses Trade Name, falls back to Legal Name if N/A.
        - If preference is 'LEGAL': uses Legal Name, falls back to Trade Name if N/A.
        """
        if not gstin:
            return "B2C Sales"
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT trade_name, legal_name, state_name FROM party_mappings WHERE gstin = ?", (gstin,))
            row = c.fetchone()
            if row:
                t_name = (row[0] or "").strip()
                l_name = (row[1] or "").strip()
                na_list = ("PARTY", "NA", "N/A", "N.A.", "NONE", "NOT APPLICABLE", "NOT AVAILABLE", "-", "--", "")
                if t_name.upper() in na_list:
                    t_name = ""
                if l_name.upper() in na_list:
                    l_name = ""

                if preference.upper() == "LEGAL":
                    chosen = l_name if l_name else t_name
                else:
                    chosen = t_name if t_name else l_name

                if not chosen:
                    chosen = gstin
                return f"{chosen} ({gstin})"
        return gstin

    def get_party_legal_name(self, gstin: str) -> str:
        """Returns the official legal name for a GSTIN from the database."""
        if not gstin:
            return ""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT legal_name, trade_name FROM party_mappings WHERE gstin = ?", (gstin,))
            row = c.fetchone()
            if row:
                return (row[0] or row[1] or "").strip()
        return ""

    def save_checkpoint(self, batch_id: str, index: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO execution_checkpoints (batch_id, last_processed_index, status) VALUES (?, ?, ?)",
                      (batch_id, index, status))
            conn.commit()

    def get_checkpoint(self, batch_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT last_processed_index FROM execution_checkpoints WHERE batch_id = ?", (batch_id,))
            row = c.fetchone()
            return row[0] if row else 0