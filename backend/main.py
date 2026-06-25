"""
AlarmIQ — FastAPI Backend (Socket.IO Enhanced Edition)
REST + WebSocket + Socket.IO API. Simulation runs in a background thread,
updates are pushed via asyncio.Queue → WebSocket AND Socket.IO /predictions namespace.
"""

import os
import json
import asyncio
import warnings
import threading
import math
warnings.filterwarnings("ignore")

from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import socketio as socketio_lib

from predictor import predictor
from ml_pipeline import normalise_node
from socketio_server import sio, set_predictor_ref, broadcast_prediction, broadcast_llm_summary, get_status as sio_get_status
from llm_summarizer import summarize_node_alert

DATA_PATH  = os.path.join(os.path.dirname(__file__), "..", "alarms_scrubbed6.json")
DASHBOARD  = os.path.join(os.path.dirname(__file__), "dashboard.html")
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "..", "public")

# Global variable to store computed dataset metadata (with fallback to pre-computed static values)
DEFAULT_METADATA = {
    "start": "2024-01-03T08:04:50.881000+00:00",
    "end": "2026-05-27T10:36:01.153000+00:00",
    "duration": "875 days, 2 hours, 31 minutes"
}
dataset_metadata = dict(DEFAULT_METADATA)

# Thread-safe caching of processed dataset
_alarms_cache = None
_alarms_cache_lock = threading.Lock()
_uploaded_alarms = None

def process_uploaded_json(raw_data):
    processed = []
    for r in raw_data:
        if not isinstance(r, dict):
            continue
        # Check if Cisco DTO or flat format
        if "alarmsDTO" in r:
            dto = r.get("alarmsDTO", {})
            ts_str = dto.get("timeStamp", "") or dto.get("alarmFoundAt", "") or dto.get("deviceTimestamp", "")
            node_val = dto.get("deviceName", "UNKNOWN")
            cond_val = dto.get("condition", {}).get("value") if isinstance(dto.get("condition"), dict) else dto.get("condition", "UNKNOWN")
            sev_val = dto.get("severity", "UNKNOWN")
            cat_val = dto.get("category", {}).get("value") if isinstance(dto.get("category"), dict) else dto.get("category", "UNKNOWN")
        else:
            ts_str = r.get("ts", "") or r.get("timeStamp", "") or r.get("alarmFoundAt", "")
            node_val = r.get("node", "") or r.get("deviceName", "UNKNOWN")
            cond_val = r.get("condition", "UNKNOWN")
            sev_val = r.get("severity", "UNKNOWN")
            cat_val = r.get("category", "UNKNOWN")

        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except:
            continue

        processed.append({
            "node":      normalise_node(node_val),
            "ts":        ts,
            "condition": cond_val,
            "severity":  sev_val,
            "category":  cat_val,
            "raw":       r,
        })
    processed.sort(key=lambda a: a["ts"])
    return processed

def _get_or_load_alarms():
    """Load and process the 296K alarms dataset once, caching the results in memory (or using uploaded custom alarms)."""
    global _alarms_cache, _uploaded_alarms
    if _uploaded_alarms is not None:
        return _uploaded_alarms
    with _alarms_cache_lock:
        if _alarms_cache is not None:
            return _alarms_cache

        print("[startup] Loading and parsing 296K alarms from disk (first time)...")
        if not os.path.exists(DATA_PATH):
            print(f"[startup] Error: Dataset file not found at {DATA_PATH}")
            return []

        try:
            with open(DATA_PATH) as f:
                raw = json.load(f)
            
            processed = []
            for r in raw:
                dto = r.get("alarmsDTO", {})
                ts_str = dto.get("timeStamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except:
                    continue
                processed.append({
                    "node":      normalise_node(dto.get("deviceName", "UNKNOWN")),
                    "ts":        ts,
                    "condition": dto.get("condition", {}).get("value", "UNKNOWN"),
                    "severity":  dto.get("severity", "UNKNOWN"),
                    "category":  dto.get("category", {}).get("value", "UNKNOWN"),
                    "raw":       r,
                })
            
            processed.sort(key=lambda a: a["ts"])
            _alarms_cache = processed
            print(f"[startup] ✅ Loaded and cached {len(processed):,} alarms in memory.")
            return _alarms_cache
        except Exception as e:
            print(f"[startup] Error loading alarms: {e}")
            return []

def _compute_dataset_metadata():
    global dataset_metadata
    try:
        alarms = _get_or_load_alarms()
        if not alarms:
            return
        
        start = alarms[0]["ts"]
        end = alarms[-1]["ts"]
        diff = end - start
        
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        duration_parts = []
        if days > 0:
            duration_parts.append(f"{days} days")
        if hours > 0:
            duration_parts.append(f"{hours} hours")
        if minutes > 0:
            duration_parts.append(f"{minutes} minutes")
            
        dataset_metadata = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": ", ".join(duration_parts) if duration_parts else "0 minutes"
        }
        print(f"[startup] Dataset metadata computed: {dataset_metadata['start']} to {dataset_metadata['end']} ({dataset_metadata['duration']})")
    except Exception as e:
        print(f"[startup] Error computing dataset metadata: {e}")


app = FastAPI(title="AlarmIQ", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Mount public static files for BSNL assets
if os.path.isdir(PUBLIC_DIR):
    app.mount("/public", StaticFiles(directory=PUBLIC_DIR), name="public")

# Socket.IO ASGI wrapper — this is the uvicorn entrypoint
socket_app = socketio_lib.ASGIApp(sio, other_asgi_app=app)

# In-memory event log for paginated /api/events
_event_log: list = []  # append-only list of alarm feed entries
_EVENT_LOG_MAX = 50000

# ── Shared state (thread-safe via GIL for simple reads/writes) ────────────────
sim_state = {
    "running": False, "ingested": 0, "total": 0,
    "warnings_issued": 0, "recent_alarms": [],
    "sleep_ms": 150,  # Default to 150ms sleep per alarm for comfortable reading
}

# Queue: simulation thread → async broadcaster
_update_queue: asyncio.Queue = None

# ── WebSocket manager ─────────────────────────────────────────────────────────
class ConnManager:
    def __init__(self): self.active: list[WebSocket] = []
    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active.append(ws)
    def disconnect(self, ws: WebSocket):
        if ws in self.active: self.active.remove(ws)
    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try: await ws.send_json(data)
            except: dead.append(ws)
        for ws in dead: self.active.remove(ws)

manager = ConnManager()

# ── Async broadcaster: drains queue → WebSocket ───────────────────────────────
async def broadcaster():
    """Runs as a long-lived asyncio task; takes payloads from the queue and broadcasts."""
    global _update_queue
    while True:
        try:
            payload = await asyncio.wait_for(_update_queue.get(), timeout=1.0)
            await manager.broadcast(payload)
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

# ── Simulation thread (pure Python, no asyncio) ───────────────────────────────
def _simulation_thread(loop: asyncio.AbstractEventLoop, score_every: int = 1000):
    """Runs entirely in a thread. Puts WS payloads onto the queue thread-safely."""

    sim_state["running"]  = True
    sim_state["ingested"] = 0
    sim_state["recent_alarms"] = []

    print("[sim] Fetching alarms from memory cache...")
    alarms = _get_or_load_alarms()

    sim_state["total"] = len(alarms)
    print(f"[sim] Replaying {len(alarms):,} alarms (background thread)...")

    def push(payload):
        asyncio.run_coroutine_threadsafe(_update_queue.put(payload), loop)

    def sio_push(node):
        """Fire-and-forget Socket.IO broadcast for a node's prediction."""
        try:
            payload = predictor.build_socketio_payload(node)
            if payload:
                payload["simulation"] = {
                    "is_running": sim_state["running"],
                    "speed_mode": _get_speed_mode(),
                    "alarms_processed": sim_state["ingested"],
                    "total_alarms": sim_state["total"],
                    "progress_pct": round(sim_state["ingested"] / max(sim_state["total"], 1) * 100, 2),
                    "current_alarm_ts": predictor.latest_sim_time.isoformat() if predictor.latest_sim_time else None,
                }
                asyncio.run_coroutine_threadsafe(broadcast_prediction(payload), loop)
                # Fire LLM summary for HIGH/CRITICAL nodes (non-blocking)
                if payload["risk"]["level"] in ("HIGH", "CRITICAL"):
                    asyncio.run_coroutine_threadsafe(_trigger_llm_summary(node, payload), loop)
        except Exception as e:
            pass  # Never block scoring hot path

    for i, alarm in enumerate(alarms):
        # FAST PATH: Score ONLY the active node that received the alarm (<1ms)
        predictor.ingest_alarm(alarm, score=True)
        sim_state["ingested"] += 1

        feed_entry = {
            "node": alarm["node"], "condition": alarm["condition"],
            "severity": alarm["severity"], "ts": alarm["ts"].isoformat(),
        }
        sim_state["recent_alarms"].append(feed_entry)
        if len(sim_state["recent_alarms"]) > 60:
            sim_state["recent_alarms"].pop(0)
        # Append to paginated event log
        _event_log.append(feed_entry)
        if len(_event_log) > _EVENT_LOG_MAX:
            _event_log.pop(0)

        # Socket.IO broadcast for this node
        sio_push(alarm["node"])

        # Dynamic sleep checking
        sleep_sec = sim_state.get("sleep_ms", 150) / 1000.0

        if sleep_sec >= 0.01:
            # Human-readable speed: push updates on every alarm for seamless real-time visual feedback
            push({
                "type":          "progress",
                "ingested":      sim_state["ingested"],
                "total":         sim_state["total"],
                "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                "sim_time":      alarm["ts"].isoformat(),
            })
            
            # Retrieve active risks incrementally in sub-milliseconds without stuttering
            if (i + 1) % 50 == 0 or i == 0 or (i + 1) == len(alarms):
                nodes    = predictor.update_and_expire_scores(ref_time=alarm["ts"])
                warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
                sim_state["warnings_issued"] = len(warnings)
                push({
                    "type":          "update",
                    "ingested":      sim_state["ingested"],
                    "total":         sim_state["total"],
                    "nodes":         nodes[:80],
                    "warnings":      warnings,
                    "summary":       predictor.summary(ref_time=alarm["ts"]),
                    "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                    "sim_time":      alarm["ts"].isoformat(),
                })
            import time as _time
            _time.sleep(sleep_sec)
        else:
            # High-speed/Hyper mode: throttle broadcasts to save resources
            if (i + 1) % 10 == 0:
                import time as _time
                _time.sleep(0.0015)
                push({
                    "type":          "progress",
                    "ingested":      sim_state["ingested"],
                    "total":         sim_state["total"],
                    "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                    "sim_time":      alarm["ts"].isoformat(),
                })

            if (i + 1) % score_every == 0:
                import time as _time
                _time.sleep(0.02)
                nodes    = predictor.update_and_expire_scores(ref_time=alarm["ts"])
                warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
                sim_state["warnings_issued"] = len(warnings)
                push({
                    "type":          "update",
                    "ingested":      sim_state["ingested"],
                    "total":         sim_state["total"],
                    "nodes":         nodes[:80],
                    "warnings":      warnings,
                    "summary":       predictor.summary(ref_time=alarm["ts"]),
                    "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                    "sim_time":      alarm["ts"].isoformat(),
                })

    # Final broadcast
    sim_state["running"] = False
    nodes    = predictor.update_and_expire_scores(ref_time=alarms[-1]["ts"])
    warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
    sim_state["warnings_issued"] = len(warnings)
    push({
        "type":          "done",
        "ingested":      sim_state["ingested"],
        "total":         sim_state["total"],
        "nodes":         nodes,
        "warnings":      warnings,
        "summary":       predictor.summary(ref_time=alarms[-1]["ts"]),
        "recent_alarms": list(reversed(sim_state["recent_alarms"])),
        "sim_time":      alarms[-1]["ts"].isoformat(),
    })
    print("[sim] ✅ Replay complete.")

# ── Kafka Consumer Task ───────────────────────────────────────────────────────
async def kafka_consumer_task():
    """
    Subscribes to 'telecom-alarms' on KAFKA_BROKER.
    For each message, processes it via predictor and broadcasts updates.
    """
    from aiokafka import AIOKafkaConsumer
    from datetime import timezone
    import json

    broker = os.environ.get("KAFKA_BROKER", "localhost:9092")
    topic = "telecom-alarms"
    print(f"[kafka-consumer] Initializing consumer for {topic} on broker {broker}...")

    consumer = None
    retries = 30
    for attempt in range(1, retries + 1):
        try:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=broker,
                group_id="alarmiq-backend",
                auto_offset_reset="latest",
                value_deserializer=lambda m: json.loads(m.decode("utf-8"))
            )
            await consumer.start()
            print(f"[kafka-consumer] ✅ Connected to Kafka! Listening to '{topic}'...")
            break
        except Exception as e:
            print(f"[kafka-consumer] Connection failed (attempt {attempt}/{retries}): {e}")
            await asyncio.sleep(2)

    if not consumer:
        print("[kafka-consumer] ❌ Could not connect to Kafka. Consumer task exiting.")
        return

    try:
        sim_state["running"] = True
        sim_state["ingested"] = 0
        sim_state["total"] = 0
        sim_state["recent_alarms"] = []

        def push(payload):
            _update_queue.put_nowait(payload)

        async for msg in consumer:
            alarm_data = msg.value

            ts_str = alarm_data.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(tz=timezone.utc)

            alarm = {
                "node": normalise_node(alarm_data.get("node", "UNKNOWN")),
                "ts": ts,
                "condition": alarm_data.get("condition", "UNKNOWN"),
                "severity": alarm_data.get("severity", "UNKNOWN"),
                "category": alarm_data.get("category", "UNKNOWN"),
            }

            predictor.ingest_alarm(alarm, score=True)
            sim_state["ingested"] += 1
            sim_state["total"] = max(sim_state["total"], sim_state["ingested"])

            feed_entry = {
                "node": alarm["node"],
                "condition": alarm["condition"],
                "severity": alarm["severity"],
                "ts": alarm["ts"].isoformat(),
            }
            sim_state["recent_alarms"].append(feed_entry)
            if len(sim_state["recent_alarms"]) > 60:
                sim_state["recent_alarms"].pop(0)

            push({
                "type": "progress",
                "ingested": sim_state["ingested"],
                "total": sim_state["total"],
                "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                "sim_time": alarm["ts"].isoformat(),
            })

            nodes = predictor.update_and_expire_scores(ref_time=alarm["ts"])
            warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
            sim_state["warnings_issued"] = len(warnings)

            push({
                "type": "update",
                "ingested": sim_state["ingested"],
                "total": sim_state["total"],
                "nodes": nodes[:80],
                "warnings": warnings,
                "summary": predictor.summary(ref_time=alarm["ts"]),
                "recent_alarms": list(reversed(sim_state["recent_alarms"])),
                "sim_time": alarm["ts"].isoformat(),
            })

    except Exception as e:
        print(f"[kafka-consumer] Error in consumer loop: {e}")
    finally:
        print("[kafka-consumer] Stopping consumer...")
        sim_state["running"] = False
        await consumer.stop()
        print("[kafka-consumer] Consumer stopped.")


# ── Helper functions ──────────────────────────────────────────────────────────
def _get_speed_mode() -> str:
    ms = sim_state.get("sleep_ms", 150)
    if ms >= 500: return "slow"
    if ms >= 50: return "medium"
    if ms >= 5: return "fast"
    return "hyper"

async def _trigger_llm_summary(node: str, sio_payload: dict):
    """Fire-and-forget LLM summary generation for a node."""
    try:
        risk = sio_payload.get("risk", {})
        markov = sio_payload.get("markov_forecast", {})
        topo = sio_payload.get("topology", {})
        result = await summarize_node_alert(
            node=node,
            risk_score=risk.get("score", 0),
            risk_level=risk.get("level", "HIGH"),
            ttf_minutes=str(risk.get("ttf_minutes", "N/A")),
            next_condition=markov.get("next_condition", "UNKNOWN"),
            transition_probability=markov.get("transition_probability", 0),
            predicted_transition_path=markov.get("predicted_transition_path", []),
            top_features=sio_payload.get("top_features", []),
            recent_alarms=sio_payload.get("recent_alarms_raw", []),
            neighbor_summary=str(topo.get("neighbors", [])),
        )
        if result:
            await broadcast_llm_summary(result)
    except Exception as e:
        print(f"[llm] Error triggering summary for {node}: {e}")

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _update_queue
    _update_queue = asyncio.Queue()
    predictor.load()
    set_predictor_ref(predictor)

    # ── Guarantee simulation is IDLE on every fresh server boot ──
    sim_state["running"]         = False
    sim_state["ingested"]        = 0
    sim_state["total"]           = 0
    sim_state["recent_alarms"]   = []
    sim_state["warnings_issued"] = 0
    print("[startup] Simulation idle — waiting for user to press Play.")

    # Start thread to compute exact dataset dates dynamically without blocking startup
    t = threading.Thread(target=_compute_dataset_metadata, daemon=True)
    t.start()

    # Start broadcaster coroutine
    asyncio.create_task(broadcaster())

    # Initialize Telegram notifier
    try:
        import telegram_notifier
        telegram_notifier.init_telegram(asyncio.get_event_loop())
    except Exception as te:
        print(f"[startup] Failed to initialize Telegram notifier: {te}")

    # Start Kafka consumer only if KAFKA_BROKER env var is set
    if os.environ.get("KAFKA_BROKER"):
        asyncio.create_task(kafka_consumer_task())


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    with open(DASHBOARD) as f:
        return HTMLResponse(content=f.read())

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model_loaded": predictor._loaded,
        "sim_running": sim_state["running"],
        "ingested": sim_state["ingested"],
        "total": sim_state["total"],
    }

@app.get("/api/model/metrics")
def model_metrics():
    if not predictor.metrics:
        raise HTTPException(404, "Model not loaded")
    return predictor.metrics

@app.get("/api/nodes")
def all_nodes():
    return predictor.score_all_nodes()

@app.get("/api/warnings")
def active_warnings(threshold: float = None):
    from predictor import RISK_THRESHOLDS
    if threshold is None:
        threshold = RISK_THRESHOLDS["HIGH"]
    return [w for w in predictor.get_warnings() if w["risk_score"] >= threshold]

@app.get("/api/summary")
def summary():
    s = predictor.summary()
    s.update({
        "sim_ingested": sim_state["ingested"],
        "sim_total":    sim_state["total"],
        "sim_running":  sim_state["running"],
        "sim_time":     predictor.latest_sim_time.isoformat() if predictor.latest_sim_time else None,
        "dataset_start": dataset_metadata["start"],
        "dataset_end":   dataset_metadata["end"],
        "dataset_duration": dataset_metadata["duration"],
    })
    return s

class SpeedPayload(BaseModel):
    sleep_ms: int

@app.post("/api/sim/speed")
def change_speed(payload: SpeedPayload):
    sim_state["sleep_ms"] = max(0, min(payload.sleep_ms, 2000))
    print(f"[sim] Playback speed set to {sim_state['sleep_ms']}ms sleep per alarm")
    return {"status": "ok", "sleep_ms": sim_state["sleep_ms"]}

@app.post("/api/sim/restart")
async def restart_sim():
    if sim_state["running"]:
        return {"status": "already running"}
    loop = asyncio.get_event_loop()
    t = threading.Thread(target=_simulation_thread, args=(loop,), daemon=True)
    t.start()
    return {"status": "started"}

@app.post("/api/sim/scenario")
async def start_interactive_scenario():
    """Reset predictor and trigger a gradual failure sequence on demo-node-01."""
    if sim_state["running"]:
        return {"status": "error", "message": "Simulation is already running"}
        
    predictor._windows.clear()
    predictor._scores.clear()
    predictor.latest_sim_time = None
    
    sim_state["running"] = True
    sim_state["ingested"] = 0
    sim_state["total"] = 5
    sim_state["recent_alarms"] = []
    
    def run_scenario(loop):
        import time as _time
        alarms_to_replay = [
            {"node": "host-er2-router-03", "condition": "cefcPowerStatusChange", "severity": "CRITICAL", "category": "Power"},
            {"node": "host-er2-router-03", "condition": "cefcFRURemoved", "severity": "CRITICAL", "category": "Hardware"},
            {"node": "demo-node-01", "condition": "LINK_DOWN", "severity": "MAJOR", "category": "Interface"},
            {"node": "demo-node-01", "condition": "ROUTING-OSPF-5-ADJCHG", "severity": "MAJOR", "category": "Routing"},
            {"node": "demo-node-01", "condition": "BGP-5-ADJCHANGE_DOWN", "severity": "CRITICAL", "category": "Routing"},
        ]
        
        def push(payload):
            asyncio.run_coroutine_threadsafe(_update_queue.put(payload), loop)
            
        for idx, a in enumerate(alarms_to_replay):
            _time.sleep(2.0)  # Replay slowly
            a["ts"] = datetime.now(tz=timezone.utc)
            predictor.ingest_alarm(a, score=True)
            sim_state["ingested"] += 1
            
            feed_entry = {
                "node": a["node"], "condition": a["condition"],
                "severity": a["severity"], "ts": a["ts"].isoformat(),
            }
            sim_state["recent_alarms"].append(feed_entry)
            
            nodes = predictor.score_all_nodes()
            warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
            sim_state["warnings_issued"] = len(warnings)
            
            push({
                "type":          "update",
                "ingested":      sim_state["ingested"],
                "total":         sim_state["total"],
                "nodes":         nodes,
                "warnings":      warnings,
                "summary":       predictor.summary(),
                "recent_alarms": list(reversed(sim_state["recent_alarms"])),
            })
            
        sim_state["running"] = False
        nodes = predictor.score_all_nodes()
        warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
        push({
            "type":          "done",
            "ingested":      sim_state["ingested"],
            "total":         sim_state["total"],
            "nodes":         nodes,
            "warnings":      warnings,
            "summary":       predictor.summary(),
            "recent_alarms": list(reversed(sim_state["recent_alarms"])),
        })
        print("[scenario] ✅ Replay complete.")
        
    loop = asyncio.get_event_loop()
    t = threading.Thread(target=run_scenario, args=(loop,), daemon=True)
    t.start()
    return {"status": "started"}

class AlarmIn(BaseModel):
    node: str; condition: str; severity: str; category: str = "Unknown"

@app.post("/api/ingest")
def ingest_alarm(alarm: AlarmIn):
    a = dict(alarm)
    a["ts"] = datetime.now(tz=timezone.utc)
    return predictor.ingest_alarm(a)

@app.post("/api/sim/reset")
def reset_all_state():
    """Clear all predictor states and windows so they can start fresh."""
    global _uploaded_alarms, dataset_metadata
    predictor._windows.clear()
    predictor._scores.clear()
    predictor.latest_sim_time = None
    predictor.clear_ledger()
    _uploaded_alarms = None
    dataset_metadata = dict(DEFAULT_METADATA)
    sim_state["running"] = False
    sim_state["ingested"] = 0
    sim_state["total"] = 0
    sim_state["recent_alarms"] = []
    sim_state["warnings_issued"] = 0
    return {"status": "reset"}

@app.get("/api/predictions/verification")
def get_verification(
    status: str = Query("ALL"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100)
):
    """Return the prediction verification ledger with precision metrics, paginated."""
    ledger = predictor.get_verification_ledger()
    all_items = ledger.get("resolved", []) + ledger.get("pending", [])
    
    all_items = sorted(all_items, key=lambda x: x.get("predicted_at", ""), reverse=True)
    
    if status and status != "ALL":
        filtered_items = []
        for item in all_items:
            item_status = item.get("status", "")
            if status == "TRUE_POSITIVE" and "True Positive" in item_status:
                filtered_items.append(item)
            elif status == "FALSE_POSITIVE" and "False Positive" in item_status:
                filtered_items.append(item)
            elif status == "PENDING" and "Pending" in item_status:
                filtered_items.append(item)
        all_items = filtered_items

    total = len(all_items)
    pages = max(1, math.ceil(total / limit))
    start = (page - 1) * limit
    end = start + limit
    ledger["items"] = all_items[start:end]
    ledger["page"] = page
    ledger["pages"] = pages
    ledger["total_items"] = total
    return ledger


@app.get("/api/events")
def get_events(page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)):
    """Return paginated alarm event log."""
    total = len(_event_log)
    pages = max(1, math.ceil(total / limit))
    start = max(0, total - page * limit)
    end = max(0, total - (page - 1) * limit)
    events = list(reversed(_event_log[start:end]))
    return {"events": events, "total": total, "page": page, "pages": pages}

@app.get("/api/topology")
def topology_graph():
    """Return the complete static topology graph with live risk overlays."""
    from predictor import TOPOLOGY
    nodes = []
    links = []
    added_links = set()

    for short_name, entry in TOPOLOGY.items():
        if not isinstance(entry, dict):
            continue
        # Find live risk info for this node
        risk_level = "LOW"
        risk_score = 0.0
        full_name = short_name
        for n, s in predictor._scores.items():
            if n.split(".")[0] == short_name:
                risk_level = s.get("risk_level", "LOW")
                risk_score = round(s.get("risk_score", 0.0), 4)
                full_name = n
                break

        nodes.append({
            "id": short_name,
            "fullName": full_name,
            "riskLevel": risk_level,
            "riskScore": risk_score,
            "type": entry.get("type", "PE_ROUTER"),
            "location": entry.get("location", "UNKNOWN"),
        })

        for neighbor in entry.get("neighbors", []):
            link_key = tuple(sorted([short_name, neighbor]))
            if link_key not in added_links:
                added_links.add(link_key)
                # Determine link status
                link_status = "UP"
                for n, s in predictor._scores.items():
                    if n.split(".")[0] == neighbor:
                        feats = s.get("features", {})
                        if feats.get("link_down_count", 0) > 0 or feats.get("bgp_down_count", 0) > 0:
                            link_status = "DOWN"
                        break
                links.append({
                    "source": short_name,
                    "target": neighbor,
                    "status": link_status,
                })

    return {"nodes": nodes, "links": links}


@app.get("/api/topology/rootcause")
def topology_rootcause(node: str = Query(...)):
    """Return root cause analysis for a specific node."""
    from predictor import TOPOLOGY
    score = predictor._scores.get(node)
    if not score:
        # Try matching by short name
        for n, s in predictor._scores.items():
            if n.split(".")[0] == node.split(".")[0]:
                score = s
                node = n
                break
    if not score:
        raise HTTPException(404, f"Node {node} not found")
    topo = score.get("topology_correlation", {})
    return {
        "node": node,
        "root_cause_node": topo.get("root_cause_node"),
        "root_cause_type": topo.get("root_cause_type", "UNKNOWN"),
        "causal_chain": topo.get("causal_chain", []),
        "impacted_services": topo.get("impacted_services", {}),
        "neighbors": topo.get("neighbors", []),
    }


@app.get("/api/socketio/status")
def socketio_status():
    """Return Socket.IO connection status."""
    return sio_get_status()

class RepartitionPayload(BaseModel):
    test_size: float  # fraction for test, e.g. 0.80

@app.post("/api/sim/repartition")
async def repartition_and_retrain(payload: RepartitionPayload):
    """
    Stop any running sim, retrain on custom time-based split, reload model,
    and return the timestamp of the start of the test partition so the UI
    can resume the simulation from that point.
    """
    if sim_state["running"]:
        return {"status": "error", "message": "Stop the simulation first before repartitioning."}

    test_size = max(0.10, min(0.90, payload.test_size))

    import ml_pipeline as mlp
    import concurrent.futures

    loop = asyncio.get_event_loop()
    print(f"[repartition] Retraining with test_size={test_size:.0%} …")

    try:
        def _retrain():
            metrics, df = mlp.retrain_custom_split(test_size=test_size)
            # Compute the window_end timestamp at the split boundary
            df_sorted = df.sort_values("window_end").reset_index(drop=True)
            train_size = 1.0 - test_size
            split_idx = int(len(df_sorted) * train_size)
            split_ts = df_sorted.iloc[split_idx]["window_end"]
            return metrics, split_ts

        with concurrent.futures.ThreadPoolExecutor() as pool:
            metrics, split_ts = await loop.run_in_executor(pool, _retrain)

        # Reload predictor with fresh model
        predictor.load()
        predictor._windows.clear()
        predictor._scores.clear()
        predictor.latest_sim_time = None
        predictor.clear_ledger()
        sim_state["running"] = False
        sim_state["ingested"] = 0
        sim_state["total"] = 0
        sim_state["recent_alarms"] = []
        sim_state["warnings_issued"] = 0

        print(f"[repartition] ✅ Model reloaded. Test window starts at: {split_ts}")
        return {
            "status": "ok",
            "test_size": test_size,
            "train_size": round(1.0 - test_size, 2),
            "split_timestamp": split_ts,
            "model": metrics.get("best_model"),
            "roc_auc": metrics.get("roc_auc"),
            "avg_precision": metrics.get("avg_precision"),
            "train_samples": metrics.get("train_samples"),
            "test_samples":  metrics.get("test_samples"),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Retraining failed: {str(e)}"}

@app.post("/api/sim/upload")
async def upload_custom_data(file: UploadFile = File(...)):
    global _uploaded_alarms, dataset_metadata
    try:
        content = await file.read()
        raw_data = json.loads(content)
        if not isinstance(raw_data, list):
            raise HTTPException(400, "Invalid JSON format: Expected a list of alarm records.")
            
        processed = process_uploaded_json(raw_data)
        if not processed:
            raise HTTPException(400, "No valid alarm records found (could not parse timestamps).")
            
        _uploaded_alarms = processed
        
        # Clear predictor states to start fresh with new custom dataset
        predictor._windows.clear()
        predictor._scores.clear()
        predictor.latest_sim_time = None
        predictor.clear_ledger()
        
        # Compute metadata for custom dataset
        start = processed[0]["ts"]
        end = processed[-1]["ts"]
        diff = end - start
        
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        duration_parts = []
        if days > 0:
            duration_parts.append(f"{days} days")
        if hours > 0:
            duration_parts.append(f"{hours} hours")
        if minutes > 0:
            duration_parts.append(f"{minutes} minutes")
            
        dataset_metadata = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": ", ".join(duration_parts) if duration_parts else "0 minutes"
        }
        
        sim_state["running"] = False
        sim_state["ingested"] = 0
        sim_state["total"] = len(processed)
        sim_state["recent_alarms"] = []
        sim_state["warnings_issued"] = 0
        
        print(f"[upload] ✅ Loaded and cached {len(processed):,} custom alarms in memory.")
        return {
            "status": "ok",
            "message": f"Successfully loaded {len(processed):,} custom alarms.",
            "total_alarms": len(processed),
            "dataset_start": dataset_metadata["start"],
            "dataset_end": dataset_metadata["end"],
            "dataset_duration": dataset_metadata["duration"],
        }
    except Exception as e:
        raise HTTPException(400, f"Failed to upload and parse JSON: {str(e)}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send current snapshot immediately
        nodes    = predictor.score_all_nodes()
        warnings = [n for n in nodes if n["risk_level"] in ("HIGH", "CRITICAL")]
        await websocket.send_json({
            "type":          "init",
            "ingested":      sim_state["ingested"],
            "total":         sim_state["total"],
            "nodes":         nodes[:80],
            "warnings":      warnings,
            "summary":       predictor.summary(),
            "recent_alarms": list(reversed(sim_state["recent_alarms"])),
            "sim_time":      predictor.latest_sim_time.isoformat() if predictor.latest_sim_time else None,
            "dataset_start": dataset_metadata["start"],
            "dataset_end":   dataset_metadata["end"],
            "dataset_duration": dataset_metadata["duration"],
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("main:socket_app", host="0.0.0.0", port=8001, reload=False)
