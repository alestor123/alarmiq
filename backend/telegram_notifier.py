import asyncio
import json
import os
import httpx
from datetime import datetime

TELEGRAM_TOKEN = "8998782488:AAFE7mQnCD3_zJ2PiMpGEKe8cBuBx9IAxjE"
CHATS_FILE = os.path.join(os.path.dirname(__file__), "telegram_chats.json")

# Thread-safe event loop reference
_event_loop = None

# Set to store registered chat IDs
_telegram_chats = set()

def load_chats():
    global _telegram_chats
    if os.path.exists(CHATS_FILE):
        try:
            with open(CHATS_FILE, "r") as f:
                data = json.load(f)
                _telegram_chats = set(data)
                print(f"[telegram] Loaded {len(_telegram_chats)} registered chats: {_telegram_chats}")
        except Exception as e:
            print(f"[telegram] Error loading chats: {e}")

def save_chats():
    try:
        with open(CHATS_FILE, "w") as f:
            json.dump(list(_telegram_chats), f)
    except Exception as e:
        print(f"[telegram] Error saving chats: {e}")

# Queue for outgoing messages to send asynchronously
_message_queue = None

def queue_message(text: str, parse_mode: str = "HTML"):
    global _message_queue, _event_loop
    if _message_queue and _event_loop:
        _event_loop.call_soon_threadsafe(
            _message_queue.put_nowait,
            {"text": text, "parse_mode": parse_mode}
        )

async def _send_message_api(chat_id: int, text: str, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except Exception as e:
            print(f"[telegram] Error sending message to chat {chat_id}: {e}")

async def message_sender_loop():
    global _message_queue
    while True:
        try:
            msg = await _message_queue.get()
            # Send message to all registered chats
            for chat_id in list(_telegram_chats):
                await _send_message_api(chat_id, msg["text"], msg["parse_mode"])
            _message_queue.task_done()
        except Exception as e:
            print(f"[telegram] Error in message sender loop: {e}")
            await asyncio.sleep(1)

async def poll_telegram_updates():
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    load_chats()
    
    print("[telegram] Started polling Telegram updates...")
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {"offset": offset, "timeout": 5}
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            message = update.get("message")
                            if message:
                                chat = message.get("chat")
                                if chat:
                                    chat_id = chat.get("id")
                                    text = message.get("text", "")
                                    
                                    if chat_id not in _telegram_chats:
                                        _telegram_chats.add(chat_id)
                                        save_chats()
                                        print(f"[telegram] Registered new chat ID: {chat_id}")
                                        welcome_text = (
                                            "<b>🤖 Welcome to AlarmIQ Operators Bot!</b>\n\n"
                                            "You have successfully registered to receive real-time BSNL network "
                                            "failure predictions and NOC diagnostics alerts."
                                        )
                                        await _send_message_api(chat_id, welcome_text)
                                    elif text.startswith("/start"):
                                        welcome_text = (
                                            "<b>🤖 AlarmIQ Operators Bot</b>\n\n"
                                            "You are already registered to receive real-time alerts. "
                                            "Any critical alarm predictions or resolutions will be posted here."
                                        )
                                        await _send_message_api(chat_id, welcome_text)
        except Exception as e:
            print(f"[telegram] Error polling updates: {e}")
        await asyncio.sleep(3)

def init_telegram(loop):
    global _message_queue, _event_loop
    _event_loop = loop
    _message_queue = asyncio.Queue()
    loop.create_task(poll_telegram_updates())
    loop.create_task(message_sender_loop())

# ── Message Formatting ────────────────────────────────────────────────────────

def format_new_prediction(rec: dict, ts: datetime) -> str:
    node = rec.get("node", "UNKNOWN")
    score = rec.get("risk_score", 0.0)
    level = rec.get("risk_level", "HIGH")
    score_pct = round(score * 100, 1)
    crit = rec.get("critical_alarms", 0)
    maj = rec.get("major_alarms", 0)
    w_total = rec.get("window_alarms", 0)
    ttf = rec.get("estimated_ttf", "Stable")
    last_c = rec.get("last_condition", "N/A")
    next_c = rec.get("next_predicted", "N/A")
    next_p = rec.get("next_prob", 0.0)
    pred_stages = rec.get("predicted_stages", [])
    topo = rec.get("topology_correlation", {})
    
    msg = (
        f"🚨 <b>CRITICAL RISK DETECTED ON NODE:</b>\n"
        f"   ➜ <code>{node}</code>\n\n"
        f"📊 <b>Current Status:</b>\n"
        f"   • Risk Score: <b>{score_pct}% ({level})</b>\n"
        f"   • Window Load: {w_total} alarms ({crit} CRIT, {maj} MAJ)\n"
        f"   • Est. TTF: <b>{ttf}</b>\n\n"
        f"🔮 <b>Sequence Forecast:</b>\n"
    )
    if pred_stages:
        path_parts = [last_c] + [f"{s['condition']} ({round(s['prob']*100)}%)" for s in pred_stages]
        path_str = " ➜ ".join(path_parts)
        msg += f"   {path_str}\n"
    else:
        msg += f"   {last_c} ➜ {next_c} ({round(next_p*100)}% conf)\n"
        
    if topo.get("correlated"):
        msg += f"\n🌐 <b>Topology Correlation:</b>\n   Root cause likely: <code>{topo.get('root_cause_node', '?')}</code>\n"
        
    msg += (
        f"\n🛠 <b>Action Plan:</b>\n"
        f"   1. SSH to device:\n"
        f"      <code>sshadmin@{node}</code>\n"
        f"   2. Pull interface diagnostic:\n"
        f"      <code>show interfaces summary | include down</code>\n"
        f"   3. Check routing adjacency status:\n"
        f"      <code>show bgp neighbors | show ospf neighbor</code>"
    )
    return msg

def format_true_positive(rec: dict, ts: datetime) -> str:
    node = rec.get("node", "UNKNOWN")
    elapsed = rec.get("elapsed_mins", 0)
    res_time = rec.get("resolution_time", "")
    actual = rec.get("actual_path", [])
    pred_at = rec.get("predicted_at", "")
    ttf = rec.get("estimated_ttf", "N/A")
    
    msg = (
        f"✅ <b>FAILURE PREDICTION CONFIRMED (TP)</b>\n"
        f"   Node: <code>{node}</code>\n\n"
        f"📈 <b>Incident Timeline:</b>\n"
        f"   • Predicted At: {pred_at}\n"
        f"   • Failure At:   {res_time}\n"
        f"   • Time-to-fail: <b>{elapsed} minutes</b> (model predicted {ttf})\n"
    )
    if actual:
        path_str = " ➜ ".join(actual)
        msg += f"\n🔗 <b>Observed Precursor Sequence:</b>\n   {path_str}\n"
        
    msg += (
        f"\n🛠 <b>Post-Mortem Next Steps:</b>\n"
        f"   1. Confirm traffic has rerouted successfully\n"
        f"   2. File incident ticket with timeline above\n"
        f"   3. Pull tech-support log file for RCA:\n"
        f"      <code>show tech-support</code>"
    )
    return msg

def format_false_positive(rec: dict, ts: datetime) -> str:
    node = rec.get("node", "UNKNOWN")
    elapsed = rec.get("elapsed_mins", 0)
    res_time = rec.get("resolution_time", "")
    actual = rec.get("actual_path", [])
    pred_at = rec.get("predicted_at", "")
    score = rec.get("risk_score", 0.0)
    score_pct = round(score * 100, 1)
    
    msg = (
        f"ℹ️ <b>ALERT AUTO-EXPIRED (FALSE POSITIVE)</b>\n"
        f"   Node: <code>{node}</code>\n\n"
        f"📉 <b>Summary Details:</b>\n"
        f"   • Sent At:    {pred_at}\n"
        f"   • Expired At: {res_time}\n"
        f"   • Duration:   <b>{elapsed} minutes</b>\n"
        f"   • Original P: {score_pct}% risk\n"
    )
    if actual:
        path_str = " ➜ ".join(actual)
        msg += f"\n🔗 <b>Observed Mismatched Sequence:</b>\n   {path_str}\n"
        
    msg += (
        f"\n🛠 <b>Follow-Up Actions:</b>\n"
        f"   1. Standard close — node self-recovered successfully.\n"
        f"   2. Review if triggered by a planned maintenance window.\n"
        f"   3. If flapping interface, apply dampening:\n"
        f"      <code>dampening 15 750 2000 60</code>"
    )
    return msg

def format_llm_summary(payload: dict) -> str:
    node = payload.get("node", "UNKNOWN")
    llm_summary = payload.get("llm_summary", {})
    what = llm_summary.get("what", "N/A")
    why = llm_summary.get("why", "N/A")
    action = llm_summary.get("action", "N/A")
    impact = llm_summary.get("impact", "N/A")
    urgency = llm_summary.get("urgency", "N/A")
    
    msg = (
        f"💡 <b>BSNL NOC LLM DIAGNOSTIC INSIGHT</b>\n"
        f"   Node: <code>{node}</code>\n\n"
        f"📝 <b>WHAT:</b> {what}\n\n"
        f"❓ <b>WHY:</b> {why}\n\n"
        f"🛠 <b>ACTION:</b> {action}\n\n"
        f"⚡ <b>IMPACT:</b> {impact}\n\n"
        f"🚨 <b>URGENCY:</b> <b>{urgency}</b>"
    )
    return msg
