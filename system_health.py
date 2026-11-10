#!/usr/bin/env python3
"""
system_health.py - quick health check for an Ubuntu machine.

Checks CPU, memory and root-disk usage, prints a short report and appends
the same results to a log file. Uses only the Python standard library
(reads /proc directly), so nothing needs installing.

Usage:
    python3 system_health.py                 # report + log to default location
    python3 system_health.py --log /tmp/h.log
    python3 system_health.py --disk /home    # check a different mount point

Exit code reflects the worst status: 0 = OK, 1 = WARNING, 2 = CRITICAL,
which makes it easy to use from cron or a monitoring script.
"""

import argparse
import logging
import os
import shutil
import socket
import sys
import time
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------------------
# Thresholds (percent used). A value >= WARNING is a warning, >= CRITICAL is
# critical, anything lower is OK.
#
# CPU:    brief spikes are normal; sustained >75% means something is busy,
#         >90% means the box is struggling to keep up.
# Memory: Linux uses spare RAM for cache, so we measure "available" memory.
#         >80% used is worth watching, >90% risks swapping / OOM kills.
# Disk:   >80% is time to tidy up; >90% is urgent (logs, apt and DBs can
#         fail when a disk fills, and ext4 reserves ~5% for root).
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "CPU":    {"WARNING": 75, "CRITICAL": 90},
    "Memory": {"WARNING": 80, "CRITICAL": 90},
    "Disk":   {"WARNING": 80, "CRITICAL": 90},
}

STATUS_LEVEL = {"OK": 0, "WARNING": 1, "CRITICAL": 2}

DEFAULT_LOG = os.path.expanduser("~/.local/state/system_health/system_health.log")


# --------------------------------------------------------------------------- metrics
def _read_cpu_times():
    """Return (idle, total) jiffies from the aggregate 'cpu' line of /proc/stat."""
    with open("/proc/stat") as f:
        fields = [int(x) for x in f.readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    return idle, sum(fields)


def cpu_percent(interval=1.0):
    """CPU usage across all cores, sampled over `interval` seconds."""
    idle1, total1 = _read_cpu_times()
    time.sleep(interval)
    idle2, total2 = _read_cpu_times()
    total_delta = total2 - total1
    if total_delta <= 0:
        return 0.0
    return 100.0 * (1 - (idle2 - idle1) / total_delta)


def memory_percent():
    """Memory in use, based on MemAvailable (excludes reclaimable cache)."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0])  # kB
    total = info["MemTotal"]
    available = info.get("MemAvailable", info["MemFree"])
    return 100.0 * (total - available) / total


def disk_percent(path="/"):
    """Disk usage of the filesystem holding `path`, as `df` reports it."""
    usage = shutil.disk_usage(path)
    # df's Use% = used / (used + available); this accounts for root-reserved blocks
    return 100.0 * usage.used / (usage.used + usage.free)


# --------------------------------------------------------------------------- logic
def classify(metric, value):
    t = THRESHOLDS[metric]
    if value >= t["CRITICAL"]:
        return "CRITICAL"
    if value >= t["WARNING"]:
        return "WARNING"
    return "OK"


def collect(disk_path="/"):
    readings = [
        ("CPU", cpu_percent()),
        ("Memory", memory_percent()),
        ("Disk", disk_percent(disk_path)),
    ]
    return [(name, value, classify(name, value)) for name, value in readings]


def setup_logger(log_path):
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    logger = logging.getLogger("system_health")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


LOG_LEVEL = {"OK": logging.INFO, "WARNING": logging.WARNING, "CRITICAL": logging.CRITICAL}


def main():
    parser = argparse.ArgumentParser(description="Show CPU, memory and disk health.")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"log file (default: {DEFAULT_LOG})")
    parser.add_argument("--disk", default="/", help="mount point to check (default: /)")
    args = parser.parse_args()

    hostname = socket.gethostname()
    results = collect(args.disk)
    worst = max(results, key=lambda r: STATUS_LEVEL[r[2]])[2]

    # --- display
    print("SYSTEM HEALTH")
    print("-" * 25)
    print(f"Hostname: {hostname}")
    for name, value, status in results:
        print(f"{name + ':':<10} {value:>3.0f}%     {status}")

    # --- log
    try:
        logger = setup_logger(args.log)
        for name, value, status in results:
            logger.log(LOG_LEVEL[status], f"host={hostname} metric={name} value={value:.1f}% status={status}")
        logger.log(LOG_LEVEL[worst], f"host={hostname} overall={worst}")
    except OSError as e:
        print(f"\n(could not write log {args.log}: {e})", file=sys.stderr)

    return STATUS_LEVEL[worst]


if __name__ == "__main__":
    sys.exit(main())
