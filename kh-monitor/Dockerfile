FROM python:3.11-slim

WORKDIR /app

# Copy and install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy monitor script and run wrapper
COPY . .

# Make the run script executable
RUN chmod +x run.sh

# Create data directory for local SQLite fallback
RUN mkdir -p /app/data

CMD ["sh", "run.sh"]