"""Track queue manager supporting 2-4 concurrent cars on track for autocross."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional
from .logger import FarmTekLogger
from .jac_normal_parser import FinishEvent, StartEvent, EyeBEvent, ResetEvent, ParseResult, JACNormalParser
from .driver_db import DriverDatabase, DriverEntry


@dataclass
class TrackCar:
    """Represents a car staged or actively driving on track."""
    car_number: str
    driver_name: str = "Driver"
    is_on_track: bool = False
    dispatched_at: datetime = field(default_factory=datetime.now)
    penalty_cones: int = 0
    is_dnf: bool = False
    dnf_run_id: Optional[int] = None



@dataclass
class CompletedRun:
    """Represents a finished autocross run."""
    run_id: int
    car_number: str
    driver_name: str
    raw_time_seconds: float
    car_run_number: int = 1
    penalty_cones: int = 0
    cone_penalty_seconds: float = 2.0
    status: str = "OFFICIAL"  # OFFICIAL, DNF, RERUN
    is_locked: bool = False
    completed_at: datetime = field(default_factory=datetime.now)

    @property
    def final_time_seconds(self) -> float:
        return self.raw_time_seconds + (self.penalty_cones * self.cone_penalty_seconds)

    @property
    def final_time_formatted(self) -> str:
        if self.status == "DNF":
            return "DNF"
        if self.status == "RERUN":
            return "RE-RUN"
        return f"{self.final_time_seconds:.3f}"


class TrackQueueManager:
    """Manages active cars on track (max 2-4 concurrent), staging queue, and run limits."""

    def __init__(
        self,
        logger: FarmTekLogger,
        max_active_cars: int = 4,
        auto_launch: bool = True,
        max_runs_per_car: int = 3,
        driver_db: Optional[DriverDatabase] = None,
        event_name: str = "Autocross Event",
        event_date: Optional[str] = None,
        cone_penalty_seconds: float = 2.0,
        num_heats: int = 1,
        runs_per_heat: int = 3,
    ):
        self.logger = logger
        self.max_active_cars = max(2, min(4, max_active_cars))
        self.auto_launch = auto_launch
        self.driver_db = driver_db
        self.is_heat_finalized: bool = False

        self.event_name: str = event_name
        self.event_date: str = event_date or datetime.now().strftime("%Y-%m-%d")
        self.cone_penalty_seconds: float = cone_penalty_seconds
        self.num_heats: int = max(1, num_heats)
        self.runs_per_heat: int = max(1, runs_per_heat)
        self.max_runs_per_car: int = self.num_heats * self.runs_per_heat


        self.staging_queue: List[TrackCar] = []
        self.active_queue: List[TrackCar] = []
        self.completed_runs: List[CompletedRun] = []

        self._run_counter = 1
        self.listeners: List[Callable[[], None]] = []

    def notify_listeners(self):
        """Notify UI listeners of queue updates."""
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                pass

    def get_run_count(self, car_number: str) -> int:
        """Calculate how many official/counted runs a car has completed so far."""
        clean_num = car_number.strip().lstrip('#')
        return sum(
            1 for r in self.completed_runs
            if r.car_number.strip().lstrip('#') == clean_num and r.status in ("OFFICIAL", "PROVISIONAL", "DNF")
        )

    def get_zero_run_drivers(self) -> List[DriverEntry]:
        """Get all drivers from the driver database who have 0 completed runs."""
        if not self.driver_db:
            return []
        return [
            driver for driver in self.driver_db.get_all_drivers()
            if self.get_run_count(driver.car_number) == 0
        ]


    def _resolve_driver_name(self, car_number: str, provided_name: str) -> str:
        clean_name = provided_name.strip()
        if (not clean_name or clean_name.lower() == "driver") and self.driver_db:
            entry = self.driver_db.lookup(car_number)
            if entry and entry.full_name:
                return entry.full_name
        return clean_name or "Driver"

    def add_to_staging(self, car_number: str, driver_name: str = "Driver", at_front: bool = False) -> bool:
        """Add a car to the staging queue and auto-launch if track has room."""
        clean_car = car_number.strip().lstrip('#')

        # Reject duplicate: car already in staging or on active track
        already_staged = any(c.car_number == clean_car for c in self.staging_queue)
        already_active = any(c.car_number == clean_car for c in self.active_queue)
        if already_staged or already_active:
            location = "staging" if already_staged else "active track"
            self.logger.log_event(
                f"QUEUE REJECTED - Car #{clean_car} is already in {location}."
            )
            return False

        # Reject if track queue has reached maximum capacity (max 4 cars)
        if len(self.active_queue) + len(self.staging_queue) >= self.max_active_cars:
            self.logger.log_event(
                f"QUEUE REJECTED - Track full! Maximum {self.max_active_cars} cars allowed on track."
            )
            return False

        resolved_name = self._resolve_driver_name(clean_car, driver_name)
        car = TrackCar(car_number=clean_car, driver_name=resolved_name)

        if at_front:
            self.staging_queue.insert(0, car)
        else:
            self.staging_queue.append(car)

        runs_done = self.get_run_count(clean_car)
        self.logger.log_event(
            f"QUEUE - Staged car #{car.car_number} ({car.driver_name}) [Run {runs_done + 1} of {self.max_runs_per_car}]"
        )

        # Auto-launch onto active track if enabled and room exists
        if self.auto_launch and len(self.active_queue) < self.max_active_cars:
            self._dispatch_next_staged()
        else:
            self.notify_listeners()

        return True

    def starter_launch(self) -> bool:
        """Starter action: Take top staged car and launch it onto the active track."""
        return self._dispatch_next_staged()

    def _dispatch_next_staged(self) -> bool:
        """Helper to pop top car from staging and dispatch to active track."""
        if not self.staging_queue:
            return False

        if len(self.active_queue) >= self.max_active_cars:
            self.logger.log_event(
                f"STARTER REJECTED - Track full ({len(self.active_queue)}/{self.max_active_cars} active cars)."
            )
            return False

        car = self.staging_queue.pop(0)
        car.dispatched_at = datetime.now()
        self.active_queue.append(car)
        runs_done = self.get_run_count(car.car_number)

        self.logger.log_event(
            f"STARTER LAUNCH - Car #{car.car_number} on track (Run {runs_done + 1}/{self.max_runs_per_car}). "
            f"Active: {len(self.active_queue)}/{self.max_active_cars}"
        )
        self.notify_listeners()
        return True

    def dispatch_car(self, car_number: Optional[str] = None, driver_name: str = "Driver") -> bool:
        """Dispatch a car onto the active track queue."""
        if len(self.active_queue) >= self.max_active_cars:
            self.logger.log_event(
                f"QUEUE REJECTED - Track full! Maximum {self.max_active_cars} cars allowed concurrently."
            )
            return False

        if car_number:
            clean_car = car_number.strip().lstrip('#')
            staged_match = next((c for c in self.staging_queue if c.car_number == clean_car), None)
            if staged_match:
                self.staging_queue.remove(staged_match)
                car = staged_match
                car.dispatched_at = datetime.now()
            else:
                resolved_name = self._resolve_driver_name(clean_car, driver_name)
                car = TrackCar(car_number=clean_car, driver_name=resolved_name)
        else:
            if not self.staging_queue:
                car = TrackCar(car_number=f"Car-{len(self.completed_runs) + len(self.active_queue) + 1}")
            else:
                car = self.staging_queue.pop(0)
                car.dispatched_at = datetime.now()

        self.active_queue.append(car)
        runs_done = self.get_run_count(car.car_number)
        self.logger.log_event(
            f"DISPATCH - Car #{car.car_number} on track (Run {runs_done + 1}/{self.max_runs_per_car}). "
            f"Active: {len(self.active_queue)}/{self.max_active_cars}"
        )
        self.notify_listeners()
        return True

    def issue_fault(self, car_number: str, restage: bool = True) -> bool:
        """Issue a timing Fault/Re-Run for an active car on track or staged car."""
        clean_num = car_number.strip()
        match = next((c for c in self.active_queue if c.car_number == clean_num), None)

        if match:
            self.active_queue.remove(match)

            runs_done_before = self.get_run_count(match.car_number)
            run = CompletedRun(
                run_id=self._run_counter,
                car_number=match.car_number,
                driver_name=match.driver_name,
                raw_time_seconds=0.0,
                car_run_number=runs_done_before + 1,
                penalty_cones=0,
                status="RERUN"
            )
            self._run_counter += 1
            self.completed_runs.append(run)
            self.logger.log_run(match.car_number, match.driver_name, 0.0, 0, status="RERUN")
            self.logger.log_event(f"TIMING FAULT - Issued Re-Run for Car #{match.car_number}.")

            # Re-stage car at front of staging queue for driver's re-run
            if restage:
                self.add_to_staging(match.car_number, match.driver_name, at_front=True)
            else:
                self.notify_listeners()
            return True

        return False

    def delete_active_car(self, car_number: str) -> bool:
        """Scratch/delete an active car from the track queue without recording a run."""
        match = next((c for c in self.active_queue if c.car_number == car_number), None)
        if match:
            self.active_queue.remove(match)
            self.logger.log_event(f"SCRATCHED - Removed active Car #{car_number} from track queue.")
            if self.auto_launch and self.staging_queue and len(self.active_queue) < self.max_active_cars:
                self._dispatch_next_staged()
            else:
                self.notify_listeners()
            return True
        return False

    def process_timer_event(self, event: ParseResult) -> Optional[CompletedRun]:
        """Handle incoming timer event (finish, start, or reset)."""
        if isinstance(event, FinishEvent):
            return self._handle_finish(event)
        elif isinstance(event, StartEvent):
            # Find the first car in queue that has not tripped start beam yet
            next_start_car = next((c for c in self.active_queue if not c.is_on_track), None)
            if next_start_car:
                next_start_car.is_on_track = True
                next_start_car.dispatched_at = datetime.now()
                self.logger.log_event(
                    f"START BEAM TRIPPED - Car #{next_start_car.car_number} ({next_start_car.driver_name}) launched onto track! (Next to Finish)"
                )
            else:
                self.logger.log_event(
                    f"START BEAM TRIPPED - Eye #{event.eye_number} crossed."
                )
            self.notify_listeners()
            return None
        elif isinstance(event, EyeBEvent):
            self.logger.log_event(f"FINISH BEAM TRIPPED - Eye #2 crossed ({event.raw_message}).")
            self.notify_listeners()
            return None
        elif isinstance(event, ResetEvent):
            self.logger.log_event("TIMER RESET - Polaris console reset signal received.")
            self.notify_listeners()
            return None
        return None

    def _handle_finish(self, finish_event: FinishEvent) -> Optional[CompletedRun]:
        """Assign finish time to the lead car on track."""
        if not self.active_queue:
            self.logger.log_event(
                f"UNMATCHED FINISH TIME: {finish_event.time_formatted}s - No active cars on track queue!"
            )
            return None

        # Prefer the first car that is on track (is_on_track == True)
        on_track_cars = [c for c in self.active_queue if c.is_on_track]
        if on_track_cars:
            finished_car = on_track_cars[0]
            self.active_queue.remove(finished_car)
        else:
            # Fallback if start beam was missed or manual start: pop top car from queue
            finished_car = self.active_queue.pop(0)

        # Handle case where finished_car was already flagged DNF in-flight
        if finished_car.is_dnf:
            existing_run = next((r for r in self.completed_runs if r.run_id == finished_car.dnf_run_id), None)
            if not existing_run:
                existing_run = next((r for r in reversed(self.completed_runs) if r.car_number == finished_car.car_number and r.status == "DNF"), None)

            if existing_run:
                existing_run.raw_time_seconds = finish_event.time_seconds
                self.logger.log_event(
                    f"FINISH BEAM TRIPPED BY DNF CAR #{finished_car.car_number} ({finished_car.driver_name}) - Raw: {finish_event.time_formatted}s (Status: DNF). Active queue updated."
                )

            # Auto-launch next staged car onto active track queue if enabled
            if self.auto_launch and self.staging_queue and len(self.active_queue) < self.max_active_cars:
                self._dispatch_next_staged()
            else:
                self.notify_listeners()

            return existing_run

        runs_done_before = self.get_run_count(finished_car.car_number)

        # Check if car has exceeded maximum allowed runs
        status = "PROVISIONAL"
        if runs_done_before >= self.max_runs_per_car:
            status = "FAULT"
            self.logger.log_event(
                f"MAX RUNS FAULT - Car #{finished_car.car_number} has already completed {runs_done_before}/{self.max_runs_per_car} allowed runs! Run set to FAULT."
            )

        run = CompletedRun(
            run_id=self._run_counter,
            car_number=finished_car.car_number,
            driver_name=finished_car.driver_name,
            raw_time_seconds=finish_event.time_seconds,
            car_run_number=runs_done_before + 1,
            penalty_cones=finished_car.penalty_cones,
            cone_penalty_seconds=self.cone_penalty_seconds,
            status=status,
            is_locked=False
        )
        self._run_counter += 1
        self.completed_runs.append(run)

        # Log run
        self.logger.log_run(
            car_number=run.car_number,
            driver_name=run.driver_name,
            raw_time_seconds=run.raw_time_seconds,
            penalty_count=run.penalty_cones,
            status=run.status
        )

        # Auto-launch next staged car onto active track queue if enabled
        if self.auto_launch and self.staging_queue and len(self.active_queue) < self.max_active_cars:
            self._dispatch_next_staged()
        else:
            self.notify_listeners()

        return run

    def flag_dnf(self, car_number: Optional[str] = None) -> bool:
        """Flag an active car on track as DNF (remains in active queue until it crosses finish beam or is deleted)."""
        match = None
        if car_number:
            clean_num = car_number.strip().lstrip('#')
            match = next((c for c in self.active_queue if c.car_number.strip().lstrip('#') == clean_num), None)
        else:
            on_track_cars = [c for c in self.active_queue if c.is_on_track]
            if on_track_cars:
                match = on_track_cars[0]
            elif self.active_queue:
                match = self.active_queue[0]

        if match:
            if match.is_dnf:
                return True

            match.is_dnf = True
            runs_done_before = self.get_run_count(match.car_number)
            run = CompletedRun(
                run_id=self._run_counter,
                car_number=match.car_number,
                driver_name=match.driver_name,
                raw_time_seconds=0.0,
                car_run_number=runs_done_before + 1,
                penalty_cones=match.penalty_cones,
                cone_penalty_seconds=self.cone_penalty_seconds,
                status="DNF",
                is_locked=False
            )
            self._run_counter += 1
            match.dnf_run_id = run.run_id
            self.completed_runs.append(run)
            self.logger.log_run(match.car_number, match.driver_name, 0.0, match.penalty_cones, status="DNF")
            self.logger.log_event(
                f"FLAGGED DNF - Car #{match.car_number} ({match.driver_name}) marked DNF. Still on track waiting to consume finish beam."
            )
            self.notify_listeners()
            return True
        return False

    def update_settings(
        self,
        event_name: Optional[str] = None,
        event_date: Optional[str] = None,
        cone_penalty_seconds: Optional[float] = None,
        num_heats: Optional[int] = None,
        runs_per_heat: Optional[int] = None,
        auto_launch: Optional[bool] = None,
    ):
        """Update system settings (Event Name, Date, Cone Penalty, Heats, Runs per Heat, Auto-Launch)."""
        if event_name is not None:
            self.event_name = event_name.strip() or "Autocross Event"
        if event_date is not None:
            self.event_date = event_date.strip() or datetime.now().strftime("%Y-%m-%d")
        if cone_penalty_seconds is not None:
            self.cone_penalty_seconds = max(0.0, float(cone_penalty_seconds))
            for r in self.completed_runs:
                if not r.is_locked:
                    r.cone_penalty_seconds = self.cone_penalty_seconds
        if num_heats is not None:
            self.num_heats = max(1, int(num_heats))
        if runs_per_heat is not None:
            self.runs_per_heat = max(1, int(runs_per_heat))

        self.max_runs_per_car = self.num_heats * self.runs_per_heat

        if auto_launch is not None:
            self.auto_launch = auto_launch

        self.logger.log_event(
            f"SETTINGS UPDATED - Event: '{self.event_name}', Date: '{self.event_date}', "
            f"Cone Penalty: {self.cone_penalty_seconds}s, Heats: {self.num_heats}, Runs/Heat: {self.runs_per_heat} (Total Max Runs: {self.max_runs_per_car})"
        )
        self.notify_listeners()



    def update_active_cones(self, car_number: Optional[str] = None, delta: int = 0, penalty_cones: Optional[int] = None) -> bool:
        """Update penalty cones for an active car on track (defaults to the #1 next-to-finish car)."""
        match = None
        if car_number:
            clean_num = car_number.strip().lstrip('#')
            match = next((c for c in self.active_queue if c.car_number.strip().lstrip('#') == clean_num), None)
        else:
            on_track_cars = [c for c in self.active_queue if c.is_on_track]
            if on_track_cars:
                match = on_track_cars[0]
            elif self.active_queue:
                match = self.active_queue[0]

        if match:
            if penalty_cones is not None:
                match.penalty_cones = max(0, penalty_cones)
            else:
                match.penalty_cones = max(0, match.penalty_cones + delta)
            self.logger.log_event(
                f"CONE UPDATE - Active Car #{match.car_number}: {match.penalty_cones} cones."
            )
            self.notify_listeners()
            return True
        return False


    def finalize_heat(self) -> int:
        """Permanently lock all provisional completed runs recorded so far and promote them to OFFICIAL."""
        count = 0
        for run in self.completed_runs:
            if not run.is_locked:
                run.is_locked = True
                if run.status == "PROVISIONAL":
                    run.status = "OFFICIAL"
                count += 1

        self.logger.log_event(
            f"🔒 HEAT FINALIZED - Promoted and permanently locked {count} runs to OFFICIAL (Total locked: {sum(1 for r in self.completed_runs if r.is_locked)})."
        )
        self.notify_listeners()
        return count

    def update_penalty(self, run_id: int, penalty_cones: int, status: str = "OFFICIAL") -> bool:
        """Update cone penalties for a completed run (unless specific run is locked)."""
        run = next((r for r in self.completed_runs if r.run_id == run_id), None)
        if run:
            if run.is_locked:
                self.logger.log_event(f"UPDATE REJECTED - Run #{run_id} (Car #{run.car_number}) is locked!")
                return False

            run.penalty_cones = max(0, penalty_cones)
            run.status = status
            self.logger.log_event(
                f"PENALTY UPDATE - Run #{run_id} Car #{run.car_number}: {run.penalty_cones} cones, status={status}"
            )
            self.notify_listeners()
            return True
        return False

    def reorder_active_queue(self, index_a: int, index_b: int) -> bool:
        """Swap position of two cars in active queue (if pass occurred on track)."""
        if 0 <= index_a < len(self.active_queue) and 0 <= index_b < len(self.active_queue):
            self.active_queue[index_a], self.active_queue[index_b] = (
                self.active_queue[index_b],
                self.active_queue[index_a],
            )
            self.logger.log_event(
                f"QUEUE SWAPPED - Active car position {index_a} and {index_b} swapped."
            )
            self.notify_listeners()
            return True
        return False
