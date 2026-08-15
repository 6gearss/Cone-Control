# 🏁 Cone Control — Autocross Timing & Queue Management System

**Cone Control** is a real-time, multi-car autocross timing and queue management platform designed for **FarmTek Polaris / JAC Chrono** hardware (`9600 8-N-1`). It combines a high-reliability hardware serial telemetry parser, dynamic multi-car track queue manager, an interactive live terminal interface, and a real-time FastAPI + WebSocket web dashboard for race control, timing display, and CSV export.

---

## ✨ Key Features

- ⏱️ **Hardware Integration**: Real-time RS-232 serial telemetry parser for FarmTek Polaris / JAC Chrono 9600 baud timers (handles Finish events, Reset events, and frame checksum integrity validation).
- 🏎️ **Multi-Car Track Queue**: Track up to 4 concurrent vehicles on course with staging queues, active track dispatch, automated finish matching, and heat run tracking.
- 📋 **Driver Roster Integration**: Load driver databases directly from AxWare CSV exports (`--drivers-csv`) with automatic car # lookup for driver name, class, vehicle model, and color.
- 🟡 **Penalty & Run Management**: Dynamic cone penalty tracking (+2.0s default, configurable), DNF handling, fault/re-stage controls, run locking, and heat finalization.
- 🖥️ **Real-Time Web Dashboard & WebSockets**: Modern web UI powered by FastAPI and WebSockets for live timing updates, queue control, starter auto-launch, raw serial traffic inspection, and settings adjustments.
- 📊 **CSV Export**: Generate clean, event-formatted CSV reports for official timing & scoring.
- 💻 **Live Terminal UI**: High-contrast, interactive terminal interface built with `rich` for command-center operation straight from the terminal.
- 🎮 **Built-in Hardware Simulator**: PTY pseudo-terminal simulator (`--simulate`) for full end-to-end testing without physical hardware connected.

---

## 📁 Repository Structure

```
Cone Control/
├── main.py                     # Main CLI entry point & system launcher
├── requirements.txt            # Python dependencies
├── src/
│   ├── app.py                  # FastAPI server, REST routes, and WebSocket hub
│   ├── static/
│   │   └── index.html          # Web UI dashboard
│   └── farmtek/
│       ├── driver_db.py        # AxWare CSV driver database loader & lookup
│       ├── jac_normal_parser.py# Serial frame parser for JAC Chrono protocol
│       ├── logger.py           # Logging engine for raw serial traffic & system events
│       ├── serial_listener.py  # PySerial listener thread with auto-reconnect
│       ├── simulator.py        # PTY pseudo-terminal FarmTek Polaris timer simulator
│       ├── terminal_ui.py      # Rich terminal UI renderer
│       └── track_queue.py      # Core track queue manager & run state engine
└── tests/
    ├── test_parser.py          # Unit tests for JAC Normal serial parser
    └── test_queue.py           # Unit tests for track queue manager & driver lookup
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.10 or higher
- Virtual environment (recommended)

### 2. Installation

Clone the repository and install the required dependencies:

```bash
# Clone the repository
git clone https://github.com/your-org/cone-control.git
cd "Cone Control"

# Create & activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 🏃 Running the Application

### 🧪 Simulation Mode (No hardware required)

Run the full system using the built-in PTY serial simulator:

```bash
python3 main.py --simulate
```

This launches:
1. Built-in Polaris timer simulator (generating simulated finish events).
2. FastAPI Web UI at **`http://localhost:8000`**.
3. Live Terminal UI in your console.

### 🔌 Live Hardware Mode

Connect your FarmTek Polaris timer via USB-to-Serial RS-232 adapter:

```bash
# Specify serial port explicitly
python3 main.py --port /dev/tty.usbserial-BG01LX0O --baud 9600

# Or let the system auto-detect the available serial port
python3 main.py
```

### 🏎️ Custom Driver Roster CSV

Specify an AxWare CSV export file to automatically populate the driver roster:

```bash
python3 main.py --drivers-csv 20260622-Autocross-AxWareEventExport.csv
```

---

## ⚙️ Command-Line Arguments

| Flag | Short | Default | Description |
| --- | --- | --- | --- |
| `--port` | `-p` | Auto-detect | Serial port device path (e.g. `/dev/tty.usbserial-XXXX`) |
| `--baud` | `-b` | `9600` | Serial baud rate |
| `--simulate` | `-s` | `False` | Run using built-in FarmTek Polaris timer simulator |
| `--max-cars` | | `4` | Maximum concurrent cars allowed on track (2–4) |
| `--log-dir` | | `logs` | Directory for raw serial logs & event logs |
| `--drivers-csv` | | `20260622-Autocross-AxWareEventExport.csv` | AxWare CSV roster file |
| `--web-port` | | `8000` | Port for FastAPI web server & WebSocket API |
| `--no-web` | | `False` | Run in terminal-only mode (disable web server) |
| `--show-zero-runs` | | `False` | List drivers with 0 runs completed and exit |

---

## 🌐 Web UI & API Endpoints

Access the Web Dashboard at **`http://localhost:8000`**.

### Key REST API Routes

- `GET /api/state` — Returns full live system state (active queue, staging queue, completed runs, settings).
- `POST /api/stage` — Stage a car (`{"car_number": "42", "driver_name": "Speed Demon"}`).
- `DELETE /api/stage/{car_number}` — Remove a car from staging.
- `POST /api/starter_launch` — Starter release: moves top staged car onto track.
- `POST /api/dispatch` — Dispatch a car onto track.
- `POST /api/active_cone` — Adjust cone penalties for active cars.
- `POST /api/dnf_active` — Flag an active car as DNF.
- `POST /api/fault` — Issue fault / re-stage car.
- `POST /api/swap_active` — Reorder active track queue.
- `POST /api/update_run` — Edit completed run penalties, status, or driver info.
- `POST /api/finalize_heat` — Lock completed runs for current heat.
- `GET /api/export_csv` — Download event results as a formatted CSV file.
- `GET /api/zero_runs` — List all rostered drivers who haven't completed any runs.

### WebSocket Endpoint

- `WS /ws` — Real-time bidirectional WebSocket stream broadcasting live queue updates and raw serial telemetry frames.

---

## 🧪 Running Tests

Execute the test suite using `pytest`:

```bash
python3 -m pytest
```

Tests verify:
- Frame integrity and timestamp parsing in [`JACNormalParser`](file:///Users/dlambert/Projects/Cone%20Control/src/farmtek/jac_normal_parser.py).
- Multi-car queue transitions, staging capacity, cone adjustments, and run locking in [`TrackQueueManager`](file:///Users/dlambert/Projects/Cone%20Control/src/farmtek/track_queue.py).
- AxWare CSV parsing and driver lookups in [`DriverDatabase`](file:///Users/dlambert/Projects/Cone%20Control/src/farmtek/driver_db.py).

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
