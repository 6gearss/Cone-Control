"""Serial port reader for FarmTek Polaris timer (9600 8-N-1 JAC Normal)."""

import serial
import serial.tools.list_ports
import threading
import time
from typing import Callable, Optional
from .logger import FarmTekLogger
from .jac_normal_parser import JACNormalParser, ParseResult


class SerialListener:
    """Manages thread-safe serial port connection and JAC Normal byte decoding."""

    def __init__(
        self,
        port_name: str,
        logger: FarmTekLogger,
        event_callback: Callable[[ParseResult], None],
        baudrate: int = 9600,
        read_timeout: float = 0.5
    ):
        self.port_name = port_name
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.logger = logger
        self.event_callback = event_callback

        self.serial_port: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def list_available_ports():
        """Returns list of active serial port names on the system."""
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]

    def start(self, custom_stream=None):
        """Start serial reading thread.
        
        Args:
            custom_stream: Optional file-like object (e.g. pty or mock stream) for simulation.
        """
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop,
            args=(custom_stream,),
            daemon=True
        )
        self._thread.start()
        self.logger.log_event(f"SERIAL LISTENER STARTED on port '{self.port_name}' @ {self.baudrate} 8-N-1")

    def stop(self):
        """Stop serial thread and close port."""
        self._running = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.serial_port = None
        if self._thread:
            self._thread.join(timeout=1.0)
        self.logger.log_event("SERIAL LISTENER STOPPED")

    def _open_port(self):
        """Open physical serial port."""
        try:
            self.serial_port = serial.Serial(
                port=self.port_name,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.read_timeout
            )
            self.logger.log_event(f"Connected to serial port '{self.port_name}' successfully.")
            return True
        except serial.SerialException as err:
            self.logger.log_event(f"Serial port connection error on '{self.port_name}': {err}")
            self.serial_port = None
            return False

    def _read_loop(self, custom_stream=None):
        """Main serial reading and byte processing loop."""
        buffer = bytearray()

        while self._running:
            stream = custom_stream or self.serial_port

            if stream is None and custom_stream is None:
                if not self._open_port():
                    time.sleep(2.0)  # Retry connection after delay
                    continue
                stream = self.serial_port

            try:
                if hasattr(stream, 'read'):
                    chunk = stream.read(64)
                else:
                    time.sleep(0.1)
                    continue

                if not chunk:
                    continue

                for b in chunk:
                    buffer.append(b)
                    # 0x0D (\r) or 0x0A (\n) marks end of JAC line frame
                    if b in (0x0D, 0x0A):
                        if buffer:
                            try:
                                self._process_raw_frame(bytes(buffer))
                            except Exception as err:
                                self.logger.log_event(f"FRAME PROCESSING ERROR: {err}")
                            finally:
                                buffer.clear()
            except (serial.SerialException, OSError) as err:
                self.logger.log_event(f"Serial read error: {err}. Reconnecting in 2s...")
                if self.serial_port and self.serial_port.is_open:
                    try:
                        self.serial_port.close()
                    except Exception:
                        pass
                self.serial_port = None
                time.sleep(2.0)
            except Exception as err:
                self.logger.log_event(f"UNHANDLED SERIAL THREAD ERROR: {err}")
                time.sleep(0.5)

    def _process_raw_frame(self, raw_bytes: bytes):
        """Decode raw bytes converting high-bit JAC prefixes (0x80..0x86) and ASCII (A/B/C/R/S)."""
        chars = []
        for b in raw_bytes:
            if b in (0x80, 0x86):
                chars.append('R')
            elif b == 0x81:
                chars.append('A')
            elif b == 0x83:
                chars.append('S')
            elif 0x20 <= b <= 0x7E:
                chars.append(chr(b))

        decoded_str = "".join(chars).strip()

        if not decoded_str:
            return

        # Log raw serial traffic
        self.logger.log_raw_serial(raw_bytes, decoded_str)

        # Parse JAC Normal format
        event = JACNormalParser.parse_line(decoded_str, raw_bytes)

        # Forward to queue manager / application callback
        if self.event_callback:
            self.event_callback(event)
