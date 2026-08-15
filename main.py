"""FarmTek Polaris Autocross Timing System Entry Point."""

import argparse
import os
import sys
import threading

import uvicorn

from src.farmtek.logger import FarmTekLogger
from src.farmtek.track_queue import TrackQueueManager
from src.farmtek.serial_listener import SerialListener
from src.farmtek.simulator import PolarisSimulator
from src.farmtek.terminal_ui import TerminalUI
from src.farmtek.driver_db import DriverDatabase
from src.app import app, init_app


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cone Control Autocross Timing System (JAC Chrono 9600 8-N-1)"
    )
    parser.add_argument(
        "--port", "-p",
        
        type=str,
        default=None,
        help="Serial port device path (e.g. /dev/tty.usbserial-BG01LX0O)"
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=9600,
        help="Serial baud rate (default: 9600)"
    )
    parser.add_argument(
        "--simulate", "-s",
        action="store_true",
        help="Run using built-in FarmTek Polaris timer simulator"
    )
    parser.add_argument(
        "--max-cars",
        type=int,
        default=4,
        help="Maximum concurrent cars allowed on track (2 to 4, default: 4)"
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="logs",
        help="Directory to save raw serial logs and CSV run records (default: logs)"
    )
    parser.add_argument(
        "--drivers-csv",
        type=str,
        default="20260622-Autocross-AxWareEventExport.csv",
        help="AxWare export CSV file containing driver roster"
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8000,
        help="Port for Web UI & REST API (default: 8000)"
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable web UI server and run in terminal-only mode"
    )
    parser.add_argument(
        "--show-zero-runs",
        action="store_true",
        help="Print all drivers from the database who have completed 0 runs and exit"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 1. Initialize Logger
    logger = FarmTekLogger(log_dir=args.log_dir)
    logger.log_event("=== FARMTEK POLARIS TIMING SYSTEM INITIALIZING ===")

    # 2. Load Driver Database
    driver_db = DriverDatabase(args.drivers_csv)
    if driver_db.drivers:
        logger.log_event(f"DRIVER DATABASE LOADED - Loaded {len(driver_db.drivers)} drivers from '{args.drivers_csv}'.")

    # 3. Initialize Queue Manager
    queue_mgr = TrackQueueManager(logger=logger, max_active_cars=args.max_cars, driver_db=driver_db)

    if args.show_zero_runs:
        zero_drivers = queue_mgr.get_zero_run_drivers()
        print(f"\n=== DRIVERS WITH 0 RUNS ({len(zero_drivers)} Total) ===")
        for d in zero_drivers:
            car_str = f"#{d.car_number:<6}"
            class_str = f"[{d.class_name}]" if d.class_name else ""
            vehicle = f"({d.car_model} - {d.car_color})" if (d.car_model or d.car_color) else ""
            print(f"Car {car_str} {d.full_name:<25} {class_str:<12} {vehicle}")
        sys.exit(0)

    simulator = None
    serial_listener = None

    # 3. Setup Simulator or Serial Port
    if args.simulate:
        logger.log_event("SIMULATION MODE ACTIVATED - Creating PTY serial simulator...")
        simulator = PolarisSimulator()
        pty_port = simulator.setup_pty()

        serial_listener = SerialListener(
            port_name=pty_port,
            logger=logger,
            event_callback=queue_mgr.process_timer_event,
            baudrate=args.baud
        )
        serial_listener.start()
        simulator.start_auto_simulation(interval_range=(4.0, 8.0))
    else:
        port_name = args.port
        if not port_name:
            ports = SerialListener.list_available_ports()
            if ports:
                port_name = ports[0]
                print(f"[*] Auto-selected available serial port: {port_name}")
            else:
                port_name = "/dev/tty.usbserial-BG01LX0O"
                print(f"[!] No active serial ports found. Defaulting to {port_name}")

        serial_listener = SerialListener(
            port_name=port_name,
            logger=logger,
            event_callback=queue_mgr.process_timer_event,
            baudrate=args.baud
        )
        serial_listener.start()

    # 4. Initialize Web Application (if enabled)
    if not args.no_web:
        init_app(logger=logger, queue_mgr=queue_mgr, simulator=simulator)
        web_thread = threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": app,
                "host": "0.0.0.0",
                "port": args.web_port,
                "log_level": "critical"
            },
            daemon=True
        )
        web_thread.start()
        logger.log_event(f"WEB SERVER RUNNING at http://localhost:{args.web_port}")

    # 5. Pre-stage sample cars if in simulation mode
    if args.simulate:
        queue_mgr.dispatch_car("99", "Fast Driver")
        queue_mgr.dispatch_car("42", "Speed Demon")

    # 6. Run Live Terminal UI
    terminal_ui = TerminalUI(logger=logger, queue_mgr=queue_mgr)
    try:
        terminal_ui.run_live(simulator=simulator)
    finally:
        logger.log_event("SHUTTING DOWN TIMING SYSTEM...")
        if serial_listener:
            serial_listener.stop()
        if simulator:
            simulator.stop()


if __name__ == "__main__":
    main()
