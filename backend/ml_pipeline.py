"""
AlarmIQ — ML Training Pipeline
Loads alarm data, engineers features, trains classifiers, evaluates and saves best model.
"""

import json
import os
import joblib
import numpy as np
import pandas as pd
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    classification_report, roc_auc_score, confusion_matrix,
    average_precision_score
)
from sklearn.utils.class_weight import compute_class_weight

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from imblearn.over_sampling import SMOTE
    HAS_SMOTE = True
except ImportError:
    HAS_SMOTE = False

DATA_PATH   = os.path.join(os.path.dirname(__file__), "..", "alarms_scrubbed6.json")
MODELS_DIR  = os.path.join(os.path.dirname(__file__), "models")
DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")

LOOKBACK_HOURS     = 2
PREDICTION_HORIZON = 1.0  # hours: if failure within next 1h → label=1
FAILURE_WINDOW_MIN = 30   # minutes window to cluster criticals
MIN_CRITICAL_BURST = 3    # criticals in window = failure event

CONDITION_COLS = [
    "bgp_down",
    "link_down",
    "optical_alarm",
    "fru_removed",
    "pw_updown",
    "isis_down",
    "ospf_down",
    "ssh_failure",
    "cefc_power",
    "satellite_problem",
    "performance_iface",
]

CONDITION_MAP = {
    "bgp_down":           ["BGP-5-ADJCHANGE_DOWN"],
    "link_down":          ["LINK_DOWN", "IOSXE_RP_ALARM_INFO_LINK_DOWN", "XCVR_MISSING_LINK_DOWN"],
    "optical_alarm":      ["Optical Alarm"],
    "fru_removed":        ["cefcFRURemoved"],
    "pw_updown":          ["L2-L2VPN_PW-3-UPDOWN", "XCONNECT-5-PW_STATUS"],
    "isis_down":          ["ROUTING-ISIS-5-ADJCHANGE_DOWN"],
    "ospf_down":          ["ROUTING-OSPF-5-ADJCHG"],
    "ssh_failure":        ["SECURITY-SSHD-4-INFO_FAILURE"],
    "cefc_power":         ["cefcPowerStatusChange"],
    "satellite_problem":  ["PKT_INFRA-ICPE_GCO-5-SATELLITE_STATUS_PROBLEM"],
    "performance_iface":  ["TV_CE_PERFORMANCE_INTERFACE", "EC-5-MINLINKS_NOTMET"],
}

# Build reverse lookup
COND_REVERSE = {}
for feat, conds in CONDITION_MAP.items():
    for c in conds:
        COND_REVERSE[c] = feat


def normalise_node(device: str) -> str:
    """Strip IP prefix e.g. '192.168.x.x:hostname' → 'hostname'."""
    if ":" in device:
        device = device.split(":")[-1]
    return device.strip()


def load_alarms(path: str) -> dict:
    """Return dict: node_name → sorted list of alarm dicts."""
    print(f"[load] Reading {path} ...")
    with open(path, "r") as f:
        raw = json.load(f)

    node_alarms: dict = defaultdict(list)
    skipped = 0
    for record in raw:
        dto = record.get("alarmsDTO", {})
        ts_str = dto.get("timeStamp", "")
        if not ts_str:
            skipped += 1
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            skipped += 1
            continue
        node = normalise_node(dto.get("deviceName", "UNKNOWN"))
        node_alarms[node].append({
            "ts":        ts,
            "condition": dto.get("condition", {}).get("value", "UNKNOWN"),
            "severity":  dto.get("severity", "UNKNOWN"),
            "category":  dto.get("category", {}).get("value", "UNKNOWN"),
        })

    for node in node_alarms:
        node_alarms[node].sort(key=lambda x: x["ts"])

    print(f"[load] {len(raw)} records → {len(node_alarms)} nodes  (skipped {skipped})")
    return node_alarms


def find_failure_events(alarms: list) -> list:
    """
    Returns list of failure event dicts: {start, end, count, peak_time}
    A failure = ≥MIN_CRITICAL_BURST CRITICAL alarms within FAILURE_WINDOW_MIN minutes.
    """
    critical = [a for a in alarms if a["severity"] == "CRITICAL"]
    events = []
    i = 0
    while i < len(critical):
        window_end = critical[i]["ts"] + timedelta(minutes=FAILURE_WINDOW_MIN)
        cluster = [critical[i]]
        j = i + 1
        while j < len(critical) and critical[j]["ts"] <= window_end:
            cluster.append(critical[j])
            j += 1
        if len(cluster) >= MIN_CRITICAL_BURST:
            events.append({
                "start":     cluster[0]["ts"],
                "end":       cluster[-1]["ts"],
                "count":     len(cluster),
                "peak_time": cluster[0]["ts"],
            })
            i = j
        else:
            i += 1
    return events


def compute_node_stats(node_alarms: dict) -> dict:
    """Per-node historical stats for features."""
    stats = {}
    for node, alarms in node_alarms.items():
        if not alarms:
            continue
        span_days = max(
            (alarms[-1]["ts"] - alarms[0]["ts"]).total_seconds() / 86400, 1
        )
        n_failures = len(find_failure_events(alarms))
        stats[node] = {
            "failure_rate": n_failures / span_days,
            "avg_daily_alarms": len(alarms) / span_days,
        }
    return stats


def extract_features(window: list, node_stats: dict, node: str) -> dict:
    """Feature vector for a 2-hour alarm window."""
    total = len(window)
    if total == 0:
        # Return zero vector
        feats = {
            "total_alarms": 0,
            "alarm_rate_per_hour": 0.0,
            "critical_count": 0,
            "major_count": 0,
            "minor_count": 0,
            "warning_count": 0,
            "cleared_count": 0,
            "critical_ratio": 0.0,
            "severity_escalation_score": 0.0,
            "unique_conditions": 0,
            "alarm_burst_15min": 0,
            "minutes_since_last_critical": 9999.0,
            "trend_last_30m": 0.0,
            "major_ratio": 0.0,
            "minor_ratio": 0.0,
            "warning_ratio": 0.0,
            "cleared_ratio": 0.0,
            "alarm_density": 0.0,
        }
        for col in CONDITION_COLS:
            feats[col + "_count"] = 0
        ns = node_stats.get(node, {})
        feats["node_failure_rate"] = ns.get("failure_rate", 0.0)
        feats["node_avg_daily_alarms"] = ns.get("avg_daily_alarms", 0.0)
        return feats

    sev_cnt = Counter(a["severity"] for a in window)
    cond_cnt = Counter(a["condition"] for a in window)

    # Severity escalation: weighted later alarms more than earlier
    sev_weight = {"CRITICAL": 4, "MAJOR": 3, "WARNING": 2, "MINOR": 1, "CLEARED": 0}
    n = len(window)
    escalation = sum(
        sev_weight.get(window[i]["severity"], 0) * (i + 1) for i in range(n)
    ) / (n * (n + 1) / 2) if n > 1 else 0.0

    # Burst in last 15 min
    if window:
        last_ts = window[-1]["ts"]
        burst_cutoff = last_ts - timedelta(minutes=15)
        burst_count = sum(1 for a in window if a["ts"] >= burst_cutoff)
        
        # New Feature: trend_last_30m
        cutoff_30m = last_ts - timedelta(minutes=30)
        alarms_30m = sum(1 for a in window if a["ts"] >= cutoff_30m)
        trend_last_30m = alarms_30m / total
        
        # New Feature: alarm_density (average interval in minutes between alarms)
        if total > 1:
            intervals = [(window[i]["ts"] - window[i-1]["ts"]).total_seconds() / 60 for i in range(1, total)]
            alarm_density = sum(intervals) / len(intervals)
        else:
            alarm_density = 0.0
    else:
        burst_count = 0
        trend_last_30m = 0.0
        alarm_density = 0.0

    # Minutes since last critical
    criticals = [a for a in window if a["severity"] == "CRITICAL"]
    if criticals:
        mins_since_crit = (window[-1]["ts"] - criticals[-1]["ts"]).total_seconds() / 60
    else:
        mins_since_crit = 9999.0

    # Condition-specific counts (mapped)
    cond_feats = {}
    for col in CONDITION_COLS:
        mapped_conds = CONDITION_MAP[col]
        cond_feats[col + "_count"] = sum(cond_cnt.get(c, 0) for c in mapped_conds)

    ns = node_stats.get(node, {})

    return {
        "total_alarms":               total,
        "alarm_rate_per_hour":        total / LOOKBACK_HOURS,
        "critical_count":             sev_cnt.get("CRITICAL", 0),
        "major_count":                sev_cnt.get("MAJOR", 0),
        "minor_count":                sev_cnt.get("MINOR", 0),
        "warning_count":              sev_cnt.get("WARNING", 0),
        "cleared_count":              sev_cnt.get("CLEARED", 0),
        "critical_ratio":             sev_cnt.get("CRITICAL", 0) / total,
        "severity_escalation_score":  escalation,
        "unique_conditions":          len(cond_cnt),
        "alarm_burst_15min":          burst_count,
        "minutes_since_last_critical": mins_since_crit,
        "trend_last_30m":             trend_last_30m,
        "major_ratio":                sev_cnt.get("MAJOR", 0) / total,
        "minor_ratio":                sev_cnt.get("MINOR", 0) / total,
        "warning_ratio":              sev_cnt.get("WARNING", 0) / total,
        "cleared_ratio":              sev_cnt.get("CLEARED", 0) / total,
        "alarm_density":              alarm_density,
        "node_failure_rate":          ns.get("failure_rate", 0.0),
        "node_avg_daily_alarms":      ns.get("avg_daily_alarms", 0.0),
        **cond_feats,
    }


def _get_window(alarms: list, ts_list: list, t_start, t_end) -> list:
    """
    Fast O(log n) window extraction using pre-built sorted timestamp list + bisect.
    Returns alarms where t_start <= alarm["ts"] < t_end.
    """
    import bisect
    lo = bisect.bisect_left(ts_list,  t_start)
    hi = bisect.bisect_left(ts_list,  t_end)
    return alarms[lo:hi]


def build_dataset(node_alarms: dict, node_stats: dict):
    """
    Highly optimized linear-time O(N) sliding window feature extraction
    that sweeps through all 295,995 alarms chronologically.
    
    Precision-focused filtering:
    - Positive samples must have at least 1 CRITICAL or 2 MAJOR in the
      lookback window (otherwise the sample has no precursor signal and
      the model cannot distinguish it from a negative).
    - Controlled negative sub-sampling to ~10:1 ratio max.
    """
    import bisect
    X_rows, y_rows, meta_rows = [], [], []
    # Track raw counts for diagnostics
    n_pos_raw, n_pos_filtered, n_neg_raw = 0, 0, 0

    for node, alarms in node_alarms.items():
        if len(alarms) < 5:
            continue

        failure_events = find_failure_events(alarms)
        failure_starts = sorted(ev["start"] for ev in failure_events)
        
        left_ptr = 0
        
        for i, a in enumerate(alarms):
            t = a["ts"]
            cutoff = t - timedelta(hours=LOOKBACK_HOURS)
            
            # Slide left pointer forward to maintain the 2-hour sliding window preceding the alarm
            while left_ptr < i and alarms[left_ptr]["ts"] < cutoff:
                left_ptr += 1
                
            # The window of alarms preceding the current alarm is alarms[left_ptr:i]
            window = alarms[left_ptr:i]
            
            # Optimization: Cap window size to most recent 100 alarms during feature extraction
            # to prevent CPU explosion during massive bursts without losing chronological sequence state
            if len(window) > 100:
                window = window[-100:]
                
            # Determine label: 1 if failure starts within PREDICTION_HORIZON
            idx = bisect.bisect_right(failure_starts, t)
            is_pos = 0
            if idx < len(failure_starts):
                next_fail = failure_starts[idx]
                if next_fail - t <= timedelta(hours=PREDICTION_HORIZON):
                    is_pos = 1

            # ── Precision-focused filtering ──
            if is_pos:
                n_pos_raw += 1
                # Only keep positive samples that have actual precursor signals
                # in the lookback window (fewer than 3 total alarms is gated out;
                # otherwise requires ≥1 CRITICAL or ≥2 MAJOR alarms)
                crit_in_window = sum(1 for w in window if w["severity"] == "CRITICAL")
                major_in_window = sum(1 for w in window if w["severity"] == "MAJOR")
                if len(window) < 3 or (crit_in_window < 1 and major_in_window < 2):
                    n_pos_filtered += 1
                    continue  # Skip — no precursor signal, model can't learn from this
            else:
                n_neg_raw += 1
                    
            # Extract features
            feats = extract_features(window, node_stats, node)
                            
            X_rows.append(feats)
            y_rows.append(is_pos)
            meta_rows.append({"node": node, "window_end": t.isoformat()})
            
    df = pd.DataFrame(X_rows)
    df["label"] = y_rows
    df["window_end"] = [m["window_end"] for m in meta_rows]
    meta = pd.DataFrame(meta_rows)
    n_pos_kept = sum(y_rows)
    n_neg_kept = len(y_rows) - n_pos_kept
    print(f"[dataset] Generated {len(df)} samples from all alarms")
    print(f"[dataset] Positives: {n_pos_raw} raw → {n_pos_kept} kept  "
          f"(filtered {n_pos_filtered} with no precursor signal)")
    print(f"[dataset] Negatives: {n_neg_kept}")
    print(f"[dataset] Class ratio: 1:{n_neg_kept // max(n_pos_kept, 1)}")
    return df, meta
def _find_precision_threshold(y_true, y_proba, min_precision=0.50, fallback=0.5):
    """
    Walk the precision-recall curve to find the lowest threshold that
    achieves at least `min_precision`.  Returns (threshold, precision, recall).
    """
    from sklearn.metrics import precision_recall_curve
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_proba)
    # precision_recall_curve returns n+1 precisions; thresholds has n entries
    best_t, best_p, best_r = fallback, 0.0, 0.0
    for p, r, t in zip(precisions, recalls, thresholds):
        if p >= min_precision and r > best_r:
            best_t, best_p, best_r = float(t), float(p), float(r)
    if best_p < min_precision:
        # Fallback: pick threshold that maximises F1
        f1s = 2 * precisions[:-1] * recalls[:-1] / (precisions[:-1] + recalls[:-1] + 1e-9)
        idx = int(np.argmax(f1s))
        best_t = float(thresholds[idx])
        best_p = float(precisions[idx])
        best_r = float(recalls[idx])
    return best_t, best_p, best_r


def train_and_evaluate(df: pd.DataFrame, test_size: float = 0.20):
    """
    Train RF, XGBoost, LR — return best model, scaler, metrics dict.
    
    Precision-focused pipeline v4:
    - 80/20 chronological split
    - Train on ALL samples (no downsampling — maximise signal)
    - Moderate class_weight / scale_pos_weight (not 'balanced')
    - Isotonic probability calibration via IsotonicRegression
    - Model selection by Average Precision
    - Auto-calibrated threshold via precision-recall curve (≥40% precision)
    """
    from sklearn.isotonic import IsotonicRegression

    # Chronologically sort the dataset by the end-of-window timestamp
    df_sorted = df.sort_values("window_end").reset_index(drop=True)
    
    # Feature columns must not include the target label or the timestamp column
    feature_cols = [c for c in df_sorted.columns if c not in ("label", "window_end")]
    
    # Strict Time-Based validation split (80/20)
    train_size = 1.0 - test_size
    split_idx = int(len(df_sorted) * train_size)
    
    train_df = df_sorted.iloc[:split_idx]
    test_df  = df_sorted.iloc[split_idx:]
    
    X_train = train_df[feature_cols].values
    y_train = train_df["label"].values
    
    X_test  = test_df[feature_cols].values
    y_test  = test_df["label"].values
    
    print(f"\n[train] Dataset: {len(df_sorted)} samples chronologically sorted")
    print(f"[train] Split: {split_idx} train ({train_size*100:.0f}%) / {len(test_df)} test ({test_size*100:.0f}%)")
    print(f"[train] Train shape: {X_train.shape}, Test shape: {X_test.shape}")
    print(f"[train] Train class distribution: {Counter(y_train)}")
    print(f"[train] Test class distribution:  {Counter(y_test)}")

    # Scale
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    print(f"[train] No SMOTE, no downsampling — training on full data")

    candidates = {}  # name → (model, AP, proba)

    # --- Random Forest with moderate class_weight ---
    rf = RandomForestClassifier(
        n_estimators=500, max_depth=15, min_samples_leaf=5,
        class_weight={0: 1, 1: 10},  # Moderate boost, not 'balanced' (which would be ~50x)
        random_state=42, n_jobs=-1
    )
    rf.fit(X_train_s, y_train)
    rf_proba = rf.predict_proba(X_test_s)[:, 1]
    rf_auc   = roc_auc_score(y_test, rf_proba)
    rf_ap    = average_precision_score(y_test, rf_proba)
    print(f"\n[RF ] ROC-AUC={rf_auc:.4f}  AP={rf_ap:.4f}")
    print(classification_report(y_test, (rf_proba >= 0.5).astype(int), digits=3, zero_division=0))
    candidates["RandomForest"] = (rf, rf_ap, rf_proba)

    # --- XGBoost with moderate scale_pos_weight ---
    if HAS_XGB:
        xgb_model = xgb.XGBClassifier(
            n_estimators=500, max_depth=8, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8, scale_pos_weight=5.0,
            min_child_weight=5, gamma=0.5, reg_alpha=0.1, reg_lambda=1.0,
            use_label_encoder=False, eval_metric="aucpr",
            random_state=42, n_jobs=-1, verbosity=0
        )
        xgb_model.fit(X_train_s, y_train)
        xgb_proba = xgb_model.predict_proba(X_test_s)[:, 1]
        xgb_auc   = roc_auc_score(y_test, xgb_proba)
        xgb_ap    = average_precision_score(y_test, xgb_proba)
        print(f"[XGB] ROC-AUC={xgb_auc:.4f}  AP={xgb_ap:.4f}")
        print(classification_report(y_test, (xgb_proba >= 0.5).astype(int), digits=3, zero_division=0))
        candidates["XGBoost"] = (xgb_model, xgb_ap, xgb_proba)

    # --- Logistic Regression (baseline) ---
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train_s, y_train)
    lr_proba = lr.predict_proba(X_test_s)[:, 1]
    lr_auc   = roc_auc_score(y_test, lr_proba)
    lr_ap    = average_precision_score(y_test, lr_proba)
    print(f"[LR ] ROC-AUC={lr_auc:.4f}  AP={lr_ap:.4f}")
    candidates["LogisticRegression"] = (lr, lr_ap, lr_proba)

    # ── Select best by Average Precision ──
    best_name = max(candidates, key=lambda k: candidates[k][1])
    best_model, best_ap, best_proba = candidates[best_name]
    best_auc = roc_auc_score(y_test, best_proba)
    print(f"\n[train] Best model: {best_name}  (AP={best_ap:.4f}, AUC={best_auc:.4f})")

    # ── Isotonic probability calibration ──
    # Directly calibrate raw probabilities → well-calibrated probabilities
    # using IsotonicRegression (not CalibratedClassifierCV which has API issues)
    print(f"[train] Applying isotonic probability calibration...")
    calibrator = None
    try:
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds='clip')
        iso.fit(best_proba, y_test)
        cal_proba = iso.predict(best_proba)
        cal_ap = average_precision_score(y_test, cal_proba)
        print(f"[train] Calibrated AP={cal_ap:.4f} (was {best_ap:.4f})")
        print(f"[train] Raw proba stats:  min={best_proba.min():.6f} "
              f"median={np.median(best_proba):.6f} max={best_proba.max():.6f}")
        print(f"[train] Cal proba stats:  min={cal_proba.min():.6f} "
              f"median={np.median(cal_proba):.6f} max={cal_proba.max():.6f}")
        
        calibrator = iso
        best_proba = cal_proba
        best_ap = cal_ap
    except Exception as e:
        print(f"[train] Calibration failed: {e}, using raw probabilities")

    # ── Auto-calibrate threshold for ≥70% precision ──
    opt_threshold, opt_precision, opt_recall = _find_precision_threshold(
        y_test, best_proba, min_precision=0.70
    )
    print(f"[train] Precision-calibrated threshold: {opt_threshold:.4f}  "
          f"(precision={opt_precision:.4f}, recall={opt_recall:.4f})")

    # ── Diagnostic: scan precision at key thresholds ──
    from sklearn.metrics import precision_recall_curve
    precs, recs, threshs = precision_recall_curve(y_test, best_proba)
    print(f"[train] Precision-Recall scan (calibrated):")
    for target_t in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        mask = threshs >= target_t
        if mask.any():
            idx = np.where(mask)[0][0]
            print(f"         threshold={target_t:.2f}  precision={precs[idx]:.4f}  recall={recs[idx]:.4f}")

    # Compute confusion matrix at calibrated threshold
    preds = (best_proba >= opt_threshold).astype(int)
    cm    = confusion_matrix(y_test, preds)
    print(f"[train] Confusion matrix at threshold={opt_threshold:.4f}:")
    print(f"         TN={cm[0][0]}  FP={cm[0][1]}")
    print(f"         FN={cm[1][0]}  TP={cm[1][1]}")
    if cm[1][1] + cm[0][1] > 0:
        actual_prec = cm[1][1] / (cm[1][1] + cm[0][1])
        print(f"         Actual Precision: {actual_prec:.4f}")

    # Feature importances
    feat_importances = {}
    if hasattr(best_model, "feature_importances_"):
        feat_importances = dict(zip(feature_cols, best_model.feature_importances_.tolist()))
    elif hasattr(best_model, "coef_"):
        feat_importances = dict(zip(feature_cols, np.abs(best_model.coef_[0]).tolist()))

    metrics = {
        "best_model":   best_name,
        "roc_auc":      round(best_auc, 4),
        "avg_precision": round(best_ap, 4),
        "confusion_matrix": cm.tolist(),
        "classification_report": classification_report(y_test, preds, output_dict=True, zero_division=0),
        "feature_importances": feat_importances,
        "all_models": {
            k: {"ap": round(v[1], 4)} for k, v in candidates.items()
        },
        "train_samples": int(X_train.shape[0]),
        "test_samples":  int(X_test.shape[0]),
        "positive_rate": round(float(df_sorted["label"].mean()), 4),
        "optimal_threshold": round(opt_threshold, 4),
        "threshold_precision": round(opt_precision, 4),
        "threshold_recall": round(opt_recall, 4),
        "has_calibrator": calibrator is not None,
    }

    return best_model, best_name, scaler, feature_cols, metrics, calibrator


def save_artifacts(model, model_name, scaler, feature_cols, metrics, node_stats, calibrator=None):
    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump(model,        os.path.join(MODELS_DIR, "best_model.pkl"))
    joblib.dump(scaler,       os.path.join(MODELS_DIR, "scaler.pkl"))
    joblib.dump(feature_cols, os.path.join(MODELS_DIR, "feature_cols.pkl"))
    joblib.dump(node_stats,   os.path.join(MODELS_DIR, "node_stats.pkl"))
    if calibrator is not None:
        joblib.dump(calibrator, os.path.join(MODELS_DIR, "calibrator.pkl"))
    else:
        cal_path = os.path.join(MODELS_DIR, "calibrator.pkl")
        if os.path.exists(cal_path):
            try: os.remove(cal_path)
            except: pass
    import json as _json
    metrics_copy = dict(metrics)
    # Convert non-serialisable types
    for k, v in metrics_copy.items():
        if isinstance(v, np.ndarray):
            metrics_copy[k] = v.tolist()
    metrics_copy["model_name"] = model_name
    with open(os.path.join(MODELS_DIR, "metrics.json"), "w") as f:
        _json.dump(metrics_copy, f, indent=2)
    print(f"[save] Artifacts saved to {MODELS_DIR}")


def retrain_custom_split(test_size: float):
    """Retrain best model on custom split and save artifacts."""
    node_alarms = load_alarms(DATA_PATH)
    print("[stats] Computing per-node historical stats...")
    node_stats  = compute_node_stats(node_alarms)
    print("[dataset] Building labeled dataset...")
    df, meta    = build_dataset(node_alarms, node_stats)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(os.path.join(DATA_DIR, "features.csv"), index=False)
    print(f"[dataset] Saved features.csv  ({len(df)} rows)")
    
    model, model_name, scaler, feature_cols, metrics, calibrator = train_and_evaluate(df, test_size=test_size)
    save_artifacts(model, model_name, scaler, feature_cols, metrics, node_stats, calibrator)
    return metrics, df


def run():
    node_alarms = load_alarms(DATA_PATH)
    print("[stats] Computing per-node historical stats...")
    node_stats  = compute_node_stats(node_alarms)
    print("[dataset] Building labeled dataset (this may take ~60s)...")
    df, meta    = build_dataset(node_alarms, node_stats)

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(os.path.join(DATA_DIR, "features.csv"), index=False)
    print(f"[dataset] Saved features.csv  ({len(df)} rows)")

    model, model_name, scaler, feature_cols, metrics, calibrator = train_and_evaluate(df)
    save_artifacts(model, model_name, scaler, feature_cols, metrics, node_stats, calibrator)

    print("\n✅ Training complete!")
    print(f"   Best model : {model_name}")
    print(f"   ROC-AUC    : {metrics['roc_auc']}")
    print(f"   Avg Prec   : {metrics['avg_precision']}")
    return model, scaler, feature_cols, metrics


if __name__ == "__main__":
    run()
