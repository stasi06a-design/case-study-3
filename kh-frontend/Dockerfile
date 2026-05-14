FROM python:3.11-slim

WORKDIR /app

# Copy and install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and templates
COPY . .

EXPOSE 5000

CMD ["python", "app.py"]