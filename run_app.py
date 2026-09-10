"""
GSTR-1 to TallyPrime Web App Launcher
-------------------------------------
Launches the FastAPI backend and serves the interactive HTML UI.
Opens http://localhost:8000 in your browser automatically.
"""

import sys
import webbrowser
import threading
import time
from pathlib import Path
import uvicorn

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "web_app") not in sys.path:
    sys.path.insert(0, str(ROOT / "web_app"))

def open_browser(url: str):
    time.sleep(1.5)
    print(f"\nOpening browser at: {url}")
    webbrowser.open(url)

def main():
    port = 8000
    url = f"http://localhost:{port}"
    
    print("=" * 65)
    print(" GSTR-1 TO TALLYPRIME WEB APPLICATION ")
    print("=" * 65)
    print(f" -> Backend API & Frontend UI running at: {url}")
    print(" -> Press Ctrl + C in this terminal to stop the server.")
    print("=" * 65 + "\n")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    from web_app.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    main()
