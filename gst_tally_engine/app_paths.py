import sys
from pathlib import Path


def is_frozen() -> bool:
    """True once this code is running inside a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def get_data_root() -> Path:
    """Directory the app reads/writes its data in (SQLite DB, party_mappings.csv,
    input_json/, output_xml/). Frozen builds use the folder next to the .exe so
    data survives restarts; running from source keeps using the project root
    (two levels above this file, which lives in gst_tally_engine/)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
