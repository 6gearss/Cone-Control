"""Rich terminal interface for real-time traffic monitoring and track management."""

from datetime import datetime
import threading
import time
from typing import List

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .logger import FarmTekLogger
from .track_queue import TrackQueueManager, CompletedRun


class TerminalUI:
    """Live Rich terminal monitor for FarmTek serial traffic and autocross queue."""

    def __init__(self, logger: FarmTekLogger, queue_mgr: TrackQueueManager):
        self.logger = logger
        self.queue_mgr = queue_mgr
        self.console = Console()

        self.raw_logs_history: List[str] = []
        self.max_raw_history = 12

        self.event_logs_history: List[str] = []
        self.max_event_history = 8

        # Subscribe to logger updates
        self.logger.raw_listeners.append(self._on_raw_serial)
        self.logger.event_listeners.append(self._on_event)

    def _on_raw_serial(self, timestamp: str, raw_bytes: bytes, decoded_str: str):
        hex_repr = raw_bytes.hex(" ")
        line = f"[{timestamp}] HEX: {hex_repr:<22} | DECODED: {decoded_str}"
        self.raw_logs_history.append(line)
        if len(self.raw_logs_history) > self.max_raw_history:
            self.raw_logs_history.pop(0)

    def _on_event(self, event_str: str):
        self.event_logs_history.append(event_str)
        if len(self.event_logs_history) > self.max_event_history:
            self.event_logs_history.pop(0)

    def generate_layout(self) -> Layout:
        """Construct current terminal UI layout."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3),
        )

        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        layout["left"].split_column(
            Layout(name="traffic", ratio=1),
            Layout(name="events", ratio=1),
        )

        layout["right"].split_column(
            Layout(name="active_track", ratio=1),
            Layout(name="completed_runs", ratio=1),
        )

        # Header Panel
        header_text = Text(
            "🏁  CONE CONTROL - JAC CHRONO (9600 8-N-1) 🏁",
            style="bold white on blue",
            justify="center",
        )
        layout["header"].update(Panel(header_text))

        # Serial Traffic Stream Panel
        traffic_table = Table(show_header=True, header_style="bold magenta", expand=True)
        traffic_table.add_column("Live Serial Byte Stream")
        for entry in self.raw_logs_history[-10:]:
            traffic_table.add_row(entry)
        layout["traffic"].update(Panel(traffic_table, title="[bold yellow]📡 Serial Traffic Monitor (JAC Chrono 9600 8-N-1)"))

        # Event Log Panel
        event_table = Table(show_header=True, header_style="bold cyan", expand=True)
        event_table.add_column("Parsed Events & Messages")
        for entry in self.event_logs_history[-6:]:
            event_table.add_row(entry)
        layout["events"].update(Panel(event_table, title="[bold cyan]📋 Parsed Event Log (JAC Chrono)"))

        # Active Track Monitor Panel
        active_table = Table(show_header=True, header_style="bold green", expand=True)
        active_table.add_column("Pos", style="dim", width=4)
        active_table.add_column("Car #", style="bold white")
        active_table.add_column("Driver Name", style="cyan")
        active_table.add_column("Cones", style="bold yellow")

        if not self.queue_mgr.active_queue:
            active_table.add_row("-", "No cars on track", "-", "-")
        else:
            for idx, car in enumerate(self.queue_mgr.active_queue, start=1):
                pos_str = f"{idx} [bold red]DNF[/bold red]" if getattr(car, 'is_dnf', False) else f"{idx}"
                cones_str = str(getattr(car, 'penalty_cones', 0))
                active_table.add_row(pos_str, f"{car.car_number}", car.driver_name, cones_str)



        track_title = f"[bold green]🏁 Active Track Queue ({len(self.queue_mgr.active_queue)}/{self.queue_mgr.max_active_cars} Cars Max)"
        layout["active_track"].update(Panel(active_table, title=track_title))

        # Completed Runs Table Panel
        runs_table = Table(show_header=True, header_style="bold white", expand=True)
        runs_table.add_column("Run #", width=6)
        runs_table.add_column("Car #")
        runs_table.add_column("Raw Time")
        runs_table.add_column("Cones")
        runs_table.add_column("Final Time", style="bold yellow")
        runs_table.add_column("Status")

        if not self.queue_mgr.completed_runs:
            runs_table.add_row("-", "-", "-", "-", "-", "Waiting for finishes...")
        else:
            for run in self.queue_mgr.completed_runs[-6:]:
                status_color = "green" if run.status == "OFFICIAL" else "red"
                runs_table.add_row(
                    str(run.run_id),
                    f"{run.car_number}",
                    f"{run.raw_time_seconds:.3f}s",
                    str(run.penalty_cones),
                    run.final_time_formatted + ("s" if run.status != "DNF" else ""),
                    f"[{status_color}]{run.status}[/{status_color}]"
                )

        layout["completed_runs"].update(Panel(runs_table, title="[bold white]🏆 Completed Runs"))

        # Footer Panel
        zero_runs_count = len(self.queue_mgr.get_zero_run_drivers()) if self.queue_mgr else 0
        footer_text = Text(
            f"Press [q] + Enter to Quit | Drivers with 0 Runs: {zero_runs_count} | Web UI Controls: http://localhost:8000",
            style="bold white on dark_blue",
            justify="center",
        )
        layout["footer"].update(Panel(footer_text))

        return layout

    def run_live(self, simulator=None):
        """Run live serial monitoring rendering loop with quit-only terminal input."""
        import sys
        import select

        with Live(self.generate_layout(), console=self.console, refresh_per_second=4) as live:
            try:
                while True:
                    live.update(self.generate_layout())

                    # Check for quit input from terminal
                    if sys.stdin in select.select([sys.stdin], [], [], 0.25)[0]:
                        cmd = sys.stdin.readline().strip().lower()
                        if cmd in ("q", "quit", "exit"):
                            break
            except KeyboardInterrupt:
                pass
