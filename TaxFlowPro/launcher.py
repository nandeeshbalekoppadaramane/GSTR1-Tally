"""
TaxFlow Pro - Packaged App Entry Point
---------------------------------------
Entry point used by the PyInstaller build (see TaxFlowPro.spec). Starts the
FastAPI backend and opens the UI in the default browser. Running this
directly with plain `python launcher.py` also works from source.
"""

import sys
import socket
import webbrowser
import threading
import time
import traceback
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


def _first_free_port(candidates):
    """Tries each port in order and returns the first one nothing is already
    listening on. A distributed console app has no one around to explain why
    it vanished if its fixed port happens to be taken - trying a few
    alternatives means a coincidental clash doesn't stop the app outright."""
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return None


class StartupFailure(Exception):
    """Raised for a startup problem that's already fully explained on screen -
    the outer handler just needs to pause before closing, not print a traceback
    on top of the message that was already printed."""


def main():
    port = _first_free_port([8000, 8001, 8002, 8003])
    if port is None:
        print("=" * 65)
        print(" TaxFlow Pro could not start: ports 8000-8003 are all already")
        print(" in use on this machine. Close whatever else is using them")
        print(" (or another copy of TaxFlow Pro), then try again.")
        print("=" * 65)
        raise StartupFailure()

    url = f"http://127.0.0.1:{port}"

    print("=" * 65)
    print(" TaxFlow Pro - GST Accounting Suite")
    print("=" * 65)
    print(f" -> Running at: {url}")
    if port != 8000:
        print(f" -> Port 8000 was already in use, so this session is using {port} instead.")
    print(" -> Opening your browser automatically...")
    print(" -> Close this window (or press Ctrl+C) to stop the app.")
    print("=" * 65 + "\n")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    from server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except StartupFailure:
        # Message already printed in main() - just pause so it can be read.
        input("\nPress Enter to close this window...")
    except Exception:
        # A console window with nothing pausing it closes the instant the
        # process exits - on any unexpected failure that would otherwise look
        # like the app "just closing instantly" with no way to see why.
        print("\n" + "=" * 65)
        print(" TaxFlow Pro hit an unexpected error and has to close:")
        print("=" * 65)
        traceback.print_exc()
        input("\nPress Enter to close this window...")
