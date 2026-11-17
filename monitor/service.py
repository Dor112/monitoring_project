import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from uuid import uuid4

import numpy as np
from sklearn.metrics import precision_recall_fscore_support

from action import DemoService
from detector import Detector

STEP = 60
WINDOW = 12
TARGET = os.getenv("ACTION_MODE", "off")
if TARGET not in {"off", "live", "smd"}:
    raise ValueError("ACTION_MODE must be off, live or smd")
EXPRESSIONS = {
    "CPU busy %": '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m])))',
    "CPU iowait %": '100 * avg(rate(node_cpu_seconds_total{mode="iowait"}[2m]))',
    "RAM used %": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "Disk IOPS": 'sum(rate(node_disk_reads_completed_total{device!~"loop.*|ram.*"}[2m])) + sum(rate(node_disk_writes_completed_total{device!~"loop.*|ram.*"}[2m]))',
    "Network RX bytes/s": 'sum(rate(node_network_receive_bytes_total{device!="lo"}[2m]))',
    "Network TX bytes/s": 'sum(rate(node_network_transmit_bytes_total{device!="lo"}[2m]))',
}
LOCK = threading.Lock()
STATE = {
    mode: {"status": "starting", "points": deque(maxlen=240), "benchmark": None}
    for mode in ("live", "smd")
}
ACTIONS = deque(maxlen=30)
LIVE_TELEMETRY = deque(maxlen=240)
STREAM_ID = uuid4().hex


def memory_guard_status(filename="status.json"):
    try:
        state = json.loads((Path("/memory-guard") / filename).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {key: state.get(key) for key in ("target", "mode", "status", "limit", "samples", "history", "events", "updated_at")}


def update(mode, **values):
    with LOCK:
        STATE[mode].update(values)


def fetch_rows(start, end):
    rows = np.full(((end - start) // STEP + 1, len(EXPRESSIONS)), np.nan)
    for column, expression in enumerate(EXPRESSIONS.values()):
        # Stale values still inside Prometheus's lookback do not count after a failed scrape.
        available = 'max(up{job="vps"}) == 1'
        query = urlencode(dict(
            query=f"({expression}) and on() ({available})", start=start, end=end, step=STEP
        ))
        with urlopen("http://127.0.0.1:9090/api/v1/query_range?" + query, timeout=6) as response:
            result = json.load(response)["data"]["result"]
        if len(result) != 1:
            continue
        for timestamp, value in result[0]["values"]:
            rows[(int(timestamp) - start) // STEP, column] = float(value)
    return rows


def aligned_time():
    return ((int(time.time()) - 15) // STEP) * STEP


def record_telemetry(rows, end):
    start = end - (len(rows) - 1) * STEP
    with LOCK:
        last_step = LIVE_TELEMETRY[-1]["step"] if LIVE_TELEMETRY else start - STEP
        for step in range(max(start, last_step + STEP), end + STEP, STEP):
            values = {
                name: float(value) if np.isfinite(value) else None
                for name, value in zip(EXPRESSIONS, rows[(step - start) // STEP])
            }
            LIVE_TELEMETRY.append({"step": step, "values": values})


DEMO = DemoService()


class Reaction:
    def __init__(self, mode):
        self.mode = mode
        self.count = 0
        self.attempted = False

    def observe(self, point):
        if point["state"] != "critical":
            self.count = 0
            if point["state"] == "normal":
                self.attempted = False
            return
        self.count += 1
        if self.mode != TARGET or self.attempted or self.count < 3:
            return
        if time.monotonic() - DEMO.last_attempt < 300:
            return
        DEMO.last_attempt = time.monotonic()
        self.attempted = True
        event = {"mode": self.mode, "time": int(time.time()), "step": point["step"]}
        try:
            old, new = DEMO.restart()
            event.update(status="restarted", old_pid=old, new_pid=new)
        except (OSError, RuntimeError) as exc:
            event.update(status="failed", error=str(exc))
        with LOCK:
            ACTIONS.append(event)
        print(json.dumps(event), flush=True)


def publish(mode, model, names, step, score, scores, errors, reaction, label=None, values=None,
            inference_ms=None):
    if score > model.critical:
        severity = "critical"
    elif score > model.warning:
        severity = "warning"
    else:
        severity = "normal"
    total = errors.sum()
    top = [
        {"metric": names[i], "share": round(float(errors[i] / total) * 100, 1)}
        for i in np.argsort(errors)[-4:][::-1]
    ] if total > 0 else []
    point = {
        "step": step, "score": float(score), "forest": float(scores[0]),
        "autoencoder": float(scores[1]), "state": severity,
        "top": top, "label": label, "values": values,
        "reconstruction_mse": float(errors.mean()), "inference_ms": inference_ms,
    }
    with LOCK:
        STATE[mode]["points"].append(point)
        STATE[mode]["status"] = "running"
    reaction.observe(point)


def live():
    reaction = Reaction("live")
    model = None
    previous = None
    last = None
    while True:
        try:
            end = aligned_time()
            if model is None:
                rows = fetch_rows(end - 719 * STEP, end)
                record_telemetry(rows[-240:], end)
                # Use a contiguous suffix. Missing data cannot masquerade as a healthy sample.
                invalid = np.flatnonzero(~np.isfinite(rows).all(axis=1))
                rows = rows[invalid[-1] + 1:] if len(invalid) else rows
                if len(rows) < 240:
                    update("live", status=f"baseline: {len(rows)}/240 consecutive minutes")
                    time.sleep(30)
                    continue
                update("live", status="training")
                model = Detector(rows, WINDOW)
                update("live", warning=model.warning, critical=model.critical)
                last = end
            elif last != end:
                rows = fetch_rows(end - (WINDOW - 1) * STEP, end)
                record_telemetry(rows, end)
                if not np.isfinite(rows).all():
                    update("live", status="missing or invalid telemetry")
                    previous = None
                    reaction.count = 0
                else:
                    if last + STEP != end:
                        previous = None
                        reaction.count = 0
                    started = time.perf_counter()
                    scores, models, errors = model.predict(rows, previous)
                    inference_ms = 1000 * (time.perf_counter() - started)
                    previous = scores[0]
                    publish("live", model, list(EXPRESSIONS), end, previous, models[0],
                            errors[0], reaction, values=dict(zip(EXPRESSIONS, rows[-1].tolist())),
                            inference_ms=inference_ms)
                last = end
        except (OSError, ValueError, KeyError) as exc:
            update("live", status=f"telemetry error: {exc}")
            previous = None
            reaction.count = 0
        time.sleep(3)


def evaluate(labels, prediction):
    p, r, f, _ = precision_recall_fscore_support(
        labels, prediction, average="binary", zero_division=0
    )
    return dict(precision=p, recall=r, f1=f)


def replay(root=Path("/data")):
    machine = os.getenv("SMD_MACHINE", "machine-1-1")
    if not re.fullmatch(r"machine-[123]-[1-9][0-9]*", machine):
        raise ValueError("Invalid SMD_MACHINE")
    paths = [root / group / f"{machine}.txt" for group in ("train", "test", "test_label")]
    if not all(path.is_file() for path in paths):
        update("smd", status=f"SMD files missing for {machine}")
        return

    update("smd", status=f"training on {machine}", machine=machine)
    training, testing, labels = [np.loadtxt(path, delimiter=",") for path in paths]
    if training.ndim != 2 or training.shape[1] != 38 or testing.shape != (len(labels), 38):
        raise ValueError("Invalid SMD dimensions")
    if len(testing) < WINDOW or not np.isfinite(testing).all() or not np.isin(labels, [0, 1]).all():
        raise ValueError("Invalid SMD test values or labels")
    model = Detector(training, WINDOW)
    started = time.perf_counter()
    scores, models, errors = model.predict(testing)
    elapsed = time.perf_counter() - started
    labels = labels[WINDOW - 1:].astype(int)
    update("smd", warning=model.warning, critical=model.critical, benchmark={
        "Isolation Forest": evaluate(labels, models[:, 0] > model.model_cutoffs[0]),
        "GRU": evaluate(labels, models[:, 1] > model.model_cutoffs[1]),
        "Combined": evaluate(labels, scores > model.warning),
        "points": len(scores), "batch_ms_per_point": 1000 * elapsed / len(scores),
    })
    names = [f"SMD #{index}" for index in range(1, 39)]
    update("smd", metric_names=names)
    reaction = Reaction("smd")
    interval = float(os.getenv("SMD_INTERVAL_SECONDS", "0.1"))
    if not np.isfinite(interval) or interval <= 0:
        raise ValueError("SMD_INTERVAL_SECONDS must be positive")
    start = int(os.getenv("SMD_START_INDEX", "0"))
    if not 0 <= start < len(testing):
        raise ValueError("SMD_START_INDEX is outside the test set")
    offset = max(0, start - (WINDOW - 1))
    for index in range(offset, len(scores)):
        step = index + WINDOW - 1
        publish("smd", model, names, step, scores[index], models[index],
                errors[index], reaction, label=int(labels[index]),
                values=dict(zip(names, testing[step].tolist())))
        time.sleep(interval)
    update("smd", status="replay completed")


def run(mode, worker):
    try:
        worker()
    except Exception as exc:
        update(mode, status=f"failed: {exc}")
        raise


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/status":
            self.send_error(404)
            return
        params = parse_qs(url.query)
        mode = params.get("mode", [None])[0]
        if mode is not None and mode not in STATE:
            self.send_error(400, "Unknown telemetry mode")
            return
        same_stream = mode is not None and params.get("stream") == [STREAM_ID]
        try:
            after = int(params.get("after", [-1])[0]) if same_stream else -1
            after_telemetry = int(params.get("after_telemetry", [-1])[0]) if same_stream else -1
        except ValueError:
            self.send_error(400, "Invalid telemetry cursor")
            return
        with LOCK:
            snapshot = {
                key: dict(STATE[key], points=[
                    point for point in STATE[key]["points"] if point["step"] > after
                ])
                for key in ([mode] if mode else STATE)
            }
            snapshot["stream"] = STREAM_ID
            snapshot["actions"] = [event for event in ACTIONS if mode is None or event["mode"] == mode]
            snapshot["action_mode"] = TARGET
            if "live" in snapshot:
                snapshot["live"]["telemetry"] = [
                    point for point in LIVE_TELEMETRY if point["step"] > after_telemetry
                ]
                snapshot["live"]["metric_names"] = list(EXPRESSIONS)
        if "live" in snapshot:
            snapshot["live"]["memory_guard"] = memory_guard_status()
            snapshot["live"]["memory_lab"] = memory_guard_status("lab/status.json")
        data = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    if TARGET != "off":
        DEMO.start()
    for source, function in (("smd", replay), ("live", live)):
        threading.Thread(target=run, args=(source, function), daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8090), Handler).serve_forever()
