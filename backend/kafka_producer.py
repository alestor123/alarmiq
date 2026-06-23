#!/usr/bin/env python3
"""
AlarmIQ — Live Kafka Producer
Reads alarms_scrubbed6.json and publishes them in chronological order
to a local Kafka topic ('telecom-alarms') to test the live streaming pipeline.
"""

import os
import sys
import json
import time
import asyncio
from datetime import datetime, timezone
from aiokafka import AIOKafkaProducer

KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "localhost:9092")
TOPIC = "telecom-alarms"
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "alarms_scrubbed6.json")

# Severity terminal colors
COLORS = {
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
BOLD = "\033[1m"


def color_text(text, level):
    return f"{COLORS.get(level, '')}{text}{RESET}"


def load_sorted_alarms(path: str) -> list:
    """Load and return all alarms sorted by timestamp."""
    print(f"[producer] Loading raw alarm data from {path}...")
    if not os.path.exists(path):
        print(f"\033[91m[error] Data file not found at {path}!\033[0m")
        sys.exit(1)

    with open(path) as f:
        raw = json.load(f)

    alarms = []
    for r in raw:
        dto = r.get("alarmsDTO", {})
        ts_str = dto.get("timeStamp", "")
        if not ts_str:
            continue
        alarms.append({
            "node":      dto.get("deviceName", "UNKNOWN"),
            "ts":        ts_str,
            "condition": dto.get("condition", {}).get("value", "UNKNOWN"),
            "severity":  dto.get("severity", "UNKNOWN"),
            "category":  dto.get("category", {}).get("value", "UNKNOWN"),
            "message":   dto.get("message", ""),
            "alarm_id":  dto.get("alarmId", 0),
        })

    # Sort using original timeStamp string
    alarms.sort(key=lambda a: a["ts"])
    print(f"[producer] Loaded {len(alarms):,} alarms sorted chronologically.")
    return alarms


async def main():
    print(f"\n{'='*70}")
    print(f"  AlarmIQ Live Kafka Producer")
    print(f"  Target Broker : {KAFKA_BROKER}")
    print(f"  Target Topic  : {TOPIC}")
    print(f"{'='*70}\n")

    alarms = load_sorted_alarms(DATA_PATH)
    if not alarms:
        print("[producer] No alarms to send.")
        return

    # Attempt connection with retries (Kafka in docker might take a few seconds to boot)
    producer = None
    retries = 15
    for attempt in range(1, retries + 1):
        try:
            print(f"[producer] Connecting to Kafka broker (attempt {attempt}/{retries})...")
            producer = AIOKafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
            await producer.start()
            print("\033[92m[producer] ✅ Connected to Kafka successfully!\033[0m")
            break
        except Exception as e:
            print(f"[producer] Connection failed: {e}")
            if attempt == retries:
                print("\033[91m[error] Could not connect to Kafka. Exiting.\033[0m")
                sys.exit(1)
            await asyncio.sleep(2)

    try:
        sent = 0
        total = len(alarms)
        print(f"\n[producer] Streaming alarms to topic '{TOPIC}'... Press Ctrl+C to stop.")
        
        # We'll stream with a dynamic speed that is comfortable to read but fast enough
        # Default: 100ms sleep between messages
        sleep_sec = float(os.environ.get("PRODUCER_SLEEP", "0.08"))
        
        for alarm in alarms:
            # Send message to Kafka
            await producer.send_and_wait(TOPIC, alarm)
            sent += 1

            if sent % 10 == 0 or sent == 1:
                ts_fmt = alarm["ts"].replace("Z", "").split(".")[0]
                sev = alarm["severity"]
                print(
                    f"  [sent {sent:,}/{total:,}] | {ts_fmt} | "
                    f"{color_text(f'{sev:8}', sev)} | "
                    f"{alarm['node'][:30]:30} | {alarm['condition'][:30]}"
                )

            await asyncio.sleep(sleep_sec)

    except asyncio.CancelledError:
        print("\n[producer] Streaming cancelled by user.")
    except Exception as e:
        print(f"\n[producer] Error during streaming: {e}")
    finally:
        if producer:
            print("[producer] Stopping Kafka producer...")
            await producer.stop()
        print("[producer] Stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[producer] Exited gracefully.")
