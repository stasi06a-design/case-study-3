"""
app.py - Knowledge Hub Monitoring Frontend
Serves the web dashboard for the monitoring application.
Retrieves data from the kh-api backend and renders HTML pages.

Routes:
    GET /           - Redirect to dashboard
    GET /dashboard  - Overview: latest metrics per host, API health
    GET /metrics    - Filterable table of all stored measurements
    GET /health     - Frontend health check (JSON)
"""

import os
import logging
from datetime import datetime

import requests as http_client
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, jsonify

load_dotenv()

app = Flask(__name__)

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/frontend.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8000')

METRIC_UNITS = {
    'cpu':          '%',
    'memory':       '%',
    'disk':         '%',
    'network_sent': 'MB',
    'network_recv': 'MB',
    'boot_time':    'hours'
}

METRIC_LABELS = {
    'cpu':          'CPU Usage',
    'memory':       'Memory Usage',
    'disk':         'Disk Usage',
    'network_sent': 'Network Sent',
    'network_recv': 'Network Received',
    'boot_time':    'Uptime'
}


def fetch_from_api(endpoint, params=None):
    """Make a GET request to the backend API.

    Args:
        endpoint (str): API path, e.g. '/metrics' or '/health'
        params (dict, optional): Query string parameters.

    Returns:
        tuple: (data dict or None, error string or None)
    """
    try:
        url = f"{API_BASE_URL}{endpoint}"
        response = http_client.get(url, params=params, timeout=5)
        response.raise_for_status()
        return response.json(), None
    except http_client.exceptions.ConnectionError:
        logging.warning(f"Cannot reach API at {API_BASE_URL}")
        return None, "Cannot reach the monitoring API. Check that the backend is running."
    except http_client.exceptions.Timeout:
        logging.warning(f"API request timed out: {endpoint}")
        return None, "The monitoring API did not respond in time."
    except Exception as e:
        logging.error(f"Unexpected error fetching {endpoint}: {str(e)}")
        return None, f"Unexpected error: {str(e)}"


@app.route('/')
def index():
    """Redirect root to dashboard."""
    return redirect(url_for('dashboard'))


@app.route('/health')
def health():
    """Frontend health check endpoint. Used by container monitoring in Task 5."""
    return jsonify({
        'status': 'ok',
        'service': 'kh-frontend',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/dashboard')
def dashboard():
    """Main overview page.

    Fetches the latest reading for each metric and the API health status.
    Renders metric summary cards and a time-series chart for cpu, memory and disk.
    Falls back to error page if the API is unreachable.
    """
    # Check API health
    health_data, health_error = fetch_from_api('/health')
    api_healthy = health_data is not None

    # Fetch recent measurements for charts and latest values (limit 100)
    data, error = fetch_from_api('/metrics', params={'limit': 100})

    if error and not api_healthy:
        logging.warning("Dashboard rendered in degraded state — API unreachable")
        return render_template('error.html', error=error), 503

    measurements = data.get('measurements', []) if data else []

    # Compute latest value per metric across all measurements
    latest = {}
    for m in measurements:
        metric = m['metric']
        if metric not in latest:
            latest[metric] = m  # measurements are newest-first

    # Build chart data for cpu, memory, disk (reverse to chronological order)
    chart_metrics = ['cpu', 'memory', 'disk']
    chart_data = {}
    for metric in chart_metrics:
        series = [m for m in measurements if m['metric'] == metric]
        series.reverse()  # oldest first for chart
        chart_data[metric] = {
            'labels': [m['timestamp'] for m in series],
            'values': [m['value'] for m in series]
        }

    return render_template(
        'dashboard.html',
        latest=latest,
        chart_data=chart_data,
        api_healthy=api_healthy,
        metric_labels=METRIC_LABELS,
        metric_units=METRIC_UNITS,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    )


@app.route('/metrics')
def metrics():
    """Detailed metrics table with optional filters.

    Query parameters:
        hostname (str): filter by hostname
        metric (str):   filter by metric name

    Falls back to error page if the API is unreachable.
    """
    hostname = request.args.get('hostname', '')
    metric_filter = request.args.get('metric', '')

    params = {'limit': 200}
    if hostname:
        params['hostname'] = hostname
    if metric_filter:
        params['metric'] = metric_filter

    data, error = fetch_from_api('/metrics', params=params)

    if error:
        return render_template('error.html', error=error), 503

    measurements = data.get('measurements', []) if data else []

    # Collect unique hostnames for the filter dropdown
    all_data, _ = fetch_from_api('/metrics', params={'limit': 500})
    all_measurements = all_data.get('measurements', []) if all_data else []
    hostnames = sorted(set(m['hostname'] for m in all_measurements))

    return render_template(
        'metrics.html',
        measurements=measurements,
        hostnames=hostnames,
        metric_names=list(METRIC_LABELS.keys()),
        metric_labels=METRIC_LABELS,
        selected_hostname=hostname,
        selected_metric=metric_filter,
        count=len(measurements)
    )


if __name__ == '__main__':
    logging.info("Starting KH Monitoring Frontend")
    app.run(host='0.0.0.0', port=5000, debug=False)