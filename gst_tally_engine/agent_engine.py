import os
import json
from typing import Dict, Any
from domain import GSTDataExtractor
from xml_engine import TallyXMLEngine

class GSTTallyEngine:
    """Framework-Agnostic Core Engine. Wraps parsing and XML generation into an Agent Tool interface."""

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def process_payload(self, raw_json: Dict[str, Any], gstr_type: str) -> Dict[str, Any]:
        """Core tool function callable by CLI, Agent loops, or custom backend services."""
        # 1. Extract and Normalize Documents
        documents = GSTDataExtractor.extract_documents(raw_json, gstr_type)
        
        if not documents:
            return {
                "status": "warning", 
                "message": "No valid B2B, CDNR, B2BA, or CDNRA records found in JSON payload."
            }

        # 2. Render XML Payloads
        masters_xml = TallyXMLEngine.generate_masters_xml(documents, gstr_type)
        entries_xml = TallyXMLEngine.generate_entries_xml(documents, gstr_type)

        # 3. Save Files
        masters_path = os.path.join(self.output_dir, "Masters.xml")
        entries_path = os.path.join(self.output_dir, "Entries.xml")

        with open(masters_path, "w", encoding="utf-8") as f:
            f.write(masters_xml)

        with open(entries_path, "w", encoding="utf-8") as f:
            f.write(entries_xml)

        return {
            "status": "success",
            "processed_count": len(documents),
            "masters_path": os.path.abspath(masters_path),
            "entries_path": os.path.abspath(entries_path),
            "breakdown": self._build_breakdown(documents)
        }

    def _build_breakdown(self, documents) -> Dict[str, int]:
        counts = {}
        for doc in documents:
            counts[doc.doc_type] = counts.get(doc.doc_type, 0) + 1
        return counts