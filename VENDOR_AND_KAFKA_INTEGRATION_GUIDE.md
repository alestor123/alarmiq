# AlarmIQ: Vendor & Kafka Integration Guide

This guide explains how to integrate external Kafka streams into AlarmIQ and how to add support for networking vendors other than Cisco (e.g., Juniper, Nokia, Huawei).

## 1. Connecting an External Kafka Stream

AlarmIQ has a built-in asynchronous Kafka consumer (`kafka_consumer_task` in `backend/main.py`) that subscribes to the `telecom-alarms` topic and streams telemetry directly into the AI prediction engine.

### Step 1: Configure the Broker
To connect AlarmIQ to your production Kafka stream, simply set the `KAFKA_BROKER` environment variable before starting the backend.

```bash
# Example: Connecting to a production broker
export KAFKA_BROKER="kafka.prod.internal:9092"
./run.sh
```

*(Note: If `KAFKA_BROKER` is not set, AlarmIQ falls back to its offline memory cache for simulation.)*

### Step 2: Kafka Message Schema
AlarmIQ expects messages on the `telecom-alarms` topic to be JSON-formatted with the following fields:

```json
{
  "node": "bgl-pe-router-01",
  "ts": "2023-10-25T14:30:00Z",
  "condition": "BGP-5-ADJCHANGE_DOWN",
  "severity": "CRITICAL",
  "category": "Routing"
}
```
* **`node`**: The hostname or IP of the device.
* **`ts`**: Timestamp of the alarm in ISO 8601 format.
* **`condition`**: The specific alarm/trap name.
* **`severity`**: String representing severity (e.g., `CRITICAL`, `MAJOR`, `MINOR`, `WARNING`, `CLEARED`).
* **`category`**: The domain of the alarm (e.g., `Routing`, `Hardware`, `Interface`).

---

## 2. Adding Support for Other Vendors

AlarmIQ's ML prediction engine and topology root-cause analyzer use pattern matching on specific alarm `condition` names. Out of the box, it contains Cisco-specific Syslog/SNMP trap names (e.g., `BGP-5-ADJCHANGE_DOWN`, `PORT-5-IF_DOWN`).

There are **two methods** to add support for non-Cisco vendors:

### Method A: Normalization at the Producer (Recommended)
The easiest and most robust method is to map vendor-specific alarms to AlarmIQ's standard taxonomy *before* pushing the message to Kafka. 

For example, your Kafka Producer (e.g., Logstash, Telegraf, or a custom Python script) would map:
* **Juniper**: `rpd_bgp_neighbor_state_changed` ➔ `BGP-5-ADJCHANGE_DOWN`
* **Nokia**: `vRtrBgpNeighborDown` ➔ `BGP-5-ADJCHANGE_DOWN`
* **Huawei**: `hwBgpPeerStateDown` ➔ `BGP-5-ADJCHANGE_DOWN`

By normalizing at the source, you do not need to modify AlarmIQ's backend code, and the ML models will automatically understand the topology and risk implications across a multi-vendor environment.

### Method B: Extending the AlarmIQ Backend
If you prefer to stream raw vendor alarms directly into AlarmIQ, you must update the condition categorizations in `backend/predictor.py`.

Open `backend/predictor.py` and locate the condition dictionaries near the top of the file:

```python
# Add your vendor-specific strings to these dictionaries

SEVERE_CONDITIONS = {
    # Cisco
    "BGP-5-ADJCHANGE_DOWN": True,
    # Juniper
    "rpd_bgp_neighbor_state_changed": True,
    # Nokia
    "vRtrBgpNeighborDown": True
}

HARDWARE_CONDITIONS = {
    "cefcPowerStatusChange": True,
    "cefcFRURemoved": True,
    "chassis_alarm_red": True, # Juniper
    "hwBoardRemoved": True,    # Huawei
}

ROUTING_CONDITIONS = {
    "ROUTING-OSPF-5-ADJCHG": True,
    "ISIS-5-ADJCHANGE": True,
    "isis_adjacency_down": True, # Juniper
}
```

Once you add the vendor-specific strings to these sets, AlarmIQ's feature extraction engine will correctly parse them, count them in the sliding time windows, and use them for Root Cause Analysis (RCA) and TTF (Time-To-Failure) predictions.
