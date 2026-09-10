# **SOFTWARE REQUIREMENTS SPECIFICATION (SRS) & FUNCTIONAL REQUIREMENTS SPECIFICATION (FRS)**

**Document ID:** SRS-FRS-GTT-2026-V1.0  
**System Name:** GST-to-Tally Intelligent Agent Engine (SDK / Standalone Engine)  
**Standard Compliance:** IEEE 830-1998 (Recommended Practice for Software Requirements Specifications) / ISO/IEC/IEEE 29148:2018  
**Target Audience:** Enterprise Software Engineers, Systems Architects, Integration Leads, Lead QA Engineers, Product Owners

## **DOCUMENT CONTROL & REVISION HISTORY**

| Version | Date | Primary Author / Role | Summary of Changes | Document Status |
| :---- | :---- | :---- | :---- | :---- |
| **1.0** | September 9, 2026 | Lead Enterprise Architect | Complete Specification Release of SRS and FRS for GST-to-Tally Engine | APPROVED FOR IMPLEMENTATION |

### **Document Sign-Off & Approvals**

* **Lead Software Architect:** Approved on September 9, 2026  
* **Lead Integration Engineer:** Approved on September 9, 2026  
* **Domain Specialist / Chartered Accountant:** Approved on September 9, 2026

# 

# **PART 1: SOFTWARE REQUIREMENTS SPECIFICATION (SRS)**

## **1.1 INTRODUCTION**

### **1.1.1 Purpose**

This Software Requirements Specification (SRS) document defines the complete functional, non-functional, interface, and behavioral requirements for the **GST-to-Tally Intelligent Agent Engine**. This document serves as the formal baseline for development teams, system integrators, quality assurance engineers, and automated testing frameworks.

### **1.1.2 Scope of the Software**

The GST-to-Tally Intelligent Agent Engine is a decoupled, privacy-compliant, local software SDK and standalone processing engine designed to:

> 1. Ingest raw GST Portal JSON payloads representing GSTR-1 (Sales) and GSTR-2B (Purchases).  
> 2. Extract and parse standard B2B Invoices (b2b), Credit & Debit Notes (cdnr), Amended B2B Invoices (b2ba), and Amended Credit & Debit Notes (cdnra).  
> 3. Normalize financial transaction data into an internal domain structure.  
> 4. Apply domain-specific accounting mechanics, including nearest-rupee rounding, state-code ledger formatting, and debit/credit sign orientation for Tally XML envelopes.  
> 5. Generate two distinct, schema-compliant XML payloads:  
   * **Masters.xml**: Ledgers for party entities and duty/tax accounts.  
   * **Entries.xml**: Voucher entries for financial transactions.  
> 6. Provide execution harnesses for batch validation, HTTP POST communication with Tally Prime (port 9000), anomaly audit logging, and state checkpoint recovery.

### **1.1.3 Definitions, Acronyms, and Abbreviations**

| Term / Acronym | Full Definition |
| :---- | :---- |
| **GST** | Goods and Services Tax (Indian Tax Structure) |
| **GSTIN** | Goods and Services Tax Identification Number (15-character alphanumeric identifier) |
| **GSTR-1** | Monthly/Quarterly Return of Outward Taxable Supplies (Sales Data) |
| **GSTR-2B** | Auto-drafted ITC Statement for Inward Supplies (Purchase Data) |
| **B2B** | Business-to-Business Taxable Transactions |
| **CDNR** | Credit and Debit Notes Registered |
| **B2BA** | Amended Business-to-Business Transactions |
| **CDNRA** | Amended Credit and Debit Notes Registered |
| **ITC** | Input Tax Credit |
| **Tally XML** | Proprietary XML envelope format accepted by TallyPrime for HTTP/ODBC bulk data import |
| **MOM** | Month-on-Month (Data Segmentation Strategy for bulk payloads) |
| **SDK** | Software Development Kit |
| **RTM** | Requirements Traceability Matrix |

### **1.1.4 References**

> 1. *IEEE Std 830-1998: IEEE Recommended Practice for Software Requirements Specifications.*  
> 2. *GSTN Portal JSON Schema Specifications (GSTR-1 v2.4, GSTR-2B v1.2).*  
> 3. *TallyPrime Developer Reference: XML Data Import Protocols & TallyMessage Specifications.*

## **1.2 OVERALL DESCRIPTION**

### **1.2.1 Product Perspective**

The engine operates as a self-contained processing layer positioned between raw GST portal downloads and on-premise accounting instances running TallyPrime. It can be executed as a standalone CLI tool, embedded into custom desktop applications, or wrapped as a tool inside AI Agent Frameworks (e.g., LangGraph, CrewAI, AutoGen).

┌────────────────────────────────────────────────────────────────────────┐  
│                        AI AGENT / CLI RUNNER                           │  
└──────────────────────────────────┬─────────────────────────────────────┘  
                                   │  
                                   ▼  
┌────────────────────────────────────────────────────────────────────────┐  
│                   GST-TO-TALLY INTELLIGENT ENGINE                      │  
│                                                                        │  
│   ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  │  
│   │ Validation Harness│  │ Context / Memory  │  │ Processing Graph  │  │  
│   └─────────┬─────────┘  └─────────┬─────────┘  └─────────┬─────────┘  │  
│             │                      │                      │            │  
│             ▼                      ▼                      ▼            │  
│   ┌───────────────────────────────────────────────────────────────┐    │  
│   │             Domain Extractor & XML Builder Engine             │    │  
│   └───────────────────────────────────────────────────────────────┘    │  
└──────────────────────────────────┬─────────────────────────────────────┘  
                                   │  
                                   ▼  
┌────────────────────────────────────────────────────────────────────────┐  
│                       LOCAL TALLYPRIME INSTANCE                        │  
│                       (HTTP Server Port 9000\)                          │  
└────────────────────────────────────────────────────────────────────────┘

### **1.2.2 Product Functions Overview**

* **Ingestion & Validation:** Inspects filenames, verifies Financial Year (FY) matching, validates payload schemas, and enforces a hard limit of 10,000 invoices per run.  
* **Extraction & Normalization:** Extracts B2B, CDNR, B2BA, and CDNRA records into a unified NormalizedDocument structure.  
* **Master Ledger Resolution:** Checks existing party mappings in local SQLite memory; creates state-appended party ledger names (Name (GSTIN) \- State) and rate-specific duty ledgers (Output CGST 9% A/c, Input IGST 18% A/c).  
* **Voucher Construction:** Constructs XML envelopes for Sales, Purchases, Credit Notes, and Debit Notes using Tally sign conventions (Debit \= Negative, Credit \= Positive).  
* **Audit Logging:** Filters zero-value invoices and state-code discrepancies into downloadable CSV audit streams.  
* **Breakpoint Recovery:** Saves sequential iteration checkpoints in SQLite to allow resumption after mid-batch failures.

### **1.2.3 User Characteristics**

* **Chartered Accountants / Tax Consultants:** Expect exact accounting rules, correct debit/credit sign conventions, rate-specific tax ledgers, and clear audit logging.  
* **Software Integration Engineers:** Expect clean Python interfaces, type hints, minimal external dependencies, and predictable JSON responses for agent tool integration.

### **1.2.4 General Constraints**

* **Local Processing Only:** Zero network transmission of financial data to external cloud servers.  
* **Version Agnostic:** Output XML must be compatible with Tally.ERP 9 and all releases of TallyPrime.  
* **Execution Cap:** Hard ceiling of 10,000 invoices per execution batch to prevent local memory exhaustion.

### **1.2.5 Assumptions and Dependencies**

* Target machine runs Python 3.10 or higher.  
* TallyPrime (if receiving direct HTTP POST sync) is configured with HTTP Web Server enabled on port 9000\.  
* Incoming JSON payloads originate directly from the official GST Portal or standardized GST Suvidha Provider (GSP) API responses.

## **1.3 EXTERNAL INTERFACE REQUIREMENTS**

### **1.3.1 User Interfaces**

* **Command Line Interface (CLI):** Provides terminal progress outputs, batch completion metrics, and diagnostic exception messages.  
* **Audit CSV File Interface:** Generates exportable CSV files detailing skipped records, zero-value drops, and validation warnings.

### **1.3.2 Software Interfaces**

* **SQLite Database (clients.db / system\_memory.db):** Local persistence store for mapping preferences, party state references, and batch breakpoint checkpoints.  
* **TallyPrime HTTP Web Interface:** Sends generated XML payloads via HTTP POST requests to http://localhost:9000.

### **1.3.3 Data Interfaces**

* **Input Interface:** UTF-8 encoded JSON files following GSTN official schemas for GSTR-1 and GSTR-2B.  
* **Output Interface:** UTF-8 encoded XML files matching Tally \<ENVELOPE\> and \<TALLYMESSAGE\> schemas.

## **1.4 SYSTEM FEATURES & PERFORMANCE SPECIFICATIONS**

### **1.4.1 Batch Processing Performance**

* **Throughput Requirement:** The engine shall parse, normalize, and generate XML for 1,000 invoices in under 2.5 seconds on a standard dual-core host processor.  
* **Memory Footprint:** Peak RAM usage shall not exceed 150 MB during processing of a full 10,000-invoice payload.

### **1.4.2 Reliability & Fault Tolerance**

* **Transaction Atomicity:** An error in parsing a single invoice line shall not crash the entire execution engine; the error shall be trapped, logged to the audit buffer, and the loop shall proceed to the next record.  
* **Breakpoint Persistence:** On hard application failure (e.g., power interruption), the last successfully processed record index shall remain stored in SQLite.

# 

# **PART 2: FUNCTIONAL REQUIREMENTS SPECIFICATION (FRS)**

## **2.1 INGESTION & PARSING ENGINE MODULE**

### **FRS-ING-01: GSTR-1 B2B Invoice Ingestion**

* **Description:** The system shall parse standard Business-to-Business (b2b) sales invoice nodes from GSTR-1 JSON payloads.  
* **Input:** JSON path root.b2b\[\*\] containing supplier GSTIN (ctin), customer trade/legal name (tradeName / c\_name), invoice array (inv\[\*\]), invoice number (inum), invoice date (idt), total value (val), and line item details (itms\[\*\]).  
* **Processing Rule:**  
  1. Extract txval, rt, camt, samt, iamt, and csamt from each line item.  
  2. Map target voucher type to Sales.  
  3. Mark Party Ledger as Debtor (Debit polarity).  
  4. Mark Sales Revenue Ledger as Credit polarity.  
  5. Mark Output Tax Ledgers as Credit polarity.

### **FRS-ING-02: GSTR-2B B2B Purchase Invoice Ingestion**

* **Description:** The system shall parse inward purchase invoice nodes from GSTR-2B JSON payloads.  
* **Input:** JSON path root.docdata.b2b\[\*\] or root.b2b\[\*\].  
* **Processing Rule:**  
  1. Extract financial amounts from line item arrays.  
  2. Map target voucher type to Purchase.  
  3. Mark Party Ledger as Creditor (Credit polarity).  
  4. Mark Purchase Expense Ledger as Debit polarity.  
  5. Mark Input Tax Ledgers as Debit polarity.

### **FRS-ING-03: Credit Note Ingestion (cdrn \- Note Type C)**

* **Description:** The system shall parse Credit Note records from GSTR-1 and GSTR-2B payloads.  
* **Input:** JSON path root.cdnr\[\*\].nt\[\*\] or root.cdnr\[\*\].notes\[\*\] where ntty \== "C".  
* **Processing Rule:**  
  1. Map target voucher type to Credit Note.  
  2. For GSTR-1 (Sales Credit Note):  
     * Party Ledger is **Credited** (reversing debtor balance).  
     * Revenue Ledger maps to Sales Returns A/c and is **Debited**.  
     * Output Tax Ledgers are **Debited**.  
  3. For GSTR-2B (Purchase Credit Note):  
     * Party Ledger is **Debited** (reversing creditor balance).  
     * Expense Ledger maps to Purchase Returns A/c and is **Credited**.  
     * Input Tax Ledgers are **Credited**.

### **FRS-ING-04: Debit Note Ingestion (cdnr \- Note Type D)**

* **Description:** The system shall parse Debit Note records from GSTR-1 and GSTR-2B payloads.  
* **Input:** JSON path root.cdnr\[\*\].nt\[\*\] where ntty \== "D".  
* **Processing Rule:**  
  1. Map target voucher type to Debit Note.  
  2. For GSTR-1 (Sales Debit Note):  
     * Party Ledger is **Debited**.  
     * Sales A/c is **Credited**.  
     * Output Tax Ledgers are **Credited**.  
  3. For GSTR-2B (Purchase Debit Note):  
     * Party Ledger is **Credited**.  
     * Purchase A/c is **Debited**.  
     * Input Tax Ledgers are **Debited**.

### **FRS-ING-05: Amended Invoices & Notes Parsing (b2ba & cdnra)**

* **Description:** The system shall parse amended invoice (b2ba) and amended credit/debit note (cdnra) nodes.  
* **Input:** JSON paths root.b2ba\[\*\] and root.cdnra\[\*\].  
* **Processing Rule:**  
  1. Extract revised invoice values, tax rates, and amounts.  
  2. Extract original document number (oinum / ont\_num) and original document date (oidt / ont\_dt).  
  3. Construct narration string appending original document references:  
     Auto-imported \[{GSTR\_TYPE}\] {DOC\_TYPE} Doc \#{DOC\_NUM} (Amended \- Original Doc \#{ORIG\_NUM}, Dated: {ORIG\_DATE})

## **2.2 DOMAIN NORMALIZATION & ACCOUNTING MECHANICS MODULE**

### **FRS-ACC-01: Nearest-Rupee Fractional Rounding Rule**

* **Description:** The system shall eliminate fractional paise values by rounding all currency amounts to the nearest whole rupee integer.  
* **Rule:**  
  * Implement mathematical rounding (round(amount)) on total invoice value, taxable value, CGST, SGST, IGST, and Cess.  
  * Generated XML \<AMOUNT\> nodes shall contain zero decimal points (e.g., \-11800 instead of \-11800.45).

### **FRS-ACC-02: State-Code Resolution & Party Ledger Formatting**

* **Description:** The system shall standardize party ledger names using the two-digit state code prefix from the party GSTIN.  
* **State Code Mapping Matrix:**

| GSTIN Prefix | Resolved State Name | GSTIN Prefix | Resolved State Name |
| :---- | :---- | :---- | :---- |
| **01** | Jammu & Kashmir | **19** | West Bengal |
| **02** | Himachal Pradesh | **20** | Jharkhand |
| **03** | Punjab | **21** | Odisha |
| **04** | Chandigarh | **22** | Chhattisgarh |
| **05** | Uttarakhand | **23** | Madhya Pradesh |
| **06** | Haryana | **24** | Gujarat |
| **07** | Delhi | **27** | Maharashtra |
| **08** | Rajasthan | **29** | Karnataka |
| **09** | Uttar Pradesh | **30** | Goa |
| **10** | Bihar | **33** | Tamil Nadu |
| **18** | Assam | **36** | Telangana |

* **Formatting Template:** {Trade / Legal Name} ({GSTIN}) \- {Resolved State Name}  
  *Example Output:* Acme Industrial Traders (27AADCB2230M1Z2) \- Maharashtra

### **FRS-ACC-03: Tax Ledger Rate Classification**

* **Description:** The system shall dynamically generate rate-specific duty ledger names based on line-item tax rates.  
* **Naming Conventions:**  
  * Sales CGST Ledger: Output CGST {rate/2}% A/c (e.g., Output CGST 9% A/c)  
  * Sales SGST Ledger: Output SGST {rate/2}% A/c (e.g., Output SGST 9% A/c)  
  * Sales IGST Ledger: Output IGST {rate}% A/c (e.g., Output IGST 18% A/c)  
  * Purchase CGST Ledger: Input CGST {rate/2}% A/c (e.g., Input CGST 9% A/c)  
  * Purchase SGST Ledger: Input SGST {rate/2}% A/c (e.g., Input SGST 9% A/c)  
  * Purchase IGST Ledger: Input IGST {rate}% A/c (e.g., Input IGST 18% A/c)

### **FRS-ACC-04: Tally XML Polarity & Sign Rules**

* **Description:** The system shall apply Tally's specific polarity rules for ledger entries in XML envelopes.  
* **Polarity Rules:**  
  * **Debit Entry:** \<ISDEEMEDPOSITIVE\>Yes\</ISDEEMEDPOSITIVE\>, \<AMOUNT\>-Value\</AMOUNT\> (Negative number).  
  * **Credit Entry:** \<ISDEEMEDPOSITIVE\>No\</ISDEEMEDPOSITIVE\>, \<AMOUNT\>Value\</AMOUNT\> (Positive number).

## **2.3 TALLY XML GENERATION MODULE**

### **FRS-XML-01: Standalone Masters.xml Generation**

* **Description:** The system shall generate a standalone Masters.xml payload containing ledger creation definitions for all parties and tax accounts present in the batch.  
* **Content Constraints:**  
  * Shall contain only \<LEDGER ACTION="Create"\> definitions inside \<TALLYMESSAGE\>.  
  * Must NOT contain any \<VOUCHER\> elements.  
  * Party ledgers must declare \<PARENT\>Sundry Debtors\</PARENT\> (for GSTR-1) or \<PARENT\>Sundry Creditors\</PARENT\> (for GSTR-2B).  
  * Tax ledgers must declare \<PARENT\>Duties & Taxes\</PARENT\>, \<TAXTYPE\>GST\</TAXTYPE\>, and \<PERCENTAGEOFCALCULATION\>.

### **FRS-XML-02: Standalone Entries.xml Generation**

* **Description:** The system shall generate a standalone Entries.xml payload containing all transaction vouchers.  
* **Content Constraints:**  
  * Shall contain only \<VOUCHER ACTION="Create"\> elements inside \<TALLYMESSAGE\>.  
  * Must NOT contain any \<LEDGER\> creation definitions.  
  * Voucher date must be formatted as YYYYMMDD (e.g., 20260410).

## **2.4 VALIDATION, HARNESS & GUARDRAIL MODULE**

### **FRS-GRD-01: Filename & Financial Year Mismatch Check**

* **Description:** The system shall inspect uploaded filenames against the selected return type and Financial Year prior to payload parsing.  
* **Rules:**  
  * If gstr\_type is GSTR1, string "GSTR1" must exist in the filename (case-insensitive).  
  * Selected FY string (e.g., "2025-2026" or "20252026") must exist in the filename.  
  * Violation raises ValueError and halts processing immediately.

### **FRS-GRD-02: Batch Capacity Cap Enforcement**

* **Description:** The system shall count total document nodes prior to processing and enforce a hard limit of 10,000 records.  
* **Rule:**  
  * If total\_records \> 10000, processing is blocked.  
  * System returns exception: "Payload contains {count} records, exceeding max limit of 10000\. Perform Month-on-Month (MOM) split."

### **FRS-GRD-03: Zero-Value Invoice Dropping & Logging**

* **Description:** The system shall exclude invoices where val \== 0.00 from XML generation and log them to an anomaly buffer.  
* **Rule:**  
  * Zero-value document is bypassed during XML construction.  
  * An entry is written to AuditHarness: \[Invoice Number, GSTIN, "DROPPED", "Zero-value invoice excluded from XML generation"\].

## **2.5 AGENT LOOP & BREAKPOINT RECOVERY MODULE**

### **FRS-LOP-01: Sequential Execution Loop**

* **Description:** The system shall process document arrays sequentially, incrementing a zero-based iteration index (current\_index).

### **FRS-LOP-02: SQLite Checkpoint Persistence**

* **Description:** On every completed document iteration, the system shall save (batch\_id, current\_index, status) to the execution\_checkpoints table in client\_memory.db.

### **FRS-LOP-03: Breakpoint Resume Capability**

* **Description:** When invoked with resume\_from\_breakpoint=True and a batch\_id, the loop engine shall read last\_processed\_index from SQLite and skip all array items prior to that index.

# 

# **PART 3: REQUIREMENTS TRACEABILITY MATRIX (RTM)**

The Requirements Traceability Matrix maps high-level SRS requirements down to specific FRS modules and their corresponding User Acceptance Testing (UAT) case IDs.

| SRS Req ID | SRS Requirement Description | FRS Req ID | FRS Module Description | UAT Case ID |
| :---- | :---- | :---- | :---- | :---- |
| **SRS-FUNC-01** | Ingest and parse GSTR-1 B2B invoices | **FRS-ING-01** | GSTR-1 B2B Sales Invoice Ingestion | UTC-01 |
| **SRS-FUNC-02** | Ingest and parse GSTR-2B B2B purchase invoices | **FRS-ING-02** | GSTR-2B B2B Purchase Invoice Ingestion | UTC-02 |
| **SRS-FUNC-03** | Process Sales and Purchase Credit Notes | **FRS-ING-03** | Credit Note Ingestion (cdnr \- Note Type C) | UTC-04 |
| **SRS-FUNC-04** | Process Sales and Purchase Debit Notes | **FRS-ING-04** | Debit Note Ingestion (cdnr \- Note Type D) | UTC-05 |
| **SRS-FUNC-05** | Process Amended Invoices and Notes | **FRS-ING-05** | Amended Invoices & Notes Parsing (b2ba / cdnra) | UTC-03, UTC-06 |
| **SRS-ACC-01** | Apply nearest-rupee integer rounding | **FRS-ACC-01** | Nearest-Rupee Fractional Rounding Rule | UTC-08 |
| **SRS-ACC-02** | State-code party ledger formatting | **FRS-ACC-02** | State-Code Resolution & Party Formatting | UTC-01, UTC-02 |
| **SRS-ACC-03** | Rate-specific duty ledger creation | **FRS-ACC-03** | Tax Ledger Rate Classification | UTC-01, UTC-02 |
| **SRS-ACC-04** | Enforce Tally XML debit/credit polarity | **FRS-ACC-04** | Tally XML Polarity & Sign Rules | UTC-01, UTC-04, UTC-05 |
| **SRS-XML-01** | Generate standalone Masters.xml | **FRS-XML-01** | Standalone Masters.xml Generation | UTC-01, UTC-12 |
| **SRS-XML-02** | Generate standalone Entries.xml | **FRS-XML-02** | Standalone Entries.xml Generation | UTC-01, UTC-12 |
| **SRS-GRD-01** | Validate filename and Financial Year | **FRS-GRD-01** | Filename & FY Mismatch Check | UTC-10 |
| **SRS-GRD-02** | Enforce 10,000 invoice batch cap | **FRS-GRD-02** | Batch Capacity Cap Enforcement | UTC-09 |
| **SRS-GRD-03** | Drop zero-value invoices & log anomalies | **FRS-GRD-03** | Zero-Value Invoice Dropping & Logging | UTC-07 |
| **SRS-STATE-01** | Persist party state mappings in SQLite | **FRS-ACC-02** | SQLite Party Memory Persistence | UTC-11 |
| **SRS-STATE-02** | Support breakpoint resume recovery | **FRS-LOP-03** | Breakpoint Resume Capability | UTC-11 |

# 

# **PART 4: NON-FUNCTIONAL REQUIREMENTS SPECIFICATION**

## **4.1 SECURITY & PRIVACY SPECIFICATIONS**

* **NFR-SEC-01 (Zero External Data Egress):** The engine shall operate completely offline without sending any invoice data, GSTINs, or financial amounts to external HTTP endpoints (except the user's local Tally HTTP server on localhost:9000).  
* **NFR-SEC-02 (Local Storage Scope):** SQLite database files (clients.db, system\_memory.db) and audit logs shall be written strictly to local application directory paths with restricted OS file permissions.

## **4.2 MAINTAINABILITY & CODE STYLE SPECIFICATIONS**

* **NFR-MAINT-01 (Zero-AI Boilerplate):** The Python codebase shall be written without placeholders (// TODO), dead imports, or redundant boilerplate comments.  
* **NFR-MAINT-02 (Type Hints):** All function signatures in domain.py, xml\_engine.py, agent\_engine.py, and harness/wrappers.py shall include explicit Python type hints (typing.List, typing.Dict, typing.Optional).

## **4.3 PORTABILITY & DEPLOYABILITY**

* **NFR-PORT-01 (OS Independence):** The software engine shall run identically across Microsoft Windows (Windows 10/11, Windows Server 2019+), macOS (12.0+), and Linux (Ubuntu 20.04+, RHEL 8+).  
* **NFR-PORT-02 (Container Compatibility):** The codebase shall be packageable into lightweight Docker containers (python:3.11-slim baseline) without requiring native C-compiler extensions.