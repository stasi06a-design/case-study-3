"""
monitor.py - System Monitoring Application
Knowledge Hub Library IT Infrastructure

Usage:
  Measure metrics and store them:
    python3 monitor.py measure
    python3 monitor.py measure --metrics cpu memory disk

  Retrieve stored measurements:
    python3 monitor.py report
    python3 monitor.py report --start "2024-01-01 00:00:00" --end "2024-12-31 23:59:59"
    python3 monitor.py report --metric cpu
    python3 monitor.py report --average

  Reset the database (clears all stored data):
    python3 monitor.py reset
"""

import argparse
import sqlite3
import socket
import sys
import os
from datetime import datetime

try:
    import psutil
except ImportError:
    print("Error: psutil is not installed. Run: pip3 install psutil")
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_FILE = "monitor_data.db"

AVAILABLE_METRICS = ["cpu", "memory", "disk", "network_sent", "network_recv", "boot_time"]


# ── Database ──────────────────────────────────────────────────────────────────

def init_db(conn):
    """Create the measurements table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            hostname    TEXT    NOT NULL,
            metric      TEXT    NOT NULL,
            value       REAL    NOT NULL,
            unit        TEXT    NOT NULL
        )
    """)
    conn.commit()


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    return conn


# ── Metric collection ─────────────────────────────────────────────────────────

def collect_metrics(selected):
    """Collect selected metrics using psutil. Returns list of (metric, value, unit)."""
    results = []
    if "cpu" in selected:
        results.append(("cpu", psutil.cpu_percent(interval=1), "%"))
    if "memory" in selected:
        mem = psutil.virtual_memory()
        results.append(("memory", mem.percent, "%"))
    if "disk" in selected:
        disk = psutil.disk_usage("/")
        results.append(("disk", disk.percent, "%"))
    if "network_sent" in selected:
        net = psutil.net_io_counters()
        results.append(("network_sent", round(net.bytes_sent / (1024 ** 2), 2), "MB"))
    if "network_recv" in selected:
        net = psutil.net_io_counters()
        results.append(("network_recv", round(net.bytes_recv / (1024 ** 2), 2), "MB"))
    if "boot_time" in selected:
        uptime_seconds = (datetime.now() - datetime.fromtimestamp(psutil.boot_time())).seconds
        results.append(("boot_time", round(uptime_seconds / 3600, 2), "hours"))
    return results


# ── API integration ───────────────────────────────────────────────────────────

def send_to_api(readings, hostname, timestamp):
    """Send collected metrics to the Flask API via HTTP POST.
    
    Reads API_URL from .env file. Failures are non-fatal —
    data is always saved locally regardless of API availability.
    """
    if requests is None:
        print("[API] Warning: requests library not installed - skipping API send")
        return

    api_url = os.getenv('API_URL')
    if not api_url:
        print("[API] Warning: API_URL not set in .env file - skipping API send")
        return

    payload = [
        {
            'hostname': hostname,
            'metric': metric,
            'value': value,
            'unit': unit
        }
        for metric, value, unit in readings
    ]

    try:
        response = requests.post(
            f"{api_url}/metrics",
            json=payload,
            timeout=10
        )
        if response.status_code == 201:
            print(f"[API] Successfully sent {len(payload)} metrics to API")
        else:
            print(f"[API] Warning: API returned status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("[API] Warning: Could not connect to API - data saved locally only")
    except requests.exceptions.Timeout:
        print("[API] Warning: API request timed out - data saved locally only")
    except Exception as e:
        print(f"[API] Warning: Failed to send to API: {str(e)}")


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_measure(args):
    """Collect metrics and write them to the database."""
    selected = args.metrics if args.metrics else AVAILABLE_METRICS
    invalid = [m for m in selected if m not in AVAILABLE_METRICS]
    if invalid:
        print(f"Error: Unknown metrics: {', '.join(invalid)}")
        print(f"Available metrics: {', '.join(AVAILABLE_METRICS)}")
        sys.exit(1)

    hostname = socket.gethostname()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    readings = collect_metrics(selected)

    conn = get_connection()
    for metric, value, unit in readings:
        conn.execute(
            "INSERT INTO measurements (timestamp, hostname, metric, value, unit) VALUES (?, ?, ?, ?, ?)",
            (timestamp, hostname, metric, value, unit)
        )
    conn.commit()
    conn.close()

    # Send to API — non-fatal if unavailable
    send_to_api(readings, hostname, timestamp)

    print(f"\n[{timestamp}] Measurements stored for host: {hostname}")
    print(f"{'Metric':<16} {'Value':>10} {'Unit':<6}")
    print("-" * 36)
    for metric, value, unit in readings:
        print(f"{metric:<16} {value:>10} {unit:<6}")


def mode_report(args):
    """Retrieve and display stored measurements, optionally filtered by time and metric."""
    conn = get_connection()

    query = "SELECT timestamp, hostname, metric, value, unit FROM measurements WHERE 1=1"
    params = []

    if args.start:
        query += " AND timestamp >= ?"
        params.append(args.start)
    if args.end:
        query += " AND timestamp <= ?"
        params.append(args.end)
    if args.metric:
        query += " AND metric = ?"
        params.append(args.metric)

    query += " ORDER BY timestamp ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("No measurements found for the given filters.")
        return

    print(f"\n{'Timestamp':<22} {'Host':<16} {'Metric':<16} {'Value':>10} {'Unit':<6}")
    print("-" * 74)
    for ts, host, metric, value, unit in rows:
        print(f"{ts:<22} {host:<16} {metric:<16} {value:>10} {unit:<6}")

    if args.average:
        print("\n--- Averages ---")
        metrics_seen = set(r[2] for r in rows)
        for m in sorted(metrics_seen):
            values = [r[3] for r in rows if r[2] == m]
            unit = next(r[4] for r in rows if r[2] == m)
            avg = sum(values) / len(values)
            print(f"  {m:<16} avg: {avg:>8.2f} {unit}")

    print(f"\nTotal records displayed: {len(rows)}")


def mode_reset(args):
    """Drop and recreate the measurements table, wiping all data."""
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS measurements")
    conn.commit()
    init_db(conn)
    conn.close()
    print("Database reset. All measurements have been deleted.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Knowledge Hub System Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # measure
    p_measure = subparsers.add_parser("measure", help="Collect and store metrics")
    p_measure.add_argument(
        "--metrics", nargs="+", metavar="METRIC",
        help=f"Metrics to collect (default: all). Choices: {', '.join(AVAILABLE_METRICS)}"
    )

    # report
    p_report = subparsers.add_parser("report", help="Retrieve and display stored metrics")
    p_report.add_argument("--start", metavar="DATETIME", help='Filter start time e.g. "2024-01-01 00:00:00"')
    p_report.add_argument("--end",   metavar="DATETIME", help='Filter end time   e.g. "2024-12-31 23:59:59"')
    p_report.add_argument("--metric", metavar="METRIC",  help="Filter by metric name")
    p_report.add_argument("--average", action="store_true", help="Show averages for displayed metrics")

    # reset
    subparsers.add_parser("reset", help="Delete all stored measurements (clean slate)")

    args = parser.parse_args()

    if args.mode == "measure":
        mode_measure(args)
    elif args.mode == "report":
        mode_report(args)
    elif args.mode == "reset":
        mode_reset(args)


if __name__ == "__main__":
    main()