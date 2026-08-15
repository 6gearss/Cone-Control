import csv
from datetime import datetime
import io
from pathlib import Path
import re
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .farmtek.logger import FarmTekLogger
from .farmtek.track_queue import TrackQueueManager

app = FastAPI(title="FarmTek Autocross Timing API")

# Shared state references (injected on startup)
logger_instance: FarmTekLogger = None
queue_mgr_instance: TrackQueueManager = None
simulator_instance = None

active_websockets: List[WebSocket] = []


class StageRequest(BaseModel):
    car_number: str
    driver_name: str = "Driver"


class DispatchRequest(BaseModel):
    car_number: Optional[str] = None
    driver_name: str = "Driver"


class RunUpdateRequest(BaseModel):
    run_id: int
    penalty_cones: int
    status: str = "OFFICIAL"
    car_number: Optional[str] = None
    driver_name: Optional[str] = None


class ActiveSwapRequest(BaseModel):
    index_a: int
    index_b: int


class DnfRequest(BaseModel):
    car_number: Optional[str] = None


class ActiveConeRequest(BaseModel):
    car_number: Optional[str] = None
    delta: int = 1
    penalty_cones: Optional[int] = None


class SettingsUpdateRequest(BaseModel):
    event_name: Optional[str] = None
    event_date: Optional[str] = None
    cone_penalty_seconds: Optional[float] = None
    num_heats: Optional[int] = None
    runs_per_heat: Optional[int] = None
    auto_launch: Optional[bool] = None



recent_raw_traffic: List[dict] = []
main_event_loop = None


def init_app(logger: FarmTekLogger, queue_mgr: TrackQueueManager, simulator=None):
    global logger_instance, queue_mgr_instance, simulator_instance
    logger_instance = logger
    queue_mgr_instance = queue_mgr
    simulator_instance = simulator

    # Subscribe sync queue listener wrapper to schedule async WebSocket updates
    queue_mgr_instance.listeners.append(trigger_queue_update)
    logger_instance.raw_listeners.append(broadcast_serial_traffic)


def trigger_queue_update():
    """Sync wrapper to schedule broadcast_queue_state thread-safely onto event loop."""
    if not active_websockets:
        return
    try:
        import asyncio
        loop = main_event_loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast_queue_state(), loop)
        else:
            try:
                running_loop = asyncio.get_running_loop()
                asyncio.create_task(broadcast_queue_state())
            except RuntimeError:
                pass
    except Exception:
        pass


async def broadcast_queue_state():
    """Broadcast queue and run updates to all connected WebSockets."""
    if not active_websockets:
        return

    payload = get_state_payload()
    disconnected = []
    for ws in active_websockets:
        try:
            await ws.send_json({"type": "queue_update", "data": payload})
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


async def send_ws_raw(payload: dict):
    disconnected = []
    for ws in active_websockets:
        try:
            await ws.send_json({"type": "raw_serial", "data": payload})
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


def broadcast_serial_traffic(timestamp: str, raw_bytes: bytes, decoded_str: str):
    """Notify web sockets of live raw serial traffic safely from thread."""
    payload = {
        "timestamp": timestamp,
        "hex": raw_bytes.hex(' '),
        "decoded": decoded_str
    }
    recent_raw_traffic.append(payload)
    if len(recent_raw_traffic) > 20:
        recent_raw_traffic.pop(0)

    if not active_websockets:
        return

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(send_ws_raw(payload), loop)
    except Exception:
        pass


class FaultRequest(BaseModel):
    car_number: str
    restage: bool = True


class MaxRunsRequest(BaseModel):
    max_runs: int


def get_state_payload():
    def _db_entry(car_number):
        if queue_mgr_instance and queue_mgr_instance.driver_db:
            return queue_mgr_instance.driver_db.lookup(car_number)
        return None

    def _car_dict(c, include_dispatched=False):
        e = _db_entry(c.car_number)
        d = {
            "car_number": c.car_number,
            "driver_name": c.driver_name,
            "is_on_track": getattr(c, 'is_on_track', False),
            "is_dnf": getattr(c, 'is_dnf', False),
            "penalty_cones": getattr(c, 'penalty_cones', 0),
            "class_name": e.class_name if e else "",
            "car_model": e.car_model if e else "",
            "runs_completed": queue_mgr_instance.get_run_count(c.car_number),
        }
        if include_dispatched:
            d["dispatched_at"] = c.dispatched_at.strftime("%H:%M:%S")
        return d

    zero_run_drivers = [
        {
            "car_number": d.car_number,
            "full_name": d.full_name,
            "first_name": d.first_name,
            "last_name": d.last_name,
            "class_name": d.class_name,
            "car_model": d.car_model,
            "car_color": d.car_color,
            "is_staged": any(c.car_number == d.car_number.strip().lstrip('#') for c in queue_mgr_instance.staging_queue),
            "is_active": any(c.car_number == d.car_number.strip().lstrip('#') for c in queue_mgr_instance.active_queue),
        }
        for d in (queue_mgr_instance.get_zero_run_drivers() if queue_mgr_instance else [])
    ]

    return {
        "active_queue":  [_car_dict(c, include_dispatched=True)  for c in queue_mgr_instance.active_queue],
        "staging_queue": [_car_dict(c, include_dispatched=False) for c in queue_mgr_instance.staging_queue],
        "completed_runs": [
            {
                "run_id": r.run_id,
                "car_number": r.car_number,
                "driver_name": r.driver_name,
                "class_name": (e := _db_entry(r.car_number)) and e.class_name or "",
                "car_run_number": getattr(r, 'car_run_number', 1),
                "raw_time_seconds": r.raw_time_seconds,
                "penalty_cones": r.penalty_cones,
                "final_time_seconds": r.final_time_seconds,
                "final_time_formatted": r.final_time_formatted,
                "status": r.status,
                "is_locked": getattr(r, 'is_locked', False),
                "completed_at": r.completed_at.strftime("%Y-%m-%d %H:%M:%S")
            }
            for r in queue_mgr_instance.completed_runs
        ],
        "max_active_cars": queue_mgr_instance.max_active_cars,
        "auto_launch": queue_mgr_instance.auto_launch,
        "event_name": getattr(queue_mgr_instance, 'event_name', "Autocross Event"),
        "event_date": getattr(queue_mgr_instance, 'event_date', datetime.now().strftime("%Y-%m-%d")),
        "cone_penalty_seconds": getattr(queue_mgr_instance, 'cone_penalty_seconds', 2.0),
        "num_heats": getattr(queue_mgr_instance, 'num_heats', 1),
        "runs_per_heat": getattr(queue_mgr_instance, 'runs_per_heat', 3),
        "max_runs_per_car": queue_mgr_instance.max_runs_per_car,
        "raw_traffic": recent_raw_traffic,
        "is_simulating": simulator_instance is not None,
        "zero_run_drivers": zero_run_drivers,
        "zero_runs_count": len(zero_run_drivers)
    }


@app.get("/api/state")
def get_state():
    return get_state_payload()


@app.get("/api/drivers/zero_runs")
@app.get("/api/zero_runs")
def get_zero_runs_drivers():
    if queue_mgr_instance:
        drivers = queue_mgr_instance.get_zero_run_drivers()
        formatted = [
            {
                "car_number": d.car_number,
                "full_name": d.full_name,
                "first_name": d.first_name,
                "last_name": d.last_name,
                "class_name": d.class_name,
                "car_model": d.car_model,
                "car_color": d.car_color,
                "is_staged": any(c.car_number == d.car_number.strip().lstrip('#') for c in queue_mgr_instance.staging_queue),
                "is_active": any(c.car_number == d.car_number.strip().lstrip('#') for c in queue_mgr_instance.active_queue),
            }
            for d in drivers
        ]
        return {
            "count": len(formatted),
            "drivers": formatted
        }
    return {"count": 0, "drivers": []}


@app.post("/api/finalize_heat")
def finalize_heat_api():
    if queue_mgr_instance:
        count = queue_mgr_instance.finalize_heat()
        return {"status": "success", "locked_count": count}
    raise HTTPException(status_code=500, detail="Queue manager not initialized")


@app.get("/api/lookup_driver/{car_number}")
def lookup_driver(car_number: str):
    if queue_mgr_instance and queue_mgr_instance.driver_db:
        entry = queue_mgr_instance.driver_db.lookup(car_number)
        if entry:
            return {
                "found": True,
                "full_name": entry.full_name,
                "first_name": entry.first_name,
                "last_name": entry.last_name,
                "class_name": entry.class_name,
                "car_model": entry.car_model,
                "car_color": entry.car_color
            }
    return {"found": False}



@app.post("/api/starter_launch")
def starter_launch():
    success = queue_mgr_instance.starter_launch()
    if not success:
        raise HTTPException(status_code=400, detail="Cannot launch: Staging empty or track full")
    return {"success": success}


@app.post("/api/fault")
def issue_fault(req: FaultRequest):
    success = queue_mgr_instance.issue_fault(req.car_number, req.restage)
    return {"success": success}


@app.post("/api/set_max_runs")
def set_max_runs(req: MaxRunsRequest):
    queue_mgr_instance.max_runs_per_car = max(1, req.max_runs)
    logger_instance.log_event(f"SETTINGS - Max runs per car updated to: {queue_mgr_instance.max_runs_per_car}")
    queue_mgr_instance.notify_listeners()
    return {"max_runs_per_car": queue_mgr_instance.max_runs_per_car}


@app.post("/api/stage")
def stage_car(req: StageRequest):
    if not req.car_number.strip():
        raise HTTPException(status_code=400, detail="Car number is required")

    clean_car = req.car_number.strip().lstrip('#')

    # Check for duplicate before attempting to stage
    already_queued = (
        any(c.car_number == clean_car for c in queue_mgr_instance.staging_queue) or
        any(c.car_number == clean_car for c in queue_mgr_instance.active_queue)
    )
    if already_queued:
        return {"success": False, "already_queued": True, "track_full": False, "car_number": clean_car}

    # Check for track capacity limit (max 4 cars)
    total_queued = len(queue_mgr_instance.active_queue) + len(queue_mgr_instance.staging_queue)
    if total_queued >= queue_mgr_instance.max_active_cars:
        return {
            "success": False,
            "already_queued": False,
            "track_full": True,
            "max_cars": queue_mgr_instance.max_active_cars,
            "car_number": clean_car
        }

    runs_done = queue_mgr_instance.get_run_count(req.car_number)
    over_max = runs_done >= queue_mgr_instance.max_runs_per_car
    success = queue_mgr_instance.add_to_staging(req.car_number, req.driver_name)
    return {
        "success": success,
        "already_queued": False,
        "track_full": False,
        "over_max_runs": over_max,
        "runs_completed": runs_done,
        "max_runs": queue_mgr_instance.max_runs_per_car
    }


@app.delete("/api/stage/{car_number}")
def remove_staged_car(car_number: str):
    match = next((c for c in queue_mgr_instance.staging_queue if c.car_number == car_number), None)
    if match:
        queue_mgr_instance.staging_queue.remove(match)
        logger_instance.log_event(f"REMOVED FROM STAGING - Car #{car_number}")
        queue_mgr_instance.notify_listeners()
        return {"success": True}
    return {"success": False, "error": "Car not found in staging"}


@app.delete("/api/active/{car_number}")
def delete_active_car(car_number: str):
    success = queue_mgr_instance.delete_active_car(car_number)
    return {"success": success}


@app.post("/api/toggle_auto_launch")
def toggle_auto_launch():
    queue_mgr_instance.auto_launch = not queue_mgr_instance.auto_launch
    logger_instance.log_event(f"AUTO-LAUNCH TOGGLED - Enabled: {queue_mgr_instance.auto_launch}")
    queue_mgr_instance.notify_listeners()
    return {"auto_launch": queue_mgr_instance.auto_launch}


@app.post("/api/dispatch")
def dispatch_car(req: DispatchRequest):
    success = queue_mgr_instance.dispatch_car(req.car_number, req.driver_name)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot dispatch: Track is full (max active cars reached)")
    return {"success": success}


@app.post("/api/dnf_active")
def dnf_active_car(req: DnfRequest):
    success = queue_mgr_instance.flag_dnf(req.car_number)
    return {"success": success}


@app.post("/api/active_cone")
def update_active_cone(req: ActiveConeRequest):
    if not queue_mgr_instance:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")
    success = queue_mgr_instance.update_active_cones(
        car_number=req.car_number,
        delta=req.delta,
        penalty_cones=req.penalty_cones
    )
    return {"success": success}



@app.post("/api/swap_active")
def swap_active_cars(req: ActiveSwapRequest):
    success = queue_mgr_instance.reorder_active_queue(req.index_a, req.index_b)
    return {"success": success}


@app.post("/api/update_run")
def update_run(req: RunUpdateRequest):
    run = next((r for r in queue_mgr_instance.completed_runs if r.run_id == req.run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run.penalty_cones = max(0, req.penalty_cones)
    run.status = req.status
    if req.car_number:
        run.car_number = req.car_number
    if req.driver_name:
        run.driver_name = req.driver_name

    logger_instance.log_event(
        f"RUN UPDATED - Run #{req.run_id}: Car #{run.car_number}, Cones: {run.penalty_cones}, Status: {run.status}"
    )
    queue_mgr_instance.notify_listeners()
    return {"success": True}


@app.delete("/api/run/{run_id}")
def delete_run(run_id: int):
    match = next((r for r in queue_mgr_instance.completed_runs if r.run_id == run_id), None)
    if match:
        queue_mgr_instance.completed_runs.remove(match)
        logger_instance.log_event(f"RUN DELETED - Run #{run_id} (Car #{match.car_number})")
        queue_mgr_instance.notify_listeners()
        return {"success": True}
    return {"success": False, "error": "Run not found"}


@app.post("/api/simulate_finish")
def trigger_simulated_finish(seconds: float = 42.123):
    if simulator_instance:
        simulator_instance.send_finish_time(seconds)
        return {"success": True, "time": seconds}
    return {"success": False, "error": "Simulator not active"}


@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    if not queue_mgr_instance:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")
    queue_mgr_instance.update_settings(
        event_name=req.event_name,
        event_date=req.event_date,
        cone_penalty_seconds=req.cone_penalty_seconds,
        num_heats=req.num_heats,
        runs_per_heat=req.runs_per_heat,
        auto_launch=req.auto_launch
    )
    return {"status": "success", "settings": get_state_payload()}


@app.get("/api/export_csv")
def export_csv():
    if not queue_mgr_instance:
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    event_name = getattr(queue_mgr_instance, 'event_name', "Autocross Event") or "Autocross Event"
    event_date = getattr(queue_mgr_instance, 'event_date', datetime.now().strftime("%Y-%m-%d")) or datetime.now().strftime("%Y-%m-%d")
    cone_penalty = getattr(queue_mgr_instance, 'cone_penalty_seconds', 2.0)

    clean_name = re.sub(r'[^\w\-]', '_', event_name.strip())
    clean_date = re.sub(r'[^\w\-]', '_', event_date.strip())
    filename = f"{clean_name}_{clean_date}.csv"

    output = io.StringIO()
    writer = csv.writer(output)

    # Write event metadata comments
    writer.writerow([f"# Event Name: {event_name}"])
    writer.writerow([f"# Event Date: {event_date}"])
    writer.writerow([f"# Cone Penalty: {cone_penalty}s"])
    writer.writerow([f"# Total Heats: {getattr(queue_mgr_instance, 'num_heats', 1)}"])
    writer.writerow([f"# Runs per Heat: {getattr(queue_mgr_instance, 'runs_per_heat', 3)}"])
    writer.writerow([f"# Total Max Runs: {queue_mgr_instance.max_runs_per_car}"])
    writer.writerow([])

    # Table Header
    writer.writerow([
        "Run #",
        "Timestamp",
        "Car #",
        "Class",
        "Driver Name",
        "Run",
        "Raw Time (s)",
        "Cones",
        "Penalty Time (s)",
        "Final Time (s)",
        "Status",
        "Locked"
    ])

    for r in queue_mgr_instance.completed_runs:
        e = queue_mgr_instance.driver_db.lookup(r.car_number) if queue_mgr_instance.driver_db else None
        class_name = e.class_name if e else ""
        penalty_time = r.penalty_cones * getattr(r, 'cone_penalty_seconds', cone_penalty)
        writer.writerow([
            r.run_id,
            r.completed_at.strftime("%Y-%m-%d %H:%M:%S"),
            r.car_number,
            class_name,
            r.driver_name,
            getattr(r, 'car_run_number', 1),
            f"{r.raw_time_seconds:.3f}",
            r.penalty_cones,
            f"{penalty_time:.1f}",
            r.final_time_formatted,
            r.status,
            "Yes" if r.is_locked else "No"
        ])

    csv_content = output.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global main_event_loop
    main_event_loop = asyncio.get_running_loop()
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        await websocket.send_json({"type": "initial_state", "data": get_state_payload()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


# Mount static assets
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def get_index():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>FarmTek Autocross Timing API Active</h1>")
