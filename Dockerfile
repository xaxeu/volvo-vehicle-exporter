FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash volvo && \
    chown -R volvo:volvo /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY auth.py exporter.py ./

# Ensure app directory is writable (for token files)
RUN chown -R volvo:volvo /app

# Switch to non-root user
USER volvo

# Expose Prometheus metrics port
EXPOSE 9100

# Run the exporter
CMD ["python", "exporter.py"]
