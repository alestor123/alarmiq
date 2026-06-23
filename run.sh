#!/usr/bin/env bash
# ==============================================================================
# AlarmIQ — Unified Runner Script
# Supports:
#   1) Live Kafka Testing (Docker-based KRaft broker + Python Producer/Consumer)
#   2) Just Simulation (Native Python replay engine)
# ==============================================================================

# Terminal Colors & Styling
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Script Directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

# Header
print_header() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "    =================================================================="
    echo "      ____  _                        ___ ___   ___                    "
    echo "     / __ \| |                      |_ _/ _ \ / _ \                   "
    echo "    | |  | | | __ _ _ __ _ __ ___    | | | | | | | |                  "
    echo "    | |  | | |/ _\` | '__| '_ \` _ \   | | | | | | | |                  "
    echo "    | |__| | | (_| | |  | | | | | | _| | |_| | |_| |                  "
    echo "     \____/|_|\__,_|_|  |_| |_| |_||___\__\_\\\\\__\_\\                   "
    echo "                                                                      "
    echo "                  Telecom Alarm Intelligence System                   "
    echo "    =================================================================="
    echo -e "${NC}"
}

# Graceful cleanup on Ctrl+C or exit
cleanup() {
    echo -e "\n${YELLOW}[cleanup] Stopping background processes and cleaning up...${NC}"
    
    # Kill background python processes
    if [ -n "$FASTAPI_PID" ]; then
        echo -e "${YELLOW}[cleanup] Stopping FastAPI Backend (PID $FASTAPI_PID)...${NC}"
        kill "$FASTAPI_PID" 2>/dev/null
    fi

    if [ -n "$PRODUCER_PID" ]; then
        echo -e "${YELLOW}[cleanup] Stopping Kafka Producer (PID $PRODUCER_PID)...${NC}"
        kill "$PRODUCER_PID" 2>/dev/null
    fi

    # Check if docker Kafka container is running, optionally stop it
    if [ "$RUNNING_KAFKA" = true ]; then
        echo -e "${YELLOW}[cleanup] Stopping Docker Kafka Container (alarmiq-kafka)...${NC}"
        docker stop alarmiq-kafka >/dev/null 2>&1
        docker rm alarmiq-kafka >/dev/null 2>&1
        echo -e "${GREEN}[cleanup] Docker Kafka Container stopped and removed.${NC}"
    fi

    echo -e "${GREEN}${BOLD}[cleanup] Done. Exiting. Goodbye!${NC}\n"
    exit 0
}

# Trap Ctrl+C (SIGINT) and SIGTERM
trap cleanup SIGINT SIGTERM

# Setup Python environment and install requirements
setup_environment() {
    echo -e "${BLUE}${BOLD}[setup] Checking Python Environment...${NC}"
    
    # Check if python3 exists
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}${BOLD}[error] Python 3 is not installed! Please install Python 3.10+ and try again.${NC}"
        exit 1
    fi

    # Create virtual environment if it doesn't exist
    if [ ! -d "$SCRIPT_DIR/.venv" ]; then
        echo -e "${YELLOW}[setup] Creating Python Virtual Environment (.venv)...${NC}"
        python3 -m venv "$SCRIPT_DIR/.venv" 2>/dev/null
        if [ $? -ne 0 ]; then
            echo -e "${YELLOW}[warning] Could not create virtual environment via venv. Using system Python.${NC}"
            PYTHON_CMD="python3"
            PIP_CMD="pip3"
        else
            echo -e "${GREEN}[setup] Virtual environment created successfully.${NC}"
            source "$SCRIPT_DIR/.venv/bin/activate"
            PYTHON_CMD="python"
            PIP_CMD="pip"
        fi
    else
        echo -e "${GREEN}[setup] Found existing Virtual Environment. Activating...${NC}"
        source "$SCRIPT_DIR/.venv/bin/activate"
        PYTHON_CMD="python"
        PIP_CMD="pip"
    fi

    # Install/upgrade requirements
    echo -e "${BLUE}[setup] Installing / verifying dependencies from requirements.txt...${NC}"
    $PIP_CMD install -r "$BACKEND_DIR/requirements.txt" --quiet
    if [ $? -ne 0 ]; then
        # If --break-system-packages is needed (newer Debian/Ubuntu)
        echo -e "${YELLOW}[setup] Standard pip install failed. Retrying with --break-system-packages...${NC}"
        $PIP_CMD install -r "$BACKEND_DIR/requirements.txt" --break-system-packages --quiet
    fi
    echo -e "${GREEN}${BOLD}[setup] ✅ Python environment is ready!${NC}\n"
}

# Wait for a port to become active
wait_for_port() {
    local host="$1"
    local port="$2"
    local timeout="$3"
    local count=0
    
    while ! nc -z "$host" "$port" >/dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
        if [ "$count" -ge "$timeout" ]; then
            return 1
        fi
    done
    return 0
}

# Option 1: Live Kafka Testing
run_kafka_mode() {
    print_header
    echo -e "${MAGENTA}${BOLD}=== Option 1: Live Kafka Testing ===${NC}\n"
    
    # 1. Verify Docker is installed and running
    echo -e "${BLUE}[kafka] Checking Docker daemon status...${NC}"
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}${BOLD}[error] Docker is not installed! Option 1 requires Docker to run the Zookeeperless KRaft Kafka Broker.${NC}"
        echo -e "${YELLOW}Please install Docker or use Option 2 (Just Simulation) instead.${NC}"
        read -n 1 -s -r -p "Press any key to return to menu..."
        return
    fi

    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}${BOLD}[error] Docker daemon is not running! Please start Docker and try again.${NC}"
        read -n 1 -s -r -p "Press any key to return to menu..."
        return
    fi
    echo -e "${GREEN}[kafka] Docker is available and running.${NC}"

    # 2. Run KRaft Kafka broker in Docker
    echo -e "${BLUE}[kafka] Preparing Apache Kafka KRaft Broker container...${NC}"
    
    # Stop and remove any old container if exists
    docker rm -f alarmiq-kafka >/dev/null 2>&1

    echo -e "${YELLOW}[kafka] Launching single-node Kafka KRaft cluster (apache/kafka:3.7.0)...${NC}"
    docker run -d \
        --name alarmiq-kafka \
        -p 9092:9092 \
        -e KAFKA_NODE_ID=1 \
        -e KAFKA_PROCESS_ROLES=broker,controller \
        -e KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093 \
        -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
        -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
        -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
        -e KAFKA_LISTEN_CONTROLLER=true \
        apache/kafka:3.7.0 >/dev/null
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}${BOLD}[error] Failed to start Docker Kafka container!${NC}"
        read -n 1 -s -r -p "Press any key to return to menu..."
        return
    fi
    
    RUNNING_KAFKA=true
    echo -e "${GREEN}[kafka] Container launched successfully. Waiting for broker to bind to port 9092...${NC}"
    
    # Wait up to 30 seconds for Kafka to be ready
    wait_for_port "localhost" "9092" 30
    if [ $? -ne 0 ]; then
        echo -e "${RED}${BOLD}[error] Kafka broker failed to start or bind to port 9092 in time!${NC}"
        cleanup
    fi
    echo -e "${GREEN}${BOLD}[kafka] ✅ Apache Kafka Broker is healthy and listening on port 9092!${NC}\n"

    # 3. Start FastAPI Backend with Kafka Consumer active
    echo -e "${BLUE}[backend] Launching FastAPI Backend in Kafka Consumer mode...${NC}"
    export KAFKA_BROKER="localhost:9092"
    
    cd "$BACKEND_DIR"
    $PYTHON_CMD -W ignore main.py > "$SCRIPT_DIR/fastapi_backend.log" 2>&1 &
    FASTAPI_PID=$!
    
    echo -e "${GREEN}[backend] FastAPI started in background (PID: $FASTAPI_PID). Log: fastapi_backend.log${NC}"
    echo -e "${YELLOW}[backend] Waiting for FastAPI to listen on port 8001...${NC}"
    
    wait_for_port "localhost" "8001" 15
    if [ $? -ne 0 ]; then
        echo -e "${RED}${BOLD}[error] FastAPI failed to start on port 8001! Check fastapi_backend.log for errors.${NC}"
        cleanup
    fi
    echo -e "${GREEN}${BOLD}[backend] ✅ FastAPI Backend is live on http://localhost:8001!${NC}\n"

    # 4. Start Kafka Producer to stream alarms
    echo -e "${BLUE}[producer] Launching Kafka Producer to stream alarms chronologically...${NC}"
    export PRODUCER_SLEEP="0.08" # comfortable live feed speed (80ms per alarm)
    
    $PYTHON_CMD "$BACKEND_DIR/kafka_producer.py" > "$SCRIPT_DIR/kafka_producer.log" 2>&1 &
    PRODUCER_PID=$!
    echo -e "${GREEN}[producer] Kafka Producer started in background (PID: $PRODUCER_PID). Log: kafka_producer.log${NC}"
    echo -e "${GREEN}${BOLD}[producer] ✅ Live Alarm stream is publishing to topic 'telecom-alarms'!${NC}\n"

    # 5. Open Web Browser
    echo -e "${BLUE}[browser] Opening AlarmIQ Web Dashboard...${NC}"
    if command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8001" >/dev/null 2>&1
    elif command -v gnome-open &> /dev/null; then
        gnome-open "http://localhost:8001" >/dev/null 2>&1
    elif command -v open &> /dev/null; then
        open "http://localhost:8001" >/dev/null 2>&1
    fi

    # Keep script alive and print stream status
    echo -e "${GREEN}${BOLD}=================================================================="
    echo -e "  🚀 ALARMIQ LIVE KAFKA TESTING IS ACTIVE!"
    echo -e "  Dashboard:     http://localhost:8001"
    echo -e "  Kafka Broker:  localhost:9092 (Topic: telecom-alarms)"
    echo -e "  "
    echo -e "  Tail FastAPI logs :  tail -f fastapi_backend.log"
    echo -e "  Tail Producer logs:  tail -f kafka_producer.log"
    echo -e "  "
    echo -e "  👉 Press [Ctrl+C] at any time to stop Kafka, producer, and FastAPI."
    echo -e "==================================================================${NC}"
    
    # Stay in foreground
    while true; do
        sleep 2
    done
}

# Option 2: Just Simulation
run_simulation_mode() {
    print_header
    echo -e "${MAGENTA}${BOLD}=== Option 2: Just Simulation ===${NC}\n"

    # Start FastAPI Backend in standard mode (no KAFKA_BROKER env variable)
    echo -e "${BLUE}[backend] Launching FastAPI Backend in standard simulation mode...${NC}"
    
    cd "$BACKEND_DIR"
    $PYTHON_CMD -W ignore main.py > "$SCRIPT_DIR/fastapi_backend.log" 2>&1 &
    FASTAPI_PID=$!
    
    echo -e "${GREEN}[backend] FastAPI started in background (PID: $FASTAPI_PID). Log: fastapi_backend.log${NC}"
    echo -e "${YELLOW}[backend] Waiting for FastAPI to listen on port 8001...${NC}"
    
    wait_for_port "localhost" "8001" 15
    if [ $? -ne 0 ]; then
        echo -e "${RED}${BOLD}[error] FastAPI failed to start on port 8001! Check fastapi_backend.log for errors.${NC}"
        cleanup
    fi
    echo -e "${GREEN}${BOLD}[backend] ✅ FastAPI Backend is live on http://localhost:8001!${NC}\n"

    # Open Web Browser
    echo -e "${BLUE}[browser] Opening AlarmIQ Web Dashboard...${NC}"
    if command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8001" >/dev/null 2>&1
    elif command -v gnome-open &> /dev/null; then
        gnome-open "http://localhost:8001" >/dev/null 2>&1
    elif command -v open &> /dev/null; then
        open "http://localhost:8001" >/dev/null 2>&1
    fi

    # Keep script alive and print stream status
    echo -e "${GREEN}${BOLD}=================================================================="
    echo -e "  🚀 ALARMIQ SIMULATION MODE IS ACTIVE!"
    echo -e "  Dashboard:     http://localhost:8001"
    echo -e "  "
    echo -e "  Tail FastAPI logs:  tail -f fastapi_backend.log"
    echo -e "  "
    echo -e "  👉 Press [Ctrl+C] at any time to stop the FastAPI backend."
    echo -e "==================================================================${NC}"
    
    # Stay in foreground
    while true; do
        sleep 2
    done
}

# Main Execution Flow
main() {
    print_header
    setup_environment
    
    while true; do
        print_header
        echo -e "  Please select how you would like to run the AlarmIQ app:\n"
        echo -e "  [1] ${BOLD}Live Kafka Testing${NC} (Requires Docker)"
        echo -e "      Spins up Zookeeperless Kafka, boots Python producer to"
        echo -e "      stream live alarms, backend consumes via consumer thread."
        echo ""
        echo -e "  [2] ${BOLD}Just Simulation${NC} (No Docker required)"
        echo -e "      Launches FastAPI backend instantly. Use the interactive"
        echo -e "      dashboard to play, pause, or throttle the alarm stream."
        echo ""
        echo -e "  [3] ${RED}Exit${NC}"
        echo ""
        read -p "  Enter choice [1-3]: " choice
        
        case $choice in
            1)
                run_kafka_mode
                ;;
            2)
                run_simulation_mode
                ;;
            3)
                echo -e "\n${GREEN}Goodbye!${NC}\n"
                exit 0
                ;;
            *)
                echo -e "\n${RED}Invalid choice! Please choose 1, 2, or 3.${NC}"
                sleep 1.5
                ;;
        esac
    done
}

main
