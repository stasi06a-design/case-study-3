"""
container_monitor.py - Docker Container Metrics Collector
Knowledge Hub Monitoring Application

Collects per-container CPU, memory, network and status metrics
from the Docker Engine API and sends them to the kh-api backend.

Usage:
    python3 container_monitor.py
"""

import os
import logging
import requests

try:
    import docker
except ImportError:
    print("[Container Monitor] docker library not installed. Run: pip install docker")
    exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/container_monitor.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

API_URL = os.getenv('API_URL', 'http://localhost:8000')


def calculate_cpu_percent(stats):
    """Calculate CPU usage percentage from Docker stats snapshot.

    Docker returns raw CPU nanoseconds. This formula converts them
    to a percentage by comparing the container's CPU delta against
    the total system CPU delta across all cores.

    Args:
        stats (dict): Raw stats dict from container.stats(stream=False)

    Returns:
        float: CPU usage percentage, or 0.0 if calculation fails.
    """
    try:
        cpu_delta = (
            stats['cpu_stats']['cpu_usage']['total_usage'] -
            stats['precpu_stats']['cpu_usage']['total_usage']
        )
        system_delta = (
            stats['cpu_stats']['system_cpu_usage'] -
            stats['precpu_stats']['system_cpu_usage']
        )
        num_cpus = stats['cpu_stats'].get('online_cpus',
                   len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1])))

        if system_delta > 0 and cpu_delta > 0:
            return round((cpu_delta / system_delta) * num_cpus * 100, 2)
        return 0.0
    except (KeyError, ZeroDivisionError):
        return 0.0


def collect_container_metrics():
    """Collect metrics for all running containers via Docker Engine API.

    Connects to Docker via the socket, iterates all running containers,
    and collects CPU, memory, network and status metrics for each.

    Returns:
        list: List of metric dicts ready to POST to kh-api, or empty list
              if Docker is not accessible.
    """
    try:
        client = docker.from_env()
    except Exception as e:
        logging.warning(f"Cannot connect to Docker socket: {e}")
        print(f"[Container Monitor] Cannot connect to Docker: {e}")
        return []

    payload = []

    try:
        containers = client.containers.list()
    except Exception as e:
        logging.error(f"Failed to list containers: {e}")
        return []

    for container in containers:
        name = container.name
        status = 1 if container.status == 'running' else 0

        try:
            stats = container.stats(stream=False)

            cpu_percent = calculate_cpu_percent(stats)

            memory_bytes = stats.get('memory_stats', {}).get('usage', 0)
            memory_mb = round(memory_bytes / (1024 ** 2), 2)

            networks = stats.get('networks', {})
            net_recv = 0.0
            net_sent = 0.0
            for iface in networks.values():
                net_recv += iface.get('rx_bytes', 0)
                net_sent += iface.get('tx_bytes', 0)
            net_recv_mb = round(net_recv / (1024 ** 2), 2)
            net_sent_mb = round(net_sent / (1024 ** 2), 2)

        except Exception as e:
            logging.warning(f"Failed to get stats for {name}: {e}")
            cpu_percent = 0.0
            memory_mb = 0.0
            net_recv_mb = 0.0
            net_sent_mb = 0.0

        payload.extend([
            {'hostname': name, 'metric': 'container_cpu',
             'value': cpu_percent,    'unit': '%'},
            {'hostname': name, 'metric': 'container_memory',
             'value': memory_mb,      'unit': 'MB'},
            {'hostname': name, 'metric': 'container_net_recv',
             'value': net_recv_mb,    'unit': 'MB'},
            {'hostname': name, 'metric': 'container_net_sent',
             'value': net_sent_mb,    'unit': 'MB'},
            {'hostname': name, 'metric': 'container_status',
             'value': status,         'unit': '1=run/0=stop'},
        ])

        print(f"[Container Monitor] {name}: CPU={cpu_percent}% "
              f"MEM={memory_mb}MB STATUS={'running' if status else 'stopped'}")

    return payload


def send_to_api(payload):
    """Send collected container metrics to the kh-api backend.

    Args:
        payload (list): List of metric dicts to POST.
    """
    if not payload:
        print("[Container Monitor] No metrics to send")
        return

    try:
        response = requests.post(
            f"{API_URL}/metrics",
            json=payload,
            timeout=10
        )
        if response.status_code == 201:
            print(f"[Container Monitor] Successfully sent {len(payload)} container metrics")
            logging.info(f"Sent {len(payload)} container metrics")
        else:
            print(f"[Container Monitor] API returned {response.status_code}")
            logging.warning(f"API returned {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("[Container Monitor] Cannot reach API — data not sent")
        logging.warning("Cannot reach API")
    except Exception as e:
        print(f"[Container Monitor] Error sending metrics: {e}")
        logging.error(f"Error: {e}")


if __name__ == '__main__':
    print("[Container Monitor] Collecting container metrics...")
    payload = collect_container_metrics()
    send_to_api(payload)