import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

LOG = logging.getLogger("memory-guard")
ROOT = Path("/sys/fs/cgroup")
STATE = Path(os.getenv("NIRS_GUARD_STATE", "/var/lib/nirs-memory-guard/status.json"))
WINDOW = 11


def run(*args, timeout=65):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


class Target:
    def __init__(self, kind, name):
        valid = re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*", name)
        if kind not in {"docker", "systemd"} or not valid:
            raise ValueError("Set TARGET_KIND and a single valid TARGET_NAME")
        if kind == "systemd" and not name.endswith(".service"):
            raise ValueError("TARGET_NAME must end in .service")
        self.kind, self.name = kind, name

    def measurement(self):
        if self.kind == "docker":
            info = json.loads(run("docker", "inspect", "--type", "container", self.name))[0]
            pid = info["State"]["Pid"]
            if pid <= 0:
                raise ValueError("Target container is stopped")
            entry = next((line[3:] for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                          if line.startswith("0::")), "")
            identity = f"{info['Id']}:{info['State']['StartedAt']}"
        else:
            properties = dict(line.split("=", 1) for line in run(
                "systemctl", "show", "--property=ControlGroup,InvocationID,ActiveState", self.name
            ).splitlines() if "=" in line)
            if properties.get("ActiveState") != "active":
                raise ValueError("Target unit is inactive")
            entry = properties.get("ControlGroup", "")
            identity = properties.get("InvocationID", "")
        if not entry.startswith("/") or entry == "/" or not identity:
            raise ValueError("Target has no dedicated cgroup v2")
        location = (ROOT / entry.lstrip("/")).resolve()
        if not location.is_relative_to(ROOT) or location == ROOT:
            raise ValueError("Unexpected cgroup path")
        usage = int((location / "memory.current").read_text())
        limits = []
        while location != ROOT:
            value = (location / "memory.max").read_text().strip()
            if value != "max":
                limits.append(int(value))
            location = location.parent
        return usage, min(limits, default=None), identity

    def restart(self):
        if self.kind == "docker":
            run("docker", "restart", "--time", "10", self.name)
        else:
            run("systemctl", "restart", self.name)


def leak_candidate(samples, limit):
    if len(samples) < WINDOW:
        return False
    recent = samples[-WINDOW:]
    if any(not 35 <= b[0] - a[0] <= 90 for a, b in zip(recent, recent[1:])):
        return False
    memory = [sample[1] for sample in recent]
    increases = sum(b > a for a, b in zip(memory, memory[1:]))
    return (memory[-1] >= 0.85 * limit and memory[-1] - memory[0] >= 0.05 * limit
            and increases >= 7)


def healthy(url):
    with urlopen(url, timeout=3) as response:
        return 200 <= response.status < 300


def event(state, status, message):
    item = {"time": time.time(), "status": status, "message": message}
    state["events"] = (state.get("events", []) + [item])[-30:]
    LOG.info("%s: %s", status, message)
    return item


def notify(item, target):
    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return
    try:
        body = json.dumps(dict(item, target=target)).encode()
        request = Request(url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5):
            pass
    except OSError:
        LOG.error("Alert webhook delivery failed; see the retained event in the journal")


def save(state):
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.chmod(0o644)
    temp.replace(STATE)


def remember(state, when, memory):
    history = state.get("history", state.get("samples", []))
    state["history"] = (history + [[when, memory]])[-120:]


def check_recovery(target, identity, previous, limit, url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            memory, _, new_identity = target.measurement()
            if new_identity != identity and previous - memory >= 0.05 * limit and memory < 0.85 * limit and healthy(url):
                return memory
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(3)
    raise RuntimeError("Не подтвердились новый экземпляр, HTTP-доступность и снижение памяти")


def main():
    name = os.environ["TARGET_NAME"]
    target = Target(os.environ["TARGET_KIND"], name)
    mode = os.getenv("GUARD_MODE", "observe")
    if mode not in {"observe", "restart"}:
        raise ValueError("GUARD_MODE must be observe or restart")
    budget_mib = int(os.getenv("MEMORY_BUDGET_MIB", "0"))
    cooldown = int(os.getenv("RESTART_COOLDOWN_SECONDS", "3600"))
    url = os.getenv("HEALTH_URL", "")
    if budget_mib < 0 or cooldown < 900 or (mode == "restart" and not url.startswith(("http://", "https://"))):
        raise ValueError("Check memory budget, cooldown and health URL")
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}
    target_key = f"{target.kind}:{name}"
    if state.get("target") != target_key:
        state = {}
    state.update(target=target_key, mode=mode, updated_at=int(time.time()))

    try:
        memory, hard_limit, identity = target.measurement()
        limits = [value for value in (hard_limit, budget_mib * 1024 ** 2) if value is not None and value > 0]
        if not limits:
            raise ValueError("No memory limit; set MEMORY_BUDGET_MIB")
        limit = min(limits)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        LOG.error("Measurement unavailable: %s", exc)
        state.update(status="unavailable", samples=[], limit=None)
        save(state)
        return

    if identity != state.get("identity") or limit != state.get("limit"):
        state["samples"] = []
        state.pop("alerted", None)
    state.update(identity=identity, limit=limit)
    now = int(time.time())
    remember(state, now, memory)
    state["samples"] = (state.get("samples", []) + [[now, memory]])[-60:]
    sustained = leak_candidate(state["samples"], limit)
    high = memory >= 0.85 * limit
    state["status"] = "sustained" if sustained else "high" if high else "normal"
    if high and not state.get("high_logged"):
        event(state, "high", "Память выше 85% бюджета")
    state["high_logged"] = high
    if state.get("latched"):
        state["status"] = "latched"
    if not sustained:
        state.pop("alerted", None)
    last_attempt = state.get("last_attempt")
    cooling_down = last_attempt is not None and now - last_attempt < cooldown
    if not sustained or mode == "observe" or state.get("latched") or cooling_down:
        if sustained and not state.get("alerted"):
            state["alerted"] = True
            item = event(state, "detected", "Длительный рост памяти вблизи лимита")
            save(state)
            notify(item, name)
        else:
            save(state)
        return

    # Persist the lockout before invoking the restart.
    state.update(last_attempt=now, latched=True, status="restarting")
    event(state, "restart", "Перезапуск после длительного роста памяти")
    save(state)
    try:
        target.restart()
        after = check_recovery(target, identity, memory, limit, url)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        state["status"] = "latched"
        LOG.error("Recovery was not verified: %s", exc)
        item = event(state, "failed", "Восстановление не подтверждено. Повторные перезапуски заблокированы")
    else:
        state.update(latched=False, status="recovered", samples=[])
        item = event(state, "recovered", f"HTTP отвечает, память {memory / 1024**2:.1f} → {after / 1024**2:.1f} МиБ")
        remember(state, item["time"], after)
    save(state)
    notify(item, name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
