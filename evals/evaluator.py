import json
import xml.etree.ElementTree as ET
from typing import Dict, Any, List
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_GST_ENGINE = _ROOT / "gst_tally_engine"
if str(_GST_ENGINE) not in sys.path:
    sys.path.insert(0, str(_GST_ENGINE))

from agent_engine import GSTTallyEngine

class EngineEvaluator:
    """Automated Evaluation & Validation Harness for testing engine outputs."""

    def __init__(self):
        self.engine = GSTTallyEngine(output_dir="./eval_output")

    def run_all_evals(self, mock_payload: Dict[str, Any]) -> Dict[str, Any]:
        results = {
            "eval_zero_value_dropping": False,
            "eval_polarity_correctness": False,
            "eval_rounding_precision": False,
            "eval_xml_schema_validity": False,
            "passed_count": 0,
            "failed_count": 0
        }

        try:
            output = self.engine.process_payload(mock_payload, "GSTR1")
            
            # Read rendered entries XML
            with open(output["entries_path"], "r", encoding="utf-8") as f:
                entries_xml_str = f.read()

            # Test 1: XML Schema Validity
            results["eval_xml_schema_validity"] = self._eval_xml_syntax(entries_xml_str)

            # Test 2: Polarity & Amount Sign Rules (Debits = Negative, Credits = Positive)
            results["eval_polarity_correctness"] = self._eval_polarity(entries_xml_str)

            # Test 3: Zero Value Dropping
            results["eval_zero_value_dropping"] = self._eval_zero_value_filtering(mock_payload, output["processed_count"])

            # Test 4: Nearest Rupee Rounding
            results["eval_rounding_precision"] = self._eval_rounding(entries_xml_str)

            # Tally Scores
            for k, v in results.items():
                if k.startswith("eval_"):
                    if v:
                        results["passed_count"] += 1
                    else:
                        results["failed_count"] += 1

        except Exception as e:
            results["eval_error"] = str(e)

        return results

    def _eval_xml_syntax(self, xml_str: str) -> bool:
        try:
            ET.fromstring(xml_str)
            return True
        except ET.ParseError:
            return False

    def _eval_polarity(self, xml_str: str) -> bool:
        root = ET.fromstring(xml_str)
        for vch in root.findall(".//VOUCHER"):
            vch_type = vch.attrib.get("VCHTYPE")
            entries = vch.findall("ALLLEDGERENTRIES.LIST")
            if not entries:
                return False
            
            # Check Party line amount for Sales (Must be negative for Debit in Tally XML)
            if vch_type == "Sales":
                party_amt = float(entries[0].find("AMOUNT").text)
                if party_amt >= 0:
                    return False
        return True

    def _eval_zero_value_filtering(self, payload: Dict[str, Any], processed_count: int) -> bool:
        # Payload contains 3 records (1 valid B2B, 1 zero-value B2B, 1 Credit Note)
        return processed_count == 2

    def _eval_rounding(self, xml_str: str) -> bool:
        root = ET.fromstring(xml_str)
        for amt_node in root.findall(".//AMOUNT"):
            val = float(amt_node.text)
            if not val.is_integer():
                return False
        return True