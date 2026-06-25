"""
AlarmIQ — Socket.IO Real-Time API Server
Namespaces:
  /predictions — emits "prediction_update" after every scoring cycle
  /llm         — emits "llm_summary" when Ollama returns a valid summary
"""

import socketio
import asyncio
from collections import deque
from datetime import datetime, timezone

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

# Ring buffer: last 10 LLM summaries
llm_ring_buffer: deque = deque(maxlen=10)

# Track connected client counts per namespace
_client_counts = {"/predictions": 0, "/llm": 0}

# Timestamps for status endpoint
_last_prediction_emit_ts = None
_last_llm_emit_ts = None

# Reference to predictor — injected at startup from main.py
_predictor_ref = None


def set_predictor_ref(pred):
    """Called once from main.py startup to give socketio_server access to predictor."""
    global _predictor_ref
    _predictor_ref = pred


# ── /predictions namespace ────────────────────────────────────────────────────

@sio.event(namespace="/predictions")
async def connect(sid, environ):
    _client_counts["/predictions"] += 1
    print(f"[socketio] /predictions client connected: {sid} (total: {_client_counts['/predictions']})")
    # Emit latest snapshot for all currently tracked nodes
    await emit_all_node_snapshots(sid)


@sio.event(namespace="/predictions")
async def disconnect(sid):
    _client_counts["/predictions"] = max(0, _client_counts["/predictions"] - 1)
    print(f"[socketio] /predictions client disconnected: {sid} (total: {_client_counts['/predictions']})")


@sio.event(namespace="/predictions")
async def subscribe_node(sid, data):
    node = data.get("node") if isinstance(data, dict) else None
    if node:
        await sio.enter_room(sid, room=node, namespace="/predictions")
        print(f"[socketio] {sid} subscribed to room: {node}")


async def emit_all_node_snapshots(sid):
    """On connect, emit latest prediction_update for every tracked node."""
    try:
        if _predictor_ref is None:
            return
        scores = _predictor_ref._scores
        for node, score_data in scores.items():
            try:
                payload = _predictor_ref.build_socketio_payload(node)
                if payload:
                    await sio.emit("prediction_update", payload, to=sid, namespace="/predictions")
            except Exception as e:
                print(f"[socketio] Error emitting snapshot for {node}: {e}")
    except Exception as e:
        print(f"[socketio] Error in emit_all_node_snapshots: {e}")


# ── /llm namespace ────────────────────────────────────────────────────────────

@sio.event(namespace="/llm")
async def connect(sid, environ):
    _client_counts["/llm"] += 1
    print(f"[socketio] /llm client connected: {sid} (total: {_client_counts['/llm']})")
    # Replay ring buffer on connect
    for summary in llm_ring_buffer:
        try:
            await sio.emit("llm_summary", summary, to=sid, namespace="/llm")
        except Exception:
            pass


@sio.event(namespace="/llm")
async def disconnect(sid):
    _client_counts["/llm"] = max(0, _client_counts["/llm"] - 1)
    print(f"[socketio] /llm client disconnected: {sid} (total: {_client_counts['/llm']})")


# ── Broadcast functions (fire-and-forget) ─────────────────────────────────────

async def broadcast_prediction(payload: dict):
    """Broadcast a prediction_update to all /predictions clients. Fire-and-forget."""
    global _last_prediction_emit_ts
    try:
        node = payload.get("node")
        _last_prediction_emit_ts = datetime.now(tz=timezone.utc).isoformat()
        # Emit to specific node room (subscribers)
        await sio.emit("prediction_update", payload, room=node, namespace="/predictions")
        # Also broadcast to all /predictions clients
        await sio.emit("prediction_update", payload, namespace="/predictions")
    except Exception as e:
        print(f"[socketio] broadcast_prediction error: {e}")


async def broadcast_llm_summary(payload: dict):
    """Broadcast an llm_summary to all /llm clients. Fire-and-forget."""
    global _last_llm_emit_ts
    try:
        llm_ring_buffer.append(payload)
        _last_llm_emit_ts = datetime.now(tz=timezone.utc).isoformat()
        await sio.emit("llm_summary", payload, namespace="/llm")
        
        # Dispatch LLM summary to Telegram
        try:
            import telegram_notifier
            telegram_notifier.queue_event("LLM_INSIGHT", payload, payload.get("timestamp", ""))
        except Exception as te:
            print(f"[telegram] Failed to queue LLM summary: {te}")
    except Exception as e:
        print(f"[socketio] broadcast_llm_summary error: {e}")


def get_status() -> dict:
    """Return current Socket.IO status for the REST endpoint."""
    return {
        "connected_clients": dict(_client_counts),
        "llm_ring_buffer_size": len(llm_ring_buffer),
        "last_prediction_emit_ts": _last_prediction_emit_ts,
        "last_llm_emit_ts": _last_llm_emit_ts,
    }
