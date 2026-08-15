"""Unit tests for multi-car track queue manager."""

import pytest
from src.farmtek.logger import FarmTekLogger
from src.farmtek.track_queue import TrackQueueManager
from src.farmtek.driver_db import DriverDatabase
from src.farmtek.jac_normal_parser import FinishEvent


@pytest.fixture
def queue_mgr(tmp_path):
    logger = FarmTekLogger(log_dir=str(tmp_path / "logs"))
    return TrackQueueManager(logger=logger, max_active_cars=4)


def test_dispatch_and_max_capacity(queue_mgr):
    assert queue_mgr.dispatch_car("101", "Driver A")
    assert queue_mgr.dispatch_car("102", "Driver B")
    assert queue_mgr.dispatch_car("103", "Driver C")
    assert queue_mgr.dispatch_car("104", "Driver D")

    # 5th car should be rejected because capacity is 4
    assert not queue_mgr.dispatch_car("105", "Driver E")
    assert len(queue_mgr.active_queue) == 4


def test_fifo_finish_processing(queue_mgr):
    queue_mgr.dispatch_car("42", "Alice")
    queue_mgr.dispatch_car("99", "Bob")

    # First finish event -> assigned to Car 42
    finish_event_1 = FinishEvent(raw_message="R001702", time_seconds=207.100, time_formatted="207.100")
    run1 = queue_mgr.process_timer_event(finish_event_1)

    assert run1 is not None
    assert run1.car_number == "42"
    assert run1.raw_time_seconds == 207.100
    assert len(queue_mgr.active_queue) == 1

    # Second finish event -> assigned to Car 99
    finish_event_2 = FinishEvent(raw_message="R523100", time_seconds=13.250, time_formatted="13.250")
    run2 = queue_mgr.process_timer_event(finish_event_2)

    assert run2 is not None
    assert run2.car_number == "99"
    assert run2.raw_time_seconds == 13.250
    assert len(queue_mgr.active_queue) == 0


def test_penalty_calculation(queue_mgr):
    queue_mgr.dispatch_car("7", "Charlie")
    finish_event = FinishEvent(raw_message="R523100", time_seconds=50.000, time_formatted="50.000")
    run = queue_mgr.process_timer_event(finish_event)

    assert run.final_time_seconds == 50.000

    # Add 2 cones (+4.0s)
    queue_mgr.update_penalty(run.run_id, penalty_cones=2)
    assert run.penalty_cones == 2
    assert run.final_time_seconds == 54.000
    assert run.final_time_formatted == "54.000"


def test_run_counting_and_fault_rerun(queue_mgr):
    queue_mgr.max_runs_per_car = 4
    queue_mgr.add_to_staging("42", "Driver A")

    assert queue_mgr.get_run_count("42") == 0

    # Finish run 1
    finish_1 = FinishEvent(raw_message="R001702", time_seconds=45.0, time_formatted="45.000")
    queue_mgr.process_timer_event(finish_1)

    assert queue_mgr.get_run_count("42") == 1

    # Stage and dispatch for run 2, then issue timing fault
    queue_mgr.add_to_staging("42", "Driver A")
    assert len(queue_mgr.active_queue) == 1

    # Issue Fault
    assert queue_mgr.issue_fault("42", restage=True)

    # Car 42 should be re-staged/auto-launched for re-run, and official runs count is still 1!
    assert queue_mgr.get_run_count("42") == 1
    assert (queue_mgr.active_queue[0].car_number == "42" if queue_mgr.auto_launch else queue_mgr.staging_queue[0].car_number == "42")
    assert queue_mgr.completed_runs[-1].status == "RERUN"


def test_auto_fault_when_exceeding_max_runs(queue_mgr):
    queue_mgr.max_runs_per_car = 1
    queue_mgr.add_to_staging("99", "Driver B")

    # Run 1 (Provisional -> Official when finalized)
    finish_1 = FinishEvent("R001702", 40.0, "40.000")
    queue_mgr.process_timer_event(finish_1)
    assert queue_mgr.completed_runs[-1].status == "PROVISIONAL"
    queue_mgr.finalize_heat()
    assert queue_mgr.completed_runs[-1].status == "OFFICIAL"
    assert queue_mgr.get_run_count("99") == 1

    # Attempt Run 2 (Exceeds Max Runs limit of 1)
    queue_mgr.add_to_staging("99", "Driver B")
    finish_2 = FinishEvent("R001702", 41.5, "41.500")
    run2 = queue_mgr.process_timer_event(finish_2)

    # Run 2 should automatically be set to FAULT
    assert run2.status == "FAULT"
    assert queue_mgr.get_run_count("99") == 1  # FAULT does not count as official run


def test_driver_database_lookup(tmp_path):
    csv_file = tmp_path / "drivers.csv"
    csv_file.write_text(
        '"Class","Number","First Name","Last Name"\n'
        '"Stock","131","David","Lambert"\n'
        '"Stock","28","Don","Lambert"\n'
    )
    db = DriverDatabase(str(csv_file))
    logger = FarmTekLogger(log_dir=str(tmp_path / "logs"))
    qm = TrackQueueManager(logger=logger, driver_db=db)

    qm.add_to_staging("131")
    assert qm.active_queue[0].driver_name == "David Lambert" or qm.staging_queue[0].driver_name == "David Lambert"


def test_zero_runs_identification(tmp_path):
    csv_file = tmp_path / "drivers.csv"
    csv_file.write_text(
        '"Class","Number","First Name","Last Name","Car Model"\n'
        '"SS","131","David","Lambert","Corvette"\n'
        '"SS","28","Don","Lambert","Miata"\n'
        '"BS","99","Alice","Smith","M3"\n'
    )
    db = DriverDatabase(str(csv_file))
    logger = FarmTekLogger(log_dir=str(tmp_path / "logs"))
    qm = TrackQueueManager(logger=logger, driver_db=db)

    # Initial state: all 3 drivers have 0 runs
    zero_drivers = qm.get_zero_run_drivers()
    assert len(zero_drivers) == 3
    car_nums = {d.car_number for d in zero_drivers}
    assert car_nums == {"131", "28", "99"}

    # Car 131 completes a run
    qm.dispatch_car("131")
    finish_event = FinishEvent(raw_message="R001702", time_seconds=42.5, time_formatted="42.500")
    qm.process_timer_event(finish_event)

    # Now only 28 and 99 have 0 runs
    remaining_zero = qm.get_zero_run_drivers()
    assert len(remaining_zero) == 2
    rem_car_nums = {d.car_number for d in remaining_zero}
    assert rem_car_nums == {"28", "99"}


def test_active_car_shortcuts_and_cones(queue_mgr):
    queue_mgr.dispatch_car("101", "Driver Active")
    car = queue_mgr.active_queue[0]
    assert car.penalty_cones == 0

    # Add 1 cone (+)
    assert queue_mgr.update_active_cones(delta=1)
    assert car.penalty_cones == 1

    # Add another cone (+)
    assert queue_mgr.update_active_cones(delta=1)
    assert car.penalty_cones == 2

    # Remove 1 cone (-)
    assert queue_mgr.update_active_cones(delta=-1)
    assert car.penalty_cones == 1

    # Remove 2 cones -> minimum 0 (-)
    assert queue_mgr.update_active_cones(delta=-2)
    assert car.penalty_cones == 0

    # Test finish with cones
    queue_mgr.update_active_cones(delta=2)
    finish_event = FinishEvent(raw_message="R001702", time_seconds=30.000, time_formatted="30.000")
    run = queue_mgr.process_timer_event(finish_event)
    assert run.penalty_cones == 2
    assert run.final_time_seconds == 34.000

    # Test DNF shortcut (/)
    queue_mgr.dispatch_car("102", "Driver DNF")
    queue_mgr.update_active_cones(delta=1)
    assert queue_mgr.flag_dnf()
    dnf_run = queue_mgr.completed_runs[-1]
    assert dnf_run.car_number == "102"
    assert dnf_run.status == "DNF"
    assert dnf_run.penalty_cones == 1


def test_dnf_car_consumes_next_finish_beam(queue_mgr):
    # Dispatch 2 cars onto track
    queue_mgr.dispatch_car("454", "Michael")
    queue_mgr.dispatch_car("1100", "Ken")

    assert len(queue_mgr.active_queue) == 2
    car1 = queue_mgr.active_queue[0]
    car2 = queue_mgr.active_queue[1]

    # Car 454 DNFs on course
    queue_mgr.flag_dnf("454")
    assert car1.is_dnf is True
    # Car 454 should STILL be in active_queue waiting for finish beam
    assert len(queue_mgr.active_queue) == 2
    assert queue_mgr.active_queue[0].car_number == "454"
    assert queue_mgr.completed_runs[-1].status == "DNF"
    assert queue_mgr.completed_runs[-1].car_number == "454"

    # Finish beam trips when Car 454 passes finish line
    finish_454 = FinishEvent(raw_message="R001702", time_seconds=44.123, time_formatted="44.123")
    run1 = queue_mgr.process_timer_event(finish_454)

    # Finish event was consumed by DNF Car 454!
    assert run1.car_number == "454"
    assert run1.status == "DNF"
    assert run1.raw_time_seconds == 44.123

    # Now Car 454 is removed, and Car 1100 is next to finish
    assert len(queue_mgr.active_queue) == 1
    assert queue_mgr.active_queue[0].car_number == "1100"

    # Car 1100 passes finish line
    finish_1100 = FinishEvent(raw_message="R523100", time_seconds=38.500, time_formatted="38.500")
    run2 = queue_mgr.process_timer_event(finish_1100)

    # Car 1100 gets its correct time!
    assert run2.car_number == "1100"
    assert run2.status == "PROVISIONAL"
    assert run2.raw_time_seconds == 38.500
    assert len(queue_mgr.active_queue) == 0


def test_update_settings_and_heats_calculation(queue_mgr):
    # Test default settings
    assert queue_mgr.event_name == "Autocross Event"
    assert queue_mgr.num_heats == 1
    assert queue_mgr.runs_per_heat == 3
    assert queue_mgr.max_runs_per_car == 3
    assert queue_mgr.cone_penalty_seconds == 2.0

    # Update settings
    queue_mgr.update_settings(
        event_name="Summer Championship",
        event_date="2026-08-15",
        cone_penalty_seconds=3.0,
        num_heats=2,
        runs_per_heat=3
    )

    assert queue_mgr.event_name == "Summer Championship"
    assert queue_mgr.event_date == "2026-08-15"
    assert queue_mgr.cone_penalty_seconds == 3.0
    assert queue_mgr.num_heats == 2
    assert queue_mgr.runs_per_heat == 3
    assert queue_mgr.max_runs_per_car == 6  # 2 heats * 3 runs/heat = 6 max runs

    # Test penalty calculation with 3.0s cone penalty
    queue_mgr.dispatch_car("7", "Charlie")
    finish_event = FinishEvent(raw_message="R523100", time_seconds=50.000, time_formatted="50.000")
    run = queue_mgr.process_timer_event(finish_event)
    queue_mgr.update_penalty(run.run_id, penalty_cones=2)

    # 50.0 + (2 cones * 3.0s) = 56.0s
    assert run.final_time_seconds == 56.000



