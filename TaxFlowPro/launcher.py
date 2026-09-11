"""
TaxFlow Pro - Packaged App Entry Point
---------------------------------------
Entry point used by the PyInstaller build (see TaxFlowPro.spec). Starts the
FastAPI backend and opens the UI in the default browser. Running this
directly with plain `python launcher.py` also works from source.
"""

import sys
import webbrowser
import threading
import time
from pathlib import Path

import uvicorn

# Only needed running from source - a frozen build already has every module
# resolved at build time via PyInstaller's pathex (see TaxFlowPro.spec).
if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parent
    for sub in (ROOT, ROOT / "engine"):
        if str(sub) not in sys.path:
            sys.path.insert(0, str(sub))


def open_browser(url: str):
    time.sleep(1.5)
    webbrowser.open(url)


def main():
    port = 8000
    url = f"http://127.0.0.1:{port}"

    print("=" * 65)
    print(" TaxFlow Pro - GST Accounting Suite")
    print("=" * 65)
    print(f" -> Running at: {url}")
    print(" -> Opening your browser automatically...")
    print(" -> Close this window (or press Ctrl+C) to stop the app.")
    print("=" * 65 + "\n")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    from server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
