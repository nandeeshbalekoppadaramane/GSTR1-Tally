"""
GST Portal Taxpayer Verification Service
-----------------------------------------
Automates official GST portal taxpayer lookup:
1. Launches Chrome browser to GST login page.
2. Waits for user to log in (or accepts manual confirmation).
3. Automatically handles Aadhaar / 'Remind Me Later' popup.
4. Navigates to Search Taxpayer -> Search by GSTIN/UIN.
5. Ingests GSTINs, extracts official Legal Name, Trade Name, Status, and Jurisdictions.
6. Permanently saves Trade & Legal Name mappings into SQLite database and party_mappings.csv.
"""

import os
import sys
import time
import re
import threading
from pathlib import Path
from typing import List, Dict, Optional, Any

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    WebDriverException
)

import client_db

LOGIN_URL = "https://services.gst.gov.in/services/login"
AUTHENTICATED_SEARCH_URL = "https://services.gst.gov.in/services/auth/searchtp"

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


def _switch_to_default_desktop():
    """Ensures current thread is bound to interactive 'default' desktop."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hDesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if hDesk:
            user32.SetThreadDesktop(hDesk)
    except Exception as e:
        print(f"[Desktop] Notice: {e}")


def _find_chrome_executable() -> Optional[str]:
    """Finds Google Chrome executable path on Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return None


def _get_free_port() -> int:
    """Finds a free local TCP port for Chrome DevTools Protocol."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _bring_window_to_foreground(driver: webdriver.Chrome):
    """Reliably brings the Selenium Chrome window to the foreground on Windows."""
    _switch_to_default_desktop()
    try:
        driver.switch_to.window(driver.current_window_handle)
    except Exception:
        pass

    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        def enum_cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.lower()
                if "gst" in title or "goods & services tax" in title or "login" in title or "chrome" in title:
                    user32.AllowSetForegroundWindow(-1)
                    fore_hwnd = user32.GetForegroundWindow()
                    fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, 0)
                    cur_thread = kernel32.GetCurrentThreadId()

                    if fore_thread != cur_thread:
                        user32.AttachThreadInput(cur_thread, fore_thread, True)
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.SetFocus(hwnd)
                        user32.AttachThreadInput(cur_thread, fore_thread, False)
                    else:
                        user32.ShowWindow(hwnd, 9)
                        user32.SetForegroundWindow(hwnd)
                        user32.BringWindowToTop(hwnd)
                        user32.SetFocus(hwnd)

                    # Momentarily set TOPMOST then NOTOPMOST to force window over full-screen apps
                    SWP_FLAGS = 0x0002 | 0x0001 | 0x0040
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP_FLAGS)
                    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP_FLAGS)
                    user32.SetForegroundWindow(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as e:
        print(f"[WARN] Failed bringing Chrome window to foreground: {e}")


def _initialize_driver() -> tuple:
    """
    Initializes modern Chrome WebDriver.
    On Windows, uses Win32 CreateProcessW with lpDesktop='WinSta0\\default' to ensure
    Chrome opens directly on the user's interactive desktop, and attaches via Chrome DevTools.
    Returns (driver, chrome_process_info).
    """
    import tempfile
    temp_profile_dir = tempfile.mkdtemp(prefix="gst_chrome_clean_")

    if sys.platform == "win32":
        chrome_exe = _find_chrome_executable()
        if chrome_exe:
            try:
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.windll.kernel32
                user32 = ctypes.windll.user32

                class STARTUPINFOW(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("lpReserved", wintypes.LPWSTR),
                        ("lpDesktop", wintypes.LPWSTR),
                        ("lpTitle", wintypes.LPWSTR),
                        ("dwX", wintypes.DWORD),
                        ("dwY", wintypes.DWORD),
                        ("dwXSize", wintypes.DWORD),
                        ("dwYSize", wintypes.DWORD),
                        ("dwXCountChars", wintypes.DWORD),
                        ("dwYCountChars", wintypes.DWORD),
                        ("dwFillAttribute", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD),
                        ("wShowWindow", wintypes.WORD),
                        ("cbReserved2", wintypes.WORD),
                        ("lpReserved2", ctypes.c_char_p),
                        ("hStdInput", wintypes.HANDLE),
                        ("hStdOutput", wintypes.HANDLE),
                        ("hStdError", wintypes.HANDLE),
                    ]

                class PROCESS_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("hProcess", wintypes.HANDLE),
                        ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD),
                        ("dwThreadId", wintypes.DWORD),
                    ]

                _switch_to_default_desktop()
                port = _get_free_port()

                si = STARTUPINFOW()
                si.cb = ctypes.sizeof(STARTUPINFOW)
                si.lpDesktop = r"WinSta0\default"
                pi = PROCESS_INFORMATION()

                cmd_line = (
                    f'"{chrome_exe}" '
                    f'--remote-debugging-port={port} '
                    f'--user-data-dir="{temp_profile_dir}" '
                    f'--new-window '
                    f'--start-maximized '
                    f'--no-first-run '
                    f'--no-default-browser-check '
                    f'--disable-infobars '
                    f'--disable-extensions '
                    f'--window-position=30,30 '
                    f'--window-size=1366,850 '
                    f'"{LOGIN_URL}"'
                )

                created = kernel32.CreateProcessW(
                    None, cmd_line, None, None, False, 0, None, None,
                    ctypes.byref(si), ctypes.byref(pi)
                )

                if created:
                    time.sleep(1.2)
                    options = webdriver.ChromeOptions()
                    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
                    driver = webdriver.Chrome(options=options)
                    if driver.window_handles:
                        driver.switch_to.window(driver.window_handles[0])
                    driver.set_page_load_timeout(60)
                    return driver, pi
            except Exception as e:
                print(f"[Driver] Win32 CreateProcess launch fallback due to: {e}")

    # Standard fallback
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={temp_profile_dir}")
    options.add_argument("--new-window")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1366,850")
    options.add_argument("--window-position=30,30")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
        "chrome.autofill.enabled": False
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

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
                driver.set_page_load_timeout(60)
                return driver, None
            except Exception:
                pass

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver, None


def _handle_aadhaar_popup(driver: webdriver.Chrome) -> bool:
    """Handles the optional Aadhaar/Remind Me Later pop-up."""
    short_wait = WebDriverWait(driver, 4)
    popup_xpath = (
        '//*[@id="caNumpopupV"]/div/div/div[2]/a[2] | '
        '//div[@id="caNumpopupV"]//a[2] | '
        '//button[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "remind")] | '
        '//a[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "remind")] | '
        '//button[contains(translate(normalize-space(), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "later")]'
    )
    try:
        remind_me_btn = short_wait.until(EC.element_to_be_clickable((By.XPATH, popup_xpath)))
        try:
            remind_me_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", remind_me_btn)
        time.sleep(1.2)
        return True
    except Exception:
        return False


def _navigate_to_search_page(driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
    """Navigates to Search Taxpayer -> Search by GSTIN/UIN."""
    try:
        input_elem = driver.find_element(By.ID, "for_gstin")
        if input_elem.is_displayed():
            return True
    except Exception:
        pass

    _handle_aadhaar_popup(driver)

    try:
        cur_url = driver.current_url.lower()
        if "services/auth" in cur_url:
            if "searchtp" not in cur_url:
                driver.get(AUTHENTICATED_SEARCH_URL)
                time.sleep(1.2)
                _handle_aadhaar_popup(driver)
            if driver.find_elements(By.ID, "for_gstin") and driver.find_element(By.ID, "for_gstin").is_displayed():
                return True
    except Exception:
        pass

    try:
        if "accessdenied" in driver.current_url.lower():
            try:
                dash = driver.find_element(By.XPATH, "//a[contains(text(), 'Dashboard')] | //a[contains(@class, 'navbar-brand')]")
                dash.click()
                time.sleep(1.5)
                _handle_aadhaar_popup(driver)
            except Exception:
                pass

        search_taxpayer_xpath = '//*[@id="main"]/ul/li[5]/a | //a[contains(normalize-space(), "Search Taxpayer")]'
        search_taxpayer_menu = wait.until(EC.presence_of_element_located((By.XPATH, search_taxpayer_xpath)))

        try:
            ActionChains(driver).move_to_element(search_taxpayer_menu).perform()
        except Exception:
            pass

        try:
            search_taxpayer_menu.click()
        except Exception:
            driver.execute_script("arguments[0].click();", search_taxpayer_menu)

        time.sleep(0.5)

        search_gstin_xpath = '//*[@id="main"]/ul/li[5]/ul[1]/li[1]/a | //a[contains(normalize-space(), "Search by GSTIN")]'
        search_by_gstin_link = wait.until(EC.presence_of_element_located((By.XPATH, search_gstin_xpath)))

        try:
            search_by_gstin_link.click()
        except Exception:
            driver.execute_script("arguments[0].click();", search_by_gstin_link)

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
        self._chrome_pi: Optional[Any] = None
        self.status: str = "idle"  # idle, starting, waiting_login, navigating, verifying, completed, error, cancelled
        self.login_confirmed: bool = False
        self.should_stop: bool = False
        self.progress: int = 0
        self.total: int = 0
        self.current_gstin: str = ""
        self.logs: List[str] = []
        self.results: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None
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
                "error": self.error_message
            }

    def confirm_login(self):
        """User manual click indicating they have finished login in Chrome."""
        self._log("User manual login confirmation received.")
        self.login_confirmed = True

    def reset(self):
        """Completely resets verifier state to clean idle state."""
        self.cancel()
        with self._lock:
            self.status = "idle"
            self.is_running = False
            self.should_stop = False
            self.login_confirmed = False
            self.progress = 0
            self.total = 0
            self.current_gstin = ""
            self.logs = []
            self.results = []
            self.error_message = None

    def cancel(self):
        """Cancels verification session and cleanly shuts down Chrome."""
        self._log("Cancellation requested by user.")
        self.should_stop = True
        self.status = "cancelled"
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        if hasattr(self, "_chrome_pi") and self._chrome_pi:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.TerminateProcess(self._chrome_pi.hProcess, 0)
                kernel32.CloseHandle(self._chrome_pi.hProcess)
                kernel32.CloseHandle(self._chrome_pi.hThread)
            except Exception:
                pass
            self._chrome_pi = None
        self.is_running = False

    def start(self, gstins: List[str], gst_username: Optional[str] = None, gst_password: Optional[str] = None) -> bool:
        with self._lock:
            # If thread finished or died, clear running flag
            if self._worker_thread and not self._worker_thread.is_alive():
                self.is_running = False
                self._worker_thread = None

            # If previous run was completed, error, or cancelled, reset cleanly
            if self.status in ("completed", "cancelled", "error", "idle"):
                self.is_running = False

            # If still marked running, gracefully cancel previous run and proceed
            if self.is_running:
                self.should_stop = True
                if self.driver:
                    try:
                        self.driver.quit()
                    except Exception:
                        pass
                    self.driver = None
                if hasattr(self, "_chrome_pi") and self._chrome_pi:
                    try:
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        kernel32.TerminateProcess(self._chrome_pi.hProcess, 0)
                        kernel32.CloseHandle(self._chrome_pi.hProcess)
                        kernel32.CloseHandle(self._chrome_pi.hThread)
                    except Exception:
                        pass
                    self._chrome_pi = None
                self.is_running = False

            self.is_running = True
            self.status = "starting"
            self.should_stop = False
            self.login_confirmed = False
            self.progress = 0
            self.total = len(gstins)
            self.current_gstin = ""
            self.logs = []
            self.results = []
            self.error_message = None

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(gstins, gst_username, gst_password),
            daemon=True
        )
        self._worker_thread.start()
        return True

    def _worker_loop(self, gstins: List[str], gst_username: Optional[str] = None, gst_password: Optional[str] = None):
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
            self._log("Launching Chrome browser...")
            self.driver, self._chrome_pi = _initialize_driver()
            standard_wait = WebDriverWait(self.driver, 20)

            self._log(f"Opening GST portal login page: {LOGIN_URL}")
            self.driver.get(LOGIN_URL)
            with self._lock:
                self.status = "waiting_login"

            # Pop Chrome window to the front immediately
            _bring_window_to_foreground(self.driver)

            # Auto-enter credentials if provided
            if gst_username and gst_password:
                self._log(f"Auto-entering GST Portal credentials for user '{gst_username}'...")
                try:
                    time.sleep(0.6)
                    user_elem = standard_wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="username"]')))
                    user_elem.clear()
                    user_elem.send_keys(gst_username)
                    self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", user_elem)

                    pass_elem = standard_wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="user_pass"]')))
                    pass_elem.clear()
                    pass_elem.send_keys(gst_password)
                    self.driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", pass_elem)

                    time.sleep(0.3)
                    # Automatically put cursor focus on CAPTCHA field
                    try:
                        cap_wait = WebDriverWait(self.driver, 5)
                        cap_elem = cap_wait.until(EC.visibility_of_element_located((By.ID, "captcha")))
                        cap_elem.click()
                        cap_elem.clear()
                    except Exception:
                        pass

                    _bring_window_to_foreground(self.driver)
                    self._log("CREDENTIALS AUTO-ENTERED! Cursor is focused on CAPTCHA box.")
                    self._log(">> ACTION: Switch to the Chrome window, type the 6-character CAPTCHA, and hit Enter <<")
                except Exception as fill_err:
                    _bring_window_to_foreground(self.driver)
                    self._log(f"Auto-fill notice: {fill_err}. Please enter credentials manually in Chrome.")
            else:
                _bring_window_to_foreground(self.driver)
                self._log(">> CHROME IS OPEN: Awaiting manual login in Chrome window (Username, Password & CAPTCHA)...")

            # Polling loop for login
            login_detected = False
            poll_start = time.time()
            max_wait_seconds = 300  # 5 minutes to log in

            while not login_detected and not self.should_stop:
                if self.login_confirmed:
                    self._log("Manual confirmation flag detected.")
                    login_detected = True
                    break

                try:
                    cur_url = self.driver.current_url.lower()

                    # While still on login page, user is NOT logged in yet
                    if "services/login" not in cur_url and "accessdenied" not in cur_url:
                        if "services/auth" in cur_url or "fowelcome" in cur_url:
                            self._log(f"Login successfully detected on portal! URL: {self.driver.current_url}")
                            login_detected = True
                            break
                        if len(self.driver.find_elements(By.XPATH, "//a[contains(translate(text(), 'LOGOUT', 'logout'), 'logout')] | //button[contains(translate(text(), 'LOGOUT', 'logout'), 'logout')] | //a[contains(text(), 'Dashboard')]")) > 0:
                            self._log("Login successfully detected via authenticated navigation elements!")
                            login_detected = True
                            break
                except Exception as e:
                    if "no such window" in str(e).lower():
                        raise RuntimeError("Browser window was closed by user.")

                if time.time() - poll_start > max_wait_seconds:
                    raise TimeoutError("Login timed out after 5 minutes.")

                time.sleep(1.0)

            if self.should_stop:
                self._log("Verification aborted before navigation.")
                return

            with self._lock:
                self.status = "navigating"

            self._log("Handling post-login popups and navigating to Search Taxpayer...")
            time.sleep(1.5)
            _handle_aadhaar_popup(self.driver)

            nav_ok = _navigate_to_search_page(self.driver, standard_wait)
            if not nav_ok:
                raise RuntimeError("Failed to navigate to Search Taxpayer page. Please ensure you are logged in.")

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
                        saved_rec = client_db.save_party_mapping({
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
            if hasattr(self, "_chrome_pi") and self._chrome_pi:
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    kernel32.TerminateProcess(self._chrome_pi.hProcess, 0)
                    kernel32.CloseHandle(self._chrome_pi.hProcess)
                    kernel32.CloseHandle(self._chrome_pi.hThread)
                except Exception:
                    pass
                self._chrome_pi = None
            with self._lock:
                self.is_running = False
                self.current_gstin = ""
            self._log("Portal verification session ended.")


# Global Singleton Service
verifier_service = PortalVerificationService()
