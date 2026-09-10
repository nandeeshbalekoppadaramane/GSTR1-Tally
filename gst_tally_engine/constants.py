# Master State Code Lookup Dictionary
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

def get_state_name(gstin: str) -> str:
    """Resolves two-digit state code prefix to state name."""
    if not gstin or len(gstin) < 2:
        return "Unknown State"
    return STATE_CODES.get(gstin[:2], "Unknown State")

import os
import csv

PARTY_MAPPINGS_FILE = os.path.join(os.path.dirname(__file__), "..", "party_mappings.csv")
_PARTY_MAP = {}
_PARTY_LEGAL_MAP = {}
_NAME_PREFERENCE = "TRADE"

NA_PLACEHOLDERS = ("PARTY", "NA", "N/A", "N.A.", "NONE", "NOT APPLICABLE", "NOT AVAILABLE", "-", "--", "")

def reload_party_mappings():
    global _PARTY_MAP, _PARTY_LEGAL_MAP
    _PARTY_MAP.clear()
    _PARTY_LEGAL_MAP.clear()
    if os.path.exists(PARTY_MAPPINGS_FILE):
        try:
            with open(PARTY_MAPPINGS_FILE, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    gstin = (row.get("gstin") or row.get("GSTIN") or "").strip()
                    trade = (row.get("trade_name") or row.get("tradeName") or row.get("party_name") or "").strip()
                    legal = (row.get("legal_name") or row.get("legalName") or "").strip()
                    if gstin:
                        if trade and trade.upper() not in NA_PLACEHOLDERS:
                            _PARTY_MAP[gstin] = trade
                        if legal and legal.upper() not in NA_PLACEHOLDERS:
                            _PARTY_LEGAL_MAP[gstin] = legal
        except Exception:
            pass

    # Also load from SQLite client_memory.db for permanent persistence
    db_path = os.path.join(os.path.dirname(__file__), "..", "client_memory.db")
    if os.path.exists(db_path):
        try:
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='party_mappings'")
                if c.fetchone():
                    c.execute("SELECT gstin, trade_name, legal_name FROM party_mappings")
                    for row in c.fetchall():
                        gstin = (row[0] or "").strip()
                        trade = (row[1] or "").strip()
                        legal = (row[2] or "").strip()
                        if gstin:
                            if trade and trade.upper() not in NA_PLACEHOLDERS:
                                _PARTY_MAP[gstin] = trade
                            if legal and legal.upper() not in NA_PLACEHOLDERS:
                                _PARTY_LEGAL_MAP[gstin] = legal
        except Exception:
            pass

reload_party_mappings()

def set_name_preference(pref: str):
    """Sets ledger naming preference: 'TRADE' or 'LEGAL'."""
    global _NAME_PREFERENCE
    if pref and pref.strip().upper() in ("LEGAL", "TRADE"):
        _NAME_PREFERENCE = pref.strip().upper()

def get_name_preference() -> str:
    return _NAME_PREFERENCE

def get_party_legal_name(gstin: str) -> str:
    """Returns the legal entity name if available, else falls back to trade name."""
    gstin = (gstin or "").strip()
    return _PARTY_LEGAL_MAP.get(gstin, _PARTY_MAP.get(gstin, ""))

def get_party_trade_name(gstin: str) -> str:
    """Returns the trade name if available, else falls back to legal name."""
    gstin = (gstin or "").strip()
    return _PARTY_MAP.get(gstin, _PARTY_LEGAL_MAP.get(gstin, ""))

def format_party_ledger(trade_name: str, gstin: str, preference: str = None) -> str:
    """
    Formats standardized party ledger string: 'Name (GSTIN)' without state suffix.
    - If preference is 'TRADE': uses Trade Name; if empty/NA, defaults to Legal Name.
    - If preference is 'LEGAL': uses Legal Name; if empty/NA, defaults to Trade Name.
    - Appends (GSTIN) to the party name.
    - State suffix is removed as requested.
    - For B2C (empty gstin): returns 'B2C Sales'.
    """
    if not gstin:
        return "B2C Sales"

    pref = (preference or _NAME_PREFERENCE).upper()
    gstin = gstin.strip()

    t_name = _PARTY_MAP.get(gstin, trade_name or "").strip()
    l_name = _PARTY_LEGAL_MAP.get(gstin, "").strip()

    if t_name.upper() in NA_PLACEHOLDERS:
        t_name = ""
    if l_name.upper() in NA_PLACEHOLDERS:
        l_name = ""

    if pref == "LEGAL":
        chosen_name = l_name if l_name else t_name
    else:  # Default TRADE
        chosen_name = t_name if t_name else l_name

    if not chosen_name:
        return gstin

    return f"{chosen_name} ({gstin})"