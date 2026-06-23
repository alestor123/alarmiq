import os
import sys
from datetime import datetime

# Set up path to import backend modules
sys.path.append(os.path.dirname(__file__))

from predictor import predictor

# Construct a mock prediction record
mock_rec = {
    "node": "host-er2-router-03.my-cisco.net.in",
    "risk_score": 0.8942,
    "risk_level": "CRITICAL",
    "critical_alarms": 4,
    "major_alarms": 2,
    "minor_alarms": 11,
    "window_alarms": 17,
    "estimated_ttf": "18 minutes",
    "last_condition": "BGP-5-ADJCHANGE_DOWN",
    "next_predicted": "ROUTING-OSPF-5-ADJCHG",
    "next_prob": 0.682,
    "topology_correlation": {
        "correlated": True,
        "root_cause_node": "host-er2-router-01.my-cisco.net.in",
        "neighbor_level": "CRITICAL",
        "reason": "Adjacent router host-er2-router-01 is in CRITICAL state with multiple BGP_DOWN alarms."
    },
    "top_features": [
        {"feature": "minor_count", "value": 11.0, "importance": 0.098},
        {"feature": "severity_escalation_score", "value": 3.24, "importance": 0.108},
        {"feature": "cleared_ratio", "value": 0.15, "importance": 0.259},
        {"feature": "minor_ratio", "value": 0.65, "importance": 0.119},
        {"feature": "cleared_count", "value": 2.0, "importance": 0.063}
    ],
    "predicted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
}

print("=== TESTING NEW PREDICTION ALERT ===")
predictor._cli_notify("NEW_PREDICTION", mock_rec, datetime.now())

mock_rec["elapsed_mins"] = 14.5
mock_rec["resolution_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
mock_rec["actual_path"] = ["BGP-5-ADJCHANGE_DOWN", "LINK_DOWN", "ROUTING-OSPF-5-ADJCHG"]

print("=== TESTING TRUE POSITIVE INCIDENT CONFIRMATION ===")
predictor._cli_notify("TRUE_POSITIVE", mock_rec, datetime.now())

print("=== TESTING FALSE POSITIVE EXPIRY ===")
predictor._cli_notify("FALSE_POSITIVE", mock_rec, datetime.now())
