"""
AlarmIQ — Local LLM Alarm Summarizer
Uses Ollama (llama3.2:3b) to generate concise, actionable NOC diagnostics.
All calls are async with 10-second timeout. Never blocks the prediction pipeline.
"""

import json
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    print("[llm] httpx not installed — LLM summaries disabled")

import os

OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://ollama:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = """You are a NOC (Network Operations Center) assistant for BSNL, analyzing Cisco ASR 9000 MPLS network alarms.
You will receive a snapshot of alarms from a network node and must produce a concise, actionable diagnostic.

Rules:
- Do not state obvious facts (e.g. 'there are alarms on this node')
- Focus on IMPACT to services (L3VPN, L2VPN, iBGP, eBGP, enterprise customers)
- Distinguish hardware root causes (environmental/FRU) from software/protocol effects
- Be specific: name the protocol, the peer type, the service type
- Output ONLY valid JSON, no markdown, no preamble

Output format:
{
  "what": "1 sentence — what is failing right now",
  "why": "1 sentence — most probable root cause",
  "action": "1 sentence — what the NOC engineer should do next",
  "impact": "1 sentence — which services/customers are affected",
  "urgency": "IMMEDIATE | MONITOR | INVESTIGATE"
}"""


def _build_user_prompt(
    node: str,
    risk_score: float,
    risk_level: str,
    ttf_minutes: str,
    next_condition: str,
    transition_probability: float,
    predicted_transition_path: list,
    top_features: list,
    recent_alarms: list,
    neighbor_summary: str,
) -> str:
    """Build the user prompt with actual data injected."""
    top_features_summary = ", ".join(
        f"{f.get('feature', '?')}={f.get('raw_value', f.get('value', '?'))}"
        for f in (top_features or [])[:5]
    )

    recent_alarms_list = "\n".join(
        f"  [{a.get('severity', '?')}] {a.get('condition', '?')} at {a.get('ts', '?')}"
        for a in (recent_alarms or [])[:10]
    )

    path_str = " → ".join(predicted_transition_path) if predicted_transition_path else "N/A"

    return f"""Node: {node}
Risk Score: {risk_score:.3f} ({risk_level})
TTF Estimate: {ttf_minutes}
Markov Forecast: next condition = {next_condition} ({transition_probability:.0%} confidence)
Predicted Path: {path_str}
Top Features: {top_features_summary}
Recent Alarms (last {len(recent_alarms or [])} in window):
{recent_alarms_list}
Topology: neighbors {neighbor_summary}"""


_llm_cache = {}
_llm_semaphore = asyncio.Semaphore(1)

async def _detect_best_model(client: httpx.AsyncClient, endpoint: str) -> Optional[str]:
    """Query the Ollama tags endpoint to find the best installed model."""
    tags_url = endpoint.replace("/api/generate", "/api/tags")
    try:
        response = await client.get(tags_url, timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            installed = [m.get("name") for m in data.get("models", [])]
            preferences = ["llama3.2:latest", "llama3.2", "llama3.2:3b", "llama3:latest", "llama3"]
            for pref in preferences:
                if pref in installed:
                    return pref
            for m in installed:
                if "llama" in m.lower():
                    return m
            if installed:
                return installed[0]
    except Exception:
        pass
    return None

async def summarize_node_alert(
    node: str,
    risk_score: float,
    risk_level: str,
    ttf_minutes: str = "N/A",
    next_condition: str = "UNKNOWN",
    transition_probability: float = 0.0,
    predicted_transition_path: list = None,
    top_features: list = None,
    recent_alarms: list = None,
    neighbor_summary: str = "none",
) -> Optional[dict]:
    """
    Query Ollama for a concise diagnostic summary.
    Returns parsed JSON dict on success, None on any failure.
    Uses caching and semaphore serialization to prevent overloading.
    """
    if not _HTTPX_AVAILABLE:
        return None

    global _llm_cache
    now = time.time()
    cached = _llm_cache.get(node)

    # 1. Cache hit check
    if cached:
        time_elapsed = now - cached["timestamp"]
        # If same risk level and generated recently (60s) or risk level is high, reuse
        if cached["risk_level"] == risk_level and (time_elapsed < 60.0 or risk_level == "HIGH"):
            cached["timestamp"] = now
            print(f"[llm] Cache hit for node {node} (risk={risk_level}). Reusing summary.")
            return cached["summary"]

    # 2. Concurrency limiting check
    if _llm_semaphore.locked():
        if cached:
            print(f"[llm] Inference in progress. Returning cached summary for node {node}.")
            return cached["summary"]
        else:
            print(f"[llm] Inference in progress. Skipping summary generation for node {node} to prevent overload.")
            return None

    async with _llm_semaphore:
        # Double check cache inside lock
        cached = _llm_cache.get(node)
        if cached and cached["risk_level"] == risk_level and (time.time() - cached["timestamp"] < 30.0):
            return cached["summary"]

        start_time = time.monotonic()
        target_endpoint = OLLAMA_ENDPOINT
        target_model = OLLAMA_MODEL

        # 3. Dynamic endpoint resolution & model selection
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS)) as client:
            endpoint_ok = False
            try:
                ping_url = target_endpoint.replace("/api/generate", "/api/tags")
                await client.get(ping_url, timeout=2.0)
                endpoint_ok = True
            except Exception:
                if "ollama:" in target_endpoint:
                    for host in ("localhost:", "127.0.0.1:"):
                        fallback = target_endpoint.replace("ollama:", host)
                        try:
                            ping_url = fallback.replace("/api/generate", "/api/tags")
                            await client.get(ping_url, timeout=2.0)
                            target_endpoint = fallback
                            endpoint_ok = True
                            break
                        except Exception:
                            pass

            if not endpoint_ok:
                print(f"[llm] Neither ollama nor localhost endpoints are reachable. Disabling summary.")
                return None

            detected_model = await _detect_best_model(client, target_endpoint)
            if detected_model:
                target_model = detected_model

            # 4. Generate summary
            user_prompt = _build_user_prompt(
                node=node,
                risk_score=risk_score,
                risk_level=risk_level,
                ttf_minutes=ttf_minutes,
                next_condition=next_condition,
                transition_probability=transition_probability,
                predicted_transition_path=predicted_transition_path or [],
                top_features=top_features or [],
                recent_alarms=recent_alarms or [],
                neighbor_summary=neighbor_summary,
            )

            payload = {
                "model": target_model,
                "system": SYSTEM_PROMPT,
                "prompt": user_prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.1,
                },
            }

            try:
                print(f"[llm] Generating diagnostic summary for node {node} using {target_model} at {target_endpoint}...")
                response = await client.post(target_endpoint, json=payload)
                response.raise_for_status()
            except Exception as e:
                import traceback
                print(f"[llm] Error querying Ollama at {target_endpoint} with {target_model}: {type(e).__name__}: {e}")
                traceback.print_exc()
                return None

            result = response.json()
            raw_text = result.get("response", "").strip()

            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                raw_text = "\n".join(lines)

            # Auto-repair common LLM JSON truncations (e.g. missing closing brace)
            stripped_text = raw_text.strip()
            if stripped_text.startswith("{") and not stripped_text.endswith("}"):
                stripped_text += "\n}"

            try:
                parsed = json.loads(stripped_text)
            except json.JSONDecodeError as e:
                print(f"[llm] Failed to parse LLM JSON for {node}: {e}. Raw response: {raw_text}")
                return None

            latency_ms = int((time.monotonic() - start_time) * 1000)

            # Validate required fields
            required_fields = {"what", "why", "action", "impact", "urgency"}
            if not required_fields.issubset(parsed.keys()):
                print(f"[llm] Warning: Missing fields in LLM response for {node}")
                return None

            # Normalize urgency
            urgency = parsed.get("urgency", "INVESTIGATE").upper()
            if urgency not in ("IMMEDIATE", "MONITOR", "INVESTIGATE"):
                urgency = "INVESTIGATE"
            parsed["urgency"] = urgency

            summary_result = {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "node": node,
                "risk_level": risk_level,
                "risk_score": round(risk_score, 4),
                "llm_summary": parsed,
                "context_sent_to_llm": {
                    "node": node,
                    "risk_score": round(risk_score, 4),
                    "top_features": top_features or [],
                    "recent_conditions": [a.get("condition") for a in (recent_alarms or [])[:5]],
                    "markov_next": next_condition,
                    "topology_neighbors": neighbor_summary,
                },
                "model": target_model,
                "generation_latency_ms": latency_ms,
                "ollama_endpoint": target_endpoint,
            }

            # Update cache
            _llm_cache[node] = {
                "risk_level": risk_level,
                "timestamp": time.time(),
                "summary": summary_result
            }
            print(f"[llm] Successfully generated summary for {node} in {latency_ms}ms.")
            return summary_result


TELEGRAM_SYSTEM_PROMPT = """You are a NOC (Network Operations Center) assistant for BSNL.
Your task is to write a Telegram alert message based on the provided JSON event data.
The event_type can be NEW_PREDICTION, TRUE_POSITIVE, FALSE_POSITIVE, or LLM_INSIGHT.

Rules:
- Output ONLY the formatted HTML string. Do not output JSON. Do not include markdown code block backticks like ```html.
- Use Telegram-supported HTML tags (<b>, <i>, <code>, <u>, <s>, <a href="...">).
- Keep it concise, professional, and visually appealing using emojis (🚨, ✅, ℹ️, 🛠, etc.).
- Ensure critical numbers, hostnames, and statuses are bolded or wrapped in <code>.
- Do NOT output preamble, just the final formatted string ready to be sent to Telegram.
"""

async def generate_telegram_message(event_type: str, rec: dict, ts_str: str) -> Optional[str]:
    """
    Query Ollama to generate a formatted Telegram message string for a given event.
    """
    if not _HTTPX_AVAILABLE:
        return None

    # Wait for the semaphore so we don't overload Ollama
    async with _llm_semaphore:
        target_endpoint = OLLAMA_ENDPOINT
        target_model = OLLAMA_MODEL

        # Dynamic endpoint resolution & model selection
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS)) as client:
            endpoint_ok = False
            try:
                ping_url = target_endpoint.replace("/api/generate", "/api/tags")
                await client.get(ping_url, timeout=2.0)
                endpoint_ok = True
            except Exception:
                if "ollama:" in target_endpoint:
                    for host in ("localhost:", "127.0.0.1:"):
                        fallback = target_endpoint.replace("ollama:", host)
                        try:
                            ping_url = fallback.replace("/api/generate", "/api/tags")
                            await client.get(ping_url, timeout=2.0)
                            target_endpoint = fallback
                            endpoint_ok = True
                            break
                        except Exception:
                            pass

            if not endpoint_ok:
                print(f"[llm] Neither ollama nor localhost endpoints are reachable. Disabling Telegram generation.")
                return None

            detected_model = await _detect_best_model(client, target_endpoint)
            if detected_model:
                target_model = detected_model

            user_prompt = f"Event Type: {event_type}\nTimestamp: {ts_str}\n\nEvent Data:\n{json.dumps(rec, indent=2)}\n\nGenerate the Telegram HTML message:"

            payload = {
                "model": target_model,
                "system": TELEGRAM_SYSTEM_PROMPT,
                "prompt": user_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                },
            }

            try:
                print(f"[llm] Generating Telegram message for {rec.get('node', 'unknown')} using {target_model}...")
                response = await client.post(target_endpoint, json=payload)
                response.raise_for_status()
                result = response.json()
                raw_text = result.get("response", "").strip()

                if raw_text.startswith("```"):
                    lines = raw_text.split("\n")
                    lines = [l for l in lines if not l.startswith("```")]
                    raw_text = "\n".join(lines).strip()

                return raw_text
            except Exception as e:
                print(f"[llm] Error generating Telegram message: {e}")
                return None

