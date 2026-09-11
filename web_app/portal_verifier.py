"""
GST Portal Taxpayer Verification Service
-----------------------------------------
Automates official GST portal taxpayer lookup, fully headless:
1. Launches headless Chrome and opens the GST login page.
2. Auto-fills the saved username/password for the active client.
3. Captures the CAPTCHA image and hands it to the UI; blocks until the user
   types the answer into our own page and submits it.
4. Submits the CAPTCHA answer and completes login (retries with a fresh
   CAPTCHA up to a few times if the answer was wrong).
5. Navigates to Search Taxpayer -> Search by GSTIN/UIN.
6. Ingests GSTINs, extracts official Legal Name, Trade Name, Status, and Jurisdictions.
7. Permanently saves Trade & Legal Name mappings into SQLite database and party_mappings.csv.

Headless mode was validated against the live portal before this was wired in -
see gst_headless_captcha_poc.py for the original proof-of-concept: navigator.webdriver
is successfully hidden, the portal shows no sign of detecting/blocking headless Chrome,
and the CAPTCHA image renders normally.
"""

import os
import time
import re
import threading
from typing import List, Dict, Optional, Any

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

import client_db

LOGIN_URL = "https://services.gst.gov.in/services/login"
CAPTCHA_TABLE_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div/div/div/div/div/form/div[5]/div/div/div/table/tbody'
MAX_CAPTCHA_ATTEMPTS = 3
CAPTCHA_ANSWER_TIMEOUT_SECONDS = 300

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


def _initialize_driver() -> webdriver.Chrome:
    """Initializes headless Chrome. This exact configuration (headless=new + the
    navigator.webdriver patch) was validated against the live GST portal in
    gst_headless_captcha_poc.py before being wired into the app."""
    import tempfile
    temp_profile_dir = tempfile.mkdtemp(prefix="gst_chrome_clean_")

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={temp_profile_dir}")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    driver = None
    custom_candidates = [
        r"C:\chromedriver-win64\chromedriver.exe",
        r"C:\Program Files\Google\Chrome\Application\chromedriver.exe",
        r"C:\tools\chromedriver.exe"
    ]
    for cand in custom_candidates:
        if os.path.exists(cand):
            try:
                service = Service(executable_path=cand)
                driver = webdriver.Chrome(service=service, options=options)
                break
            except Exception:
                pass
    if driver is None:
        driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.set_page_load_timeout(60)
    return driver


def _fill_credentials(driver: webdriver.Chrome, wait: WebDriverWait, username: str, password: str):
    """Fills username/password - this is what makes the CAPTCHA table appear on the page."""
    user_elem = wait.until(EC.visibility_of_element_located((By.ID, "username")))
    user_elem.clear()
    user_elem.send_keys(username)
    driver.execute_script(
        "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
        user_elem
    )

    pass_elem = wait.until(EC.visibility_of_element_located((By.ID, "user_pass")))
    pass_elem.clear()
    pass_elem.send_keys(password)
    driver.execute_script(
        "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
        pass_elem
    )


def _capture_captcha_image(driver: webdriver.Chrome, wait: WebDriverWait) -> Optional[str]:
    """Waits for the CAPTCHA table to render and returns a base64 PNG of just the
    CAPTCHA image - falls back to a screenshot of the whole table if no <img> tag
    is found inside it (e.g. if the portal changes to canvas rendering)."""
    try:
        table = wait.until(EC.visibility_of_element_located((By.XPATH, CAPTCHA_TABLE_XPATH)))
    except Exception:
        return None
    try:
        img = table.find_element(By.TAG_NAME, "img")
        return img.screenshot_as_base64
    except Exception:
        try:
            return table.screenshot_as_base64
        except Exception:
            return None


def _submit_captcha_and_login(driver: webdriver.Chrome, answer: str):
    """Types the CAPTCHA answer into the login form and submits it."""
    cap_elem = driver.find_element(By.ID, "captcha")
    cap_elem.clear()
    cap_elem.send_keys(answer)
    driver.execute_script(
        "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
        cap_elem
    )

    login_btn_xpath = (
        "//button[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')] | "
        "//input[@type='submit']"
    )
    try:
        btn = driver.find_element(By.XPATH, login_btn_xpath)
        try:
            btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
    except Exception:
        cap_elem.send_keys(Keys.RETURN)


def _is_login_successful(driver: webdriver.Chrome) -> bool:
    try:
        cur_url = driver.current_url.lower()
        return ("services/auth" in cur_url or "fowelcome" in cur_url) and "accessdenied" not in cur_url
    except Exception:
        return False


def _find_first(driver: webdriver.Chrome, xpaths: List[str], timeout: float = 0):
    """Tries each xpath IN ORDER, polling all of them together for up to `timeout`
    seconds if none are immediately present. Always re-checks earlier (more
    specific/confirmed) xpaths before later fallbacks on every poll tick - unlike
    an XPath union ('a | b'), which returns whichever matches first in DOCUMENT
    ORDER regardless of which alternative you actually listed first, and unlike
    waiting out a full timeout on xpath 1 before ever trying xpath 2."""
    deadline = time.time() + timeout
    while True:
        for xp in xpaths:
            try:
                els = driver.find_elements(By.XPATH, xp)
                if els:
                    return els[0]
            except Exception:
                continue
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


def _click(driver: webdriver.Chrome, element):
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def _handle_aadhaar_popup(driver: webdriver.Chrome) -> bool:
    """Handles the optional Aadhaar/Remind Me Later pop-up. Confirmed-working exact
    xpath tried first, generic text-matching fallbacks only if that's not found."""
    btn = _find_first(driver, [
        '//*[@id="caNumpopupV"]/div/div/div[2]/a[2]',
        '//div[@id="caNumpopupV"]//a[2]',
        '//button[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "remind")]',
        '//a[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "remind")]',
        '//button[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "later")]',
    ], timeout=4)
    if not btn:
        return False
    _click(driver, btn)
    time.sleep(1.2)
    return True


def _navigate_to_search_page(driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
    """Navigates to Search Taxpayer -> Search by GSTIN/UIN by clicking through the
    real menu, exactly the way a human does:
      1. Dismiss the Aadhaar popup if it appears: //*[@id="caNumpopupV"]/div/div/div[2]/a[2]
      2. Click Search Taxpayer:                  //*[@id="main"]/ul/li[5]/a
      3. Click Search by GSTIN/UIN:               //*[@id="main"]/ul/li[5]/ul[1]/li[1]/a

    This deliberately never hard-navigates (driver.get()) straight to a sub-route.
    The GST portal is an Angular SPA - a fresh page load of a sub-route doesn't
    carry the app's client-side session/routing state, so it unreliably bounces
    back to login or lands somewhere wrong. Only clicking through the menu works.
    """
    try:
        input_elem = driver.find_element(By.ID, "for_gstin")
        if input_elem.is_displayed():
            return True
    except Exception:
        pass

    _handle_aadhaar_popup(driver)

    try:
        if "accessdenied" in driver.current_url.lower():
            dash = _find_first(driver, [
                "//a[contains(@class, 'navbar-brand')]",
                "//a[contains(text(), 'Dashboard')]",
            ])
            if dash:
                _click(driver, dash)
                time.sleep(1.5)
                _handle_aadhaar_popup(driver)

        search_taxpayer_menu = _find_first(driver, [
            '//*[@id="main"]/ul/li[5]/a',
            '//a[contains(normalize-space(), "Search Taxpayer")]',
        ], timeout=20)
        if not search_taxpayer_menu:
            return False

        try:
            ActionChains(driver).move_to_element(search_taxpayer_menu).perform()
        except Exception:
            pass
        _click(driver, search_taxpayer_menu)
        time.sleep(0.5)

        search_by_gstin_link = _find_first(driver, [
            '//*[@id="main"]/ul/li[5]/ul[1]/li[1]/a',
            '//a[contains(normalize-space(), "Search by GSTIN")]',
        ], timeout=20)
        if not search_by_gstin_link:
            return False
        _click(driver, search_by_gstin_link)

        wait.until(EC.visibility_of_element_located((By.ID, "for_gstin")))
        return True
    except Exception:
        # Fallback: check if direct search field is already visible
        try:
            WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "for_gstin")))
            return True
        except Exception:
            return False


def _extract_jurisdictions(driver: webdriver.Chrome) -> tuple:
    """Extracts Central & State Jurisdictions using multi-tier heuristics."""
    center_jur, state_jur = "", ""

    # Tier 1: Angular JS Scope
    try:
        js_data = driver.execute_script("""
            try {
                var el = document.querySelector('#lottable') ||
                         document.querySelector('#partners') ||
                         document.querySelector('form[name="searchtax"]') ||
                         document.querySelector('#for_gstin');
                if (window.angular && el) {
                    var scope = angular.element(el).scope();
                    if (scope && scope.searchTax_Payload) {
                        var p = scope.searchTax_Payload;
                        return {
                            admOfc: p.admOfc || [],
                            othrOfc: p.othrOfc || [],
                            stj: p.stj || "",
                            ctj: p.ctj || ""
                        };
                    }
                }
            } catch(e) {}
            return null;
        """)
        if js_data:
            adm = js_data.get("admOfc", [])
            othr = js_data.get("othrOfc", [])
            adm_text = ", ".join(str(x).strip() for x in adm if str(x).strip()) if isinstance(adm, list) else str(adm).strip()
            othr_text = ", ".join(str(x).strip() for x in othr if str(x).strip()) if isinstance(othr, list) else str(othr).strip()

            if any(k in adm_text.upper() for k in ["COMMISSIONERATE", "DIVISION", "RANGE", "CENTRE", "CENTRAL"]):
                center_jur = adm_text
                state_jur = othr_text
            elif any(k in othr_text.upper() for k in ["COMMISSIONERATE", "DIVISION", "RANGE", "CENTRE", "CENTRAL"]):
                center_jur = othr_text
                state_jur = adm_text
            else:
                center_jur = adm_text
                state_jur = othr_text

            if not center_jur and js_data.get("ctj"):
                center_jur = str(js_data.get("ctj")).strip()
            if not state_jur and js_data.get("stj"):
                state_jur = str(js_data.get("stj")).strip()
    except Exception:
        pass

    if center_jur and state_jur:
        return center_jur, state_jur

    # Tier 2: DOM via ul.jurisdictList
    try:
        uls = driver.find_elements(By.XPATH, "//ul[contains(@class, 'jurisdictList')]")
        for ul in uls:
            items_str = ul.text.strip().replace("\n", ", ")
            if not items_str:
                continue
            if any(k in items_str.upper() for k in ["COMMISSIONERATE", "DIVISION", "RANGE"]):
                center_jur = items_str
            elif any(k in items_str.upper() for k in ["CIRCLE", "WARD", "ZONE"]):
                state_jur = items_str
    except Exception:
        pass

    return center_jur, state_jur


def _lookup_gstin(driver: webdriver.Chrome, wait: WebDriverWait, gstin: str, previous_legal_name: str = "") -> Dict[str, Any]:
    """Looks up single GSTIN and returns extracted details."""
    input_field = driver.find_element(By.ID, "for_gstin")
    input_field.clear()
    input_field.send_keys(str(gstin).strip())

    search_btn = driver.find_element(By.ID, "lotsearch")
    try:
        search_btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", search_btn)

    # Dynamic wait for results
    start_t = time.time()
    result_status = "timeout"
    legal_name = ""

    while time.time() - start_t < 6.0:
        try:
            err_elem = driver.find_element(
                By.XPATH,
                "//label[contains(text(), 'Invalid GSTIN') or contains(text(), 'No records found') or contains(text(), 'not found')]"
            )
            if err_elem.is_displayed():
                return {
                    "status": "error",
                    "legal_name": "",
                    "trade_name": "",
                    "gstin_status": "Invalid",
                    "remarks": err_elem.text.strip()
                }
        except Exception:
            pass

        try:
            legal_elem = driver.find_element(
                By.XPATH,
                "//p[normalize-space(text())='Legal Name of Business']/following-sibling::p/strong"
            )
            text = legal_elem.text.strip()
            if text:
                if (not previous_legal_name) or text != previous_legal_name or (time.time() - start_t >= 0.8):
                    result_status = "success"
                    legal_name = text
                    break
        except Exception:
            pass

        time.sleep(0.08)

    if result_status != "success":
        # Fallback check
        try:
            legal_elem = driver.find_element(
                By.XPATH,
                "//p[normalize-space(text())='Legal Name of Business']/following-sibling::p/strong"
            )
            text = legal_elem.text.strip()
            if text:
                result_status = "success"
                legal_name = text
        except Exception:
            pass

    if result_status != "success":
        return {
            "status": "timeout",
            "legal_name": "",
            "trade_name": "",
            "gstin_status": "Timeout",
            "remarks": "Portal timeout"
        }

    # Trade Name
    trade_name = ""
    try:
        trade_elem = driver.find_element(By.XPATH, "//p[normalize-space(text())='Trade Name']/following-sibling::p/strong")
        trade_name = trade_elem.text.strip()
    except Exception:
        pass

    # Status
    gstin_status = "Active"
    try:
        st_elem = driver.find_element(By.XPATH, "//p[normalize-space(text())='GSTIN / UIN Status']/following-sibling::p/strong")
        gstin_status = st_elem.text.strip()
    except Exception:
        pass

    # Taxpayer Type
    taxpayer_type = ""
    try:
        type_elem = driver.find_element(By.XPATH, "//p[span[normalize-space(text())='Taxpayer Type']]/following-sibling::p/strong | //p[normalize-space(text())='Taxpayer Type']/following-sibling::p/strong")
        taxpayer_type = type_elem.text.strip()
    except Exception:
        pass

    # Date of Reg
    reg_date = ""
    try:
        d_elem = driver.find_element(By.XPATH, "//p[normalize-space(text())='Date of Registration']/following-sibling::p/strong")
        reg_date = d_elem.text.strip()
    except Exception:
        pass

    # Constitution
    constitution = ""
    try:
        c_elem = driver.find_element(By.XPATH, "//p[normalize-space(text())='Constitution of Business']/following-sibling::p/strong")
        constitution = c_elem.text.strip()
    except Exception:
        pass

    center_jur, state_jur = _extract_jurisdictions(driver)

    trade_cleaned = trade_name if trade_name and trade_name.upper() not in ("NA", "N/A", "-", "--", "NONE") else legal_name
    legal_cleaned = legal_name if legal_name and legal_name.upper() not in ("NA", "N/A", "-", "--", "NONE") else trade_cleaned

    return {
        "status": "success",
        "legal_name": legal_cleaned,
        "trade_name": trade_cleaned,
        "gstin_status": gstin_status,
        "taxpayer_type": taxpayer_type,
        "reg_date": reg_date,
        "constitution": constitution,
        "center_jurisdiction": center_jur,
        "state_jurisdiction": state_jur,
        "remarks": "Success"
    }


class PortalVerificationService:
    def __init__(self):
        self._lock = threading.Lock()
        self.is_running: bool = False
        self.driver: Optional[webdriver.Chrome] = None
        # idle, starting, waiting_captcha, navigating, verifying, completed, error, cancelled
        self.status: str = "idle"
        self.should_stop: bool = False
        self.progress: int = 0
        self.total: int = 0
        self.current_gstin: str = ""
        self.logs: List[str] = []
        self.results: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None
        self.captcha_image_b64: Optional[str] = None
        self.captcha_answer: Optional[str] = None
        self._worker_thread: Optional[threading.Thread] = None

    def _log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        print(f"[PortalVerifier] {entry}")
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 100:
                self.logs.pop(0)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "progress": self.progress,
                "total": self.total,
                "current_gstin": self.current_gstin,
                "logs": list(self.logs),
                "results": list(self.results),
                "error": self.error_message,
                "captcha_image_b64": self.captcha_image_b64,
            }

    def submit_captcha(self, answer: str):
        """Called from the API once the user has read the CAPTCHA image shown on our
        own page and typed in what they see."""
        clean = (answer or "").strip()
        with self._lock:
            self.captcha_answer = clean
        self._log(f"CAPTCHA answer submitted: '{clean}'")

    def _wait_for_captcha_answer(self, timeout_seconds: int = CAPTCHA_ANSWER_TIMEOUT_SECONDS) -> Optional[str]:
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
        """Completely resets verifier state to clean idle state."""
        self.cancel()
        with self._lock:
            self.status = "idle"
            self.is_running = False
            self.should_stop = False
            self.progress = 0
            self.total = 0
            self.current_gstin = ""
            self.logs = []
            self.results = []
            self.error_message = None
            self.captcha_image_b64 = None
            self.captcha_answer = None

    def cancel(self):
        """Cancels verification session and waits for the worker thread to actually
        stop before releasing control, so a subsequent start() can't race the old
        thread's own cleanup (which would otherwise fight over self.driver)."""
        self._log("Cancellation requested by user.")
        self.should_stop = True
        self.status = "cancelled"
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        self.is_running = False

    def start(self, gstins: List[str], gst_username: Optional[str] = None, gst_password: Optional[str] = None) -> bool:
        if not gst_username or not gst_password:
            self._log("Error: GST Portal username & password are required - headless mode has "
                       "no visible browser window for manual login.")
            with self._lock:
                self.status = "error"
                self.error_message = "GST Portal username & password are required. Save them on the client profile first."
                self.is_running = False
            return False

        with self._lock:
            if self.is_running or (self._worker_thread and self._worker_thread.is_alive()):
                return False
            self.is_running = True
            self.status = "starting"
            self.should_stop = False
            self.progress = 0
            self.total = len(gstins)
            self.current_gstin = ""
            self.logs = []
            self.results = []
            self.error_message = None
            self.captcha_image_b64 = None
            self.captcha_answer = None

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(gstins, gst_username, gst_password),
            daemon=True
        )
        self._worker_thread.start()
        return True

    def _worker_loop(self, gstins: List[str], gst_username: str, gst_password: str):
        clean_gstins = [re.sub(r'[^A-Za-z0-9]', '', g).upper() for g in gstins if g]
        clean_gstins = [g for g in clean_gstins if len(g) == 15]

        if not clean_gstins:
            self._log("Error: No valid 15-character GSTINs provided.")
            with self._lock:
                self.status = "error"
                self.error_message = "No valid 15-character GSTINs provided."
                self.is_running = False
            return

        with self._lock:
            self.total = len(clean_gstins)

        self._log(f"Starting GSTIN portal verification for {len(clean_gstins)} taxpayers...")
        try:
            self._log("Launching headless Chrome...")
            self.driver = _initialize_driver()
            standard_wait = WebDriverWait(self.driver, 20)

            self._log(f"Opening GST portal login page: {LOGIN_URL}")
            self.driver.get(LOGIN_URL)

            self._log(f"Entering saved credentials for user '{gst_username}'...")
            _fill_credentials(self.driver, standard_wait, gst_username, gst_password)

            login_success = False
            for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
                if self.should_stop:
                    self._log("Verification aborted before login.")
                    return

                captcha_b64 = _capture_captcha_image(self.driver, standard_wait)
                if not captcha_b64:
                    raise RuntimeError("Could not locate the CAPTCHA image on the login page.")

                with self._lock:
                    self.captcha_image_b64 = captcha_b64
                    self.captcha_answer = None
                    self.status = "waiting_captcha"
                self._log(f"CAPTCHA ready (attempt {attempt}/{MAX_CAPTCHA_ATTEMPTS}). "
                          f"Waiting for the answer to be submitted...")

                answer = self._wait_for_captcha_answer()
                if answer is None:
                    if self.should_stop:
                        self._log("Verification cancelled while waiting for CAPTCHA.")
                        return
                    raise TimeoutError("Timed out waiting for the CAPTCHA answer.")

                self._log("CAPTCHA answer received. Submitting login...")
                with self._lock:
                    self.status = "starting"
                _submit_captcha_and_login(self.driver, answer)

                # Poll for success instead of one fixed sleep+check - the portal's
                # post-login redirect can take a variable amount of time, and
                # checking too early misreads a still-redirecting page as a failure.
                login_ok = False
                poll_start = time.time()
                while time.time() - poll_start < 8.0:
                    if _is_login_successful(self.driver):
                        login_ok = True
                        break
                    time.sleep(0.5)

                if login_ok:
                    self._log(f"Login successful! URL: {self.driver.current_url}")
                    login_success = True
                    break

                self._log(f"Login attempt {attempt} failed (incorrect CAPTCHA or portal error). "
                          f"URL was: {self.driver.current_url}")
                if attempt < MAX_CAPTCHA_ATTEMPTS:
                    self._log("Reloading the login page for a fresh CAPTCHA...")
                    self.driver.get(LOGIN_URL)
                    _fill_credentials(self.driver, standard_wait, gst_username, gst_password)

            if not login_success:
                raise RuntimeError(f"Login failed after {MAX_CAPTCHA_ATTEMPTS} CAPTCHA attempts.")

            with self._lock:
                self.captcha_image_b64 = None
                self.status = "navigating"

            self._log("Handling post-login popups and navigating to Search Taxpayer...")
            time.sleep(2.5)  # let the post-login dashboard fully settle before clicking its menu
            _handle_aadhaar_popup(self.driver)

            nav_ok = _navigate_to_search_page(self.driver, standard_wait)
            if not nav_ok:
                self._log("First navigation attempt failed, retrying once...")
                time.sleep(2.0)
                _handle_aadhaar_popup(self.driver)
                nav_ok = _navigate_to_search_page(self.driver, standard_wait)
            if not nav_ok:
                raise RuntimeError(
                    f"Failed to navigate to Search Taxpayer page after login. Current URL: {self.driver.current_url}"
                )

            self._log("Search Taxpayer page is active and ready. Beginning extraction loop...")
            with self._lock:
                self.status = "verifying"

            last_legal = ""
            for idx, gstin in enumerate(clean_gstins, start=1):
                if self.should_stop:
                    self._log("Verification stopped by user.")
                    break

                with self._lock:
                    self.current_gstin = gstin
                    self.progress = idx - 1

                self._log(f"[{idx}/{len(clean_gstins)}] Verifying {gstin}...")

                try:
                    if not self.driver.find_elements(By.ID, "for_gstin"):
                        _navigate_to_search_page(self.driver, standard_wait)

                    data = _lookup_gstin(self.driver, standard_wait, gstin, previous_legal_name=last_legal)

                    if data["status"] == "timeout":
                        self._log(f"Timeout for {gstin}, retrying once...")
                        time.sleep(0.8)
                        if not self.driver.find_elements(By.ID, "for_gstin"):
                            _navigate_to_search_page(self.driver, standard_wait)
                        data = _lookup_gstin(self.driver, standard_wait, gstin, previous_legal_name=last_legal)

                    if data["status"] == "success":
                        last_legal = data["legal_name"]
                        trade_name = data["trade_name"]
                        legal_name = data["legal_name"]
                        st_name = STATE_CODES.get(gstin[:2], "India")

                        # Permanently save to DB and CSV!
                        client_db.save_party_mapping({
                            "gstin": gstin,
                            "trade_name": trade_name,
                            "legal_name": legal_name,
                            "state_name": st_name,
                            "gstin_status": data.get("gstin_status", "Active"),
                            "taxpayer_type": data.get("taxpayer_type", ""),
                            "reg_date": data.get("reg_date", ""),
                            "constitution": data.get("constitution", ""),
                            "center_jurisdiction": data.get("center_jurisdiction", ""),
                            "state_jurisdiction": data.get("state_jurisdiction", ""),
                            "source": "portal_verified"
                        })

                        res_item = {
                            "gstin": gstin,
                            "trade_name": trade_name,
                            "legal_name": legal_name,
                            "status": "Verified",
                            "gstin_status": data.get("gstin_status", "Active"),
                            "center_jurisdiction": data.get("center_jurisdiction", ""),
                            "state_jurisdiction": data.get("state_jurisdiction", ""),
                            "saved": True
                        }
                        self._log(f"-> SUCCESS: {gstin} | Trade: '{trade_name}' | Legal: '{legal_name}' [SAVED]")
                    else:
                        res_item = {
                            "gstin": gstin,
                            "trade_name": "N/A",
                            "legal_name": "N/A",
                            "status": data.get("remarks", "Failed"),
                            "gstin_status": data.get("gstin_status", "Error"),
                            "saved": False
                        }
                        self._log(f"-> FAILED: {gstin} ({data.get('remarks', 'Error')})")

                    with self._lock:
                        self.results.append(res_item)
                        self.progress = idx

                except Exception as item_ex:
                    self._log(f"Error checking {gstin}: {item_ex}")
                    with self._lock:
                        self.results.append({
                            "gstin": gstin,
                            "trade_name": "Error",
                            "legal_name": "Error",
                            "status": str(item_ex),
                            "saved": False
                        })
                        self.progress = idx

            self._log("All GSTINs processed. Finishing up...")
            with self._lock:
                self.status = "completed"

        except Exception as e:
            self._log(f"Critical error during verification: {e}")
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
            with self._lock:
                self.is_running = False
                self.current_gstin = ""
                self.captcha_image_b64 = None
            self._log("Portal verification session ended.")


# Global Singleton Service
verifier_service = PortalVerificationService()
