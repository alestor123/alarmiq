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

async def _process_and_send_event(event_type: str, rec: dict, ts_str: str):
    import llm_summarizer
    msg_text = await llm_summarizer.generate_telegram_message(event_type, rec, ts_str)
    if msg_text:
        queue_message(msg_text)

def queue_event(event_type: str, rec: dict, ts_str: str):
    global _event_loop
    if _event_loop:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _process_and_send_event(event_type, rec, ts_str)
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

# Hardcoded string formats removed; using Ollama for dynamic generation.

# format_llm_summary removed in favor of queue_event("LLM_INSIGHT", ...)
