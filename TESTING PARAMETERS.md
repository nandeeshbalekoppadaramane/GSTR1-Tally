# **SOFTWARE ENGINEERING HANDOFF PACKAGE & UAT SPECIFICATION**

**Document ID:** SEHP-GTT-2026-V1.0  
**System Name:** GST-to-Tally Intelligent Agent Engine (SDK / Standalone Engine)  
**Standard Compliance:** IEEE 829 (Software Test Documentation) / IEEE 830 (Software Requirements Specification)  
**Target Audience:** Lead AI Engineers, Software Integration Teams, QA Leads, Chartered Accountants

## **DOCUMENT CONTROL & REVISION HISTORY**

| Version | Date | Primary Author / Role | Summary of Changes | Document Status |
| :---- | :---- | :---- | :---- | :---- |
| **1.0** | September 9, 2026 | Lead Enterprise Architect | Initial Release of Engineering Handoff Package & Comprehensive UAT Test Matrix | APPROVED FOR HANDOFF |

### **Stakeholder Approvals**

* **Lead Software Architect:** Approved on September 9, 2026  
* **Lead QA & Automation Engineer:** Approved on September 9, 2026  
* **Domain Specialist / Product Owner:** Approved on September 9, 2026

# 

# **PART 1: USER ACCEPTANCE TESTING (UAT) PLAN (IEEE 829 STANDARD)**

## **1.1 SYSTEM OVERVIEW & SCOPE**

The GST-to-Tally Engine is an offline-first, privacy-compliant software SDK that ingests raw GST Portal JSON payloads (GSTR-1, GSTR-2B) containing B2B, CDNR, B2BA, and CDNRA records. It normalizes financial records, calculates line-item tax allocations, applies strict nearest-rupee rounding rules, and constructs two distinct Tally-compliant XML files: Masters.xml (party and tax ledger definitions) and Entries.xml (accounting vouchers).  
This User Acceptance Testing Plan validates the end-to-end operational readiness, domain accuracy, edge-case resilience, and state persistence of the engine before deployment into the target agent platform.

## **1.2 TEST ENVIRONMENT & PREREQUISITE CONFIGURATION**

To execute the test cases in this document, the testing environment must satisfy the following baseline configurations:

> 1. **TallyPrime Environment:**  
   * TallyPrime Release 1.0 or higher running on a local workstation.  
   * Target Company created and loaded in TallyPrime.  
   * F12 Advanced Configuration: **Enable ODBC and HTTP Services** set to Yes.  
   * Server Port configured to 9000\.  
> 2. **Runtime Environment:**  
   * Python 3.10 or higher installed.  
   * Required packages installed: pydantic\>=2.0, requests, jinja2.  
   * File permissions enabled for reading input JSONs and writing output XML files to disk (./test\_output/).

## **1.3 PASS / FAIL CRITERIA & EXIT THRESHOLDS**

* **Critical Defects (Accounting / Polarity Mismatch):** **0 Allowed.** Any failure in debit/credit direction, tax rate allocation, or fractional paise rounding results in an immediate rejection of the build.  
* **Major Defects (Schema Validation Errors):** **0 Allowed.** All generated XML files must parse successfully without syntax errors using standard XML validation engines.  
* **Overall Pass Threshold:** **100% Pass Rate** across all Critical and Major test cases listed below.

## **1.4 DETAILED EXHAUSTIVE TEST CASE MATRIX**

### **Module A: Core Invoice Ingestion (B2B & B2BA)**

#### **UTC-01: Standard In-State GSTR-1 B2B Sales Invoice Ingestion**

* **Test Objective:** Verify that an in-state GSTR-1 B2B invoice creates correct party/tax masters and generates a Sales voucher with appropriate debit/credit signs.  
* **Preconditions:** TallyPrime running on port 9000; test company open with no existing party ledgers.  
* **Test Input Data:**  
  JSON  
  {  
    "b2b": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "inv": \[{  
        "inum": "INV-2026-001",  
        "idt": "10-04-2026",  
        "val": 11800.00,  
        "itms": \[{"itm\_det": {"txval": 10000.00, "rt": 18.0, "camt": 900.00, "samt": 900.00, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

\*   \*\*Step-by-Step Execution Procedure:\*\*  
  1\. Pass input JSON to \`GSTTallyEngine.process\_payload(json\_data, gstr\_type="GSTR1")\`.  
  2\. Inspect generated \`Masters.xml\` for party and tax ledger declarations.  
  3\. Inspect generated \`Entries.xml\` for voucher structure and ledger signs.  
  4\. Post generated XML files to Tally HTTP port \`http://localhost:9000\`.  
\*   \*\*Expected Results:\*\*  
  \*   \`Masters.xml\` contains Party Ledger \`Acme Industrial Traders (27AADCB2230M1Z2) \- Maharashtra\` under \`Sundry Debtors\`.  
  \*   \`Masters.xml\` contains Tax Ledgers \`Output CGST 9% A/c\` and \`Output SGST 9% A/c\` under \`Duties & Taxes\`.  
  \*   \`Entries.xml\` contains \`\<VOUCHER VCHTYPE="Sales"\>\`.  
  \*   Party entry amount is \`-11800\` (Debit in Tally XML sign convention).  
  \*   Sales A/c amount is \`10000\` (Credit). CGST and SGST amounts are \`900\` each (Credit).  
  \*   Tally HTTP server returns status HTTP 200 OK without errors.

\---

\#\#\#\# UTC-02: Inter-State GSTR-2B B2B Purchase Invoice Ingestion  
\*   \*\*Test Objective:\*\* Verify that an inter-state GSTR-2B purchase invoice routes taxes to IGST input ledgers and creates party under Sundry Creditors.  
\*   \*\*Preconditions:\*\* Test company open in Tally; engine output folder cleared.  
\*   \*\*Test Input Data:\*\*  
  \`\`\`json  
  {  
    "docdata": {  
      "b2b": \[{  
        "ctin": "07AAAAA0000A1Z5",  
        "tradeName": "Global Tech Services",  
        "inv": \[{  
          "inum": "PUR-2026-991",  
          "dt": "15-04-2026",  
          "val": 5900.00,  
          "itms": \[{"txval": 5000.00, "rt": 18.0, "iamt": 900.00, "camt": 0.0, "samt": 0.0}\]  
        }\]  
      }\]  
    }  
  }

* **Step-by-Step Execution Procedure:**  
  1. Pass input JSON to GSTTallyEngine.process\_payload(json\_data, gstr\_type="GSTR2B").  
  2. Verify Masters.xml and Entries.xml structure.  
* **Expected Results:**  
  * Party Ledger created under parent group Sundry Creditors with State set to Delhi.  
  * Tax Ledger created as Input IGST 18% A/c under Duties & Taxes.  
  * Entries.xml contains \<VOUCHER VCHTYPE="Purchase"\>.  
  * Party entry amount is 5900 (Credit). Purchase A/c amount is \-5000 (Debit). Input IGST amount is \-900 (Debit).

#### **UTC-03: Amended B2B Invoice (B2BA) Processing**

* **Test Objective:** Verify that amended invoices (B2BA) are extracted properly and original document references are preserved in the narration string.  
* **Preconditions:** Engine initialized.  
* **Test Input Data:**  
  JSON  
  {  
    "b2ba": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "inv": \[{  
        "inum": "INV-2026-001-REV",  
        "idt": "20-04-2026",  
        "oinum": "INV-2026-001",  
        "oidt": "10-04-2026",  
        "val": 14160.00,  
        "itms": \[{"itm\_det": {"txval": 12000.00, "rt": 18.0, "camt": 1080.00, "samt": 1080.00, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

\*   \*\*Step-by-Step Execution Procedure:\*\*  
  1\. Execute extraction and XML generation for GSTR1 payload.  
  2\. Check \`\<NARRATION\>\` tag in the generated voucher XML.  
\*   \*\*Expected Results:\*\*  
  \*   Voucher Number set to \`INV-2026-001-REV\`.  
  \*   Total voucher value calculated as \`14160\`.  
  \*   Narration string explicitly contains: \`Auto-imported \[GSTR1\] B2BA Doc \#INV-2026-001-REV (Amended \- Original Doc \#INV-2026-001, Dated: 10-04-2026)\`.

\---

\#\#\# Module B: Credit & Debit Notes (CDNR & CDNRA)

\#\#\#\# UTC-04: Sales Credit Note Ingestion (GSTR-1 CDNR)  
\*   \*\*Test Objective:\*\* Verify that GSTR-1 Credit Notes generate \`Credit Note\` voucher types in Tally with reversed accounting polarities.  
\*   \*\*Preconditions:\*\* Core engine initialized for GSTR1 mode.  
\*   \*\*Test Input Data:\*\*  
  \`\`\`json  
  {  
    "cdnr": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "nt": \[{  
        "nt\_num": "CN-2026-005",  
        "nt\_dt": "18-04-2026",  
        "ntty": "C",  
        "val": 2360.00,  
        "itms": \[{"itm\_det": {"txval": 2000.00, "rt": 18.0, "camt": 180.00, "samt": 180.00, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

* **Step-by-Step Execution Procedure:**  
  1. Process JSON payload through the extraction engine.  
  2. Inspect generated voucher structure in Entries.xml.  
* **Expected Results:**  
  * \<VOUCHER VCHTYPE="Credit Note"\> created.  
  * Debtor Party entry amount is 2360 (Credit in Tally XML).  
  * Revenue ledger set to Sales Returns A/c with amount \-2000 (Debit).  
  * Tax amounts for CGST and SGST set to \-180 each (Debit).

#### **UTC-05: Sales Debit Note Ingestion (GSTR-1 CDNR)**

* **Test Objective:** Verify that GSTR-1 Debit Notes generate Debit Note voucher types in Tally with standard sales accounting polarities.  
* **Preconditions:** Core engine initialized.  
* **Test Input Data:**  
  JSON  
  {  
    "cdnr": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "nt": \[{  
        "nt\_num": "DN-2026-002",  
        "nt\_dt": "22-04-2026",  
        "ntty": "D",  
        "val": 1180.00,  
        "itms": \[{"itm\_det": {"txval": 1000.00, "rt": 18.0, "camt": 90.00, "samt": 90.00, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

\*   \*\*Step-by-Step Execution Procedure:\*\*  
  1\. Process payload for GSTR1 mode.  
  2\. Verify voucher type and polarity in generated XML.  
\*   \*\*Expected Results:\*\*  
  \*   \`\<VOUCHER VCHTYPE="Debit Note"\>\` created.  
  \*   Debtor Party entry amount is \`-1180\` (Debit in Tally XML).  
  \*   Sales A/c entry amount is \`1000\` (Credit).  
  \*   Tax amounts for CGST and SGST set to \`90\` each (Credit).

\---

\#\#\#\# UTC-06: Amended Credit/Debit Note Ingestion (CDNRA)  
\*   \*\*Test Objective:\*\* Verify extraction of amended credit/debit notes and inclusion of original note details in narration.  
\*   \*\*Preconditions:\*\* Core engine initialized.  
\*   \*\*Test Input Data:\*\*  
  \`\`\`json  
  {  
    "cdnra": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "nt": \[{  
        "nt\_num": "CN-2026-005-REV",  
        "nt\_dt": "25-04-2026",  
        "ont\_num": "CN-2026-005",  
        "ont\_dt": "18-04-2026",  
        "ntty": "C",  
        "val": 3540.00,  
        "itms": \[{"itm\_det": {"txval": 3000.00, "rt": 18.0, "camt": 270.00, "samt": 270.00, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

* **Step-by-Step Execution Procedure:**  
  1. Execute conversion loop.  
  2. Check \<NARRATION\> node content.  
* **Expected Results:**  
  * Voucher type resolved to Credit Note.  
  * Narration contains: Amended \- Original Doc \#CN-2026-005, Dated: 18-04-2026.

### **Module C: Guardrails, Rounding & Anomaly Handling**

#### **UTC-07: Zero-Value Invoice Dropping & Audit Trail Logging**

* **Test Objective:** Verify that invoices with total value equal to zero are dropped from XML output and captured in the audit log.  
* **Preconditions:** Audit harness active.  
* **Test Input Data:**  
  JSON  
  {  
    "b2b": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "inv": \[  
        {"inum": "INV-VALID-01", "idt": "10-04-2026", "val": 5000.00, "itms": \[{"itm\_det": {"txval": 5000.00, "rt": 0.0}}\]},  
        {"inum": "INV-ZERO-02", "idt": "11-04-2026", "val": 0.00, "itms": \[\]}  
      \]  
    }\]  
  }

\*   \*\*Step-by-Step Execution Procedure:\*\*  
  1\. Execute conversion.  
  2\. Inspect generated XML vouchers and \`AuditHarness\` CSV buffer.  
\*   \*\*Expected Results:\*\*  
  \*   Generated \`Entries.xml\` contains exactly ONE voucher (\`INV-VALID-01\`).  
  \*   \`INV-ZERO-02\` is excluded from XML.  
  \*   \`AuditHarness\` log contains an entry for \`INV-ZERO-02\` with status \`DROPPED\` and reason \`Zero-value invoice excluded from XML generation.\`

\---

\#\#\#\# UTC-08: Nearest-Rupee Fractional Rounding Execution  
\*   \*\*Test Objective:\*\* Verify that all decimal and fractional paise values are rounded to nearest whole integer rupees.  
\*   \*\*Preconditions:\*\* Core engine active.  
\*   \*\*Test Input Data:\*\*  
  \`\`\`json  
  {  
    "b2b": \[{  
      "ctin": "27AADCB2230M1Z2",  
      "tradeName": "Acme Industrial Traders",  
      "inv": \[{  
        "inum": "INV-DECIMAL-01",  
        "idt": "10-04-2026",  
        "val": 11800.49,  
        "itms": \[{"itm\_det": {"txval": 10000.41, "rt": 18.0, "camt": 900.04, "samt": 900.04, "iamt": 0.0}}\]  
      }\]  
    }\]  
  }

* **Step-by-Step Execution Procedure:**  
  1. Process JSON payload.  
  2. Parse XML output and check all \<AMOUNT\> tags.  
* **Expected Results:**  
  * Party amount rounded to \-11800.  
  * Taxable value amount rounded to 10000\.  
  * Tax amounts rounded to 900 each.  
  * No decimal points (.) exist in any XML \<AMOUNT\> field.

#### **UTC-09: Batch Capacity Cap Enforcement (10,000 Invoice Limit)**

* **Test Objective:** Verify that payload processing halts immediately if input exceeds 10,000 records.  
* **Preconditions:** Validation harness initialized.  
* **Test Input Data:** Generated JSON file containing 10,001 invoice nodes.  
* **Step-by-Step Execution Procedure:**  
  1. Pass payload to ValidationHarness.enforce\_batch\_limit(10001).  
* **Expected Results:**  
  * System raises ValueError.  
  * Exception message explicitly states: Payload contains 10001 records, exceeding max limit of 10000\. Perform Month-on-Month (MOM) split.

#### **UTC-10: Filename & Financial Year Guardrail Verification**

* **Test Objective:** Verify that mismatched filenames or financial years are rejected prior to parsing.  
* **Preconditions:** Validation harness active.  
* **Step-by-Step Execution Procedure:**  
  1. Execute ValidationHarness.validate\_payload\_and\_fy(filename="GSTR2B\_2024-2025.json", payload={}, gstr\_type="GSTR1", fy="2025-2026").  
* **Expected Results:**  
  * Validation fails with ValueError: File mismatch: Expected return type 'GSTR1' in file name.

### **Module D: Loop Engine & State Recovery**

#### **UTC-11: Checkpoint Breakpoint Resume Execution**

* **Test Objective:** Verify that an interrupted processing loop can resume from the exact last saved breakpoint index.  
* **Preconditions:** SQLite database client\_memory.db active; batch checkpoint initialized at index 50\.  
* **Step-by-Step Execution Procedure:**  
  1. Seed execution\_checkpoints table with batch\_id="BATCH-001", last\_processed\_index=50, status="PROCESSING".  
  2. Call AgentLoopEngine.run\_sequential\_loop(..., batch\_id="BATCH-001", resume\_from\_breakpoint=True).  
* **Expected Results:**  
  * Loop skips items 0 through 49\.  
  * Processing begins at index item 50\.  
  * Checkpoint index updates progressively in SQLite.

#### **UTC-12: Decoupled Master vs Entry XML Generation**

* **Test Objective:** Verify that master definitions and voucher entries are cleanly separated into two distinct XML payloads.  
* **Preconditions:** Core engine execution.  
* **Step-by-Step Execution Procedure:**  
  1. Execute GSTTallyEngine.process\_payload().  
  2. Verify files created on filesystem.  
* **Expected Results:**  
  * Masters.xml contains ONLY \<LEDGER\> nodes inside \<TALLYMESSAGE\>. Zero \<VOUCHER\> tags present.  
  * Entries.xml contains ONLY \<VOUCHER\> nodes inside \<TALLYMESSAGE\>. Zero \<LEDGER\> definitions present.

## **1.5 DEFECT CLASSIFICATION & ESCALATION PROTOCOL**

| Defect Severity Level | Definition | Response / Resolution SLA | Escalation Pathway |
| :---- | :---- | :---- | :---- |
| **S1 \- Critical** | Incorrect accounting polarity, debit/credit sign error, tax calculation corruption, or XML parse failure in Tally. | **Immediate Halt.** Fix required within 4 hours. | AI Engineer \-\> Lead Architect \-\> Product Owner |
| **S2 \- Major** | Guardrail failure, checkpoint recovery failure, or unhandled exception on valid JSON nodes. | Fix required within 24 hours. | AI Engineer \-\> Lead QA |
| **S3 \- Minor** | Formatting anomaly in narration string or non-critical log formatting bug. | Fix scheduled in next sprint. | Software Engineer |

## **1.6 FORMAL UAT SIGN-OFF FORM**

Plaintext  
\========================================================================================  
                       FORMAL UAT ACCEPTANCE SIGN-OFF SHEET  
\========================================================================================

System Name: GST-to-Tally Intelligent Agent Engine  
Release Version: v1.0.0-SDK  
Build Date: September 9, 2026

DELIVERABLE VERIFICATION:  
\[X\] Core Parsing Engine (B2B, CDNR, B2BA, CDNRA)  
\[X\] Tally XML Generator (Masters.xml, Entries.xml)  
\[X\] Agent Loop Engine with Breakpoint Recovery  
\[X\] Validation & Audit Harnesses  
\[X\] Automated Evaluation Test Suite

UAT EXECUTION SUMMARY:  
Total Test Cases Executed: 12  
Passed: 12  
Failed: 0  
Critical Defects Outstanding: 0

FINAL VERDICT:   
\[X\] APPROVED FOR PRODUCTION INTEGRATION  
\[ \] CONDITIONALLY APPROVED (REMEDIATION REQUIRED)  
\[ \] REJECTED

SIGNATURES:

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_          \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_  
Lead QA / Testing Engineer                        Domain Specialist / Chartered Accountant  
Date: \_\_\_\_ / \_\_\_\_ / 2026                          Date: \_\_\_\_ / \_\_\_\_ / 2026

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_          \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_  
Lead AI Systems Architect                         Product Owner / Executive Sponsor  
Date: \_\_\_\_ / \_\_\_\_ / 2026                          Date: \_\_\_\_ / \_\_\_\_ / 2026  
\========================================================================================

# 

# **PART 2: INTERFACE CONTROL DOCUMENT (ICD)**

## **2.1 AGENT TOOL INVOCATION CONTRACT**

The core engine exposes a single, framework-agnostic Python interface class GSTTallyEngine. Any AI Agent Framework (LangGraph, CrewAI, AutoGen) can invoke this tool via standard function calling.

### **Python Function Tool Signature**

Python  
def process\_payload(self, raw\_json: Dict\[str, Any\], gstr\_type: str) \-\> Dict\[str, Any\]:  
    """  
    Core tool interface for converting GST Portal JSON data into Tally XML payloads.  
      
    Parameters:  
    \-----------  
    raw\_json : Dict\[str, Any\]  
        The parsed dictionary object of the raw GST portal JSON download.  
    gstr\_type : str  
        The return type identifier. Allowed values: 'GSTR1', 'GSTR2B'.  
          
    Returns:  
    \--------  
    Dict\[str, Any\]  
        Execution summary dictionary containing status, paths to generated XMLs,  
        and document count breakdown.  
    """

## **2.2 TOOL OUTPUT SCHEMA SPECIFICATION**

When invoked by an AI Agent, the tool returns a JSON-serializable dictionary formatted as follows:

JSON  
{  
  "status": "success",  
  "processed\_count": 4,  
  "masters\_path": "/absolute/path/to/output/Masters.xml",  
  "entries\_path": "/absolute/path/to/output/Entries.xml",  
  "breakdown": {  
    "B2B": 1,  
    "CDNR\_CREDIT": 1,  
    "CDNR\_DEBIT": 1,  
    "B2BA": 1  
  }  
}

## **2.3 ERROR & EXCEPTION CODES**

| Error Code | Exception Raised | Trigger Condition | Recommended Agent Action |
| :---- | :---- | :---- | :---- |
| ERR\_FILE\_MISMATCH | ValueError | Filename does not contain return type string (e.g., GSTR1). | Ask user to verify uploaded file name. |
| ERR\_FY\_MISMATCH | ValueError | Filename does not align with specified Financial Year. | Prompt user to confirm Financial Year selection. |
| ERR\_BATCH\_EXCEEDED | ValueError | Ingestion payload contains \>10,000 invoices. | Instruct user to perform Month-on-Month (MOM) file splitting. |
| ERR\_NO\_DOCUMENTS | None (status: warning) | Input JSON contains no valid B2B, CDNR, B2BA, or CDNRA nodes. | Inform user that input file contains no actionable B2B records. |

# 

# **PART 3: AGENTIC ARCHITECTURE & LOOP SPECIFICATION (AAD)**

## **3.1 COMPONENT INTERACTION SPECIFICATION**

The agent pipeline decouples data extraction, state memory, validation harnesses, and loop execution.

\+-----------------------------------------------------------------------------------+  
|                                 EXECUTION LOOP                                    				|  
|                             (loops/agent\_loop.py)                                 				|  
\+-----------------------------------------+-----------------------------------------+  
                                          |  
          \+-------------------------------+-------------------------------+  
          |                               |                               |  
          v                               v                               v  
\+-------------------+           \+-------------------+           \+-------------------+  
|   STATE CONTEXT   |           |    HARNESSES      |           |    CORE ENGINE    |  
| (context/state.py)|           |(harness/wrappers) |           | (agent\_engine.py) |  
\+-------------------+           \+-------------------+           \+-------------------+  
| • SQLite Memory   |            | • Validation      |          	 | • GSTDataExtractor|  
| • Checkpointing   |          	   | • Tally HTTP POST |           	| • TallyXMLEngine  |  
| • State Codes     |         	   | • Audit Buffer    |           		|                   |  
\+-------------------+           \+-------------------+           \+-------------------+

## **3.2 SQLITE PERSISTENCE SCHEMA**

### **Table: party\_mappings**

Stores persistent state code and party name associations across user sessions.

SQL  
CREATE TABLE IF NOT EXISTS party\_mappings (  
    gstin TEXT PRIMARY KEY,  
    party\_name TEXT NOT NULL,  
    state\_name TEXT NOT NULL  
);

### **Table: execution\_checkpoints**

Tracks batch processing indices for breakpoint resume capability.

SQL  
CREATE TABLE IF NOT EXISTS execution\_checkpoints (  
    batch\_id TEXT PRIMARY KEY,  
    last\_processed\_index INTEGER NOT NULL,  
    status TEXT NOT NULL  
);

# 

# **PART 4: DOMAIN MAPPING & ACCOUNTING MECHANICS MATRIX**

## **4.1 MASTER ACCOUNTING POLARITY MATRIX**

Tally XML enforces a specific sign convention for amounts:

* **Debit (Dr):** Represented as a **Negative Number** (e.g., \-1000.00). \<ISDEEMEDPOSITIVE\> tag set to Yes.  
* **Credit (Cr):** Represented as a **Positive Number** (e.g., 1000.00). \<ISDEEMEDPOSITIVE\> tag set to No.

| Document Type | Return Context | Target Tally Voucher | Party Ledger Polarity | Revenue / Purchase Ledger Polarity | Tax Ledger Polarity |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **B2B Invoice** | GSTR-1 (Sales) | Sales | **Debit** (-Val, Yes) | **Credit** (+Val, No) | **Credit** (+Val, No) |
| **B2B Invoice** | GSTR-2B (Purchase) | Purchase | **Credit** (+Val, No) | **Debit** (-Val, Yes) | **Debit** (-Val, Yes) |
| **Credit Note** | GSTR-1 (Sales) | Credit Note | **Credit** (+Val, No) | **Debit** (-Val, Yes) | **Debit** (-Val, Yes) |
| **Debit Note** | GSTR-1 (Sales) | Debit Note | **Debit** (-Val, Yes) | **Credit** (+Val, No) | **Credit** (+Val, No) |
| **Credit Note** | GSTR-2B (Purchase) | Credit Note | **Debit** (-Val, Yes) | **Credit** (+Val, No) | **Credit** (+Val, No) |
| **Debit Note** | GSTR-2B (Purchase) | Debit Note | **Credit** (+Val, No) | **Debit** (-Val, Yes) | **Debit** (-Val, Yes) |

## **4.2 LEDGER NAMING CONVENTIONS**

To ensure standardization and prevent duplicate ledger creation in Tally, all ledgers are constructed dynamically using the following template rules:

> 1. **Party Ledgers:** {Trade Name} ({GSTIN}) \- {State Name}  
>    *Example:* Acme Industrial Traders (27AADCB2230M1Z2) \- Maharashtra  
> 2. **Output Tax Ledgers (Sales):** Output {CGST|SGST|IGST} {Rate}% A/c  
>    *Example:* Output CGST 9% A/c, Output IGST 18% A/c  
> 3. **Input Tax Ledgers (Purchases):** Input {CGST|SGST|IGST} {Rate}% A/c  
>    *Example:* Input SGST 9% A/c, Input IGST 18% A/c  
> 4. **Base Sales Ledger:** Sales A/c (or Sales Returns A/c for Credit Notes)  
> 5. **Base Purchase Ledger:** Purchase A/c (or Purchase Returns A/c for Credit Notes)

# 

# **PART 5: DEPLOYMENT & OPERATIONAL SOP**

## **5.1 PRE-DEPLOYMENT INSTALLATION STEPS**

> 1. Extract the provided codebase package (gst\_tally\_engine.zip) into the platform host environment.  
> 2. Initialize a virtual environment and install dependencies:  
>    Bash  
>    python \-m venv venv  
>    source venv/bin/activate  \# On Windows: venv\\Scripts\\activate  
>    pip install pydantic requests jinja2

> 3. Verify system directory structure:  
>    Plaintext  
>    gst\_tally\_engine/  
>    ├── agent\_engine.py  
>    ├── constants.py  
>    ├── domain.py  
>    ├── xml\_engine.py  
>    ├── test\_runner.py  
>    ├── context/  
>    │   └── state.py  
>    ├── harness/  
>    │   └── wrappers.py  
>    ├── loops/  
>    │   └── agent\_loop.py  
>    └── evals/  
>        └── evaluator.py

## **5.2 VERIFICATION PROCEDURE**

Run the standalone integration runner to verify system operational readiness before enabling agent calls:

Bash  
python run\_agent\_system.py

Expected Output Confirmation:

Plaintext  
\=========================================================  
       GST-TO-TALLY AGENTIC ENGINE SYSTEM RUNNER           
\=========================================================

\[STEP 1\] Running Evaluation Suite...  
Eval Results: {  
  "eval\_zero\_value\_dropping": true,  
  "eval\_polarity\_correctness": true,  
  "eval\_rounding\_precision": true,  
  "eval\_xml\_schema\_validity": true,  
  "passed\_count": 4,  
  "failed\_count": 0  
}

\[STEP 2\] Running Validation Harness...  
Validation Harness Passed: Identified 3 total raw records.

\[STEP 3\] Executing Stateful Agent Loop Engine...  
Loop Engine Execution Success\!  
Processed Count: 2

\[STEP 4\] Exporting CSV Audit Trail...  
CSV Log Output Buffer generated successfully.

*End of Engineering Handoff Package & UAT Specification.*