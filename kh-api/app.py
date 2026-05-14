"""
app.py - Knowledge Hub Monitoring API
Receives system metrics from monitor.py and stores them in Azure SQL Database (production)
or SQLite (development/testing).

Endpoints:
    GET  /health   - Health check
    POST /metrics  - Store metric readings
    GET  /metrics  - Retrieve stored metrics
"""

from flask import Flask, request, jsonify
import logging
import os
from datetime import datetime

app = Flask(__name__)

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DB_BACKEND = os.getenv('DB_BACKEND', 'sqlite')
SQL_SERVER = os.getenv('SQL_SERVER', '')
SQL_DATABASE = os.getenv('SQL_DATABASE', '')
SQL_USERNAME = os.getenv('SQL_USERNAME', '')
SQL_PASSWORD = os.getenv('SQL_PASSWORD', '')
SQLITE_DB = os.getenv('SQLITE_DB', 'metrics.db')

VALID_METRICS = ["cpu", "memory", "disk", "network_sent", "network_recv", "boot_time"]


def get_connection():
    """Return a database connection based on DB_BACKEND environment variable.
    
    Returns:
        pyodbc.Connection for Azure SQL or sqlite3.Connection for SQLite.
    """
    if DB_BACKEND == 'azure_sql':
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USERNAME};"
            f"PWD={SQL_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        return pyodbc.connect(conn_str)
    else:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
        return conn


def init_db():
    """Initialise the database schema.
    
    Creates the measurements table if it does not exist.
    Uses IF NOT EXISTS syntax for SQLite and checks sysobjects for Azure SQL.
    """
    conn = get_connection()
    if DB_BACKEND == 'azure_sql':
        cursor = conn.cursor()
        cursor.execute("""
            IF NOT EXISTS (
                SELECT * FROM sysobjects WHERE name='measurements' AND xtype='U'
            )
            CREATE TABLE measurements (
                id        INT IDENTITY(1,1) PRIMARY KEY,
                timestamp DATETIME2     NOT NULL,
                hostname  VARCHAR(100)  NOT NULL,
                metric    VARCHAR(50)   NOT NULL,
                value     FLOAT         NOT NULL,
                unit      VARCHAR(20)   NOT NULL
            )
        """)
        conn.commit()
    else:
        import sqlite3
        conn.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                hostname  TEXT    NOT NULL,
                metric    TEXT    NOT NULL,
                value     REAL    NOT NULL,
                unit      TEXT    NOT NULL
            )
        """)
        conn.commit()
    conn.close()
    logging.info("Database initialised")


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint.
    
    Returns:
        JSON: status ok with current UTC timestamp.
    """
    logging.info("Health check requested")
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/metrics', methods=['POST'])
def receive_metrics():
    """Receive a batch of metric readings and store them in the database.
    
    Expects a JSON array of metric objects, each containing:
        hostname (str): source host
        metric (str): metric name (must be in VALID_METRICS)
        value (float): measured value
        unit (str): unit of measurement

    Returns:
        JSON: status, number of stored rows, and timestamp.
    """
    try:
        data = request.get_json()

        if not data:
            logging.warning("POST /metrics received empty or invalid JSON")
            return jsonify({'error': 'No JSON data provided'}), 400

        if not isinstance(data, list):
            logging.warning("POST /metrics expected a list of metric rows")
            return jsonify({'error': 'Expected a list of metric readings'}), 400

        required_fields = ['hostname', 'metric', 'value', 'unit']
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_connection()
        cursor = conn.cursor()
        stored = 0

        for row in data:
            missing = [f for f in required_fields if f not in row]
            if missing:
                logging.warning(f"Skipping row missing fields: {missing}")
                continue

            if row['metric'] not in VALID_METRICS:
                logging.warning(f"Skipping unknown metric: {row['metric']}")
                continue

            cursor.execute(
                """INSERT INTO measurements
                   (timestamp, hostname, metric, value, unit)
                   VALUES (?, ?, ?, ?, ?)""",
                (timestamp, row['hostname'], row['metric'],
                 row['value'], row['unit'])
            )
            stored += 1

        conn.commit()
        conn.close()

        logging.info(f"Stored {stored} metric rows from {data[0].get('hostname', 'unknown')}")
        return jsonify({
            'status': 'ok',
            'stored': stored,
            'timestamp': timestamp
        }), 201

    except Exception as e:
        logging.error(f"Error storing metrics: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/metrics', methods=['GET'])
def get_metrics():
    """Retrieve stored metrics with optional filters.
    
    Query parameters:
        hostname (str, optional): filter by hostname
        metric (str, optional): filter by metric name
        limit (int, optional): max records to return (default 100)

    Returns:
        JSON: count and list of measurement records.
    """
    try:
        hostname = request.args.get('hostname')
        metric = request.args.get('metric')
        limit = int(request.args.get('limit', 100))

        query = """SELECT timestamp, hostname, metric, value, unit
                   FROM measurements WHERE 1=1"""
        params = []

        if hostname:
            query += " AND hostname = ?"
            params.append(hostname)
        if metric:
            if metric not in VALID_METRICS:
                return jsonify({'error': f'Unknown metric: {metric}'}), 400
            query += " AND metric = ?"
            params.append(metric)

        if DB_BACKEND == 'azure_sql':
            query = query.replace("SELECT", f"SELECT TOP {limit}")
            query += " ORDER BY timestamp DESC"
        else:
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                'timestamp': row[0],
                'hostname': row[1],
                'metric': row[2],
                'value': row[3],
                'unit': row[4]
            })

        logging.info(f"GET /metrics returned {len(results)} records")
        return jsonify({'count': len(results), 'measurements': results}), 200

    except Exception as e:
        logging.error(f"Error retrieving metrics: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Initialise DB on module load — works with both gunicorn and direct run
with app.app_context():
    try:
        init_db()
        logging.info("Database initialised on startup")
    except Exception as e:
        logging.error(f"Database init failed: {str(e)}")

if __name__ == '__main__':
    logging.info("Starting KH Monitoring API")
    app.run(host='0.0.0.0', port=8000, debug=False)