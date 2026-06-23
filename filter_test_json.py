import json
import os
from datetime import datetime, timezone

def main():
    # Target start time: 2026-05-27 16:06:01 UTC
    start_dt = datetime(2026, 5, 27, 16, 6, 1, tzinfo=timezone.utc)
    json_path = "test.json"

    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist in the current directory.")
        return

    print(f"Loading {json_path} (this may take a few seconds)...")
    with open(json_path, "r") as f:
        data = json.load(f)

    total_loaded = len(data)
    print(f"Total alarms loaded: {total_loaded:,}")

    filtered = []
    skipped = 0
    for r in data:
        if not isinstance(r, dict):
            skipped += 1
            continue
        dto = r.get("alarmsDTO", {})
        ts_str = dto.get("timeStamp") or dto.get("alarmFoundAt")
        if not ts_str:
            skipped += 1
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts >= start_dt:
                filtered.append(r)
        except Exception:
            skipped += 1
            continue

    print(f"Filtered alarms remaining: {len(filtered):,}")
    print(f"Skipped/out-of-range alarms: {total_loaded - len(filtered):,}")

    print(f"Writing filtered alarms back to {json_path}...")
    with open(json_path, "w") as f:
        json.dump(filtered, f, indent=2)

    print("✅ Filtering completed successfully.")

if __name__ == "__main__":
    main()
