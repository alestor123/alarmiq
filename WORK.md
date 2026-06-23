# AlarmIQ — Complete Technical & Scientific Documentation

> **A real-time predictive failure system for Cisco network infrastructure.**
> This document explains *every* algorithm, mathematical formula, scientific principle, and engineering decision in the project — from raw alarm ingestion to the final UI prediction card.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Data Source — Cisco DTO Alarm Schema](#2-data-source--cisco-dto-alarm-schema)
3. [Failure Event Definition — The Ground Truth Label](#3-failure-event-definition--the-ground-truth-label)
4. [Feature Engineering — The 31-Dimensional Feature Vector](#4-feature-engineering--the-31-dimensional-feature-vector)
5. [Dataset Construction — Sliding Window Labelling](#5-dataset-construction--sliding-window-labelling)
6. [Model Training Pipeline — Multi-Model Tournament](#6-model-training-pipeline--multi-model-tournament)
7. [XGBoost — Gradient Boosted Trees (The Winning Model)](#7-xgboost--gradient-boosted-trees-the-winning-model)
8. [Probability Calibration — Isotonic Regression](#8-probability-calibration--isotonic-regression)
9. [Threshold Optimisation — Precision-Recall Curve Walk](#9-threshold-optimisation--precision-recall-curve-walk)
10. [Evaluation Metrics — AUC, AP, Precision, Recall, Confusion Matrix](#10-evaluation-metrics--auc-ap-precision-recall-confusion-matrix)
11. [Real-Time Predictor — Sliding Window Scoring Engine](#11-real-time-predictor--sliding-window-scoring-engine)
12. [Precursor Signal Gate — False Positive Elimination](#12-precursor-signal-gate--false-positive-elimination)
13. [Risk Level Calibration — Dynamic Threshold Mapping](#13-risk-level-calibration--dynamic-threshold-mapping)
14. [Time-to-Failure Estimation — Physics-Inspired Decay Model](#14-time-to-failure-estimation--physics-inspired-decay-model)
15. [Alarm Sequence Forecasting — First-Order Markov Chain](#15-alarm-sequence-forecasting--first-order-markov-chain)
16. [Network Topology Correlation — Graph-Based Root Cause Analysis](#16-network-topology-correlation--graph-based-root-cause-analysis)
17. [Prediction Verification Ledger — TP/FP Classification](#17-prediction-verification-ledger--tpfp-classification)
18. [Feature Importance & Contribution Scoring](#18-feature-importance--contribution-scoring)
19. [Simulation Engine — Time-Compressed Replay](#19-simulation-engine--time-compressed-replay)
20. [Live Streaming Pipeline — Kafka Integration](#20-live-streaming-pipeline--kafka-integration)
21. [Train/Test Split — Chronological Partitioning](#21-traintests-split--chronological-partitioning)
22. [Current Model Performance](#22-current-model-performance)
23. [File Reference Map](#23-file-reference-map)

---

## 1. System Architecture Overview

```
┌──────────────────────┐       ┌────────────────────────┐       ┌─────────────────────────┐
│  Cisco EPNM / NMS    │       │  Kafka (Optional)      │       │  Static JSON Dataset    │
│  alarms_scrubbed6.json│──────▶│  topic: telecom-alarms │──────▶│  295,995 raw alarms     │
└──────────────────────┘       └────────────────────────┘       └───────────┬─────────────┘
                                                                            │
                                                                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                              main.py  (FastAPI + Uvicorn)                                 │
│                                                                                          │
│  ┌─────────────────────┐    ┌──────────────────────┐    ┌──────────────────────────────┐ │
│  │ _simulation_thread  │───▶│  predictor.py         │───▶│  transition_model.py         │ │
│  │  (Background CPU)   │    │  NodeRiskPredictor    │    │  AlarmTransitionPredictor    │ │
│  │                     │    │  - Sliding windows    │    │  - Markov chain              │ │
│  │  296K alarms        │    │  - XGBoost scoring    │    │  - Sequence forecast         │ │
│  │  chronological      │    │  - Calibration        │    │  - Per-node + global         │ │
│  │  replay             │    │  - TTF estimation     │    └──────────────────────────────┘ │
│  └─────────────────────┘    │  - Topology corr.     │                                    │
│           │                 │  - Verification ledger │                                    │
│           ▼                 └──────────────────────┘                                      │
│  ┌─────────────────────┐           │                                                     │
│  │ asyncio.Queue       │◀──────────┘                                                     │
│  │ → WebSocket /ws     │                                                                 │
│  │ → REST /api/*       │──────────────▶  dashboard.html (Browser)                        │
│  └─────────────────────┘                                                                 │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Key components:**

| File | Role | Lines |
|------|------|-------|
| [ml_pipeline.py](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py) | Offline training: load data → engineer features → train models → save artifacts | 620 |
| [predictor.py](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py) | Real-time scoring: sliding windows → model inference → risk levels → verification | 568 |
| [transition_model.py](file:///home/alestor/PROJECTS/alarmiq/backend/transition_model.py) | Markov chain sequence forecaster: predict next alarm condition | 155 |
| [main.py](file:///home/alestor/PROJECTS/alarmiq/backend/main.py) | FastAPI server: REST endpoints, WebSocket broadcaster, simulation thread | 638 |
| [stream_simulator.py](file:///home/alestor/PROJECTS/alarmiq/backend/stream_simulator.py) | CLI replay tool with terminal-coloured output | 221 |
| [kafka_producer.py](file:///home/alestor/PROJECTS/alarmiq/backend/kafka_producer.py) | Publishes alarms to Kafka topic for live pipeline testing | 144 |
| [dashboard.html](file:///home/alestor/PROJECTS/alarmiq/backend/dashboard.html) | Single-page UI: real-time node table, risk warnings, verification ledger | 1447 |

---

## 2. Data Source — Cisco DTO Alarm Schema

Each alarm originates from a **Cisco Evolved Programmable Network Manager (EPNM)** via its REST API v4. The raw JSON structure:

```json
{
  "@dtoTyrouter": "alarmsDTO",
  "@tyrouter": "Alarms",
  "@url": "https://my-cisco.net/webacs/api/v4/data/Alarms/494701807",
  "alarmsDTO": {
    "@id": 494701807,
    "alarmId": 494701807,
    "acknowledgementStatus": false,
    "alarmFoundAt": "2024-01-03T08:04:50.881Z",
    "category": { "ordinal": 17, "value": "System" },
    "condition": { "ordinal": -1, "value": "INVENTORY_SYNC_SUPPRESSED" },
    "deviceName": "host-er2-router-02.my-cisco.net.in",
    "severity": "MINOR",
    "source": "NMS:bgl-epnm-01",
    "message": "Inventory sync suppressed due to continuous events..."
  }
}
```

**Key fields extracted for ML:**

| Field | Extracted As | Usage |
|-------|-------------|-------|
| `timeStamp` | `ts` (datetime) | Temporal ordering, window boundaries |
| `deviceName` | `node` (normalised hostname) | Per-node window tracking |
| `condition.value` | `condition` (string) | Alarm type classification, Markov transitions |
| `severity` | `severity` (CRITICAL/MAJOR/MINOR/WARNING/CLEARED) | Primary risk signal |
| `category.value` | `category` (string) | Alarm domain (System, Routing, Power, etc.) |

**Node normalisation** ([ml_pipeline.py:79-83](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L79-L83)):
```
"192.168.1.24:host-er2-router-02.my-cisco.net.in"  →  "host-er2-router-02.my-cisco.net.in"
```
Strips any IP prefix before the colon so that the same physical device always maps to one node identity.

**Dataset scale:** 295,995 raw alarms spanning **875 days** (2024-01-03 to 2026-05-27) across multiple Cisco routers.

---

## 3. Failure Event Definition — The Ground Truth Label

> **Source:** [ml_pipeline.py:120-145](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L120-L145) — `find_failure_events()`

A **failure event** is defined as:

> **≥ 3 CRITICAL-severity alarms within a 30-minute window on the same node.**

This is the domain-expert-driven definition. The rationale: a single CRITICAL alarm may be transient (a flap), but 3+ within 30 minutes indicates a **sustained network outage** — a router going down, a link permanently failing, or cascading protocol collapse.

### Algorithm — Greedy Clustering

```python
FAILURE_WINDOW_MIN = 30   # minutes
MIN_CRITICAL_BURST = 3    # minimum CRITICAL alarms to qualify
```

1. Filter all alarms for severity = `CRITICAL` (sorted by timestamp).
2. Starting at index `i`, define a window `[t_i, t_i + 30min]`.
3. Count all CRITICAL alarms `j > i` where `t_j ≤ t_i + 30min`.
4. If count ≥ 3 → record failure event `{start, end, count, peak_time}`.
5. Skip pointer `i` past the cluster end to avoid double-counting.
6. If count < 3 → advance `i` by one.

**Mathematically:**

```
For a sorted sequence of CRITICAL alarm timestamps C = [c₁, c₂, ..., cₖ]:

failure_event(i) = {
    cluster = {c_j : j ≥ i, c_j ≤ c_i + 30min}
    |cluster| ≥ 3
}
```

This produces a list of failure events per node with **start time**, **end time**, **count**, and **peak time** (the first alarm in the burst).

---

## 4. Feature Engineering — The 31-Dimensional Feature Vector

> **Source:** [ml_pipeline.py:165-266](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L165-L266) — `extract_features()`

For each alarm in the dataset, a 2-hour lookback window of preceding alarms is used to compute **31 features**. These features capture temporal patterns, severity distributions, alarm velocity, and node-specific historical context.

### 4.1 — Severity Count Features (5 features)

| Feature | Formula | Description |
|---------|---------|-------------|
| `critical_count` | `Σ 𝟙[severity = CRITICAL]` | Raw count of CRITICAL alarms in window |
| `major_count` | `Σ 𝟙[severity = MAJOR]` | Raw count of MAJOR alarms |
| `minor_count` | `Σ 𝟙[severity = MINOR]` | Raw count of MINOR alarms |
| `warning_count` | `Σ 𝟙[severity = WARNING]` | Raw count of WARNING alarms |
| `cleared_count` | `Σ 𝟙[severity = CLEARED]` | Raw count of CLEARED alarms |

### 4.2 — Severity Ratio Features (5 features)

Each ratio divides the severity count by `total_alarms`:

```
critical_ratio = critical_count / total_alarms
major_ratio    = major_count / total_alarms
minor_ratio    = minor_count / total_alarms
warning_ratio  = warning_count / total_alarms
cleared_ratio  = cleared_count / total_alarms
```

These normalised features are crucial because a window with 50 alarms where 5 are CRITICAL (10%) is more dangerous than a window with 200 alarms where 5 are CRITICAL (2.5%).

### 4.3 — Temporal Features (5 features)

| Feature | Formula | Description |
|---------|---------|-------------|
| `total_alarms` | `len(window)` | Total alarm count in 2h window |
| `alarm_rate_per_hour` | `total_alarms / 2` | Alarms per hour (velocity) |
| `alarm_burst_15min` | `Σ 𝟙[t_alarm ≥ t_now - 15min]` | Short-term burst detector |
| `trend_last_30m` | `alarms_in_last_30min / total_alarms` | Acceleration — is activity *increasing*? |
| `minutes_since_last_critical` | `(t_now - max(t_crit)) / 60` | Recency of last CRITICAL alarm (9999 if none) |

### 4.4 — Severity Escalation Score (1 feature)

> **The most scientifically interesting feature.**

```python
sev_weight = {"CRITICAL": 4, "MAJOR": 3, "WARNING": 2, "MINOR": 1, "CLEARED": 0}

escalation = Σᵢ (sev_weight[alarm_i] × (i + 1)) / (n × (n + 1) / 2)
```

This is a **position-weighted severity average** that captures whether severity is *escalating* over time within the window.

**Mathematical derivation:**

Given `n` alarms ordered chronologically in the window, alarm at position `i` (0-indexed) receives positional weight `(i + 1)`. The denominator `n(n+1)/2` is the sum of the first `n` natural numbers (the **triangular number** T_n), which normalises the score.

```
         Σᵢ₌₀ⁿ⁻¹ [ W(severity_i) × (i + 1) ]
ESC = ─────────────────────────────────────────
                    n × (n + 1) / 2
```

Where `W` maps severity to weight:
- CRITICAL = 4, MAJOR = 3, WARNING = 2, MINOR = 1, CLEARED = 0

**Interpretation:**
- ESC ≈ 0 → all CLEARED alarms, or severity is flat/decreasing
- ESC ≈ 2 → mixed severity, roughly constant
- ESC ≈ 4 → CRITICAL alarms dominate the recent end of the window → **escalation detected**

**Why this works:** In real network failures, you typically see a pattern:
```
MINOR → MINOR → WARNING → MAJOR → CRITICAL → CRITICAL → CRITICAL
```
The escalation score naturally captures this "ramping up" pattern with higher weights for later (more recent) alarms.

### 4.5 — Alarm Density (1 feature)

```python
alarm_density = mean( [t_{i} - t_{i-1}  for i in 1..n] )   # in minutes
```

Average inter-alarm interval in minutes. **Low density = rapid-fire alarms = higher risk.** A healthy node might have 1 alarm every 30 minutes; a node about to fail might have 1 alarm every 10 seconds.

### 4.6 — Unique Conditions (1 feature)

```python
unique_conditions = |{condition₁, condition₂, ..., conditionₙ}|
```

The number of distinct alarm condition types in the window. High diversity suggests **multiple subsystems failing simultaneously** (e.g., BGP + OSPF + Link — cascading failure).

### 4.7 — Condition-Specific Count Features (11 features)

Each maps raw Cisco condition strings to a named network failure domain:

| Feature | Cisco Conditions Mapped | Network Meaning |
|---------|------------------------|-----------------|
| `bgp_down_count` | `BGP-5-ADJCHANGE_DOWN` | BGP peer session dropped |
| `link_down_count` | `LINK_DOWN`, `IOSXE_RP_ALARM_INFO_LINK_DOWN`, `XCVR_MISSING_LINK_DOWN` | Physical/logical interface failure |
| `optical_alarm_count` | `Optical Alarm` | Fiber optic signal degradation |
| `fru_removed_count` | `cefcFRURemoved` | Hardware module physically removed |
| `pw_updown_count` | `L2-L2VPN_PW-3-UPDOWN`, `XCONNECT-5-PW_STATUS` | Pseudowire / L2VPN circuit flapping |
| `isis_down_count` | `ROUTING-ISIS-5-ADJCHANGE_DOWN` | IS-IS routing adjacency lost |
| `ospf_down_count` | `ROUTING-OSPF-5-ADJCHG` | OSPF neighbor relationship changed |
| `ssh_failure_count` | `SECURITY-SSHD-4-INFO_FAILURE` | Authentication/management failure |
| `cefc_power_count` | `cefcPowerStatusChange` | Power supply status change |
| `satellite_problem_count` | `PKT_INFRA-ICPE_GCO-5-SATELLITE_STATUS_PROBLEM` | nV satellite chassis problem |
| `performance_iface_count` | `TV_CE_PERFORMANCE_INTERFACE`, `EC-5-MINLINKS_NOTMET` | Interface performance degradation |

> **Source:** [ml_pipeline.py:44-70](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L44-L70) — `CONDITION_MAP`

### 4.8 — Node Historical Features (2 features)

Computed once per node from the *entire* alarm history ([ml_pipeline.py:148-162](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L148-L162)):

| Feature | Formula | Description |
|---------|---------|-------------|
| `node_failure_rate` | `failure_events / span_days` | Historical failures per day |
| `node_avg_daily_alarms` | `total_alarms / span_days` | Baseline alarm volume |

These provide **prior probability context**: a node with a history of 0.06 failures/day is more likely to fail again than one that has never failed.

---

## 5. Dataset Construction — Sliding Window Labelling

> **Source:** [ml_pipeline.py:280-360](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L280-L360) — `build_dataset()`

### Algorithm — O(N) Linear-Time Sliding Window

The dataset is built in a **single pass** through all alarms per node using a two-pointer sliding window:

```
For each node with sorted alarms a₁, a₂, ..., aₙ:

    left_ptr = 0

    For i = 0 to n-1:
        t = aᵢ.ts
        cutoff = t - 2 hours

        // Slide left pointer to maintain [cutoff, t) window
        while left_ptr < i and a[left_ptr].ts < cutoff:
            left_ptr++

        window = alarms[left_ptr : i]    // Alarms PRECEDING current alarm
        features = extract_features(window)

        // Label: Is there a failure starting within the next 1 hour?
        label = 1 if (∃ failure event f : f.start - t ≤ 1 hour) else 0
```

### Label Assignment Using Binary Search

```python
failure_starts = sorted([ev["start"] for ev in failure_events])

# bisect_right gives the index of the first failure AFTER time t
idx = bisect.bisect_right(failure_starts, t)
if idx < len(failure_starts):
    next_fail = failure_starts[idx]
    if next_fail - t <= timedelta(hours=1):  # PREDICTION_HORIZON
        is_pos = 1
```

**Time complexity:** O(log F) per alarm, where F = number of failure events.

### Precision-Focused Filtering

**Problem:** Many alarms labelled positive have *no precursor signal* in the window — they happen to be 59 minutes before a failure but the window is full of MINOR/CLEARED alarms. The model can't learn anything useful from these samples — they just add noise and cause false positives.

**Solution** ([ml_pipeline.py:329-338](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L329-L338)):

```python
# Only keep positive samples with actual precursor signals:
# ≥1 CRITICAL or ≥1 MAJOR alarm in the lookback window
if is_pos:
    crit_in_window = count(severity == CRITICAL in window)
    major_in_window = count(severity == MAJOR in window)
    if crit_in_window < 1 and major_in_window < 1:
        continue  # Skip — no learnable signal
```

This dramatically improves precision by ensuring every positive training sample has observable warning signs that the model can actually detect at inference time.

### Final Dataset Scale

| Metric | Value |
|--------|-------|
| Total samples | 292,087 |
| Positive samples (label=1) | ~2% |
| Negative samples (label=0) | ~98% |
| Feature dimensions | 31 |
| Class imbalance ratio | ~1:50 |

---

## 6. Model Training Pipeline — Multi-Model Tournament

> **Source:** [ml_pipeline.py:383-552](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L383-L552) — `train_and_evaluate()`

Three models are trained and evaluated in a tournament:

### 6.1 — Random Forest Classifier

```python
RandomForestClassifier(
    n_estimators=500,        # 500 decision trees
    max_depth=15,            # Max tree depth (prevents overfitting)
    min_samples_leaf=5,      # Minimum samples at leaf (regularisation)
    class_weight={0: 1, 1: 10},  # 10× boost for positive class
    random_state=42,
    n_jobs=-1                # Parallel training across all CPU cores
)
```

**The science:** Random Forest is an **ensemble method** that builds `B` independent decision trees on bootstrapped samples of the data (bagging). Each tree sees a random subset of features at each split. Predictions are averaged:

```
ŷ = (1/B) × Σᵦ₌₁ᴮ  tree_b(x)
```

**Class weight {0:1, 1:10}** means the loss function penalises misclassifying a positive sample 10× more than a negative. This is a *moderate* boost — `'balanced'` mode would use ~50× given the 2% positive rate, which causes excessive false positives.

### 6.2 — XGBoost (Gradient Boosted Trees)

```python
XGBClassifier(
    n_estimators=500,        # 500 sequential boosting rounds
    max_depth=8,             # Deeper than RF to capture complex interactions
    learning_rate=0.03,      # Conservative step size (η)
    subsample=0.8,           # 80% row sampling per tree (stochastic gradient boosting)
    colsample_bytree=0.8,    # 80% feature sampling per tree
    scale_pos_weight=10.0,   # Equivalent to class_weight for positive class
    min_child_weight=5,      # Minimum sum of instance weight in a child
    gamma=0.5,               # Minimum loss reduction for a split (regularisation)
    reg_alpha=0.1,           # L1 regularisation (lasso)
    reg_lambda=1.0,          # L2 regularisation (ridge)
    eval_metric="aucpr",     # Optimise for area under PR curve
)
```

See [Section 7](#7-xgboost--gradient-boosted-trees-the-winning-model) for full mathematical detail.

### 6.3 — Logistic Regression (Baseline)

```python
LogisticRegression(max_iter=1000, random_state=42)
```

Simple linear model. Serves as a sanity-check baseline — if a linear model achieves similar AP, the problem doesn't need a complex model.

### Model Selection Criterion

```python
best_name = max(candidates, key=lambda k: candidates[k][1])  # Select by AP
```

Models are ranked by **Average Precision (AP)** — not AUC — because AP is more sensitive to precision-recall tradeoffs on imbalanced datasets.

---

## 7. XGBoost — Gradient Boosted Trees (The Winning Model)

### 7.1 — The Core Algorithm

XGBoost implements **gradient boosted decision trees** — an ensemble where each tree corrects the errors of all previous trees:

```
ŷ⁽⁰⁾ = 0                                          (initial prediction)
ŷ⁽¹⁾ = ŷ⁽⁰⁾ + η × f₁(x)                           (add first tree)
ŷ⁽²⁾ = ŷ⁽¹⁾ + η × f₂(x)                           (add second tree)
  ...
ŷ⁽ᵀ⁾ = Σₜ₌₁ᵀ  η × fₜ(x)                           (final: sum of T trees)
```

Where:
- `fₜ(x)` is the t-th regression tree
- `η = 0.03` is the learning rate (shrinkage factor)
- `T = 500` is the number of boosting rounds

### 7.2 — Objective Function

For binary classification, XGBoost minimises the **regularised log-loss**:

```
Obj = Σᵢ L(yᵢ, ŷᵢ) + Σₜ Ω(fₜ)
```

**Loss function** (binary cross-entropy / logistic loss):

```
L(y, ŷ) = -[ y × log(σ(ŷ)) + (1 - y) × log(1 - σ(ŷ)) ]
```

Where `σ(ŷ) = 1 / (1 + e⁻ŷ)` is the sigmoid function that maps raw scores to probabilities.

**Regularisation term** (tree complexity penalty):

```
Ω(f) = γ × T_leaves + (1/2) × λ × Σⱼ wⱼ²  + α × Σⱼ |wⱼ|
```

Where:
- `γ = 0.5` — minimum loss reduction required for a split (prunes weak splits)
- `λ = 1.0` (reg_lambda) — L2 regularisation on leaf weights
- `α = 0.1` (reg_alpha) — L1 regularisation on leaf weights (promotes sparsity)
- `T_leaves` — number of leaves in the tree
- `wⱼ` — weight (prediction value) of leaf j

### 7.3 — Tree Building with Second-Order Gradients

At each boosting round, XGBoost computes **first-order** (gradient) and **second-order** (Hessian) derivatives of the loss:

```
gᵢ = ∂L(yᵢ, ŷᵢ⁽ᵗ⁻¹⁾) / ∂ŷᵢ⁽ᵗ⁻¹⁾  =  σ(ŷᵢ) - yᵢ        (gradient)
hᵢ = ∂²L(yᵢ, ŷᵢ⁽ᵗ⁻¹⁾) / ∂ŷᵢ⁽ᵗ⁻¹⁾²  = σ(ŷᵢ) × (1 - σ(ŷᵢ))  (Hessian)
```

The optimal weight for a leaf node `j` containing instances `Iⱼ` is:

```
wⱼ* = - (Σᵢ∈Iⱼ gᵢ) / (Σᵢ∈Iⱼ hᵢ + λ)
```

The **split gain** for dividing a node into left (L) and right (R) children:

```
Gain = (1/2) × [ (Σ gₗ)² / (Σ hₗ + λ)  +  (Σ gᵣ)² / (Σ hᵣ + λ)  -  (Σ g)² / (Σ h + λ) ] - γ
```

A split is only made if `Gain > 0` (i.e., the improvement exceeds the `γ` penalty).

### 7.4 — Class Imbalance Handling

`scale_pos_weight = 10.0` multiplies the gradient `gᵢ` for positive samples by 10, effectively making each positive sample "count" 10× in the loss function. This is equivalent to:

```
Adjusted loss = -[ 10 × y × log(σ(ŷ))  +  (1 - y) × log(1 - σ(ŷ)) ]
```

### 7.5 — Stochastic Regularisation

- `subsample = 0.8` — each tree is trained on a random 80% of rows (prevents overfitting)
- `colsample_bytree = 0.8` — each tree sees a random 80% of features (adds diversity)

These combine to create a form of **stochastic gradient boosting** (Friedman, 2002), which has been shown to improve generalisation and reduce variance.

---

## 8. Probability Calibration — Isotonic Regression

> **Source:** [ml_pipeline.py:477-497](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L477-L497)

### The Problem

XGBoost's raw probability outputs (`predict_proba`) are not **well-calibrated** — a predicted 0.3 does not necessarily mean there's a 30% chance of failure. Tree-based models tend to produce probability estimates that are too extreme (close to 0 or 1).

### The Solution — Isotonic Regression

After training, the raw probabilities are passed through an **IsotonicRegression** calibrator:

```python
from sklearn.isotonic import IsotonicRegression
calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds='clip')
calibrator.fit(raw_probabilities, true_labels)
calibrated_prob = calibrator.predict(raw_probabilities)
```

**Mathematical definition:** Isotonic regression finds a monotonically non-decreasing step function `f` that minimises:

```
min_f  Σᵢ (yᵢ - f(p̂ᵢ))²   subject to   f(p̂ᵢ) ≤ f(p̂ⱼ)  whenever  p̂ᵢ ≤ p̂ⱼ
```

This is solved exactly by the **Pool Adjacent Violators Algorithm (PAVA)** in O(n) time.

**Why isotonic over Platt scaling?** Platt scaling assumes a sigmoidal relationship between raw scores and true probabilities. Isotonic regression is **non-parametric** — it makes no distributional assumptions and can model any monotonic mapping. This is important because XGBoost's probability distortions are typically non-sigmoidal.

### At Runtime

The calibrator is saved as `calibrator.pkl` and loaded by the predictor ([predictor.py:206-210](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L206-L210)):

```python
prob = model.predict_proba(x_scaled)[0][1]    # Raw probability
prob = calibrator.predict([prob])[0]           # Calibrated probability
```

---

## 9. Threshold Optimisation — Precision-Recall Curve Walk

> **Source:** [ml_pipeline.py:361-380](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L361-L380) — `_find_precision_threshold()`

### The Problem

In binary classification, you must choose a **decision threshold** `τ` such that `P(failure) ≥ τ → alarm`. Higher threshold = fewer false positives but more missed failures. Lower threshold = catches more failures but floods operators with false alarms.

### The Algorithm

1. Compute the full precision-recall curve from sklearn: `(precisions[], recalls[], thresholds[])`.
2. Walk the curve to find the **lowest threshold** that achieves `precision ≥ 40%` while maximising recall:

```python
for p, r, t in zip(precisions, recalls, thresholds):
    if p >= 0.40 and r > best_recall:
        best_threshold = t
```

3. **Fallback:** If no threshold achieves 40% precision, fall back to the threshold that maximises the **F1 score**:

```
F1 = 2 × P × R / (P + R)
```

### Current Operating Point

From [metrics.json](file:///home/alestor/PROJECTS/alarmiq/backend/models/metrics.json):

```
optimal_threshold = 0.1924
threshold_precision = 0.2759  (27.6%)
threshold_recall = 0.6511     (65.1%)
```

This means: when the model's calibrated probability ≥ 19.24%, it raises an alarm. At this threshold, about 27.6% of raised alarms are true failures, and 65.1% of all actual failures are detected.

---

## 10. Evaluation Metrics — AUC, AP, Precision, Recall, Confusion Matrix

### 10.1 — ROC-AUC (Area Under the Receiver Operating Characteristic Curve)

```
AUC = ∫₀¹ TPR(FPR) dFPR
```

- **TPR** (True Positive Rate / Recall) = TP / (TP + FN)
- **FPR** (False Positive Rate) = FP / (FP + TN)

**Interpretation:** The probability that a randomly chosen positive sample scores higher than a randomly chosen negative sample. AUC = 0.5 means random, AUC = 1.0 means perfect separation.

**Current value: AUC = 0.8743** — strong discriminative ability.

### 10.2 — Average Precision (AP)

```
AP = Σₖ (Rₖ - Rₖ₋₁) × Pₖ
```

This is the **area under the Precision-Recall curve**. Unlike AUC, AP is sensitive to performance on the *minority class* and is the preferred metric for highly imbalanced datasets.

**Current value: AP = 0.2729** — reflects the ~2% positive rate. A random classifier would achieve AP ≈ 0.02, so 0.27 represents a ~13.6× improvement over random.

### 10.3 — Confusion Matrix

From the saved metrics at `optimal_threshold = 0.1924`:

```
                  Predicted Negative    Predicted Positive
Actual Negative       46,453 (TN)          7,548 (FP)
Actual Positive        1,541 (FN)          2,876 (TP)
```

| Metric | Formula | Value |
|--------|---------|-------|
| **Precision** | TP / (TP + FP) | 2876 / (2876 + 7548) = **27.59%** |
| **Recall** | TP / (TP + FN) | 2876 / (2876 + 1541) = **65.11%** |
| **Specificity** | TN / (TN + FP) | 46453 / (46453 + 7548) = **86.02%** |
| **Accuracy** | (TP + TN) / Total | (46453 + 2876) / 58418 = **84.44%** |

---

## 11. Real-Time Predictor — Sliding Window Scoring Engine

> **Source:** [predictor.py:47-255](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L47-L255) — `NodeRiskPredictor`

### Architecture

The predictor maintains **per-node state**:

```python
_windows: dict[str, deque]  # node → rolling alarm buffer (max 5000)
_scores:  dict[str, dict]   # node → latest risk score dictionary
```

### Scoring Flow

For each incoming alarm:

```
alarm → ingest_alarm()
         ├── Append to node's deque
         ├── score_node()
         │    ├── Filter window to [t - 2h, t]
         │    ├── Precursor Signal Gate check
         │    ├── extract_features(window)
         │    ├── scaler.transform(features)
         │    ├── model.predict_proba()
         │    ├── calibrator.predict()
         │    ├── _risk_level(probability)
         │    ├── _top_features()
         │    ├── TTF estimation
         │    ├── transition_predictor.predict_next_alarm()
         │    └── _topology_correlation()
         └── _verify_predictions()
```

### Feature Scaling

Before model inference, the raw 31-dimensional feature vector is scaled using a **StandardScaler** fitted during training:

```
x_scaled = (x - μ) / σ
```

Where `μ` and `σ` are the per-feature mean and standard deviation from the training set. This is critical because XGBoost with regularisation is sensitive to feature magnitudes.

---

## 12. Precursor Signal Gate — False Positive Elimination

> **Source:** [predictor.py:175-199](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L175-L199)

### The Insight

If a node's 2-hour window contains **zero CRITICAL and zero MAJOR** alarms, there is no observable precursor signal for an imminent failure. Running the model on such a window would produce small but non-zero probabilities that accumulate into false alarms.

### The Gate

```python
crit_in_window = sum(1 for w in window if w["severity"] == "CRITICAL")
major_in_window = sum(1 for w in window if w["severity"] == "MAJOR")

if crit_in_window == 0 and major_in_window == 0:
    return risk_score = 0.0, risk_level = "LOW"
```

**The model is never even called** — the risk score is hard-coded to 0.0. This is the single most effective false positive reduction technique in the system, and mirrors the training-time filtering (Section 5) to ensure train/inference consistency.

---

## 13. Risk Level Calibration — Dynamic Threshold Mapping

> **Source:** [predictor.py:100-110](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L100-L110)

Risk thresholds are **not hardcoded** — they are dynamically derived from the precision-optimised threshold found during training:

```python
opt_t = metrics["optimal_threshold"]     # e.g., 0.1924

RISK_THRESHOLDS = {
    "HIGH":     opt_t,                    # 0.16 (after rounding in practice)
    "CRITICAL": min(opt_t + 0.15, 0.95),  # 0.31
    "MEDIUM":   max(opt_t - 0.20, 0.15),  # 0.15
    "LOW":      0.0
}
```

**Mapping function:**

```
risk_level(p) = {
    CRITICAL   if p ≥ 0.31
    HIGH       if 0.16 ≤ p < 0.31
    MEDIUM     if 0.15 ≤ p < 0.16
    LOW        if p < 0.15
}
```

When the model is retrained (e.g., with a different train/test split), the threshold automatically recalibrates, and the risk levels shift accordingly.

---

## 14. Time-to-Failure Estimation — Physics-Inspired Decay Model

> **Source:** [predictor.py:216-232](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L216-L232)

### Formula

```python
base_ttf = 120.0 × (1.0 - probability)   # Linear inverse of risk

factor = 1.0
if alarm_rate > 10/hour:  factor *= 0.8   # High alarm velocity
if alarm_rate > 50/hour:  factor *= 0.6   # Extreme alarm velocity
if escalation > 2.0:     factor *= 0.7   # Severity is escalating

ttf_minutes = max(3, int(base_ttf × factor))
```

### Mathematical Interpretation

The base TTF follows an **inverse-linear** relationship with probability:

```
TTF_base = H × (1 - p)
```

Where `H = 120 minutes` is the prediction horizon. At `p = 1.0`, TTF = 0 (imminent). At `p = 0.5`, TTF = 60 minutes.

The acceleration factors model **alarm velocity** and **severity escalation** as multiplicative decay:

```
TTF = TTF_base × Π (acceleration factors)
```

For example, with `p = 0.5`, `alarm_rate = 60/hr`, `escalation = 2.5`:
```
TTF = 120 × 0.5 × 0.8 × 0.6 × 0.7 = 60 × 0.336 = 20.16 ≈ 20 minutes
```

This is analogous to a **damped exponential decay** model in physics, where the "half-life" of the system (time to failure) decreases when multiple stress factors are active simultaneously.

---

## 15. Alarm Sequence Forecasting — First-Order Markov Chain

> **Source:** [transition_model.py](file:///home/alestor/PROJECTS/alarmiq/backend/transition_model.py)

### The Model

A **first-order Markov chain** models the sequence of alarm conditions on each node. The assumption is that the next alarm condition depends **only on the current condition** (the Markov property).

### Training ([transition_model.py:31-103](file:///home/alestor/PROJECTS/alarmiq/backend/transition_model.py#L31-L103))

1. **Group** all alarms by node, sorted chronologically.
2. **Collapse** consecutive identical conditions (e.g., `LINK_DOWN, LINK_DOWN, LINK_DOWN` → `LINK_DOWN`). This captures genuine *transitions*, not repetitions.
3. **Count** transitions: for each consecutive pair `(conditionᵢ, conditionᵢ₊₁)`, increment `count[condᵢ][condᵢ₊₁]`.
4. **Normalise** to probabilities:

```
P(next = B | current = A) = count(A → B) / Σ_C count(A → C)
```

### Two-Level Lookup

The predictor uses a **hierarchical fallback** strategy:

1. **Node-specific transitions**: Does this particular router have transition data for the current condition?
2. **Global transitions**: Fall back to fleet-wide transition probabilities.
3. **Frequency fallback**: If the condition has never been seen, return the node's most common alarm condition with probability 0.05.

### Example

If `LINK_DOWN` has historically been followed by:
- `BGP-5-ADJCHANGE_DOWN` → 45% of the time
- `ROUTING-OSPF-5-ADJCHG` → 30% of the time
- `LINK_DOWN` → 25% of the time (self-loop after deduplication only if other conditions intervened)

The prediction would be: **next alarm = BGP-5-ADJCHANGE_DOWN with 45% confidence**.

### Persistence

The transition matrices are stored as `transition_matrix.pkl` (98 KB), containing:
- Per-node transition dictionaries
- Global transition dictionary
- Per-node most-frequent alarm condition
- Global most-frequent alarm condition

---

## 16. Network Topology Correlation — Graph-Based Root Cause Analysis

> **Source:** [predictor.py:332-362](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L332-L362)

### The Topology Graph

A hardcoded adjacency list models the physical network interconnections:

```python
TOPOLOGY = {
    "host-er2-router-03": ["host-er2-router-02", "host-er2-router-01", "demo-node-01"],
    "host-er2-router-02": ["host-er2-router-03", "host-er2-router-01"],
    "host-er2-router-01": ["host-er2-router-02", "host-er2-router-03", "host-pe1-router-01"],
    "host-pe1-router-01": ["host-er2-router-01", "host-pe1-router-02"],
    "host-pe1-router-02": ["host-pe1-router-01"],
    "demo-node-01":       ["host-er2-router-03"],
}
```

### Correlation Logic

For a node `N` with features `F`:

```
IF N has BGP_DOWN or LINK_DOWN alarms in its window:
    FOR each neighbor M of N:
        IF M's cached risk_level ∈ {HIGH, CRITICAL}:
            RETURN correlated = True, root_cause = M
RETURN correlated = False
```

**The science:** In IP/MPLS networks, when a core router fails, its neighbors will almost instantly lose BGP sessions and link adjacencies. This creates a **cascade** where multiple nodes alarm simultaneously, but only one is the *root cause*. By checking if a neighbor is already in a HIGH/CRITICAL state, the system can identify the originating failure and avoid blaming the victim nodes.

---

## 17. Prediction Verification Ledger — TP/FP Classification

> **Source:** [predictor.py:420-528](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L420-L528) — `_verify_predictions()`

### Lifecycle of a Prediction

Every time a node's risk score crosses the HIGH threshold, the system opens a **prediction record** and tracks it through resolution:

```
                    risk ≥ HIGH
                        │
                        ▼
              ┌───────────────────┐
              │  Pending ⏳        │
              │  (120-min horizon) │
              └─────────┬─────────┘
                        │
           ┌────────────┴────────────┐
           │                         │
    ≥3 CRITICAL in 30min      120 min elapsed
    (failure detected)         (no failure)
           │                         │
           ▼                         ▼
   ┌───────────────┐       ┌────────────────┐
   │ True Positive │       │ False Positive  │
   │      ✅       │       │       ❌        │
   └───────────────┘       └────────────────┘
```

### True Positive Detection

```python
def _detect_failure_now(self, node, ref_time):
    # Same definition as training: ≥3 CRITICAL in last 30 min
    cutoff = ref_time - timedelta(minutes=30)
    recent_crits = [a for a in window if a.severity == "CRITICAL" and a.ts >= cutoff]
    return len(recent_crits) >= 3
```

### False Positive Expiration

```python
HORIZON = timedelta(minutes=120)
if ts - predicted_at > HORIZON:
    # No failure occurred within the prediction window → false alarm
    rec["status"] = "False Positive ❌"
```

### Rich Snapshot

When a prediction is opened, a complete snapshot of the node's state is frozen:

- Risk score and level
- Alarm breakdown (critical/major/minor counts)
- Top-5 driving features
- Estimated TTF
- Predicted next alarm (from Markov chain)
- Topology correlation
- Raw Cisco DTO alarm payloads (`recent_alarms_raw`)

This allows post-hoc forensic analysis: engineers can inspect *exactly what the model saw* at prediction time.

### Aggregate Metrics

The verification ledger computes **live precision**:

```python
precision = true_positives / (true_positives + false_positives)
```

---

## 18. Feature Importance & Contribution Scoring

> **Source:** [predictor.py:258-283](file:///home/alestor/PROJECTS/alarmiq/backend/predictor.py#L258-L283)

### Model-Level Importance

XGBoost provides **gain-based feature importance**: the average reduction in the loss function when a feature is used for a split across all trees.

### Instance-Level Contribution

The system computes a per-prediction **contribution score** for each feature:

```python
contribution = feature_value × feature_importance
```

The top 5 features by contribution are displayed in the UI. This gives engineers actionable insight — e.g., "This prediction is driven by `minor_count = 47` (importance: 0.098) and `severity_escalation_score = 3.2` (importance: 0.108)."

### Current Top Features (from metrics.json)

| Rank | Feature | Importance | Meaning |
|------|---------|-----------|---------|
| 1 | `cleared_ratio` | 0.259 | Proportion of CLEARED alarms (counter-indicator) |
| 2 | `minor_ratio` | 0.119 | Proportion of MINOR alarms |
| 3 | `severity_escalation_score` | 0.108 | Are alarms getting worse over time? |
| 4 | `minor_count` | 0.098 | Raw count of MINOR alarms |
| 5 | `cleared_count` | 0.063 | Raw count of CLEARED alarms |

The dominance of `cleared_ratio` is significant: a high proportion of CLEARED alarms means the system is auto-recovering, which is a strong negative indicator for failure.

---

## 19. Simulation Engine — Time-Compressed Replay

> **Source:** [main.py:125-247](file:///home/alestor/PROJECTS/alarmiq/backend/main.py#L125-L247) — `_simulation_thread()`

### Architecture

The simulation runs in a **dedicated Python thread** (not an asyncio coroutine) because the model scoring is CPU-bound. Inter-thread communication uses `asyncio.run_coroutine_threadsafe()` to push payloads into an asyncio.Queue, which is drained by the `broadcaster()` coroutine and sent to all connected WebSocket clients.

```
Thread (CPU-bound)              │  Event Loop (I/O-bound)
─────────────────               │  ──────────────────────
_simulation_thread()            │  broadcaster()
  for alarm in alarms:          │    while True:
    predictor.ingest_alarm()    │      payload = await queue.get()
    queue.put(payload)  ──────▶ │      await ws.broadcast(payload)
    time.sleep(sleep_ms)        │
```

### Speed Modes

| Mode | Sleep/Alarm | Effective Rate | Broadcast Strategy |
|------|-------------|---------------|-------------------|
| Ultra Slow | 1000ms | 1/sec | Every alarm |
| Slow (default) | 150ms | ~6/sec | Every alarm |
| Medium | 50ms | ~20/sec | Every alarm |
| Fast | 5ms | ~200/sec | Every alarm |
| Hyper | 0ms | Max CPU | Every 10th alarm (progress), every 1000th (full update) |

---

## 20. Live Streaming Pipeline — Kafka Integration

> **Source:** [main.py:250-354](file:///home/alestor/PROJECTS/alarmiq/backend/main.py#L250-L354) — `kafka_consumer_task()` and [kafka_producer.py](file:///home/alestor/PROJECTS/alarmiq/backend/kafka_producer.py)

### Producer (kafka_producer.py)

Reads the historical JSON dataset and publishes alarms to `telecom-alarms` Kafka topic with configurable inter-message delay (`PRODUCER_SLEEP`, default 80ms).

### Consumer (main.py)

An `AIOKafkaConsumer` subscribes to `telecom-alarms` using async/await. Each message is:
1. Parsed from JSON
2. Timestamp-normalised
3. Ingested by the predictor
4. Broadcast to WebSocket clients

This enables a **production deployment path**: replace the static JSON file with a live Kafka feed from Cisco EPNM, and the system operates in real-time.

---

## 21. Train/Test Split — Chronological Partitioning

> **Source:** [ml_pipeline.py:396-414](file:///home/alestor/PROJECTS/alarmiq/backend/ml_pipeline.py#L396-L414) and [main.py:554-607](file:///home/alestor/PROJECTS/alarmiq/backend/main.py#L554-L607)

### Why Chronological (Not Random)?

Network alarm data is **time-series data**. A random train/test split would leak future information into the training set (future alarm patterns would inform the model about past predictions). This is called **temporal leakage** and produces artificially inflated metrics that don't reflect real-world performance.

### The Split

```python
df_sorted = df.sort_values("window_end")  # Sort by timestamp
split_idx = int(len(df_sorted) * (1.0 - test_size))

train = df_sorted.iloc[:split_idx]   # EARLIER data
test  = df_sorted.iloc[split_idx:]   # LATER data
```

```
Time ──────────────────────────────────────────────────────▶

|◄───────── Train (80%) ──────────▶|◄──── Test (20%) ─────▶|
2024-01-03                    ~mid-2025              2026-05-27
```

### Available Split Options (UI)

| Option | Train % | Test % | UI Label |
|--------|---------|--------|----------|
| 0.90 | 10% | 90% | Train on first 10% |
| 0.80 | 20% | 80% | Train on first 20% (default) |
| 0.70 | 30% | 70% | Train on first 30% |
| 0.60 | 40% | 60% | Train on first 40% |
| 0.50 | 50% | 50% | Train on first 50% |
| 0.20 | 80% | 20% | Train on first 80% |

### Retraining Flow

```
User selects split → clicks "Apply & Retrain"
    → POST /api/sim/repartition {test_size: 0.20}
        → ml_pipeline.retrain_custom_split(test_size=0.20)
            → load_alarms() → 295,995 alarms
            → compute_node_stats()
            → build_dataset() → ~292K samples
            → train_and_evaluate(df, test_size=0.20)
                → Train RF, XGBoost, LR on first 80%
                → Evaluate on last 20%
                → Isotonic calibration
                → Threshold optimisation
                → Save all artifacts
        → predictor.load() (hot-reload)
        → Clear windows, scores, ledger
        → Return split_timestamp to UI
            → UI resumes simulation from test partition start
```

---

## 22. Current Model Performance

From [metrics.json](file:///home/alestor/PROJECTS/alarmiq/backend/models/metrics.json):

| Metric | Value |
|--------|-------|
| **Best Model** | XGBoost |
| **ROC-AUC** | 0.8743 |
| **Average Precision** | 0.2729 |
| **Optimal Threshold** | 0.1924 |
| **Precision @ Threshold** | 27.59% |
| **Recall @ Threshold** | 65.11% |
| **Training Samples** | 233,669 |
| **Test Samples** | 58,418 |
| **Positive Rate** | 2% |
| **Has Calibrator** | Yes (Isotonic) |

**Model comparison:**

| Model | Average Precision |
|-------|------------------|
| XGBoost | **0.2609** (raw) → 0.2729 (calibrated) |
| Random Forest | 0.1936 |
| Logistic Regression | 0.0941 |

XGBoost achieves **1.35× better AP** than Random Forest and **2.9× better** than Logistic Regression, justifying the additional model complexity.

---

## 23. File Reference Map

| Artifact File | Path | Size |
|---------------|------|------|
| Trained XGBoost model | [best_model.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/best_model.pkl) | 1.6 MB |
| Isotonic calibrator | [calibrator.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/calibrator.pkl) | 907 B |
| Feature column names | [feature_cols.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/feature_cols.pkl) | 604 B |
| StandardScaler | [scaler.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/scaler.pkl) | 1.3 KB |
| Node historical stats | [node_stats.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/node_stats.pkl) | 13 KB |
| Markov transition matrix | [transition_matrix.pkl](file:///home/alestor/PROJECTS/alarmiq/backend/models/transition_matrix.pkl) | 99 KB |
| Evaluation metrics | [metrics.json](file:///home/alestor/PROJECTS/alarmiq/backend/models/metrics.json) | 2.7 KB |
| Feature dataset | [features.csv](file:///home/alestor/PROJECTS/alarmiq/backend/data/features.csv) | 53 MB (292,087 rows × 33 cols) |
| Raw alarm dataset | alarms_scrubbed6.json | 295,995 alarms |

---

*Generated by deep analysis of the complete AlarmIQ codebase — every formula, threshold, and algorithm traced to its source line.*
