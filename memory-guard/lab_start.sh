#!/usr/bin/env bash
set -euo pipefail

name=nirs-memory-test
dir=/var/lib/nirs-memory-guard/lab
url=http://127.0.0.1:13003/health
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this experiment as root." >&2
    exit 1
fi
if docker container inspect "$name" >/dev/null 2>&1 || [ -e "$dir" ]; then
    echo "A test container or result already exists. Archive it before running another experiment." >&2
    exit 1
fi
if [ -n "$(ss -H -ltn 'sport = :13003')" ]; then
    echo "TCP port 13003 is occupied." >&2
    exit 1
fi
test -r /sys/fs/cgroup/cgroup.controllers
install -d -m 755 "$dir" "$dir/workload"
install -m 644 "$source_dir/lab_workload.py" "$dir/lab_workload.py"
install -m 644 "$source_dir/guard.py" "$dir/guard.py"

image="$(docker compose images -q monitor | head -n 1)"
test -n "$image"
docker run -d --name "$name" --memory 256m --memory-swap 256m \
    --cpus 0.25 --pids-limit 32 --cap-drop ALL --security-opt no-new-privileges \
    --user 0:0 --publish 127.0.0.1:13003:8000 \
    --mount "type=bind,src=$dir/lab_workload.py,dst=/lab_workload.py,readonly" \
    --mount "type=bind,src=$dir/workload,dst=/trial" \
    "$image" python -u /lab_workload.py

if ! curl -fsS --max-time 3 --retry 10 --retry-delay 1 --retry-all-errors "$url"; then
    docker container inspect --format '{{.State.Status}} (exit {{.State.ExitCode}})' "$name"
    docker logs --tail 30 "$name"
    exit 1
fi
systemd-run --unit=nirs-memory-guard-lab --on-calendar=minutely \
    --timer-property=AccuracySec=1s --property=TimeoutStartSec=145s \
    --setenv="NIRS_GUARD_STATE=$dir/status.json" \
    --setenv=TARGET_KIND=docker --setenv="TARGET_NAME=$name" \
    --setenv=GUARD_MODE=restart --setenv=MEMORY_BUDGET_MIB=128 \
    --setenv="HEALTH_URL=$url" --setenv=ALERT_WEBHOOK_URL= \
    /usr/bin/python3 "$dir/guard.py"

echo "Open Grafana > Server > the RAM restart experiment panel."
