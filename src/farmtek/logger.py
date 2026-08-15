"""Comprehensive logging system for FarmTek serial traffic and event recording."""

import csv
from datetime import datetime
from pathlib import Path
import threading
from typing import Callable, List, Optional


class FarmTekLogger:
    """Manages raw serial byte logging, event logging, and CSV run reporting."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.raw_log_path = self.log_dir / "raw_serial.log"
        self.events_log_path = self.log_dir / "events.log"
        self.runs_csv_path = self.log_dir / "runs.csv"

        self._lock = threading.Lock()

        # Callbacks to notify UI when new traffic or events arrive
        self.raw_listeners: List[Callable[[str, bytes, str], None]] = []
        self.event_listeners: List[Callable[[str], None]] = []

        self._init_csv()

    def _init_csv(self):
        """Ensure CSV log header exists."""
        if not self.runs_csv_path.exists():
            with self._lock:
                with open(self.runs_csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Timestamp",
                        "CarNumber",
                        "DriverName",
                        "RawTimeSeconds",
                        "Penalties",
                        "PenaltySeconds",
                        "FinalTimeSeconds",
                        "FinalTimeFormatted",
                        "Status"
                    ])

    def log_raw_serial(self, raw_bytes: bytes, decoded_str: str):
        """Log raw serial bytes received from serial port."""
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        hex_str = raw_bytes.hex(' ')

        log_entry = f"[{timestamp_str}] HEX: {hex_str:<24} | ASCII: {repr(decoded_str)}\n"

        with self._lock:
            with open(self.raw_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)

        # Notify active raw stream listeners
        for listener in self.raw_listeners:
            try:
                listener(timestamp_str, raw_bytes, decoded_str)
            except Exception:
                pass

    def log_event(self, event_description: str):
        """Log a parsed timing event or state transition."""
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp_str}] {event_description}\n"

        with self._lock:
            with open(self.events_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)

        for listener in self.event_listeners:
            try:
                listener(log_entry.strip())
            except Exception:
                pass

    def log_run(
        self,
        car_number: str,
        driver_name: str,
        raw_time_seconds: float,
        penalty_count: int,
        status: str = "OFFICIAL"
    ):
        """Log a completed run to CSV."""
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 2.0 seconds per cone penalty
        penalty_seconds = penalty_count * 2.0
        final_seconds = raw_time_seconds + penalty_seconds
        final_formatted = f"{final_seconds:.3f}" if status != "DNF" else "DNF"

        row = [
            timestamp_str,
            car_number,
            driver_name,
            f"{raw_time_seconds:.3f}",
            penalty_count,
            f"{penalty_seconds:.1f}",
            f"{final_seconds:.3f}",
            final_formatted,
            status
        ]

        with self._lock:
            with open(self.runs_csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(row)

        self.log_event(
            f"RUN RECORDED - Car: {car_number} ({driver_name}) | Raw: {raw_time_seconds:.3f}s | "
            f"Cones: {penalty_count} | Final: {final_formatted} ({status})"
        )
