"""
app.py - Knowledge Hub Monitoring Frontend
Serves the web dashboard for the monitoring application.
Retrieves data from the kh-api backend and renders HTML pages.
Authentication via Microsoft Entra ID (OAuth 2.0 / OIDC).

Routes:
    GET /           - Redirect to dashboard
    GET /dashboard  - Overview: latest metrics per host, API health [AUTH REQUIRED]
    GET /metrics    - Filterable table of all stored measurements [AUTH REQUIRED]
    GET /health     - Frontend health check (JSON) [PUBLIC]
    GET /login      - Initiates Entra ID OAuth flow [PUBLIC]
    GET /callback   - Handles OAuth callback from Microsoft [PUBLIC]
    GET /logout     - Clears session and logs out of Microsoft SSO [PUBLIC]
"""

import os
import logging
import functools
from datetime import datetime

import msal
import requests as http_client
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, session)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-change-in-production')

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/frontend.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ── Configuration ──────────────────────────────────────────────────────────────
API_BASE_URL        = os.getenv('API_BASE_URL', 'http://localhost:8000')
AZURE_CLIENT_ID     = os.getenv('AZURE_CLIENT_ID')
AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET')
AZURE_TENANT_ID     = os.getenv('AZURE_TENANT_ID')

AUTHORITY    = "https://login.microsoftonline.com/common"
REDIRECT_URI = os.getenv('REDIRECT_URI', 'http://localhost:5000/callback')
SCOPES       = ["User.Read"]

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


# ── MSAL helper ────────────────────────────────────────────────────────────────

def _build_msal_app():
    """Create and return an MSAL ConfidentialClientApplication."""
    return msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID,
        authority=AUTHORITY,
        client_credential=AZURE_CLIENT_SECRET
    )


# ── Authentication decorator ───────────────────────────────────────────────────

def login_required(f):
    """Decorator that protects routes from unauthenticated access.

    Checks for 'user' in the Flask session. If absent, stores the
    requested path in session['next'] and redirects to /login.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            session['next'] = request.path
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ── API communication ──────────────────────────────────────────────────────────

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


# ── Authentication routes ──────────────────────────────────────────────────────

@app.route('/login')
def login():
    """Initiate the Entra ID OAuth 2.0 Authorisation Code Flow.

    Builds the Microsoft login URL via MSAL and stores the flow
    state in the session for CSRF verification in /callback.
    """
    msal_app = _build_msal_app()
    flow = msal_app.initiate_auth_code_flow(
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    session['flow'] = flow
    logging.info("Auth flow initiated, redirecting to Microsoft login")
    return redirect(flow['auth_uri'])


@app.route('/callback')
def callback():
    """Handle the OAuth callback from Microsoft.

    Exchanges the authorisation code for tokens, validates the
    response, and stores user claims in the session.
    """
    flow = session.pop('flow', None)

    if not flow:
        logging.warning("Callback received with no flow in session")
        return redirect(url_for('login'))

    try:
        msal_app = _build_msal_app()
        result = msal_app.acquire_token_by_auth_code_flow(
            flow,
            request.args
        )
    except Exception as e:
        logging.error(f"Token acquisition failed: {str(e)}")
        return redirect(url_for('login'))

    if 'error' in result:
        logging.warning(f"Auth error: {result.get('error')}: {result.get('error_description')}")
        return redirect(url_for('login'))

    claims = result.get('id_token_claims', {})
    session['user'] = {
        'name':               claims.get('name', 'Unknown User'),
        'preferred_username': claims.get('preferred_username', ''),
        'oid':                claims.get('oid', '')
    }

    logging.info(f"User authenticated: {session['user']['preferred_username']}")

    next_url = session.pop('next', url_for('dashboard'))
    return redirect(next_url)


@app.route('/logout')
def logout():
    """Clear the Flask session and terminate the Microsoft SSO session."""
    preferred_username = session.get('user', {}).get('preferred_username', '')
    session.clear()
    logging.info(f"User logged out: {preferred_username}")

    logout_url = (
        f"https://login.microsoftonline.com/common/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={url_for('login', _external=True)}"
    )
    return redirect(logout_url)


# ── Application routes ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Redirect root to dashboard."""
    return redirect(url_for('dashboard'))


@app.route('/health')
def health():
    """Frontend health check. Public — no authentication required."""
    return jsonify({
        'status': 'ok',
        'service': 'kh-frontend',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/dashboard')
@login_required
def dashboard():
    """Main overview page. Requires authentication."""
    current_user = session['user']

    health_data, health_error = fetch_from_api('/health')
    api_healthy = health_data is not None

    data, error = fetch_from_api('/metrics', params={'limit': 100})

    if error and not api_healthy:
        logging.warning("Dashboard rendered in degraded state — API unreachable")
        return render_template('error.html', error=error, current_user=current_user), 503

    measurements = data.get('measurements', []) if data else []

    latest = {}
    for m in measurements:
        metric = m['metric']
        if metric not in latest:
            latest[metric] = m

    chart_metrics = ['cpu', 'memory', 'disk']
    chart_data = {}
    for metric in chart_metrics:
        series = [m for m in measurements if m['metric'] == metric]
        series.reverse()
        chart_data[metric] = {
            'labels': [m['timestamp'] for m in series],
            'values': [m['value'] for m in series]
        }

    # Fetch container metrics — filter for container_ metric types
    container_data, _ = fetch_from_api('/metrics', params={'limit': 500})
    container_measurements = container_data.get('measurements', []) if container_data else []
    container_metrics = [m for m in container_measurements
                        if m['metric'].startswith('container_')]

    # Get latest value per container per metric
    container_latest = {}
    for m in container_metrics:
        key = (m['hostname'], m['metric'])
        if key not in container_latest:
            container_latest[key] = m

    # Get unique container names
    container_names = sorted(set(m['hostname'] for m in container_metrics))

    return render_template(
        'dashboard.html',
        latest=latest,
        chart_data=chart_data,
        api_healthy=api_healthy,
        metric_labels=METRIC_LABELS,
        metric_units=METRIC_UNITS,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        current_user=current_user,
        container_latest=container_latest,
        container_names=container_names
    )


@app.route('/metrics')
@login_required
def metrics():
    """Detailed metrics table with optional filters. Requires authentication."""
    current_user = session['user']

    hostname = request.args.get('hostname', '')
    metric_filter = request.args.get('metric', '')

    params = {'limit': 200}
    if hostname:
        params['hostname'] = hostname
    if metric_filter:
        params['metric'] = metric_filter

    data, error = fetch_from_api('/metrics', params=params)

    if error:
        return render_template('error.html', error=error, current_user=current_user), 503

    measurements = data.get('measurements', []) if data else []

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
        count=len(measurements),
        current_user=current_user
    )


if __name__ == '__main__':
    logging.info("Starting KH Monitoring Frontend")
    app.run(host='0.0.0.0', port=5000, debug=False)