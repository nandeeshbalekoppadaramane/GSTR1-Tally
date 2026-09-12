# TaxFlow Pro

A desktop-style GST accounting tool that takes GSTR-1 (Sales) and GSTR-2B
(Purchases) return data — uploaded as JSON/ZIP or downloaded automatically
from the GST portal — and pushes it into TallyPrime as ready-to-import
vouchers, either as XML files or directly over HTTP.

It runs as a local web app: a FastAPI backend serves a vanilla JS/Bootstrap
frontend in your browser, with all data kept in a local SQLite file. Nothing
is uploaded anywhere except to your own TallyPrime instance and the official
GST portal (only when you use the portal login features).

## Features

- **Two independent datasets per client** — Sales (GSTR-1) and Purchases
  (GSTR-2B) are tracked, validated, and exported separately, and always
  pushed to Tally as separate requests.
- **Upload or auto-download returns** — upload a GSTR-1/GSTR-2B JSON or ZIP
  directly, or let the tool log into the GST portal itself (headless
  Chrome, one CAPTCHA per login) and download a client's GSTR-2B month by
  month.
- **Upload validations** — flags a GSTIN mismatch against the active client
  and warns before re-loading a period that already has data, instead of
  silently overwriting or guessing.
- **Tally export, two ways** — *Method 1*: generate consolidated XML files
  for manual import into Tally. *Method 2*: push vouchers directly over
  Tally's local HTTP interface.
- **Automatic cancel-on-delete sync** — deleting a return period in the
  tool also attempts to cancel the matching vouchers already pushed to
  Tally, so removed data doesn't linger there as orphans.
- **Correct, honest tax figures** — CGST/SGST/IGST are broken out as
  separate columns with the true invoice rate (not a raw schema artifact),
  and an invoice split across multiple tax rates by the source data is
  shown as "Multiple (5%, 18%)" and merged into a single multi-line Tally
  voucher instead of being silently deduplicated.
- **Transactions Explorer** — a searchable, sortable, paginated view across
  all imported invoices, with sticky headers and an Export to Excel button.
- **Counterparties & Ledgers** — party master list with a Sales/Purchases/
  All relation toggle.
- **In-app Help guide** covering every workflow end to end.

## Running from source

Requires Python 3.11+ and Google Chrome installed (for the GST portal
automation features).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python launcher.py
```

The app opens automatically in your default browser at
`http://127.0.0.1:8000` (it falls back to 8001–8003 if 8000 is busy). Data
is stored in `client_memory.db`, created next to `launcher.py` on first run.

## Building a standalone .exe

The PyInstaller spec (`TaxFlowPro.spec`) packages the app as a onedir
Windows build. To avoid bundling unrelated packages from your normal Python
environment, build from a clean, minimally-pinned virtual environment:

```bash
python -m venv .buildenv
.buildenv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller TaxFlowPro.spec
```

The output is written to `dist/TaxFlowPro/`, with `TaxFlowPro.exe` as the
entry point — the whole folder is self-contained and can be copied and run
anywhere.

## Project layout

```
launcher.py                  Entry point (dev and packaged builds)
server.py                    FastAPI app: routes, XML/Excel export, Tally push
client_db.py                 SQLite persistence layer
portal_verifier.py           GST portal login + GSTIN verification (Selenium)
gstr2b_portal_downloader.py  GST portal login + GSTR-2B download (Selenium)
engine/
  domain.py                  Return parsing, normalization, doc merging
  xml_engine.py               Tally XML generation
static/                      Frontend (HTML/CSS/JS)
TaxFlowPro.spec              PyInstaller build spec
```

## Known limitations

- ISD credit distribution and import-of-goods (Bill of Entry) sections of
  GSTR-2B are not yet supported.
- No built-in backup/export for `client_memory.db` — copy the file yourself
  to preserve your data.
- The GST portal automation needs a CAPTCHA typed in manually once per
  login.

## Credits

Built by **Nandeesh** ([LinkedIn](https://www.linkedin.com/in/nandeesh-balekoppadaramane))
with **CA Rajasekaran** ([LinkedIn](https://www.linkedin.com/in/rajasekaran-k-56b5681b/)).
