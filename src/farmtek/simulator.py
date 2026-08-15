"""FarmTek Polaris JAC Normal serial hardware simulator."""

import os
import pty
import random
import threading
import time
from typing import Optional


class PolarisSimulator:
    """Simulates a FarmTek Polaris timer transmitting JAC Normal finish strings."""

    def __init__(self):
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.slave_name: str = ""
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def setup_pty(self) -> str:
        """Create pseudo-terminal pair and return slave PTY device path."""
        self.master_fd, self.slave_fd = pty.openpty()
        self.slave_name = os.ttyname(self.slave_fd)
        return self.slave_name

    def send_finish_time(self, seconds: float, mode: str = "chrono"):
        """Send a JAC finish trigger for the specified elapsed seconds.
        
        Args:
            seconds: Elapsed run time.
            mode: 'chrono' (byte 0x82 / C), 'normal' (byte 0x80 / R), or 'extended'.
        """
        if self.master_fd is None:
            return

        # Format seconds as 6 digits milliseconds
        total_ms = int(round(seconds * 1000.0))
        formatted_ms = f"{total_ms:06d}"

        # Reverse digits according to JAC format specification
        reversed_digits = formatted_ms[::-1]

        # Choose prefix byte: 0x82 for Chrono ('C'), 0x80 for Normal ('R')
        prefix = b"\x82" if mode == "chrono" else b"\x80"

        # Construct frame: prefix byte + 6 reversed digits + carriage return (0x0D)
        frame = prefix + reversed_digits.encode('ascii') + b"\r\n"

        try:
            os.write(self.master_fd, frame)
        except OSError:
            pass

    def send_start_trigger(self, eye_number: int = 1):
        """Send a JAC Extended/Chrono start beam trigger (byte 0x83 / S)."""
        if self.master_fd is None:
            return
        frame = b"\x83000000\r\n"
        try:
            os.write(self.master_fd, frame)
        except OSError:
            pass

    def send_reset(self):
        """Send JAC Normal reset signal (R000000)."""
        if self.master_fd is None:
            return
        frame = b"\x80000000\r\n"
        try:
            os.write(self.master_fd, frame)
        except OSError:
            pass

    def start_auto_simulation(self, interval_range=(3.0, 7.0)):
        """Start auto-generating random realistic finish times for testing."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._auto_loop,
            args=(interval_range,),
            daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop simulator thread and close PTY descriptors."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except OSError:
                pass
            self.slave_fd = None

    def _auto_loop(self, interval_range):
        while self._running:
            time.sleep(random.uniform(*interval_range))
            # Generate run time between 35.000s and 65.000s
            simulated_time = round(random.uniform(35.0, 65.0), 3)
            self.send_finish_time(simulated_time)
