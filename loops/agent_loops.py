import os
import json
from typing import Dict, Any, Optional
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_GST_ENGINE = _ROOT / "gst_tally_engine"
if str(_GST_ENGINE) not in sys.path:
    sys.path.insert(0, str(_GST_ENGINE))

from domain import GSTDataExtractor, NormalizedDocument
from xml_engine import TallyXMLEngine
from contexts.states import ExecutionContext, MappingMemoryContext
from harnesses.wrappers import AuditHarness, TallyHTTPHarness


class AgentLoopEngine:
    """Agentic Execution Loop with checkpointing, self-correction, and breakpoint recovery."""

    def __init__(self, memory: MappingMemoryContext, audit: AuditHarness, tally_harness: Optional[TallyHTTPHarness] = None):
        self.memory = memory
        self.audit = audit
        self.tally_harness = tally_harness or TallyHTTPHarness()

    def run_sequential_loop(
        self, 
        raw_json: Dict[str, Any], 
        gstr_type: str, 
        batch_id: str, 
        resume_from_breakpoint: bool = False,
        push_to_tally: bool = False
    ) -> Dict[str, Any]:
        
        # 1. Resume Checkpoint Resolution
        start_index = 0
        if resume_from_breakpoint:
            start_index = self.memory.get_checkpoint(batch_id)

        # 2. Extract & Normalize Documents
        documents = GSTDataExtractor.extract_documents(raw_json, gstr_type)
        total_docs = len(documents)

        context = ExecutionContext(
            batch_id=batch_id,
            gstr_type=gstr_type,
            fy="UNKNOWN",
            current_index=start_index,
            total_records=total_docs
        )

        processed_docs = []

        # 3. Sequential Task Loop
        for i in range(start_index, total_docs):
            doc = documents[i]
            
            try:
                # Self-Correction Loop / Fallback: Handle unmapped tax rate
                self._apply_tax_fallback_guard(doc)

                # Persist Party Mapping in Memory Context
                self.memory.save_party_mapping(doc.party_gstin, doc.party_name)

                processed_docs.append(doc)
                context.current_index = i + 1
                
                # Checkpoint State
                self.memory.save_checkpoint(batch_id, context.current_index, "PROCESSING")

            except Exception as e:
                self.audit.log_anomaly(doc.doc_number, doc.party_gstin, "LOOP_ERROR", str(e))
                self.memory.save_checkpoint(batch_id, i, "FAILED")
                break

        # 4. Generate Target XML Payloads
        masters_xml = TallyXMLEngine.generate_masters_xml(processed_docs, gstr_type)
        entries_xml = TallyXMLEngine.generate_entries_xml(processed_docs, gstr_type)

        # 5. Optional Tally HTTP Sync Loop
        tally_status = None
        if push_to_tally:
            m_resp = self.tally_harness.post_xml(masters_xml)
            e_resp = self.tally_harness.post_xml(entries_xml)
            tally_status = {"masters": m_resp, "entries": e_resp}

        self.memory.save_checkpoint(batch_id, context.current_index, "COMPLETED")

        return {
            "batch_id": batch_id,
            "total_processed": len(processed_docs),
            "last_processed_index": context.current_index,
            "masters_xml": masters_xml,
            "entries_xml": entries_xml,
            "tally_sync_status": tally_status
        }

    def _apply_tax_fallback_guard(self, doc: NormalizedDocument):
        """Self-correction fallback: ensures no unmapped or invalid tax line passes without a fallback name."""
        for tax in doc.tax_lines:
            if tax.rate <= 0:
                tax.tax_type = "SUSPENSE_TAX"