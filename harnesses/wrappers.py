import requests
import io
import csv
from typing import List, Dict, Any

MAX_INVOICE_LIMIT = 10000

class ValidationHarness:
    """Enforces safety guardrails before execution."""

    @staticmethod
    def validate_payload_and_fy(filename: str, payload: Dict[str, Any], gstr_type: str, fy: str) -> int:
        # 1. Filename & FY Alignment Check
        fname = filename.upper()
        if gstr_type.upper() not in fname:
            raise ValueError(f"[ValidationHarness] Filename '{filename}' does not match return type '{gstr_type}'.")
        
        clean_fy = fy.replace("-", "")
        if fy not in fname and clean_fy not in fname:
            raise ValueError(f"[ValidationHarness] Filename '{filename}' does not align with Financial Year '{fy}'.")

        # 2. Extract Document Count
        root = payload.get("docdata", payload)
        b2b = root.get("b2b", [])
        cdnr = root.get("cdnr", [])
        
        total_docs = sum(len(rec.get("inv", [])) for rec in b2b) + \
                     sum(len(rec.get("nt", []) or rec.get("notes", [])) for rec in cdnr)

        # 3. Batch Limit Cap
        if total_docs > MAX_INVOICE_LIMIT:
            raise ValueError(f"[ValidationHarness] Payload contains {total_docs} records, exceeding max limit of {MAX_INVOICE_LIMIT}. Perform Month-on-Month (MOM) split.")

        return total_docs

class TallyHTTPHarness:
    """Wraps HTTP POST execution against Tally port 9000."""

    def __init__(self, endpoint: str = "http://localhost:9000"):
        self.endpoint = endpoint

    def post_xml(self, xml_payload: str, timeout: int = 5) -> Dict[str, Any]:
        try:
            resp = requests.post(
                self.endpoint, 
                data=xml_payload, 
                headers={'Content-Type': 'text/xml'}, 
                timeout=timeout
            )
            return {
                "success": resp.status_code == 200,
                "status_code": resp.status_code,
                "response_text": resp.text
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": str(e)
            }

class AuditHarness:
    """Captures execution anomalies and outputs exportable CSV streams."""

    def __init__(self):
        self.anomalies: List[Dict[str, Any]] = []

    def log_anomaly(self, doc_num: str, gstin: str, status: str, reason: str):
        self.anomalies.append({
            "document_number": doc_num,
            "gstin": gstin,
            "status": status,
            "reason": reason
        })

    def export_csv(self) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["document_number", "gstin", "status", "reason"])
        writer.writeheader()
        for entry in self.anomalies:
            writer.writerow(entry)
        return output.getvalue()