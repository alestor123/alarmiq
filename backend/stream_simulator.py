"""
AlarmIQ — Historical Alarm Stream Simulator
Replays alarms_scrubbed6.json in chronological order, feeding them
into the NodeRiskPredictor and printing live risk updates.
Simulates the Kafka consumer role described in the architecture.
"""

import json
import os
import time
import sys
from datetime import datetime, timezone
from collections import defaultdict

from ml_pipeline import normalise_node
from predictor import predictor

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "alarms_scrubbed6.json")

SEV_COLOR = {
    "CRITICAL": "\033[91m",   # red
    "MAJOR":    "\033[93m",   # yellow
    "HIGH":     "\033[91m",
    "MEDIUM":   "\033[93m",
    "LOW":      "\033[92m",   # green
    "MINOR":    "\033[94m",   # blue
    "WARNING":  "\033[95m",   # magenta
    "CLEARED":  "\033[90m",   # grey
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def color(text, level):
    return f"{SEV_COLOR.get(level, '')}{text}{RESET}"


def load_sorted_alarms(path: str) -> list:
    """Load and return all alarms sorted by timestamp."""
    print("[sim] Loading alarm data...")
    with open(path) as f:
        raw = json.load(f)

    alarms = []
    for r in raw:
        dto = r.get("alarmsDTO", {})
        ts_str = dto.get("timeStamp", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        alarms.append({
            "node":      normalise_node(dto.get("deviceName", "UNKNOWN")),
            "ts":        ts,
            "condition": dto.get("condition", {}).get("value", "UNKNOWN"),
            "severity":  dto.get("severity", "UNKNOWN"),
            "category":  dto.get("category", {}).get("value", "UNKNOWN"),
            "message":   dto.get("message", ""),
            "alarm_id":  dto.get("alarmId", 0),
        })

    alarms.sort(key=lambda a: a["ts"])
    print(f"[sim] {len(alarms)} alarms sorted chronologically.")
    return alarms


def run_simulation(
    speed_factor: float = 500.0,  # 1 sim-second = speed_factor real-seconds
    score_every_n: int  = 50,     # Re-score nodes every N alarms ingested
    warning_threshold: float = 0.50,
    max_alarms: int = None,       # None = run all
    verbose: bool = True,
    batch: bool = False,          # If True, skip time-compression sleep (max speed)
):
    """
    Replay all alarms through the predictor at compressed time.

    Args:
        speed_factor:  How many real-world seconds each simulated second represents.
                       500 = 1 real-minute ≈ ~8 real-hours of alarm history.
        score_every_n: Score all nodes after every N ingested alarms.
        warning_threshold: Risk score above which an early warning is printed.
        max_alarms:    Cap replay (useful for quick tests).
        verbose:       Print each ingested alarm.
    """
    if not predictor._loaded:
        print("[sim] Loading model...")
        predictor.load()

    alarms = load_sorted_alarms(DATA_PATH)
    if max_alarms:
        alarms = alarms[:max_alarms]

    total      = len(alarms)
    warnings_issued = 0
    ingested   = 0
    sim_start_real = time.time()
    sim_t0_data    = alarms[0]["ts"]

    print(f"\n{'='*70}")
    print(f"  AlarmIQ Stream Simulator")
    print(f"  Replaying {total:,} alarms  |  speed_factor={speed_factor}x")
    print(f"  Date range: {alarms[0]['ts'].date()} → {alarms[-1]['ts'].date()}")
    print(f"{'='*70}\n")

    prev_score_time = time.time()

    for i, alarm in enumerate(alarms):
        # ---- Time compression ----
        if not batch:
            data_elapsed_sec = (alarm["ts"] - sim_t0_data).total_seconds()
            real_target_sec  = data_elapsed_sec / speed_factor
            real_elapsed     = time.time() - sim_start_real
            sleep_for        = real_target_sec - real_elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

        # ---- Ingest alarm ----
        score = predictor.ingest_alarm(alarm)
        ingested += 1

        if verbose:
            ts_fmt = alarm["ts"].strftime("%Y-%m-%d %H:%M")
            sev    = alarm["severity"]
            print(
                f"  {ts_fmt} | {color(f'{sev:8}', sev)} | "
                f"{alarm['node'][:40]:40} | {alarm['condition'][:35]}"
            )

        # ---- Periodic risk scoring + warnings ----
        if ingested % score_every_n == 0 or i == total - 1:
            _print_risk_snapshot(predictor, warning_threshold)
            warnings = predictor.get_warnings()
            for w in warnings:
                if w["risk_score"] >= warning_threshold:
                    warnings_issued += 1
                    _print_warning(w)

        if (i + 1) % 1000 == 0:
            elapsed  = time.time() - sim_start_real
            rate     = ingested / elapsed if elapsed > 0 else 0
            pct      = 100 * (i + 1) / total
            print(f"\n  [{pct:5.1f}%] Processed {ingested:,}/{total:,} alarms "
                  f"({rate:.0f}/s) | Warnings issued: {warnings_issued}\n")

    print(f"\n{'='*70}")
    print(f"  Simulation complete.")
    print(f"  Total alarms processed : {ingested:,}")
    print(f"  Warnings issued        : {warnings_issued}")
    summary = predictor.summary()
    print(f"  Nodes tracked          : {summary['total_nodes']}")
    print(f"  CRITICAL risk nodes    : {summary['critical_nodes']}")
    print(f"  HIGH risk nodes        : {summary['high_nodes']}")
    print(f"{'='*70}\n")

    return predictor.score_all_nodes()


def _print_risk_snapshot(pred, threshold):
    """Print a compact risk table for all nodes above threshold."""
    all_scores = pred.score_all_nodes()
    risky = [s for s in all_scores if s["risk_score"] >= threshold]
    if not risky:
        return
    print(f"\n  ┌─── RISK SNAPSHOT ({'─'*40}┐")
    for s in risky[:10]:
        bar   = "█" * int(s["risk_score"] * 20)
        lvl   = s["risk_level"]
        rscore = s["risk_score"]
        print(
            f"  │ {color(f'{lvl:8}', lvl)} "
            f"{s['node'][:38]:38} "
            f"{color(f'{rscore:.3f}', lvl)} "
            f"{color(bar, lvl)}"
        )
    print(f"  └{'─'*70}┘\n")


def _print_warning(score: dict):
    """Print a formatted early warning card."""
    lvl = score["risk_level"]
    print(f"\n  {'⚠'*3}  {BOLD}{color('EARLY WARNING', lvl)}{RESET}  {'⚠'*3}")
    print(f"  Node       : {BOLD}{score['node']}{RESET}")
    print(f"  Risk Score : {color(str(score['risk_score']), lvl)}")
    print(f"  Risk Level : {color(lvl, lvl)}")
    print(f"  Alarms (2h): {score['window_alarms']}")
    print(f"  Top Factors:")
    for f in score.get("top_features", []):
        if f["value"] > 0:
            print(f"    • {f['feature']:35} val={f['value']:.2f}  imp={f['importance']:.4f}")
    print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlarmIQ Stream Simulator")
    parser.add_argument("--speed",   type=float, default=2000.0,
                        help="Speed factor (default 2000 = very fast replay)")
    parser.add_argument("--every",   type=int,   default=100,
                        help="Score nodes every N alarms (default 100)")
    parser.add_argument("--threshold", type=float, default=0.50,
                        help="Warning threshold (default 0.50)")
    parser.add_argument("--max",     type=int,   default=None,
                        help="Max alarms to process (default all)")
    parser.add_argument("--quiet",   action="store_true",
                        help="Suppress per-alarm output")
    parser.add_argument("--batch",   action="store_true",
                        help="Skip time-compression sleep, process at full CPU speed")
    args = parser.parse_args()

    run_simulation(
        speed_factor=args.speed,
        score_every_n=args.every,
        warning_threshold=args.threshold,
        max_alarms=args.max,
        verbose=not args.quiet,
        batch=args.batch,
    )
