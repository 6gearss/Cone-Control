"""JAC Normal protocol parser for FarmTek Polaris timing systems.

JAC Normal Format Specifications:
- Baud: 9600, 8-N-1
- Byte 0x80 maps to 'R'
- Finish time format: Rdddddd\\r (6 digits representing elapsed time in REVERSE order)
- Reset format: R000000\\r
"""

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Optional, Union


@dataclass
class FinishEvent:
    """Represents a validated finish line trigger from the timer."""
    raw_message: str
    time_seconds: float
    time_formatted: str  # e.g., "207.100"
    timestamp: datetime = field(default_factory=datetime.now)
    is_reset: bool = False


@dataclass
class StartEvent:
    """Represents a validated start beam (Eye #1) trigger from the timer."""
    raw_message: str
    eye_number: int = 1
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class EyeBEvent:
    """Represents Eye B (Finish Beam) TOD timestamp frame from Chrono mode."""
    raw_message: str
    eye_number: int = 2
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ResetEvent:
    """Represents a timer reset event (R000000)."""
    raw_message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class InvalidFrameEvent:
    """Represents unparseable or corrupted serial payload."""
    raw_bytes: bytes
    raw_string: str
    error: str
    timestamp: datetime = field(default_factory=datetime.now)


ParseResult = Union[FinishEvent, StartEvent, EyeBEvent, ResetEvent, InvalidFrameEvent]


class JACNormalParser:
    """Parser for FarmTek Polaris JAC Normal serial protocol messages."""

    # Regex matching 'R', 'S', 'C', 'A', or 'B' followed by 6 digits (Normal, Extended & Chrono formats)
    FRAME_PATTERN = re.compile(r"^([RSCAB])(\d{6})$")

    @classmethod
    def parse_line(cls, line: str, raw_bytes: Optional[bytes] = None) -> ParseResult:
        """Parse a single line of decoded ASCII text.
        
        Args:
            line: Stripped or un-stripped ASCII string received from serial.
            raw_bytes: Optional raw byte payload before ASCII conversion.
            
        Returns:
            FinishEvent, StartEvent, EyeBEvent, ResetEvent, or InvalidFrameEvent.
        """
        cleaned = line.strip("\r\n\0 ").upper()

        if not cleaned:
            return InvalidFrameEvent(
                raw_bytes=raw_bytes or b"",
                raw_string=line,
                error="Empty serial frame"
            )

        # Check for simple Start event string like "START" or "S"
        if cleaned in ("START", "S", "S1", "S 1"):
            return StartEvent(raw_message=cleaned, eye_number=1)

        match = cls.FRAME_PATTERN.match(cleaned)
        if not match:
            return InvalidFrameEvent(
                raw_bytes=raw_bytes or line.encode('utf-8', errors='replace'),
                raw_string=line,
                error=f"Frame '{cleaned}' does not match JAC format (R/S/C/A/B + 6 digits)"
            )

        prefix, digits = match.groups()

        # Handle Start Event: Sdddddd or Adddddd (Eye A / Eye #1 timestamp)
        if prefix in ("S", "A"):
            return StartEvent(raw_message=cleaned, eye_number=1)

        # Handle Eye B (Finish Beam) timestamp event: Bdddddd
        # Note: Bdddddd in JAC Chrono is the Eye B TOD timestamp.
        # The actual ELAPSED RUN TIME is sent in the back-to-back Rdddddd / Cdddddd frame!
        if prefix == "B":
            return EyeBEvent(raw_message=cleaned, eye_number=2)

        # Check for Reset Event: R000000 / B000000 / C000000
        if digits == "000000":
            return ResetEvent(raw_message=cleaned)

        # Reverse the 6 digits to obtain chronological digits (seconds & fractions)
        reversed_digits = digits[::-1]  # e.g. "001702" -> "207100"

        # Calculate time in seconds
        try:
            ms_total = int(reversed_digits)
            time_seconds = ms_total / 1000.0
            time_formatted = f"{time_seconds:.3f}"

            return FinishEvent(
                raw_message=cleaned,
                time_seconds=time_seconds,
                time_formatted=time_formatted,
                is_reset=False
            )
        except ValueError as exc:
            return InvalidFrameEvent(
                raw_bytes=raw_bytes or line.encode('utf-8', errors='replace'),
                raw_string=line,
                error=f"Failed to decode digits '{reversed_digits}': {exc}"
            )
