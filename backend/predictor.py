"""
AlarmIQ — Real-time Node Risk Predictor
Loads the trained model and scores each node's current alarm window.
"""

import os
import json
import joblib
import numpy as np
from collections import defaultdict, deque, Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from ml_pipeline import (
    LOOKBACK_HOURS, CONDITION_MAP, CONDITION_COLS,
    extract_features, normalise_node
)
from transition_model import AlarmTransitionPredictor

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
FALSE_POSITIVES_FILE = os.path.join(os.path.dirname(__file__), "data", "false_positives.jsonl")

# Correlated network protocol or hardware collapse conditions representing actual outages
SEVERE_CONDITIONS = {
    "BGP-5-ADJCHANGE_DOWN",
    "LINK_DOWN",
    "IOSXE_RP_ALARM_INFO_LINK_DOWN",
    "XCVR_MISSING_LINK_DOWN",
    "Optical Alarm",
    "cefcFRURemoved",
    "L2-L2VPN_PW-3-UPDOWN",
    "XCONNECT-5-PW_STATUS",
    "ROUTING-ISIS-5-ADJCHANGE_DOWN",
    "ROUTING-OSPF-5-ADJCHG",
    "cefcPowerStatusChange",
    "ROUTING-LDP-5-NBR_CHANGE_DOWN",
    "mplsLdpSessionDown",
    "SYSTEM_SHUTDOWN",
    "FAN_MISSING",
    "FAN_FAILURE",
    "POWER_SUPPLY_FAILURE",
    "temrouterrature alarm",
    "ACTIVE_CARD_REMOVED_OIR",
    "IOSXE_RP_ALARM_INFO_MODULE",
    "ciscoGnssModulePresenceStatus"
}

# Default thresholds — overridden dynamically when model loads optimal_threshold
RISK_THRESHOLDS = {
    "CRITICAL": 0.85,
    "HIGH":     0.70,
    "MEDIUM":   0.40,
    "LOW":      0.0,
}

# ── ANSI Terminal Colors & Styles ──────────────────────────────────────────
_RST  = "\033[0m"
_BOLD = "\033[1m"
_DIM  = "\033[2m"
_UL   = "\033[4m"
_RED  = "\033[91m"
_GRN  = "\033[92m"
_YEL  = "\033[93m"
_BLU  = "\033[94m"
_MAG  = "\033[95m"
_CYN  = "\033[96m"
_WHT  = "\033[97m"
_BG_RED = "\033[41m"
_BG_YEL = "\033[43m"
_BG_GRN = "\033[42m"
_BG_BLU = "\033[44m"
_SEV_CLR = {"CRITICAL": _RED, "HIGH": _RED, "MAJOR": _YEL, "MEDIUM": _YEL, "WARNING": _MAG, "MINOR": _BLU, "LOW": _GRN, "CLEARED": _DIM}

# Network Topology Graph Connection Model (Adjacent Cisco Routers)
# Extended with device_type and location for root cause analysis
TOPOLOGY = {
    "host-er2-router-03": {
        "neighbors": ["host-er2-router-02", "host-er2-router-01", "demo-node-01"],
        "type": "PE_ROUTER", "location": "ER2"
    },
    "host-er2-router-02": {
        "neighbors": ["host-er2-router-03", "host-er2-router-01"],
        "type": "PE_ROUTER", "location": "ER2"
    },
    "host-er2-router-01": {
        "neighbors": ["host-er2-router-02", "host-er2-router-03", "host-pe1-router-01"],
        "type": "PE_ROUTER", "location": "ER2"
    },
    "host-pe1-router-01": {
        "neighbors": ["host-er2-router-01", "host-pe1-router-02"],
        "type": "PE_ROUTER", "location": "PE1"
    },
    "host-pe1-router-02": {
        "neighbors": ["host-pe1-router-01"],
        "type": "PE_ROUTER", "location": "PE1"
    },
    "demo-node-01": {
        "neighbors": ["host-er2-router-03"],
        "type": "CE", "location": "DEMO"
    },
}

# Hardware/environmental alarm conditions that indicate a HARDWARE root cause
HARDWARE_CONDITIONS = {
    "cefcPowerStatusChange", "cefcFRURemoved", "FAN_MISSING", "FAN_FAILURE",
    "POWER_SUPPLY_FAILURE", "temrouterrature alarm", "ACTIVE_CARD_REMOVED_OIR",
    "IOSXE_RP_ALARM_INFO_MODULE",
}

# Routing protocol conditions
ROUTING_CONDITIONS = {
    "BGP-5-ADJCHANGE_DOWN", "ROUTING-ISIS-5-ADJCHANGE_DOWN",
    "ROUTING-OSPF-5-ADJCHG", "ROUTING-LDP-5-NBR_CHANGE_DOWN", "mplsLdpSessionDown",
}

def _risk_level(score: float) -> str:
    if score >= RISK_THRESHOLDS["CRITICAL"]: return "CRITICAL"
    if score >= RISK_THRESHOLDS["HIGH"]:     return "HIGH"
    if score >= RISK_THRESHOLDS["MEDIUM"]:   return "MEDIUM"
    return "LOW"


class NodeRiskPredictor:
    """
    Maintains a sliding 2-hour alarm window for every node.
    Call ingest_alarm() for each incoming alarm.
    Call score_node() to get the current risk score.
    """

    def __init__(self):
        self.model        = None
        self.scaler       = None
        self.feature_cols = None
        self.node_stats   = None
        self.metrics      = None
        self.calibrator   = None
        self._loaded      = False
        self.latest_sim_time = None
        self.transition_predictor = AlarmTransitionPredictor()

        # node → deque of alarm dicts (capped to LOOKBACK_HOURS rolling window)
        self._windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=5000))
        # node → latest score dict
        self._scores: dict[str, dict] = {}

        # Prediction Verification Ledger
        self.prediction_ledger: list[dict] = []
        self.active_predictions: dict[str, dict] = {} # node -> pending prediction record
        self.last_failure_times: dict[str, datetime] = {}

    def load(self):
        """Load model artifacts from disk."""
        global RISK_THRESHOLDS
        self.model        = joblib.load(os.path.join(MODELS_DIR, "best_model.pkl"))
        if hasattr(self.model, "n_jobs"):
            self.model.n_jobs = 1
        self.scaler       = joblib.load(os.path.join(MODELS_DIR, "scaler.pkl"))
        self.feature_cols = joblib.load(os.path.join(MODELS_DIR, "feature_cols.pkl"))
        self.node_stats   = joblib.load(os.path.join(MODELS_DIR, "node_stats.pkl"))
        
        cal_path = os.path.join(MODELS_DIR, "calibrator.pkl")
        if os.path.exists(cal_path):
            try:
                self.calibrator = joblib.load(cal_path)
                print("[predictor] Loaded probability calibrator")
            except Exception as e:
                print(f"[predictor] Failed to load calibrator: {e}")
                self.calibrator = None
        else:
            self.calibrator = None

        with open(os.path.join(MODELS_DIR, "metrics.json")) as f:
            self.metrics = json.load(f)
        self.transition_predictor.load()
        self._loaded = True

        # Dynamically calibrate risk thresholds from precision-optimized training
        opt_t = self.metrics.get("optimal_threshold")
        if opt_t is not None:
            RISK_THRESHOLDS["HIGH"] = opt_t
            RISK_THRESHOLDS["CRITICAL"] = min(opt_t + 0.15, 0.95)
            RISK_THRESHOLDS["MEDIUM"] = max(opt_t - 0.20, 0.15)
            print(f"[predictor] Risk thresholds calibrated from training:  "
                  f"HIGH={RISK_THRESHOLDS['HIGH']:.2f}  "
                  f"CRITICAL={RISK_THRESHOLDS['CRITICAL']:.2f}  "
                  f"MEDIUM={RISK_THRESHOLDS['MEDIUM']:.2f}")

        print(f"[predictor] Loaded model: {self.metrics['model_name']}  "
              f"AUC={self.metrics['roc_auc']}  AP={self.metrics.get('avg_precision', 'N/A')}  "
              f"Precision@threshold={self.metrics.get('threshold_precision', 'N/A')}")

    # ------------------------------------------------------------------
    def ingest_alarm(self, alarm: dict, score: bool = True) -> Optional[dict]:
        """
        Push one alarm into the node's window and return an updated score (if score=True).
        alarm dict must have keys: node, ts (datetime), condition, severity, category
        """
        if not self._loaded:
            raise RuntimeError("Call load() first")

        node = normalise_node(alarm.get("node", "UNKNOWN"))
        ts = alarm.get("ts")
        if isinstance(ts, str):
            try: ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except: ts = datetime.now(tz=timezone.utc)
        
        self.latest_sim_time = ts
        self._windows[node].append(alarm)
        if score:
            res = self.score_node(node, ref_time=ts)
            self._verify_predictions(node, ts, res["risk_score"])
            return res
        return None

    # ------------------------------------------------------------------
    def score_node(self, node: str, ref_time: Optional[datetime] = None) -> dict:
        """Score the current window for a node. Returns risk dict."""
        if not self._loaded:
            raise RuntimeError("Call load() first")

        if ref_time is None:
            ref_time = self.latest_sim_time or datetime.now(tz=timezone.utc)

        cutoff = ref_time - timedelta(hours=LOOKBACK_HOURS)

        # Filter and sort window chronologically to last LOOKBACK_HOURS
        raw_window = list(self._windows.get(node, []))
        window = [a for a in raw_window if a["ts"] >= cutoff and a["ts"] <= ref_time]
        window = sorted(window, key=lambda x: x["ts"])

        if not window:
            # OPTIMIZATION: Empty window has 0.0 risk, no model evaluation needed
            result = {
                "node":         node,
                "risk_score":   0.0,
                "risk_level":   "LOW",
                "window_alarms": 0,
                "top_features": [],
                "scored_at":    ref_time.isoformat(),
                "features":     {},
                "estimated_ttf": "Stable",
                "predicted_next_alarm": "None",
                "predicted_next_alarm_prob": 0.0,
                "predicted_stages": [],
                "actual_stages": [],
                "last_alarm_condition": "None",
                "topology_correlation": {"correlated": False},
            }
            self._scores[node] = result
            return result

        feats = extract_features(window, self.node_stats, node)

        # Collapse consecutive actual alarm conditions in the window
        actual_stages = []
        for a in window:
            c = a.get("condition")
            if c and c != "UNKNOWN" and c != "None":
                if not actual_stages or actual_stages[-1] != c:
                    actual_stages.append(c)

        # ── Precursor Signal Gate ──
        # If there are fewer than 3 total alarms in the window OR there are no CRITICAL
        # and fewer than 2 MAJOR alarms in the window, we gate the risk score to 0.0
        # to eliminate false positive warnings on transient single/double port flaps.
        crit_in_window = sum(1 for w in window if w["severity"] == "CRITICAL")
        major_in_window = sum(1 for w in window if w["severity"] == "MAJOR")
        if len(window) < 3 or (crit_in_window < 1 and major_in_window < 2):
            last_cond = window[-1].get("condition", "UNKNOWN")
            next_cond, next_prob = self.transition_predictor.predict_next_alarm(node, last_cond)
            stages = self.transition_predictor.predict_sequence_stages(node, last_cond)
            topo = self._topology_correlation(node, feats, ref_time)
            result = {
                "node":         node,
                "risk_score":   0.0,
                "risk_level":   "LOW",
                "window_alarms": len(window),
                "top_features": [],
                "scored_at":    ref_time.isoformat(),
                "features":     {k: round(float(v), 4) for k, v in feats.items()},
                "estimated_ttf": "Stable",
                "predicted_next_alarm": next_cond,
                "predicted_next_alarm_prob": round(next_prob, 4),
                "predicted_stages": stages,
                "actual_stages": actual_stages,
                "last_alarm_condition": last_cond,
                "topology_correlation": topo,
            }
            self._scores[node] = result
            return result

        # Build feature vector in correct column order
        x = np.array([[feats.get(col, 0.0) for col in self.feature_cols]])
        x_scaled = self.scaler.transform(x)

        prob = float(self.model.predict_proba(x_scaled)[0][1])
        if self.calibrator is not None:
            try:
                prob = float(self.calibrator.predict([prob])[0])
            except Exception as e:
                pass
        level = _risk_level(prob)

        # Top contributing features (from feature importances stored in model)
        top_features = self._top_features(feats)

        # Estimate Time to Failure (TTF) in minutes
        estimated_ttf = "Stable"
        if level in ("MEDIUM", "HIGH", "CRITICAL"):
            base_ttf = 120.0 * (1.0 - prob)
            alarm_rate = feats.get("alarm_rate_per_hour", 0.0)
            escalation = feats.get("severity_escalation_score", 0.0)
            
            factor = 1.0
            if alarm_rate > 10.0: factor *= 0.8
            if alarm_rate > 50.0: factor *= 0.6
            if escalation > 2.0:  factor *= 0.7
            
            ttf_minutes = max(3, int(base_ttf * factor))
            if ttf_minutes < 10:
                estimated_ttf = f"< {ttf_minutes + 2}m (Immediate)"
            else:
                estimated_ttf = f"~{ttf_minutes} mins"

        last_cond = window[-1].get("condition", "UNKNOWN")
        next_cond, next_prob = self.transition_predictor.predict_next_alarm(node, last_cond)
        stages = self.transition_predictor.predict_sequence_stages(node, last_cond)

        # Compute topology alarm correlation and root-cause mapping
        topo = self._topology_correlation(node, feats, ref_time)

        result = {
            "node":         node,
            "risk_score":   round(prob, 4),
            "risk_level":   level,
            "window_alarms": len(window),
            "top_features": top_features,
            "scored_at":    ref_time.isoformat(),
            "features":     {k: round(float(v), 4) for k, v in feats.items()},
            "estimated_ttf": estimated_ttf,
            "predicted_next_alarm": next_cond,
            "predicted_next_alarm_prob": round(next_prob, 4),
            "predicted_stages": stages,
            "actual_stages": actual_stages,
            "last_alarm_condition": last_cond,
            "topology_correlation": topo,
        }
        self._scores[node] = result
        return result

    # ------------------------------------------------------------------
    def _top_features(self, feats: dict, top_n: int = 5) -> list[dict]:
        """Return top N features sorted by model importance × scaled feature value."""
        if not hasattr(self.model, "feature_importances_"):
            # Logistic Regression fallback
            if hasattr(self.model, "coef_"):
                importances = dict(zip(self.feature_cols,
                                       np.abs(self.model.coef_[0])))
            else:
                return []
        else:
            importances = dict(zip(self.feature_cols,
                                   self.model.feature_importances_))

        # Scale features to compute true mathematical contribution
        x = np.array([[feats.get(col, 0.0) for col in self.feature_cols]])
        x_scaled = self.scaler.transform(x)[0]
        scaled_feats = dict(zip(self.feature_cols, x_scaled))

        scored = []
        for col in self.feature_cols:
            val = feats.get(col, 0.0)
            scaled_val = scaled_feats.get(col, 0.0)
            imp = importances.get(col, 0.0)
            contribution = abs(float(scaled_val * imp))
            scored.append({
                "feature":    col,
                "value":      round(float(val), 4),
                "importance": round(float(imp), 4),
                "contribution": round(contribution, 6),
            })

        scored.sort(key=lambda x: x["contribution"], reverse=True)
        return scored[:top_n]

    # ------------------------------------------------------------------
    def score_all_nodes(self, ref_time: Optional[datetime] = None) -> list[dict]:
        """Score every known node and return sorted by risk_score desc."""
        if ref_time is None:
            # FAST PATH: Return cached scores immediately for API requests to prevent GIL starvation
            results = list(self._scores.values())
        else:
            results = []
            for node in list(self._windows.keys()):
                results.append(self.score_node(node, ref_time=ref_time))
        results.sort(key=lambda x: x["risk_score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    def update_and_expire_scores(self, ref_time: datetime) -> list[dict]:
        """
        Fast incremental scoring and expiration:
        - Expire nodes whose latest alarm is older than 2 hours relative to ref_time.
        - Returns all current risk scores, sorted by risk_score desc.
        """
        cutoff = ref_time - timedelta(hours=2)
        
        for node, window in list(self._windows.items()):
            cached = self._scores.get(node)
            if cached and cached["risk_score"] > 0.0:
                if not window or window[-1]["ts"] < cutoff:
                    # Expire to stable
                    self._scores[node] = {
                        "node":         node,
                        "risk_score":   0.0,
                        "risk_level":   "LOW",
                        "window_alarms": 0,
                        "top_features": [],
                        "scored_at":    ref_time.isoformat(),
                        "features":     {},
                        "estimated_ttf": "Stable",
                        "predicted_next_alarm": "None",
                        "predicted_next_alarm_prob": 0.0,
                        "last_alarm_condition": "None",
                        "topology_correlation": {"correlated": False},
                    }
                    
        results = list(self._scores.values())
        results.sort(key=lambda x: x["risk_score"], reverse=True)
        return results

    # ------------------------------------------------------------------
    def _topology_correlation(self, node: str, feats: dict, ref_time: datetime) -> dict:
        """
        Enhanced root cause analysis with HARDWARE/PROTOCOL/UPSTREAM_FAILURE detection.
        Evaluates node's own conditions and neighboring nodes to determine root cause.
        """
        short_name = node.split(".")[0]
        topo_entry = TOPOLOGY.get(short_name)
        if not topo_entry:
            return {
                "correlated": False, "root_cause_node": None,
                "root_cause_type": None, "causal_chain": [],
                "neighbors": [], "impacted_services": {
                    "l3vpn_customers": 0, "l2vpn_customers": 0,
                    "ibgp_peers_affected": 0, "ebgp_peers_affected": 0,
                },
            }

        neighbor_names = topo_entry.get("neighbors", [])
        device_type = topo_entry.get("type", "PE_ROUTER")
        location = topo_entry.get("location", "UNKNOWN")

        # Build neighbor status list
        neighbor_status = []
        for n_short in neighbor_names:
            n_topo = TOPOLOGY.get(n_short, {})
            n_risk_level = "LOW"
            n_risk_score = 0.0
            n_link_status = "UP"
            for active_node, cached in self._scores.items():
                if active_node.split(".")[0] == n_short:
                    n_risk_level = cached.get("risk_level", "LOW")
                    n_risk_score = cached.get("risk_score", 0.0)
                    # Check if link is down
                    n_feats = cached.get("features", {})
                    if n_feats.get("link_down_count", 0) > 0 or n_feats.get("bgp_down_count", 0) > 0:
                        n_link_status = "DOWN"
                    break
            neighbor_status.append({
                "node": n_short,
                "risk_level": n_risk_level,
                "risk_score": round(n_risk_score, 4),
                "link_status": n_link_status,
                "device_type": n_topo.get("type", "PE_ROUTER") if isinstance(n_topo, dict) else "PE_ROUTER",
                "location": n_topo.get("location", "UNKNOWN") if isinstance(n_topo, dict) else "UNKNOWN",
            })

        causal_chain = []
        root_cause_node = None
        root_cause_type = None

        # ── Rule 1: HARDWARE root cause ──
        # Check if this node has environmental/hardware alarms in current window
        window = list(self._windows.get(node, []))
        cutoff = ref_time - timedelta(hours=2)
        recent_window = [a for a in window if a.get("ts") and a["ts"] >= cutoff]
        hw_alarms = [a for a in recent_window if a.get("condition") in HARDWARE_CONDITIONS]
        if hw_alarms:
            root_cause_type = "HARDWARE"
            root_cause_node = node
            for a in sorted(hw_alarms, key=lambda x: x["ts"]):
                ts_fmt = a["ts"].strftime("%H:%M") if isinstance(a["ts"], datetime) else str(a["ts"])
                causal_chain.append(f"{a['condition']} at {ts_fmt}")
            # Add subsequent routing alarms as secondary effects
            routing_alarms = [a for a in recent_window if a.get("condition") in ROUTING_CONDITIONS and a["ts"] > hw_alarms[0]["ts"]]
            for a in sorted(routing_alarms, key=lambda x: x["ts"])[:3]:
                ts_fmt = a["ts"].strftime("%H:%M") if isinstance(a["ts"], datetime) else str(a["ts"])
                causal_chain.append(f"{a['condition']} at {ts_fmt} (secondary)")

        # ── Rule 2: PROTOCOL root cause ──
        # If this node has BGP_DOWN and a neighbor PE is already CRITICAL
        if not root_cause_type and feats.get("bgp_down_count", 0) > 0:
            for ns in neighbor_status:
                if ns["risk_level"] in ("HIGH", "CRITICAL") and ns["device_type"] == "PE_ROUTER":
                    root_cause_type = "PROTOCOL"
                    root_cause_node = ns["node"]
                    causal_chain.append(f"{ns['node']} in {ns['risk_level']} state (root cause)")
                    causal_chain.append(f"BGP-5-ADJCHANGE_DOWN on {short_name} (secondary victim)")
                    break

        # ── Rule 3: UPSTREAM_FAILURE ──
        # If 3+ neighbors all have BGP_DOWN within same window
        if not root_cause_type:
            bgp_down_neighbors = [ns for ns in neighbor_status if ns["link_status"] == "DOWN"]
            if len(bgp_down_neighbors) >= 3:
                root_cause_type = "UPSTREAM_FAILURE"
                root_cause_node = f"shared-upstream-{location}"
                causal_chain.append(f"{len(bgp_down_neighbors)} neighbors lost connectivity simultaneously")
                for ns in bgp_down_neighbors[:5]:
                    causal_chain.append(f"{ns['node']} link DOWN")

        # ── Compute impacted services ──
        ibgp_affected = sum(1 for ns in neighbor_status if ns["link_status"] == "DOWN" and ns["device_type"] == "PE_ROUTER")
        ebgp_affected = sum(1 for ns in neighbor_status if ns["link_status"] == "DOWN" and ns["device_type"] not in ("PE_ROUTER", "CE"))
        # Estimate L3/L2 VPN impact based on node alarm severity
        l3vpn_est = max(0, int(feats.get("bgp_down_count", 0) * 2))
        l2vpn_est = max(0, int(feats.get("pw_updown_count", 0)))

        if root_cause_type and feats.get("bgp_down_count", 0) > 0:
            causal_chain.append(f"L3VPN customers impacted (~{l3vpn_est})")

        return {
            "correlated": root_cause_type is not None,
            "root_cause_node": root_cause_node,
            "root_cause_type": root_cause_type,
            "neighbor_level": neighbor_status[0]["risk_level"] if neighbor_status else None,
            "reason": causal_chain[0] if causal_chain else None,
            "causal_chain": causal_chain,
            "neighbors": neighbor_status,
            "impacted_services": {
                "l3vpn_customers": l3vpn_est,
                "l2vpn_customers": l2vpn_est,
                "ibgp_peers_affected": ibgp_affected,
                "ebgp_peers_affected": ebgp_affected,
            },
        }

    # ------------------------------------------------------------------
    def get_warnings(self, ref_time: Optional[datetime] = None) -> list[dict]:
        """Return all nodes with risk_level HIGH or CRITICAL."""
        all_scores = self.score_all_nodes(ref_time=ref_time)
        return [s for s in all_scores if s["risk_level"] in ("HIGH", "CRITICAL")]

    # ------------------------------------------------------------------
    def summary(self, ref_time: Optional[datetime] = None) -> dict:
        """High-level summary across all nodes."""
        all_scores = self.score_all_nodes(ref_time=ref_time)
        level_counts = Counter(s["risk_level"] for s in all_scores)
        return {
            "total_nodes":    len(all_scores),
            "critical_nodes": level_counts.get("CRITICAL", 0),
            "high_nodes":     level_counts.get("HIGH", 0),
            "medium_nodes":   level_counts.get("MEDIUM", 0),
            "low_nodes":      level_counts.get("LOW", 0),
            "model_name":     self.metrics.get("model_name", "N/A"),
            "model_auc":      self.metrics.get("roc_auc", 0.0),
        }

    # ------------------------------------------------------------------
    def build_socketio_payload(self, node: str) -> dict:
        """
        Build the full Socket.IO prediction_update payload for a node.
        Returns the complete schema as specified in Task 0.
        """
        score_data = self._scores.get(node)
        if not score_data:
            return None

        ref_time = self.latest_sim_time or datetime.now(tz=timezone.utc)
        feats = score_data.get("features", {})
        topo = score_data.get("topology_correlation", {"correlated": False})
        top_features_raw = score_data.get("top_features", [])

        # Build enriched top_features with bar_width_pct
        max_contrib = max((abs(f.get("contribution", 0)) for f in top_features_raw), default=0.001)
        top_features_enriched = []
        for f in top_features_raw:
            contrib = abs(f.get("contribution", 0))
            top_features_enriched.append({
                "feature": f.get("feature", ""),
                "raw_value": f.get("value", 0),
                "scaled_value": round(contrib / max_contrib, 4) if max_contrib > 0 else 0,
                "importance": f.get("importance", 0),
                "contribution": round(contrib, 6),
                "bar_width_pct": min(100, int(contrib / max_contrib * 100)) if max_contrib > 0 else 0,
            })

        # Compute window boundaries
        cutoff = ref_time - timedelta(hours=2)
        window_start = cutoff.isoformat()
        window_end = ref_time.isoformat()

        # TTF as integer minutes
        ttf_str = score_data.get("estimated_ttf", "Stable")
        try:
            import re
            ttf_match = re.search(r'(\d+)', ttf_str)
            ttf_minutes = int(ttf_match.group(1)) if ttf_match else None
        except Exception:
            ttf_minutes = None

        # Markov forecast
        next_cond = score_data.get("predicted_next_alarm", "None")
        next_prob = score_data.get("predicted_next_alarm_prob", 0.0)
        last_cond = score_data.get("last_alarm_condition", "None")
        stages = score_data.get("predicted_stages", [])

        predicted_path = [last_cond]
        for s in stages:
            predicted_path.append(f"{s['condition']} ({round(s['prob']*100)}%)")

        top_transitions = []
        if stages:
            for s in stages:
                top_transitions.append({"condition": s["condition"], "probability": round(s["prob"], 4)})
        elif next_cond and next_cond != "None":
            top_transitions.append({"condition": next_cond, "probability": round(next_prob, 4)})

        # Recent alarms for raw feed
        window_alarms = list(self._windows.get(node, []))
        recent = [a for a in window_alarms if a.get("ts") and a["ts"] >= cutoff]
        recent_sorted = sorted(recent, key=lambda x: x["ts"], reverse=True)[:20]
        recent_alarms_raw = []
        for a in recent_sorted:
            recent_alarms_raw.append({
                "alarm_id": hash(str(a.get("ts")) + a.get("condition", "")) % 9999999999,
                "ts": a["ts"].isoformat() if isinstance(a["ts"], datetime) else str(a["ts"]),
                "condition": a.get("condition", "UNKNOWN"),
                "severity": a.get("severity", "UNKNOWN"),
                "category": a.get("category", "System"),
                "message": a.get("raw", {}).get("alarmsDTO", {}).get("message", "") if isinstance(a.get("raw"), dict) else "",
            })

        # Node history from node_stats
        ns = (self.node_stats or {}).get(node, {})
        failure_rate = ns.get("failure_rate", 0.0)
        avg_daily = ns.get("avg_daily_alarms", 0.0)

        # Network health summary
        all_scores = list(self._scores.values())
        level_counts = Counter(s.get("risk_level", "LOW") for s in all_scores)
        ledger = self.get_verification_ledger()

        # Simulation state (will be enriched by main.py before emission)
        # This provides the predictor-side data; main.py adds sim state

        # Determine speed mode from TTF
        risk_score = score_data.get("risk_score", 0.0)
        risk_level = score_data.get("risk_level", "LOW")

        opt_threshold = self.metrics.get("optimal_threshold", 0.1924) if self.metrics else 0.1924

        payload = {
            "timestamp": ref_time.isoformat(),
            "node": node,
            "risk": {
                "score": round(risk_score, 4),
                "level": risk_level,
                "calibrated_probability": round(risk_score, 4),
                "optimal_threshold": round(opt_threshold, 4),
                "ttf_minutes": ttf_minutes,
            },
            "top_features": top_features_enriched,
            "alarm_breakdown": {
                "window_start": window_start,
                "window_end": window_end,
                "window_duration_hours": 2,
                "total_in_window": score_data.get("window_alarms", 0),
                "critical": int(feats.get("critical_count", 0)),
                "major": int(feats.get("major_count", 0)),
                "minor": int(feats.get("minor_count", 0)),
                "warning": int(feats.get("warning_count", 0)),
                "cleared": int(feats.get("cleared_count", 0)),
                "alarm_rate_per_hour": round(feats.get("alarm_rate_per_hour", 0), 2),
                "alarm_burst_15min": int(feats.get("alarm_burst_15min", 0)),
                "severity_escalation_score": round(feats.get("severity_escalation_score", 0), 4),
                "alarm_density_minutes": round(feats.get("alarm_density", 0), 2),
                "unique_conditions": int(feats.get("unique_conditions", 0)),
                "trend_last_30m": round(feats.get("trend_last_30m", 0), 4),
                "minutes_since_last_critical": round(feats.get("minutes_since_last_critical", 9999), 2),
            },
            "condition_counts": {
                "bgp_down_count": int(feats.get("bgp_down_count", 0)),
                "link_down_count": int(feats.get("link_down_count", 0)),
                "optical_alarm_count": int(feats.get("optical_alarm_count", 0)),
                "fru_removed_count": int(feats.get("fru_removed_count", 0)),
                "pw_updown_count": int(feats.get("pw_updown_count", 0)),
                "isis_down_count": int(feats.get("isis_down_count", 0)),
                "ospf_down_count": int(feats.get("ospf_down_count", 0)),
                "ssh_failure_count": int(feats.get("ssh_failure_count", 0)),
                "cefc_power_count": int(feats.get("cefc_power_count", 0)),
                "satellite_problem_count": int(feats.get("satellite_problem_count", 0)),
                "performance_iface_count": int(feats.get("performance_iface_count", 0)),
            },
            "markov_forecast": {
                "current_condition": last_cond,
                "next_condition": next_cond,
                "transition_probability": round(next_prob, 4),
                "forecast_label": f"{next_cond} IMMINENT" if next_cond and next_cond != "None" and next_prob > 0.3 else "Stable",
                "top_transitions": top_transitions,
                "predicted_transition_path": predicted_path,
                "lookup_level": "node",
            },
            "topology": {
                "neighbors": topo.get("neighbors", []),
                "correlated": topo.get("correlated", False),
                "root_cause_node": topo.get("root_cause_node"),
                "root_cause_type": topo.get("root_cause_type"),
                "causal_chain": topo.get("causal_chain", []),
                "impacted_services": topo.get("impacted_services", {
                    "l3vpn_customers": 0, "l2vpn_customers": 0,
                    "ibgp_peers_affected": 0, "ebgp_peers_affected": 0,
                }),
            },
            "node_history": {
                "failure_rate_per_day": round(failure_rate, 4),
                "avg_daily_alarms": round(avg_daily, 2),
                "total_historical_failures": int(failure_rate * 365) if failure_rate else 0,
            },
            "recent_alarms_raw": recent_alarms_raw,
            "network_health_summary": {
                "total_nodes": len(all_scores),
                "critical_count": level_counts.get("CRITICAL", 0),
                "high_count": level_counts.get("HIGH", 0),
                "medium_count": level_counts.get("MEDIUM", 0),
                "low_count": level_counts.get("LOW", 0),
                "alarms_per_minute": round(sum(s.get("features", {}).get("alarm_rate_per_hour", 0) for s in all_scores) / 60, 2),
                "live_precision": ledger.get("precision"),
                "true_positives": ledger.get("true_positives", 0),
                "false_positives": ledger.get("false_positives", 0),
                "pending_predictions": ledger.get("pending_count", 0),
                "total_predictions": ledger.get("total_predictions", 0),
                "avg_time_to_verify_minutes": None,  # computed by caller if needed
            },
            "simulation": {
                "is_running": False,  # enriched by main.py
                "speed_mode": "slow",
                "alarms_processed": 0,
                "total_alarms": 0,
                "progress_pct": 0,
                "current_alarm_ts": ref_time.isoformat(),
            },
        }
        return payload

    # ------------------------------------------------------------------
    def _detect_failure_now(self, node: str, ref_time: datetime) -> bool:
        """
        Returns True if the node currently has ≥3 CRITICAL alarms in the last 30 minutes.
        This is the same definition used in the offline failure labeler.
        """
        window = list(self._windows.get(node, []))
        cutoff = ref_time - timedelta(minutes=30)
        recent_crits = [
            a for a in window
            if a.get("severity") == "CRITICAL" and a.get("ts") >= cutoff
        ]
        return len(recent_crits) >= 3

    # ------------------------------------------------------------------
    def _get_actual_path(self, node: str, predicted_at_str: str, current_ts: datetime) -> list[str]:
        """
        Extract the actual sequence of alarm conditions that occurred on the node
        from predicted_at up to current_ts. Deduplicates consecutive identical states.
        """
        predicted_at = datetime.fromisoformat(predicted_at_str)
        window = list(self._windows.get(node, []))
        window = sorted(window, key=lambda x: x["ts"])
        path = []
        for a in window:
            if a["ts"] >= predicted_at and a["ts"] <= current_ts:
                path.append(a["condition"])
        
        # Deduplicate consecutive identical alarm states
        collapsed = []
        for c in path:
            if not collapsed or collapsed[-1] != c:
                collapsed.append(c)
        return collapsed

    # ------------------------------------------------------------------
    def _get_comprehensive_alarms_raw(self, node: str, predicted_at_str: str, current_ts: datetime) -> list[dict]:
        """
        Get all raw alarms from 2 hours before predicted_at up to current_ts,
        sorted chronologically by timestamp.
        """
        predicted_at = datetime.fromisoformat(predicted_at_str)
        cutoff = predicted_at - timedelta(hours=2)
        window = list(self._windows.get(node, []))
        window = sorted(window, key=lambda x: x["ts"])
        
        recent = [a for a in window if a["ts"] >= cutoff and a["ts"] <= current_ts]
        
        recent_raw = []
        for a in recent:
            if a.get("raw") is not None:
                recent_raw.append(a["raw"])
            else:
                # Construct mock representation
                mock_id = 494700000 + hash((a.get("ts").isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts"))) + a.get("condition", "")) % 99999
                recent_raw.append({
                    "@dtoTyrouter": "alarmsDTO",
                    "@tyrouter": "Alarms",
                    "@url": f"https://my-cisco.net/webacs/api/v4/data/Alarms/{mock_id}",
                    "alarmsDTO": {
                        "@displayName": str(mock_id),
                        "@id": mock_id,
                        "acknowledgementStatus": False,
                        "alarmFoundAt": a.get("ts").isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                        "alarmId": mock_id,
                        "category": {
                            "ordinal": 17,
                            "value": a.get("category", "System")
                        },
                        "condition": {
                            "ordinal": -1,
                            "value": a.get("condition", "UNKNOWN")
                        },
                        "deviceName": a.get("node", "demo-node-01"),
                        "deviceTimestamp": a.get("ts").isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                        "lastUpdatedAt": a.get("ts").isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                        "message": f"Inventory sync suppressed due to continuous events of tyrouter [{a.get('condition')}], will trigger feature sync after 5 minutes",
                        "severity": a.get("severity", "MINOR"),
                        "source": "NMS:bgl-epnm-01",
                        "timeStamp": a.get("ts").isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                        "wirelessSroutercificAlarmId": f"192.168.1.24:{a.get('condition')}"
                    }
                })
        return recent_raw

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  CLI Notification System — Rich Terminal Output for Engineers
    # ------------------------------------------------------------------
    def _cli_notify(self, event_type: str, rec: dict, ts: datetime):
        """
        Print a beautiful, detailed CLI notification styled as an SMS text message dispatch for engineers.
        event_type: 'NEW_PREDICTION' | 'TRUE_POSITIVE' | 'FALSE_POSITIVE'
        """
        try:
            import telegram_notifier
            telegram_notifier.queue_event(event_type, rec, ts.isoformat())
        except Exception as e:
            print(f"[telegram] Failed to queue notification: {e}")

        import re
        ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m|\033\[[0-9;]*m')
        def visual_len(s):
            return len(ANSI_ESCAPE.sub('', s))

        node     = rec.get("node", "UNKNOWN")
        score    = rec.get("risk_score", 0.0)
        level    = rec.get("risk_level", "HIGH")
        score_pct = round(score * 100, 1)
        lvl_clr  = _SEV_CLR.get(level, _WHT)
        ts_str   = ts.strftime("%H:%M:%S")
        date_str = ts.strftime("%Y-%m-%d")

        crit   = rec.get("critical_alarms", 0)
        maj    = rec.get("major_alarms", 0)
        minor  = rec.get("minor_alarms", 0)
        w_total = rec.get("window_alarms", 0)
        ttf    = rec.get("estimated_ttf", "Stable")
        last_c = rec.get("last_condition", "N/A")
        next_c = rec.get("next_predicted", "N/A")
        next_p = rec.get("next_prob", 0.0)
        pred_stages = rec.get("predicted_stages", [])
        topo   = rec.get("topology_correlation", {})
        feats  = rec.get("top_features", [])
        pred_at = rec.get("predicted_at", ts_str)

        # Phone screen width is 64 characters
        W = 64

        # Top phone bezel
        print()
        print(f"  📱 {_BOLD}{_WHT}SMS DISPATCH — ALARMIQ AI OPERATIONS{_RST}")
        print(f"  ┌{'─' * (W - 2)}┐")
        print(f"  │ 👤 {_BOLD}From: {_RST}AlarmIQ Operator           📡 {_BOLD}To: {_RST}On-Call Network Engineer │")
        print(f"  │ ⏰ {_BOLD}Sent: {_RST}{date_str} {ts_str}                                     │")
        print(f"  ├{'─' * (W - 2)}┤")
        print(f"  │                                                          │")

        if event_type == "NEW_PREDICTION":
            # Message bubble header
            print(f"  │  💬 {_BG_RED}{_BOLD}{_WHT}  INCOMING ALERT  {_RST}                                     │")
            print(f"  │  ┌{'─' * (W - 10)}┐  │")
            
            # Message body
            def _msg_line(txt):
                vlen = visual_len(txt)
                pad = max(0, (W - 14) - vlen)
                print(f"  │  │ {txt}{' ' * pad} │  │")

            _msg_line(f"🚨 {_RED}{_BOLD}CRITICAL RISK DETECTED ON NODE:{_RST}")
            _msg_line(f"   ➜ {_CYN}{_BOLD}{node}{_RST}")
            _msg_line("")
            _msg_line(f"📊 {_BOLD}Current Status:{_RST}")
            _msg_line(f"   • Risk Score:  {lvl_clr}{_BOLD}{score_pct}% ({level}){_RST}")
            _msg_line(f"   • Window load: {_WHT}{w_total} alarms{_RST} ({_RED}{crit} CRIT{_RST}, {_YEL}{maj} MAJ{_RST})")
            _msg_line(f"   • Est. TTF:    {_RED}{_BOLD}{ttf}{_RST}")
            _msg_line("")
            _msg_line(f"🔮 {_BOLD}Sequence Forecast (Predicted Stages):{_RST}")
            if pred_stages:
                path_parts = [last_c] + [f"{_YEL}{_BOLD}{s['condition']}{_RST} ({round(s['prob']*100)}%)" for s in pred_stages]
                path_str = f" {_DIM}➜{_RST} ".join(path_parts)
                _msg_line(f"   {path_str}")
            else:
                _msg_line(f"   {last_c} ➜ {_YEL}{_BOLD}{next_c}{_RST} ({round(next_p*100)}% conf)")

            if topo.get("correlated"):
                _msg_line("")
                _msg_line(f"🌐 {_BOLD}Topology Link Correlation:{_RST}")
                _msg_line(f"   Root cause is likely neighbor: {_RED}{_BOLD}{topo.get('root_cause_node', '?')}{_RST}")

            _msg_line("")
            _msg_line(f"🛠️  {_GRN}{_BOLD}ENGINEER ACTION PLAN:{_RST}")
            _msg_line(f"   1. SSH to device:")
            _msg_line(f"      {_DIM}$ sshadmin@{node}{_RST}")
            _msg_line(f"   2. Pull interface diagnostic:")
            _msg_line(f"      {_DIM}$ show interfaces summary | include down{_RST}")
            _msg_line(f"   3. Check routing adjacency status:")
            _msg_line(f"      {_DIM}$ show bgp neighbors | show ospf neighbor{_RST}")
            
            topo_entry = TOPOLOGY.get(node.split(".")[0], {})
            neighbors = topo_entry.get("neighbors", []) if isinstance(topo_entry, dict) else topo_entry
            if neighbors:
                _msg_line(f"   4. Inspect neighbor routers:")
                _msg_line(f"      {_DIM}{', '.join(neighbors)}{_RST}")
            
            print(f"  │  └{'─' * (W - 10)}┘  │")

        elif event_type == "TRUE_POSITIVE":
            elapsed = rec.get("elapsed_mins", 0)
            res_time = rec.get("resolution_time", ts_str)
            actual  = rec.get("actual_path", [])
            
            print(f"  │  💬 {_BG_GRN}{_BOLD}{_WHT}  DISPATCH RESOLVED  {_RST}                                  │")
            print(f"  │  ┌{'─' * (W - 10)}┐  │")
            
            def _msg_line(txt):
                vlen = visual_len(txt)
                pad = max(0, (W - 14) - vlen)
                print(f"  │  │ {txt}{' ' * pad} │  │")

            _msg_line(f"✅ {_GRN}{_BOLD}FAILURE PREDICTION CONFIRMED (TP){_RST}")
            _msg_line(f"   Node: {_CYN}{_BOLD}{node}{_RST}")
            _msg_line("")
            _msg_line(f"📈 {_BOLD}Incident Timeline:{_RST}")
            _msg_line(f"   • Predicted At: {_CYN}{pred_at}{_RST}")
            _msg_line(f"   • Failure At:   {_CYN}{res_time}{_RST}")
            _msg_line(f"   • Time-to-fail: {_GRN}{_BOLD}{elapsed} minutes{_RST} (model predicted {ttf})")
            
            if actual:
                _msg_line("")
                _msg_line(f"🔗 {_BOLD}Observed Precursor Sequence:{_RST}")
                path_str = f" {_DIM}➜{_RST} ".join(f"{_CYN}{c}{_RST}" for c in actual)
                _msg_line(f"   {path_str}")

            _msg_line("")
            _msg_line(f"🛠️  {_GRN}{_BOLD}POST-MORTEM NEXT STEPS:{_RST}")
            _msg_line(f"   1. Confirm traffic has rerouted successfully")
            _msg_line(f"   2. File incident ticket with timeline above")
            _msg_line(f"   3. Pull tech-support log file for RCA:")
            _msg_line(f"      {_DIM}$ show tech-support{_RST}")
            
            print(f"  │  └{'─' * (W - 10)}┘  │")

        elif event_type == "FALSE_POSITIVE":
            elapsed = rec.get("elapsed_mins", 0)
            res_time = rec.get("resolution_time", ts_str)
            actual  = rec.get("actual_path", [])
            
            print(f"  │  💬 {_BG_YEL}{_BOLD}{_WHT}  DISPATCH EXPIRED  {_RST}                                   │")
            print(f"  │  ┌{'─' * (W - 10)}┐  │")
            
            def _msg_line(txt):
                vlen = visual_len(txt)
                pad = max(0, (W - 14) - vlen)
                print(f"  │  │ {txt}{' ' * pad} │  │")

            _msg_line(f"ℹ️  {_YEL}{_BOLD}ALERT AUTO-EXPIRED (FALSE POSITIVE){_RST}")
            _msg_line(f"   Node: {_CYN}{_BOLD}{node}{_RST}")
            _msg_line("")
            _msg_line(f"📉 {_BOLD}Summary details:{_RST}")
            _msg_line(f"   • Sent At:     {_CYN}{pred_at}{_RST}")
            _msg_line(f"   • Expired At:  {_CYN}{res_time}{_RST}")
            _msg_line(f"   • Duration:    {_BOLD}{elapsed} minutes{_RST} (mismatched transition)")
            _msg_line(f"   • Original P:  {score_pct}% risk")
            
            if actual:
                _msg_line("")
                _msg_line(f"🔗 {_BOLD}Observed Mismatched Sequence:{_RST}")
                path_str = f" {_DIM}➜{_RST} ".join(f"{_RED}{c}{_RST}" for c in actual)
                _msg_line(f"   {path_str}")

            _msg_line("")
            _msg_line(f"🛠️  {_YEL}{_BOLD}FOLLOW-UP ACTIONS:{_RST}")
            _msg_line(f"   1. Standard close — node self-recovered successfully.")
            _msg_line(f"   2. Review if triggered by a planned maintenance window.")
            _msg_line(f"   3. If flapping interface, apply dampening:")
            _msg_line(f"      {_DIM}$ dampening 15 750 2000 60{_RST}")
            
            print(f"  │  └{'─' * (W - 10)}┘  │")

        print(f"  │                                                          │")
        print(f"  └{'─' * (W - 2)}┘")
        print()

    # ------------------------------------------------------------------
    def _verify_predictions(self, node: str, ts: datetime, risk_score: float):
        """
        Called after every scored alarm ingestion. Implements the state transition verification logic:
        1. Catastrophic Outage Failure Detected -> resolve as True Positive (Failure Detected)
        2. Sequence/Correlated Match observed -> resolve active prediction as True Positive (Seq Match / Correlated Match)
        3. Transition to incorrect condition occurred -> resolve as False Positive (Mismatched Sequence)
        4. Time-based Expiry past 120m prediction horizon -> resolve as False Positive (Horizon Expired)
        5. Risk crosses HIGH threshold and no active prediction -> open new pending prediction
        """
        # Step 0: Expire any active predictions across all nodes that have exceeded the 120-minute prediction horizon
        # [MODIFIED]: Disabling time-based auto-expiry per user request. 
        # Active predictions will remain in the "Pending ⏳" state until a transition occurs.
        pass

        # Step 1: Process active prediction state resolutions for the current node
        if node in self.active_predictions:
            rec = self.active_predictions[node]
            predicted_at = datetime.fromisoformat(rec["predicted_at"])
            actual_path = self._get_actual_path(node, rec["predicted_at"], ts)

            next_pred = rec.get("next_predicted", "N/A")
            last_cond = rec.get("last_condition", "N/A")
            predicted_stages = [stage.get("condition") for stage in rec.get("predicted_stages", []) if stage.get("condition")]

            # [MODIFIED]: Disabling immediate ground-truth Catastrophic Outage Failure detection per user request.
            # We wait until a sequence transition occurs.

            # Transitioned states are any states in actual_path that are not last_cond/N/A/UNKNOWN/empty
            transitioned_states = [c for c in actual_path if c != last_cond and c != "N/A" and c != "UNKNOWN" and c != ""]
            
            has_transitioned = len(transitioned_states) > 0

            if has_transitioned:
                # Check for Sequence Match: transitioned state is exactly the next predicted alarm,
                # matches predicted stages, or represents any severe failure progression path.
                is_tp_match = False
                if (next_pred in transitioned_states or 
                    any(stage in transitioned_states for stage in predicted_stages) or 
                    any(c in SEVERE_CONDITIONS for c in transitioned_states)):
                    is_tp_match = True

                if is_tp_match:
                    rec = self.active_predictions.pop(node)
                    rec["status"] = "True Positive ✅ (Seq Match)"
                    rec["resolution_time"] = ts.isoformat()
                    rec["resolved"] = True
                    rec["elapsed_mins"] = round((ts - predicted_at).total_seconds() / 60, 1)
                    rec["actual_path"] = actual_path
                    rec["recent_alarms_raw"] = self._get_comprehensive_alarms_raw(node, rec["predicted_at"], ts)
                    self.prediction_ledger.append(rec)
                    self._cli_notify("TRUE_POSITIVE", rec, ts)
                    return  # Prediction resolved as TP (Sequence Match)
                else:
                    # Mismatch: transition occurred, but did not match the predicted sequence
                    rec = self.active_predictions.pop(node)
                    rec["status"] = "False Positive ❌ (Mismatched Sequence)"
                    rec["resolution_time"] = ts.isoformat()
                    rec["resolved"] = True
                    rec["elapsed_mins"] = round((ts - predicted_at).total_seconds() / 60, 1)
                    rec["actual_path"] = actual_path
                    rec["recent_alarms_raw"] = self._get_comprehensive_alarms_raw(node, rec["predicted_at"], ts)
                    self.prediction_ledger.append(rec)
                    self._log_false_positive_to_file(rec)
                    self._cli_notify("FALSE_POSITIVE", rec, ts)
                    return  # Prediction resolved as FP (Sequence Mismatch)

            # Check 1.3: No transition has occurred yet (actual_path only contains last_cond)
            # We do NOT resolve as False Positive or True Positive. We keep it as "Pending ⏳" and wait!
            pass


 
        # Step 2: Open new prediction if risk crosses HIGH threshold and no active prediction remains
        if risk_score >= RISK_THRESHOLDS["HIGH"] and node not in self.active_predictions:
            # Capture rich snapshot of current state at prediction time
            current_score = self._scores.get(node, {})
            window = list(self._windows.get(node, []))
            window = sorted(window, key=lambda x: x["ts"])
            cutoff = ts - timedelta(hours=2)
            recent = [a for a in window if a.get("ts", ts) >= cutoff]
            recent = sorted(recent, key=lambda x: x["ts"])
            crit_count = sum(1 for a in recent if a.get("severity") == "CRITICAL")
            maj_count  = sum(1 for a in recent if a.get("severity") == "MAJOR")
            min_count  = sum(1 for a in recent if a.get("severity") in ("MINOR", "WARNING"))
 
            recent_raw = []
            for a in recent:
                if a.get("raw") is not None:
                    recent_raw.append(a["raw"])
                else:
                    # Construct mock cisco DTO representation dynamically
                    mock_id = 494700000 + hash((a.get("ts", ts).isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts"))) + a.get("condition", "")) % 99999
                    recent_raw.append({
                        "@dtoTyrouter": "alarmsDTO",
                        "@tyrouter": "Alarms",
                        "@url": f"https://my-cisco.net/webacs/api/v4/data/Alarms/{mock_id}",
                        "alarmsDTO": {
                            "@displayName": str(mock_id),
                            "@id": mock_id,
                            "acknowledgementStatus": False,
                            "alarmFoundAt": a.get("ts", ts).isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                            "alarmId": mock_id,
                            "category": {
                                "ordinal": 17,
                                "value": a.get("category", "System")
                            },
                            "condition": {
                                "ordinal": -1,
                                "value": a.get("condition", "UNKNOWN")
                            },
                            "deviceName": a.get("node", "demo-node-01"),
                            "deviceTimestamp": a.get("ts", ts).isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                            "lastUpdatedAt": a.get("ts", ts).isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                            "message": f"Inventory sync suppressed due to continuous events of tyrouter [{a.get('condition')}], will trigger feature sync after 5 minutes",
                            "severity": a.get("severity", "MINOR"),
                            "source": "NMS:bgl-epnm-01",
                            "timeStamp": a.get("ts", ts).isoformat() if isinstance(a.get("ts"), datetime) else str(a.get("ts")),
                            "wirelessSroutercificAlarmId": f"192.168.1.24:{a.get('condition')}"
                        }
                    })

            new_rec = {
                "node":              node,
                "predicted_at":      ts.isoformat(),
                "risk_score":        round(risk_score, 4),
                "risk_level":        "HIGH" if risk_score < RISK_THRESHOLDS["CRITICAL"] else "CRITICAL",
                "status":            "Pending ⏳",
                "resolution_time":   None,
                "resolved":          False,
                "elapsed_mins":      None,
                # ── Rich snapshot identical to what renderWarnings uses ──
                "window_alarms":     len(recent),
                "critical_alarms":   crit_count,
                "major_alarms":      maj_count,
                "minor_alarms":      min_count,
                "estimated_ttf":     current_score.get("estimated_ttf", "Stable"),
                "last_condition":    current_score.get("last_alarm_condition", "N/A"),
                "next_predicted":    current_score.get("predicted_next_alarm", "N/A"),
                "next_prob":         round(current_score.get("predicted_next_alarm_prob", 0.0), 4),
                "predicted_stages":  current_score.get("predicted_stages", []),
                "actual_stages":     current_score.get("actual_stages", []),
                "scored_at":         current_score.get("scored_at", ts.isoformat()),
                "top_features":      current_score.get("top_features", [])[:5],
                "topology_correlation": current_score.get("topology_correlation", {"correlated": False}),
                "horizon_mins":      120,
                "actual_path":       [current_score.get("last_alarm_condition", "N/A")],
                "recent_alarms_raw": recent_raw,
            }
            self.active_predictions[node] = new_rec
            self._cli_notify("NEW_PREDICTION", new_rec, ts)

    # ------------------------------------------------------------------
    def get_verification_ledger(self) -> dict:
        """Return the full prediction verification ledger with aggregate metrics."""
        ref_time = self.latest_sim_time or datetime.now(tz=timezone.utc)
        pending = []
        for rec in self.active_predictions.values():
            rec_copy = dict(rec)
            rec_copy["actual_path"] = self._get_actual_path(rec["node"], rec["predicted_at"], ref_time)
            rec_copy["recent_alarms_raw"] = self._get_comprehensive_alarms_raw(rec["node"], rec["predicted_at"], ref_time)
            pending.append(rec_copy)

        resolved  = self.prediction_ledger

        tp = sum(1 for r in resolved if "True Positive" in r["status"])
        fp = sum(1 for r in resolved if "False Positive" in r["status"])
        total_resolved = len(resolved)
        precision = round(tp / total_resolved, 4) if total_resolved > 0 else None

        return {
            "resolved":   list(resolved),
            "pending":    pending,
            "total_predictions": total_resolved + len(pending),
            "true_positives":    tp,
            "false_positives":   fp,
            "pending_count":     len(pending),
            "precision":         precision,
        }

    # ------------------------------------------------------------------
    def _log_false_positive_to_file(self, rec: dict):
        """Append details of the false positive to data/false_positives.jsonl (JSON Lines format)."""
        try:
            os.makedirs(os.path.dirname(FALSE_POSITIVES_FILE), exist_ok=True)
            
            # Create a serializable and cleaned snapshot of the false positive record
            clean_rec = {
                "node": rec.get("node"),
                "predicted_at": rec.get("predicted_at"),
                "resolution_time": rec.get("resolution_time"),
                "risk_score": rec.get("risk_score"),
                "risk_level": rec.get("risk_level"),
                "status": rec.get("status"),
                "elapsed_mins": rec.get("elapsed_mins"),
                "window_alarms": rec.get("window_alarms"),
                "critical_alarms": rec.get("critical_alarms"),
                "major_alarms": rec.get("major_alarms"),
                "minor_alarms": rec.get("minor_alarms"),
                "estimated_ttf": rec.get("estimated_ttf"),
                "last_condition": rec.get("last_condition"),
                "next_predicted": rec.get("next_predicted"),
                "next_prob": rec.get("next_prob"),
                "predicted_stages": rec.get("predicted_stages"),
                "actual_stages": rec.get("actual_stages"),
                "actual_path": rec.get("actual_path"),
                "topology_correlation": rec.get("topology_correlation"),
                "top_features": rec.get("top_features"),
                "recent_alarms_raw": rec.get("recent_alarms_raw", [])
            }
            
            with open(FALSE_POSITIVES_FILE, "a") as f:
                f.write(json.dumps(clean_rec) + "\n")
        except Exception as e:
            print(f"[predictor] Failed to write false positive log: {e}")

    # ------------------------------------------------------------------
    def clear_ledger(self):
        """Clear all verification state (call before starting a fresh simulation)."""
        self.prediction_ledger.clear()
        self.active_predictions.clear()
        self.last_failure_times.clear()
        if os.path.exists(FALSE_POSITIVES_FILE):
            try:
                os.remove(FALSE_POSITIVES_FILE)
            except Exception:
                pass


# Singleton instance
predictor = NodeRiskPredictor()

