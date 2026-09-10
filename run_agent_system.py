import os
import sys
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_GST_ENGINE = _ROOT / "gst_tally_engine"
if str(_GST_ENGINE) not in sys.path:
    sys.path.insert(0, str(_GST_ENGINE))

from contexts.states import MappingMemoryContext
from harnesses.wrappers import ValidationHarness, AuditHarness, TallyHTTPHarness
from loops.agent_loops import AgentLoopEngine
from evals.evaluator import EngineEvaluator


# Full Mock Payload with edge cases (Valid Invoice, Zero-Value Invoice, Credit Note)
MOCK_INTEGRATION_PAYLOAD = {
  "b2b": [
    {
      "ctin": "27AADCB2230M1Z2",
      "tradeName": "Apex Logistics",
      "inv": [
        {
          "inum": "INV-2026-101",
          "idt": "10-04-2026",
          "val": 11800.45,  # Fractional paise to test rounding eval
          "itms": [
            {"itm_det": {"txval": 10000.45, "rt": 18.0, "camt": 900.0, "samt": 900.0, "iamt": 0.0}}
          ]
        },
        {
          "inum": "INV-ZERO-001",
          "idt": "11-04-2026",
          "val": 0.0,  # Zero-value invoice to test dropping eval
          "itms": []
        }
      ]
    }
  ],
  "cdnr": [
    {
      "ctin": "27AADCB2230M1Z2",
      "tradeName": "Apex Logistics",
      "nt": [
        {
          "nt_num": "CN-2026-001",
          "nt_dt": "12-04-2026",
          "ntty": "C",
          "val": 1180.00,
          "itms": [
            {"itm_det": {"txval": 1000.00, "rt": 18.0, "camt": 90.0, "samt": 90.0, "iamt": 0.0}}
          ]
        }
      ]
    }
  ]
}

if __name__ == "__main__":
    print("=========================================================")
    print("       GST-TO-TALLY AGENTIC ENGINE SYSTEM RUNNER         ")
    print("=========================================================\n")

    # 1. RUN EVALUATION SUITE
    print("[STEP 1] Running Evaluation Suite...")
    evaluator = EngineEvaluator()
    eval_results = evaluator.run_all_evals(MOCK_INTEGRATION_PAYLOAD)
    print(f"Eval Results: {json.dumps(eval_results, indent=2)}\n")

    # 2. EXECUTE VALIDATION HARNESS
    print("[STEP 2] Running Validation Harness...")
    total_docs = ValidationHarness.validate_payload_and_fy(
        filename="GSTR1_2025-2026_APRIL.json",
        payload=MOCK_INTEGRATION_PAYLOAD,
        gstr_type="GSTR1",
        fy="2025-2026"
    )
    print(f"Validation Harness Passed: Identified {total_docs} total raw records.\n")

    # 3. INITIALIZE CONTEXTS & HARNESSES
    memory_context = MappingMemoryContext("system_memory.db")
    audit_harness = AuditHarness()
    tally_harness = TallyHTTPHarness("http://localhost:9000")

    # 4. EXECUTE AGENT LOOP ENGINE
    print("[STEP 3] Executing Stateful Agent Loop Engine...")
    loop_engine = AgentLoopEngine(
        memory=memory_context, 
        audit=audit_harness, 
        tally_harness=tally_harness
    )

    loop_result = loop_engine.run_sequential_loop(
        raw_json=MOCK_INTEGRATION_PAYLOAD,
        gstr_type="GSTR1",
        batch_id="BATCH-APRIL-2026-001",
        resume_from_breakpoint=False,
        push_to_tally=False  # Set True if local Tally is running on port 9000
    )

    print(f"Loop Engine Execution Success!")
    print(f"Processed Count: {loop_result['total_processed']}")
    print(f"Last Processed Index: {loop_result['last_processed_index']}")

    # 5. EXPORT AUDIT TRAIL
    print("\n[STEP 4] Exporting CSV Audit Trail...")
    csv_logs = audit_harness.export_csv()
    print("CSV Log Output Buffer:")
    print(csv_logs)