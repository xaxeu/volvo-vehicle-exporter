FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY auth.py .
COPY exporter.py .

# Expose Prometheus metrics port
EXPOSE 9100

# Run the exporter
CMD ["python", "exporter.py"]
