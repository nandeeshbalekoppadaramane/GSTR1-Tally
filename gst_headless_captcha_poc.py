"""
Headless CAPTCHA Capture — Proof of Concept
--------------------------------------------
Standalone, throwaway script to answer one question: does the GST portal's
CAPTCHA render normally in headless Chrome, or does the portal detect
headless mode and block/alter the page?

It does NOT log in, does NOT submit a CAPTCHA guess, and does NOT proceed
past this point. It only:
  1. Launches headless Chrome.
  2. Opens the GST login page.
  3. Fills username + password (this is what makes the CAPTCHA table appear).
  4. Waits for the CAPTCHA table at the given XPath and screenshots it.
  5. Quits.

Credentials are read from environment variables - never hardcode them here
and never commit a run with real values baked in.

Usage:
    set GST_USERNAME=your_usernam
    set GST_PASSWORD=your_password
    python gst_headless_captcha_poc.py

Output:
    captcha_poc_output/captcha_table.png   - screenshot of the CAPTCHA table
    captcha_poc_output/captcha_img.png     - screenshot of just the <img>, if one is found inside it
    captcha_poc_output/page_source.html    - full page HTML, for inspection if the XPath doesn't match
    captcha_poc_output/full_page.png       - full-page screenshot, for inspection if something looks wrong
"""

import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LOGIN_URL = "https://services.gst.gov.in/services/login"
CAPTCHA_TABLE_XPATH = '/html/body/div[2]/div[2]/div/div[2]/div/div/div/div/div/form/div[5]/div/div/div/table/tbody'
OUTPUT_DIR = Path(__file__).resolve().parent / "captcha_poc_output"


def build_headless_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    # Basic anti-detection: hide the automation flag and use a normal UA string.
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)
    # Patch navigator.webdriver to undefined - the single most common headless tell.
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.set_page_load_timeout(60)
    return driver


def main():
    username = os.environ.get("GST_USERNAME")
    password = os.environ.get("GST_PASSWORD")
    if not username or not password:
        print("Set GST_USERNAME and GST_PASSWORD environment variables first, e.g.:")
        print("  set GST_USERNAME=your_username")
        print("  set GST_PASSWORD=your_password")
        print("  python gst_headless_captcha_poc.py")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("[1/5] Launching headless Chrome...")
    driver = build_headless_driver()

    try:
        print(f"[2/5] Opening {LOGIN_URL} ...")
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 20)

        print("[3/5] Filling username + password (this is what reveals the CAPTCHA table)...")
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

        print("[4/5] Waiting for the CAPTCHA table to appear...")
        time.sleep(1.0)  # let any client-side JS reveal/render the table
        try:
            table = wait.until(EC.visibility_of_element_located((By.XPATH, CAPTCHA_TABLE_XPATH)))
        except Exception as e:
            print(f"    CAPTCHA table not found/visible at the given XPath: {e}")
            print("    Saving full page source + screenshot for inspection instead.")
            (OUTPUT_DIR / "page_source.html").write_text(driver.page_source, encoding="utf-8")
            driver.save_screenshot(str(OUTPUT_DIR / "full_page.png"))
            print(f"    -> {OUTPUT_DIR / 'page_source.html'}")
            print(f"    -> {OUTPUT_DIR / 'full_page.png'}")
            return

        print("[5/5] Screenshotting the CAPTCHA table...")
        table_png = OUTPUT_DIR / "captcha_table.png"
        table.screenshot(str(table_png))
        print(f"    -> {table_png}")

        # Also try to grab just the <img> inside it, if there is one - usually a tighter,
        # cleaner crop than the whole table cell.
        try:
            img = table.find_element(By.TAG_NAME, "img")
            img_png = OUTPUT_DIR / "captcha_img.png"
            img.screenshot(str(img_png))
            print(f"    -> {img_png} (found an <img> inside the table)")
        except Exception:
            print("    No <img> tag found inside the table - it may be canvas-rendered or "
                  "structured differently. captcha_table.png is still your capture; open it "
                  "and check what's actually in there.")

        # Always save a full-page screenshot too, for side-by-side sanity checking.
        driver.save_screenshot(str(OUTPUT_DIR / "full_page.png"))
        print(f"    -> {OUTPUT_DIR / 'full_page.png'} (full page, for reference)")

        webdriver_flag = driver.execute_script("return navigator.webdriver")
        print(f"\nnavigator.webdriver reports: {webdriver_flag!r} (should be undefined/None if the patch worked)")
        print(f"Final page title: {driver.title!r}")
        print(f"Final URL: {driver.current_url}")

    finally:
        driver.quit()
        print("\nDone. Chrome closed. Nothing was submitted - open the PNGs in captcha_poc_output/ "
              "and check whether the CAPTCHA looks like a normal, legible image.")


if __name__ == "__main__":
    main()
