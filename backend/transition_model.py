"""
AlarmIQ — Sequence Forecasting Model
Uses a First-Order Markov transition matrix to predict the next likely alarm condition
per node based on historical sequences.
"""

import os
import json
import joblib
from collections import defaultdict, Counter

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
DATA_PATH  = os.path.join(os.path.dirname(__file__), "..", "alarms_scrubbed6.json")


def normalise_node(device: str) -> str:
    """Strip IP prefix e.g. '192.168.x.x:hostname' → 'hostname'."""
    if ":" in device:
        device = device.split(":")[-1]
    return device.strip()


class AlarmTransitionPredictor:
    def __init__(self):
        self.node_transitions = {}      # node -> last_condition -> next_condition -> prob
        self.global_transitions = {}    # last_condition -> next_condition -> prob
        self.node_most_frequent = {}    # node -> most common condition
        self.global_most_frequent = "LINK_DOWN"
        self._loaded = False

    def train(self, path: str):
        print(f"[transition] Training sequence forecasting model from {path}...")
        with open(path, "r") as f:
            raw = json.load(f)

        # Group alarms by node, sorted by timestamp
        node_sequences = defaultdict(list)
        for r in raw:
            dto = r.get("alarmsDTO", {})
            ts_str = dto.get("timeStamp", "")
            if not ts_str:
                continue
            node = normalise_node(dto.get("deviceName", "UNKNOWN"))
            cond = dto.get("condition", {}).get("value", "UNKNOWN")
            node_sequences[node].append((ts_str, cond))

        # Sort sequences for each node
        for node in node_sequences:
            node_sequences[node].sort(key=lambda x: x[0])

        node_raw_trans = defaultdict(lambda: defaultdict(Counter))
        global_raw_trans = defaultdict(Counter)
        node_cond_counts = defaultdict(Counter)
        global_cond_counts = Counter()

        for node, seq in node_sequences.items():
            conditions = [x[1] for x in seq]
            # Deduplicate consecutive identical alarm states to capture real cross-state transitions
            collapsed = []
            for c in conditions:
                if not collapsed or collapsed[-1] != c:
                    collapsed.append(c)
            conditions = collapsed

            for cond in conditions:
                node_cond_counts[node][cond] += 1
                global_cond_counts[cond] += 1

            for i in range(len(conditions) - 1):
                c_curr = conditions[i]
                c_next = conditions[i+1]
                node_raw_trans[node][c_curr][c_next] += 1
                global_raw_trans[c_curr][c_next] += 1

        # Convert node-specific transition counts to probabilities
        self.node_transitions = {}
        for node, curr_map in node_raw_trans.items():
            self.node_transitions[node] = {}
            for curr, next_counter in curr_map.items():
                total = sum(next_counter.values())
                self.node_transitions[node][curr] = {
                    nxt: count / total for nxt, count in next_counter.items()
                }

        # Convert global transition counts to probabilities
        self.global_transitions = {}
        for curr, next_counter in global_raw_trans.items():
            total = sum(next_counter.values())
            self.global_transitions[curr] = {
                nxt: count / total for nxt, count in next_counter.items()
            }

        # Compute fallback most frequent alarms
        self.node_most_frequent = {}
        for node, counter in node_cond_counts.items():
            if counter:
                self.node_most_frequent[node] = counter.most_common(1)[0][0]

        if global_cond_counts:
            self.global_most_frequent = global_cond_counts.most_common(1)[0][0]

        self._loaded = True
        print(f"[transition] Trained transitions for {len(self.node_transitions)} nodes.")

    def save(self):
        os.makedirs(MODELS_DIR, exist_ok=True)
        artifacts = {
            "node_transitions": self.node_transitions,
            "global_transitions": self.global_transitions,
            "node_most_frequent": self.node_most_frequent,
            "global_most_frequent": self.global_most_frequent
        }
        joblib.dump(artifacts, os.path.join(MODELS_DIR, "transition_matrix.pkl"))
        print(f"[transition] Saved sequence model artifacts to {MODELS_DIR}")

    def load(self):
        path = os.path.join(MODELS_DIR, "transition_matrix.pkl")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Sequence model not found at {path}")
        artifacts = joblib.load(path)
        self.node_transitions = artifacts["node_transitions"]
        self.global_transitions = artifacts["global_transitions"]
        self.node_most_frequent = artifacts["node_most_frequent"]
        self.global_most_frequent = artifacts["global_most_frequent"]
        self._loaded = True

    def predict_next_alarm(self, node: str, last_condition: str) -> tuple[str, float]:
        if not self._loaded:
            raise RuntimeError("Model is not loaded or trained.")

        # 1. Per-node transition lookup
        node_trans = self.node_transitions.get(node, {})
        if last_condition in node_trans:
            next_probs = node_trans[last_condition]
            if next_probs:
                best_nxt = max(next_probs, key=next_probs.get)
                return best_nxt, next_probs[best_nxt]

        # 2. Global transition fallback
        if last_condition in self.global_transitions:
            next_probs = self.global_transitions[last_condition]
            if next_probs:
                best_nxt = max(next_probs, key=next_probs.get)
                return best_nxt, next_probs[best_nxt]

        # 3. Frequency-based fallback
        fallback_cond = self.node_most_frequent.get(node, self.global_most_frequent)
        return fallback_cond, 0.05

    def predict_sequence_stages(self, node: str, last_condition: str, steps: int = 3) -> list[dict]:
        """
        Predict a cascading path of future transitions (stages) from the Markov model,
        expanded to include statistically significant alternative transitions (prob >= 2%)
        to prevent false positive mismatches on correlated protocols or hardware events.
        Returns a list of dicts: [{"condition": str, "prob": float}]
        """
        if not self._loaded:
            return []
            
        stages = []
        
        # 1. Per-node transition lookup
        node_trans = self.node_transitions.get(node, {}).get(last_condition, {})
        # 2. Global transition fallback
        global_trans = self.global_transitions.get(last_condition, {})
        
        # Combine them, prioritizing node transitions
        trans_map = {}
        for cond, prob in global_trans.items():
            trans_map[cond] = prob
        for cond, prob in node_trans.items():
            trans_map[cond] = prob
            
        # Get all transitions with probability >= 0.02 (2%)
        significant_transitions = [
            {"condition": cond, "prob": float(prob)}
            for cond, prob in trans_map.items()
            if prob >= 0.02 and cond != "UNKNOWN" and cond != last_condition
        ]
        
        # Sort by probability descending
        significant_transitions.sort(key=lambda x: x["prob"], reverse=True)
        
        # Always build the primary cascading path first
        seen = {last_condition}
        curr = last_condition
        for _ in range(steps):
            nxt_cond, nxt_prob = self.predict_next_alarm(node, curr)
            if nxt_cond in seen or nxt_cond == "UNKNOWN" or nxt_prob < 0.01:
                break
            stages.append({"condition": nxt_cond, "prob": float(nxt_prob)})
            seen.add(nxt_cond)
            curr = nxt_cond
            
        # Append other significant alternative transitions to the stages list
        for t in significant_transitions:
            if t["condition"] not in seen:
                stages.append(t)
                seen.add(t["condition"])
                
        return stages


if __name__ == "__main__":
    predictor = AlarmTransitionPredictor()
    predictor.train(DATA_PATH)
    predictor.save()
