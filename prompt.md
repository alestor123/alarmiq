ADD PAGINATION SO THAT DASHBOARD WON'T HANG
DOCKERIZE THE ENTIRE PROJECT
FINE TUNE LLM so that it can send message in a non cluttered way easy to understand and act upon it.  ‌ADD LLM LOCAL ONES TO SUMMARIZE THE ISSUE 
BUILD A BEAUTIFUL DASHBOARD WITH RESULTS
BRAND EVERYTHING IN BSNL icon is in public folder
the dashboard should not display obvious messages which are not very relevant to the issue . i want you to display it beautifully and in a less cluttered way.
Add real time data to the dashboard .
by looking at a set of alarms of a node we should be able to point out the who is root node causing issue because we are using topologies such as 
alarms catagory and device type to find the root cause.

CISCO ASR 9000 SERIES 
NIB generally called 
MPLS NETWORK it is deployed as PE router in MPLS network

BGP is used for routing between 
the edge rouers and with other service provider

BGP have two type Peering such as LLD and eBGP 
LLD peering with in BSNL network 
eBGP peering with other service provider

types of alarms i am seeing these days 
we provide service to enterprice and entirprise customers
layer 3 vpn service and layer 2 vpn service
bsnl internal service depend on this routers

routing alarms
physical 
enviromental alarms[high, temprature ,fan faul] 
equiment alarms


You are a senior telecom network software engineer implementing AlarmIQ — a real-time predictive alarm intelligence system deployed for BSNL (Bharat Sanchar Nigam Limited), India's state telecom operator.

=== DOMAIN CONTEXT (read carefully) ===

Network equipment: Cisco ASR 9000 series routers deployed as PE (Provider Edge) routers in an MPLS network.

Routing protocols:
- iBGP: internal BSNL peering between PE routers
- eBGP: external peering with other ISPs/service providers
- IS-IS / OSPF for IGP

Services running on this infrastructure:
- Layer 3 VPN (L3VPN) for enterprise customers
- Layer 2 VPN (L2VPN / pseudowire) for enterprise customers
- BSNL internal services (management, NMS, backhaul)

Alarm categories you will see:
1. ROUTING alarms: BGP-5-ADJCHANGE_DOWN, ROUTING-ISIS-5-ADJCHANGE_DOWN, ROUTING-OSPF-5-ADJCHG
2. PHYSICAL alarms: LINK_DOWN, IOSXE_RP_ALARM_INFO_LINK_DOWN, XCVR_MISSING_LINK_DOWN, Optical Alarm
3. ENVIRONMENTAL alarms: fan failure, high temperature, PSU/power status change (cefcPowerStatusChange), cefcFRURemoved
4. EQUIPMENT alarms: hardware module removal/failure, satellite chassis problems

Root cause logic for MPLS/PE networks:
- Environmental alarm (temp/fan/power) on a PE router → HARDWARE root cause; all subsequent routing alarms on that node and neighbors are SECONDARY effects
- PE router BGP_DOWN → L3VPN customers on that PE are impacted; iBGP peer loss affects internal BSNL; eBGP peer loss affects external ISP connectivity
- LINK_DOWN on a PE → check if optical alarm preceded it (fiber issue) or if it is isolated (transceiver/SFP)
- Multiple neighbors losing BGP simultaneously → suspect the upstream PE/core router

=== EXISTING CODEBASE ===

Stack: FastAPI (Python), XGBoost ML model, Markov chain sequence forecaster, WebSocket broadcaster, historical dataset of 295,995 alarms (alarms_scrubbed6.json), dashboard.html single-page UI.

Key files: backend/main.py (FastAPI), backend/predictor.py (NodeRiskPredictor), backend/ml_pipeline.py (training), backend/transition_model.py (Markov), backend/dashboard.html (UI), models/ (pkl artifacts).

=== TASK 1: PAGINATION (prevent dashboard hang) ===

1a. Backend — add paginated REST endpoints:
  GET /api/events?page=1&limit=50  → returns {events, total, page, pages}
  GET /api/predictions/verification?page=1&limit=20

1b. WebSocket — continue pushing only NEW alarm deltas (not full history replay on reconnect).
  On reconnect, client fetches page 1 of /api/events via REST, then subscribes to WS for live updates.

1c. Frontend — implement pagination controls for:
  - Alarm event log (50 per page, prev/next + page numbers)
  - Verification ledger (20 per page)
  - Node risk table (25 per page)
  Use a lightweight JS virtual list for the alarm log; do not load DOM nodes for all 295K alarms.

=== TASK 2: DOCKERIZE ===

Create docker-compose.yml with these services:

Service: alarmiq-backend
  Build from backend/Dockerfile
  Ports: 8001:8001
  Volumes: ../alarms_scrubbed6.json:/app/alarms_scrubbed6.json:ro, ./models:/app/models
  Env: PYTHONUNBUFFERED=1
  Healthcheck: GET http://localhost:8001/api/health

Service: kafka (profile: kafka)
  Image: apache/kafka:3.7.0
  KRaft mode (no Zookeeper), port 9092
  Env: KAFKA_NODE_ID=1, KAFKA_PROCESS_ROLES=broker,controller, KAFKA_LISTENERS=...

Service: alarmiq-producer (profile: kafka)
  Depends on kafka health
  Runs kafka_producer.py

Service: ollama
  Image: ollama/ollama:latest
  Volumes: ollama_data:/root/.ollama
  Ports: 11434:11434
  Post-start: pull llama3.2:3b model

Create backend/Dockerfile:
  FROM python:3.11-slim
  Install requirements.txt
  CMD uvicorn main:app --host 0.0.0.0 --port 8001

=== TASK 3: LOCAL LLM ALARM SUMMARIES ===

Create backend/llm_summarizer.py:
  - Async function summarize_node_alert(node, alarms, risk_score, top_features, topology_neighbors, transition_forecast)
  - POST to http://ollama:11434/api/generate with model llama3.2:3b
  - Temperature: 0.1 (deterministic)
  - System prompt (encode this exactly):

SYSTEM PROMPT FOR LLM:
"You are a NOC (Network Operations Center) assistant for BSNL, analyzing Cisco ASR 9000 MPLS network alarms. You will receive a snapshot of alarms from a network node and must produce a concise, actionable diagnostic. 

Rules:
- Do not state obvious facts (e.g. 'there are alarms on this node')
- Focus on IMPACT to services (L3VPN, L2VPN, iBGP, eBGP, enterprise customers)
- Distinguish hardware root causes (environmental/FRU) from software/protocol effects
- Be specific: name the protocol, the peer type, the service type
- Output ONLY valid JSON, no markdown, no preamble

Output format:
{
  'what': '1 sentence — what is failing right now',
  'why': '1 sentence — most probable root cause',  
  'action': '1 sentence — what the NOC engineer should do next',
  'impact': '1 sentence — which services/customers are affected',
  'urgency': 'IMMEDIATE | MONITOR | INVESTIGATE'
}"

  - On failure/timeout (Ollama not ready): return null gracefully, never block prediction pipeline
  - Store result in prediction record as llm_summary field
  - Trigger: fire async when node crosses HIGH threshold (non-blocking)

=== TASK 4: REDESIGNED BSNL-BRANDED DASHBOARD ===

Redesign dashboard.html completely:


Layout (4-panel grid, no glassmorphism, clean flat cards):
  Panel 1 (top-left): Network health summary — total nodes, critical count, high count, live precision gauge, alarms/min rate
  Panel 2 (top-right): Active warnings list — only HIGH/CRITICAL nodes, each card shows node name, risk %, LLM summary (what + action lines), urgency badge
  Panel 3 (bottom-left): Topology visualization — D3.js force-directed graph showing node connections and risk colors
  Panel 4 (bottom-right): Live alarm feed with pagination (50/page)

Noise reduction (IMPORTANT — implement all of these):
  - CLEARED alarms: only show if they follow a CRITICAL within 10 minutes (indicates recovery), otherwise suppress
  - Duplicate conditions: collapse consecutive same-condition alarms into one row with a count badge
  - MINOR alarms: suppress from display if the node already has an active CRITICAL (they are redundant)
  - Do not show "Simulation running", "Waiting for data", or status messages that are not actionable
  - Alarm cards must show: severity icon + condition name + time ago + node — nothing else by default, expand on click for raw details

LLM summary panel:
  - Below each active warning card, show the 3-line LLM output (what/why/action) in plain readable text
  - If LLM summary not yet loaded, show a subtle "Analyzing..." skeleton, not a spinner overlay
  - Use urgency field to color the card border: IMMEDIATE = red left border, MONITOR = amber, INVESTIGATE = blue

=== TASK 5: ENHANCED ROOT CAUSE ANALYSIS ===

Update backend/predictor.py topology correlation:

Extend TOPOLOGY dict to include device_type field:
  "host-er2-router-01": {"neighbors": [...], "type": "PE_ROUTER", "location": "ER2"}

New scoring logic in _topology_correlation():
  1. If node has cefcPowerStatusChange or cefcFRURemoved or high_temp_alarm in current window:
     → Set root_cause_type = "HARDWARE"
     → Suppress routing alarms from this node's neighbors as secondary
  2. If node has BGP_DOWN and neighbor PE router is already CRITICAL:
     → Root cause is the neighbor PE, this node is secondary victim
  3. If multiple neighbors all lost BGP within the same 5-minute window:
     → Root cause is upstream shared device (flag as "UPSTREAM_FAILURE")
  4. Compute impacted_services: count L3VPN / L2VPN customers affected based on which PE is down

New API endpoint:
  GET /api/topology/rootcause?node=X
  Returns: {root_cause_node, root_cause_type, causal_chain: [...], impacted_services: {l3vpn: N, l2vpn: N}}

Add causal chain to prediction record and display as timeline in UI:
  e.g. [cefcPowerStatusChange at 14:02] → [BGP-5-ADJCHANGE_DOWN at 14:03] → [L3VPN customers impacted at 14:04]

=== CODE QUALITY REQUIREMENTS ===
- All new async functions must have try/except with graceful degradation
- LLM calls must have a 10-second timeout and never block the prediction pipeline
- Pagination endpoints must use database-style offset/limit even if backed by in-memory lists
- Dashboard must work without Ollama running (LLM summaries optional, rest of system fully functional)
- Add /public/ static file serving to FastAPI for BSNL assets

Begin by listing which files you will create or modify, then implement each task in order.