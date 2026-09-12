"""
GSTR-2B Portal Downloader
-------------------------
Automates downloading GSTR-2B JSON returns straight from the GST portal for a
client's selected months, reusing the same headless-Chrome + on-page-CAPTCHA
login already proven in portal_verifier.py (login form fill, CAPTCHA capture/
submit, Aadhaar popup handling, and resilient element lookup are imported from
there rather than duplicated).

Flow per session: login -> Returns Dashboard -> for each selected month, select
FY/period, open the GSTR-2B tile, click the JSON download button, wait for the
file, hand its parsed content to the caller's `ingest_fn`, then delete the file
(nothing downloaded is ever kept on disk - same no-storage policy as manual
uploads).

Navigation (post-login landing -> Returns Dashboard -> the GSTR-2B tile -> the
JSON download button) uses exact xpaths supplied after a live walkthrough of
the portal. Each one falls back to a text/heuristic-based lookup if the exact
xpath ever stops matching (portal markup does shift over time), but the exact
xpath is tried first since it is what was actually confirmed to work.
"""

import os
import re
import time
import json
import zipfile
import tempfile
import shutil
import threading
from typing import List, Dict, Optional, Any, Callable, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.common.action_chains import ActionChains

import portal_verifier as pv

DOWNLOAD_JSON_XPATH = '/html/body/app-root/div[2]/app-gstr2bdwld/block-ui/div/div[2]/div/div[5]/button'

# Confirmed against a live portal session - see module docstring.
RETURNS_DASHBOARD_LINK_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div[2]/div/div[1]/div[3]/div[2]/div[1]/button'
SEARCH_BUTTON_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div[2]/form/div/div[4]/button'
GSTR2B_TILE_HEADER_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div[4]/div[3]/div[1]/div[3]/div/div/a/div/p[1]'
# The tile's DOWNLOAD button, confirmed via screenshot to sit next to VIEW on the
# "Auto-drafted ITC Statement for the month GSTR-2B" card - VIEW instead opens a
# different (PDF/Excel-only) summary page.
GSTR2B_DOWNLOAD_BUTTON_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div[4]/div[3]/div[1]/div[3]/div/div/div/div/div[2]/button'

FY_SELECT_CSS = 'select[name="fin"]'
QUARTER_SELECT_CSS = 'select[name="quarter"]'
PERIOD_SELECT_CSS = 'select[name="mon"]'

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}
FISCAL_MONTH_ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


def _quarter_for(month: int) -> str:
    if 4 <= month <= 6:
        return "Quarter 1"
    if 7 <= month <= 9:
        return "Quarter 2"
    if 10 <= month <= 12:
        return "Quarter 3"
    return "Quarter 4"


def _canon(text: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', (text or '').upper())


def _norm_label(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip().upper()


def _select_text_contains(select_obj: Select, wanted_text: str) -> bool:
    """Selects the option whose label equals or contains `wanted_text`, case/space
    insensitively - mirrors the extension's own robust dropdown matching."""
    wanted = _norm_label(wanted_text)
    for opt in select_obj.options:
        label = _norm_label(opt.text)
        if label == wanted or wanted in label:
            select_obj.select_by_visible_text(opt.text)
            return True
    return False


def _get_select(driver, css: str) -> Optional[Select]:
    try:
        el = driver.find_element(By.CSS_SELECTOR, css)
        return Select(el)
    except Exception:
        return None


def _wait_dashboard_controls(driver, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_select(driver, FY_SELECT_CSS) and _get_select(driver, PERIOD_SELECT_CSS):
            return True
        time.sleep(0.4)
    return False


def _navigate_to_returns_dashboard(driver, wait: WebDriverWait) -> bool:
    """Post-login landing page -> Returns Dashboard."""
    if _wait_dashboard_controls(driver, timeout=2.0):
        return True

    pv._handle_aadhaar_popup(driver)

    # Confirmed exact button from a live walkthrough, tried first.
    try:
        exact_btn = driver.find_element(By.XPATH, RETURNS_DASHBOARD_LINK_XPATH)
        if exact_btn.is_displayed():
            pv._click(driver, exact_btn)
            time.sleep(2)
            if _wait_dashboard_controls(driver, timeout=10):
                return True
    except Exception:
        pass

    quick_link = pv._find_first(driver, [
        "//a[contains(normalize-space(),'File Returns')]",
        "//a[contains(normalize-space(),'Return Dashboard')]",
        "//a[contains(normalize-space(),'Returns Dashboard')]",
        "//button[contains(normalize-space(),'File Returns')]",
    ], timeout=3)
    if quick_link:
        pv._click(driver, quick_link)
        time.sleep(2)
        if _wait_dashboard_controls(driver, timeout=10):
            return True

    services_menu = pv._find_first(driver, [
        "//a[contains(normalize-space(),'Services') and not(contains(normalize-space(),'Services Plus'))]",
    ], timeout=10)
    if services_menu:
        try:
            ActionChains(driver).move_to_element(services_menu).perform()
        except Exception:
            pv._click(driver, services_menu)
        time.sleep(0.6)

        returns_menu = pv._find_first(driver, [
            "//a[contains(normalize-space(),'Returns') and not(contains(normalize-space(),'Dashboard'))]",
        ], timeout=5)
        if returns_menu:
            try:
                ActionChains(driver).move_to_element(returns_menu).perform()
            except Exception:
                pv._click(driver, returns_menu)
            time.sleep(0.6)

        dash_link = pv._find_first(driver, [
            "//a[contains(normalize-space(),'Returns Dashboard')]",
            "//a[contains(normalize-space(),'Return Dashboard')]",
        ], timeout=8)
        if dash_link:
            pv._click(driver, dash_link)
            time.sleep(2)

    return _wait_dashboard_controls(driver, timeout=10)


def _find_search_button(driver):
    try:
        exact_btn = driver.find_element(By.XPATH, SEARCH_BUTTON_XPATH)
        if exact_btn.is_displayed():
            return exact_btn
    except Exception:
        pass
    return pv._find_first(driver, [
        "//button[contains(translate(normalize-space(.), "
        "'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'SEARCH')]",
        "//button[@type='submit']",
    ], timeout=5)


def _find_back_button(driver):
    return pv._find_first(driver, [
        "//button[contains(translate(normalize-space(.), "
        "'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'BACK')]",
    ], timeout=5)


def _recover_to_dashboard(driver, wait, log_fn) -> bool:
    """Makes sure the next month starts from the Returns Dashboard, regardless of
    where the previous month left off (including mid-failure, where no BACK click
    ever happened). Tries increasingly forceful options until the dashboard's own
    controls are actually present."""
    if _wait_dashboard_controls(driver, timeout=2.0):
        return True

    for _ in range(3):
        back_btn = _find_back_button(driver)
        if back_btn:
            pv._click(driver, back_btn)
        else:
            try:
                driver.back()
            except Exception:
                pass
        time.sleep(1.5)
        if _wait_dashboard_controls(driver, timeout=5.0):
            return True

    log_fn("Could not click back to the dashboard - re-navigating via the menu...")
    return _navigate_to_returns_dashboard(driver, wait)


def _find_gstr2b_download_button(driver):
    """Locates the GSTR-2B tile's DOWNLOAD button (leads to the app-gstr2bdwld
    JSON/Excel page) - NOT its VIEW button, which leads to a different
    PDF/Excel-only summary page. Confirmed via screenshot: both buttons sit
    side by side on the "Auto-drafted ITC Statement for the month GSTR-2B" card.
    Tries the exact header/button pair from a live walkthrough first - the
    header is checked for "GSTR-2B" before trusting its sibling button, since
    tile ordering on the dashboard can shift. Falls back to a text/heuristic
    scan for a DOWNLOAD control if that pair doesn't match (e.g. the portal's
    markup moves)."""
    try:
        header = driver.find_element(By.XPATH, GSTR2B_TILE_HEADER_XPATH)
        if "GSTR-2B" in _canon(header.text) or "GSTR2B" in _canon(header.text):
            btn = driver.find_element(By.XPATH, GSTR2B_DOWNLOAD_BUTTON_XPATH)
            if btn.is_displayed():
                return btn
    except Exception:
        pass

    return _find_gstr2b_download_button_fallback(driver)


def _find_gstr2b_download_button_fallback(driver):
    """Ported from the working Chrome extension's findTileButton: the GSTR-2B
    Monthly tile is the smallest container carrying both marker phrases and a
    DOWNLOAD control, excluding the separate 'Quarterly View' tile."""
    needles = [_canon(x) for x in ("GSTR-2B", "Auto-drafted ITC Statement")]
    banned = [_canon("Quarterly View")]

    best_len = None
    best_btn = None
    for container in driver.find_elements(By.XPATH, "//div | //section"):
        try:
            text = container.text or ""
        except Exception:
            continue
        if not text:
            continue
        hay = _canon(text)
        if not all(n in hay for n in needles):
            continue
        if any(b in hay for b in banned):
            continue

        match = None
        for ctrl in container.find_elements(By.XPATH, ".//button | .//a"):
            try:
                if _canon(ctrl.text) == _canon("DOWNLOAD"):
                    match = ctrl
                    break
            except Exception:
                continue
        if not match:
            continue

        if best_len is None or len(text) < best_len:
            best_len = len(text)
            best_btn = match

    return best_btn


def _find_json_download_button(driver):
    try:
        el = driver.find_element(By.XPATH, DOWNLOAD_JSON_XPATH)
        if el.is_displayed():
            return el
    except Exception:
        pass
    for b in driver.find_elements(By.TAG_NAME, "button"):
        try:
            t = (b.text or "").upper()
        except Exception:
            continue
        if "JSON" in t and ("DOWNLOAD" in t or "GENERATE" in t):
            return b
    return None


def _find_ready_download_link(driver):
    """The 'click here to download' link that appears once a GENERATE step
    finishes - same pattern already known from GSTR-1's JSON download."""
    for el in driver.find_elements(By.XPATH, "//a | //span | //button"):
        try:
            t = (el.text or "").strip().lower()
        except Exception:
            continue
        if "click here to download" in t:
            return el
    return None


def _dump_page_buttons(driver, limit: int = 20) -> str:
    """Diagnostic snapshot of what's actually on the page when a button hunt
    fails, so a failed run's log tells you what to fix instead of just that
    it failed."""
    try:
        texts = []
        for b in driver.find_elements(By.TAG_NAME, "button"):
            try:
                t = (b.text or "").strip()
            except Exception:
                continue
            if t:
                texts.append(t)
        shown = texts[:limit]
        more = f" (+{len(texts) - limit} more)" if len(texts) > limit else ""
        return "; ".join(shown) + more if shown else "(no buttons with visible text found)"
    except Exception as e:
        return f"(could not enumerate buttons: {e})"


def _wait_for_new_file(directory: str, before: set, timeout: float = 60.0) -> Optional[str]:
    deadline = time.time() + timeout
    candidate = None
    while time.time() < deadline:
        try:
            current = set(os.listdir(directory))
        except Exception:
            current = set()
        new_ones = [f for f in (current - before) if not f.endswith(".crdownload")]
        if new_ones:
            candidate = os.path.join(directory, new_ones[0])
            try:
                size1 = os.path.getsize(candidate)
                time.sleep(0.5)
                size2 = os.path.getsize(candidate)
                if size1 == size2 and size1 > 0:
                    return candidate
            except Exception:
                pass
        time.sleep(0.5)
    return candidate


def _read_and_delete(path: str) -> Tuple[dict, str]:
    """Parses the downloaded file (raw JSON or a zip containing one) and removes
    it from disk immediately - downloaded returns are never kept as files,
    matching the rest of the app's no-stored-input policy."""
    fname = os.path.basename(path)
    try:
        if fname.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
                if not json_names:
                    raise RuntimeError("Downloaded zip did not contain a JSON file.")
                with zf.open(json_names[0]) as jf:
                    payload = json.load(jf)
        else:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        return payload, fname
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


class GSTR2BDownloadService:
    """Mirrors PortalVerificationService's shape (state polling + CAPTCHA relay)
    so the frontend can reuse the identical pattern for a second, independent
    Selenium session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.is_running: bool = False
        self.driver: Optional[webdriver.Chrome] = None
        self.download_dir: Optional[str] = None
        # idle, starting, waiting_captcha, navigating, downloading, completed, error, cancelled
        self.status: str = "idle"
        self.should_stop: bool = False
        self.progress: int = 0
        self.total: int = 0
        self.current_month: str = ""
        self.logs: List[str] = []
        self.results: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None
        self.captcha_image_b64: Optional[str] = None
        self.captcha_answer: Optional[str] = None
        self._worker_thread: Optional[threading.Thread] = None

    def _log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        print(f"[GSTR2BDownloader] {entry}")
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 150:
                self.logs.pop(0)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "progress": self.progress,
                "total": self.total,
                "current_month": self.current_month,
                "logs": list(self.logs),
                "results": list(self.results),
                "error": self.error_message,
                "captcha_image_b64": self.captcha_image_b64,
            }

    def submit_captcha(self, answer: str):
        clean = (answer or "").strip()
        with self._lock:
            self.captcha_answer = clean
        self._log(f"CAPTCHA answer submitted: '{clean}'")

    def _wait_for_captcha_answer(self, timeout_seconds: int = pv.CAPTCHA_ANSWER_TIMEOUT_SECONDS) -> Optional[str]:
        poll_start = time.time()
        while not self.should_stop:
            with self._lock:
                if self.captcha_answer:
                    return self.captcha_answer
            if time.time() - poll_start > timeout_seconds:
                return None
            time.sleep(0.4)
        return None

    def reset(self):
        self.cancel()
        with self._lock:
            self.status = "idle"
            self.is_running = False
            self.should_stop = False
            self.progress = 0
            self.total = 0
            self.current_month = ""
            self.logs = []
            self.results = []
            self.error_message = None
            self.captcha_image_b64 = None
            self.captcha_answer = None

    def cancel(self):
        self._log("Cancellation requested by user.")
        self.should_stop = True
        self.status = "cancelled"
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        if self.download_dir:
            shutil.rmtree(self.download_dir, ignore_errors=True)
            self.download_dir = None
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        self.is_running = False

    def start(
        self,
        fy: str,
        months: List[int],
        gst_username: Optional[str],
        gst_password: Optional[str],
        ingest_fn: Callable[[str, dict], Optional[Tuple[str, int]]],
        on_month_done: Optional[Callable[[], None]] = None,
        debug: bool = False,
    ) -> bool:
        if not gst_username or not gst_password:
            self._log("Error: GST Portal username & password are required.")
            with self._lock:
                self.status = "error"
                self.error_message = "GST Portal username & password are required. Save them on the client profile first."
                self.is_running = False
            return False

        clean_months = sorted({int(m) for m in months if 1 <= int(m) <= 12}, key=FISCAL_MONTH_ORDER.index)
        if not clean_months:
            self._log("Error: No valid months selected.")
            with self._lock:
                self.status = "error"
                self.error_message = "No valid months selected."
                self.is_running = False
            return False

        with self._lock:
            if self.is_running or (self._worker_thread and self._worker_thread.is_alive()):
                return False
            self.is_running = True
            self.status = "starting"
            self.should_stop = False
            self.progress = 0
            self.total = len(clean_months)
            self.current_month = ""
            self.logs = []
            self.results = []
            self.error_message = None
            self.captcha_image_b64 = None
            self.captcha_answer = None

        if debug:
            self._log("Debug mode: Chrome will open as a VISIBLE window instead of headless.")

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(fy, clean_months, gst_username, gst_password, ingest_fn, on_month_done, debug),
            daemon=True
        )
        self._worker_thread.start()
        return True

    def _worker_loop(
        self,
        fy: str,
        months: List[int],
        gst_username: str,
        gst_password: str,
        ingest_fn: Callable[[str, dict], Optional[Tuple[str, int]]],
        on_month_done: Optional[Callable[[], None]],
        debug: bool = False,
    ):
        self.download_dir = tempfile.mkdtemp(prefix="gst_2b_downloads_")
        self._log(f"Starting GSTR-2B download for FY {fy}, months: "
                  f"{', '.join(MONTH_NAMES[m] for m in months)}")
        try:
            self._log(f"Launching {'visible' if debug else 'headless'} Chrome...")
            self.driver = pv._initialize_driver(download_dir=self.download_dir, headless=not debug)
            standard_wait = WebDriverWait(self.driver, 20)

            self._log(f"Opening GST portal login page: {pv.LOGIN_URL}")
            self.driver.get(pv.LOGIN_URL)

            self._log(f"Entering saved credentials for user '{gst_username}'...")
            pv._fill_credentials(self.driver, standard_wait, gst_username, gst_password)

            login_success = False
            for attempt in range(1, pv.MAX_CAPTCHA_ATTEMPTS + 1):
                if self.should_stop:
                    self._log("Download aborted before login.")
                    return

                captcha_b64 = pv._capture_captcha_image(self.driver, standard_wait)
                if not captcha_b64:
                    raise RuntimeError("Could not locate the CAPTCHA image on the login page.")

                with self._lock:
                    self.captcha_image_b64 = captcha_b64
                    self.captcha_answer = None
                    self.status = "waiting_captcha"
                self._log(f"CAPTCHA ready (attempt {attempt}/{pv.MAX_CAPTCHA_ATTEMPTS}). Waiting for the answer...")

                answer = self._wait_for_captcha_answer()
                if answer is None:
                    if self.should_stop:
                        self._log("Download cancelled while waiting for CAPTCHA.")
                        return
                    raise TimeoutError("Timed out waiting for the CAPTCHA answer.")

                self._log("CAPTCHA answer received. Submitting login...")
                with self._lock:
                    self.status = "starting"
                pv._submit_captcha_and_login(self.driver, answer)

                login_ok = False
                poll_start = time.time()
                while time.time() - poll_start < 8.0:
                    if pv._is_login_successful(self.driver):
                        login_ok = True
                        break
                    time.sleep(0.5)

                if login_ok:
                    self._log(f"Login successful! URL: {self.driver.current_url}")
                    login_success = True
                    break

                self._log(f"Login attempt {attempt} failed (incorrect CAPTCHA or portal error).")
                if attempt < pv.MAX_CAPTCHA_ATTEMPTS:
                    self._log("Reloading the login page for a fresh CAPTCHA...")
                    self.driver.get(pv.LOGIN_URL)
                    pv._fill_credentials(self.driver, standard_wait, gst_username, gst_password)

            if not login_success:
                raise RuntimeError(f"Login failed after {pv.MAX_CAPTCHA_ATTEMPTS} CAPTCHA attempts.")

            with self._lock:
                self.captcha_image_b64 = None
                self.status = "navigating"

            self._log("Navigating to the Returns Dashboard...")
            time.sleep(2.0)
            if not _navigate_to_returns_dashboard(self.driver, standard_wait):
                raise RuntimeError("Could not reach the Returns Dashboard after login.")

            with self._lock:
                self.status = "downloading"

            for idx, month in enumerate(months, start=1):
                if self.should_stop:
                    self._log("Download stopped by user.")
                    break

                mname = MONTH_NAMES[month]
                with self._lock:
                    self.current_month = mname
                    self.progress = idx - 1
                self._log(f"[{idx}/{len(months)}] Fetching GSTR-2B for {mname} {fy}...")

                try:
                    doc_count, saved_type = self._download_one_month(fy, month, ingest_fn)
                    if doc_count is None:
                        res_item = {"month": mname, "status": "No GSTR-2B available", "doc_count": 0}
                        self._log(f"-> No GSTR-2B tile/data found for {mname} {fy}. Skipping.")
                    else:
                        res_item = {"month": mname, "status": "Downloaded & Imported", "doc_count": doc_count}
                        self._log(f"-> SUCCESS: {mname} {fy} - {doc_count} document(s) imported as {saved_type}.")
                        if on_month_done:
                            on_month_done()
                except Exception as month_ex:
                    res_item = {"month": mname, "status": f"Failed: {month_ex}", "doc_count": 0}
                    self._log(f"-> FAILED: {mname} {fy}: {month_ex}")

                with self._lock:
                    self.results.append(res_item)
                    self.progress = idx

                # Whatever happened above (success, "no tile", or a mid-page failure
                # that never reached the BACK click), the next month must start from
                # the dashboard - otherwise it inherits whatever page this one left
                # the browser on and fails immediately with a confusing error.
                is_last = idx == len(months)
                if not is_last and not self.should_stop:
                    if not _recover_to_dashboard(self.driver, standard_wait, self._log):
                        self._log("Could not return to the Returns Dashboard - stopping remaining months.")
                        break

            self._log("All selected months processed. Finishing up...")
            with self._lock:
                self.status = "completed"

        except Exception as e:
            self._log(f"Critical error during download: {e}")
            with self._lock:
                self.status = "error"
                self.error_message = str(e)

        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
            if self.download_dir:
                shutil.rmtree(self.download_dir, ignore_errors=True)
                self.download_dir = None
            with self._lock:
                self.is_running = False
                self.current_month = ""
                self.captcha_image_b64 = None
            self._log("GSTR-2B download session ended.")

    def _download_one_month(
        self, fy: str, month: int, ingest_fn: Callable[[str, dict], Optional[Tuple[str, int]]]
    ) -> Tuple[Optional[int], Optional[str]]:
        driver = self.driver
        mname = MONTH_NAMES[month]

        if not _wait_dashboard_controls(driver, timeout=15):
            raise RuntimeError("Returns Dashboard controls not found.")

        # The FY <select> can exist before Angular finishes populating its
        # <option> list (especially right after navigating back from a previous
        # month), so poll for the option to actually be offered rather than
        # selecting on the first look.
        fy_sel = None
        deadline = time.time() + 10
        while time.time() < deadline:
            fy_sel = _get_select(driver, FY_SELECT_CSS)
            if fy_sel and any(_norm_label(fy) in _norm_label(o.text) for o in fy_sel.options):
                break
            time.sleep(0.3)
        if not fy_sel or not _select_text_contains(fy_sel, fy):
            raise RuntimeError(f"FY '{fy}' not available in the dashboard's FY dropdown.")
        time.sleep(1.5)

        q_sel = _get_select(driver, QUARTER_SELECT_CSS)
        if q_sel:
            _select_text_contains(q_sel, _quarter_for(month))
            time.sleep(1.2)

        period_sel = None
        deadline = time.time() + 10
        while time.time() < deadline:
            period_sel = _get_select(driver, PERIOD_SELECT_CSS)
            if period_sel and any(_norm_label(mname) in _norm_label(o.text) for o in period_sel.options):
                break
            time.sleep(0.3)
        if not period_sel or not _select_text_contains(period_sel, mname):
            raise RuntimeError(f"Month '{mname}' not available in the Period dropdown for FY {fy}.")
        time.sleep(0.5)

        search_btn = _find_search_button(driver)
        if not search_btn:
            raise RuntimeError("SEARCH button not found on the Returns Dashboard.")
        pv._click(driver, search_btn)
        time.sleep(2.5)

        download_btn = None
        deadline = time.time() + 15
        while time.time() < deadline:
            download_btn = _find_gstr2b_download_button(driver)
            if download_btn:
                break
            time.sleep(0.75)
        if not download_btn:
            return None, None  # no GSTR-2B tile for this period - not an error

        pv._click(driver, download_btn)

        dl_btn = None
        deadline = time.time() + 15
        while time.time() < deadline:
            dl_btn = _find_json_download_button(driver)
            if dl_btn:
                break
            time.sleep(0.75)
        if not dl_btn:
            self._log(f"Buttons visible on the GSTR-2B download page: {_dump_page_buttons(driver)}")
            raise RuntimeError("GSTR-2B JSON download button not found on the download page.")

        before_files = set(os.listdir(self.download_dir))
        pv._click(driver, dl_btn)

        # The button reads "GENERATE JSON FILE TO DOWNLOAD" - some accounts get an
        # instant download, others (like GSTR-1's JSON) show a separate "click here
        # to download" link once generation finishes. Try the instant case first,
        # then fall back to hunting for that link.
        new_file = _wait_for_new_file(self.download_dir, before_files, timeout=20)
        if not new_file:
            ready_link = _find_ready_download_link(driver)
            if ready_link:
                self._log("JSON generated - clicking the ready download link...")
                pv._click(driver, ready_link)
                new_file = _wait_for_new_file(self.download_dir, before_files, timeout=45)

        if not new_file:
            # No file ever appeared after Generate - most likely there is simply no
            # GSTR-2B data for this period, not an error. Note it and move on.
            self._log(f"No JSON file appeared for {mname} - treating as no GSTR-2B data available.")
            back_btn = _find_back_button(driver)
            if back_btn:
                pv._click(driver, back_btn)
            else:
                driver.back()
            time.sleep(1.5)
            return None, None

        payload, fname = _read_and_delete(new_file)

        back_btn = _find_back_button(driver)
        if back_btn:
            pv._click(driver, back_btn)
        else:
            driver.back()
        time.sleep(1.5)

        result = ingest_fn(fname, payload)
        if not result:
            return 0, None
        saved_type, doc_count = result
        return doc_count, saved_type


downloader_service = GSTR2BDownloadService()
