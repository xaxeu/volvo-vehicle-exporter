#!/bin/bash
set -e

# Datasources directory (tmpfs mount)
DATASOURCES_DIR="/etc/grafana/provisioning/datasources"

# Copy prometheus.yaml from the mounted read-only file
echo "Copying Prometheus datasource configuration..."
if [ -f "/etc/grafana/provisioning/datasources/prometheus.yaml" ]; then
  # File was bind-mounted, but tmpfs overlay might hide it
  # Instead, we'll create it from a known location or embed it
  :
fi

# Create Prometheus datasource (always needed)
cat > "$DATASOURCES_DIR/prometheus.yaml" <<'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
EOF

echo "✓ Prometheus datasource configured"

# Expected placeholder value from config.yaml
PLACEHOLDER="YOUR_GEOAPIFY_API_KEY_HERE"

# Extract geoapify_api_key from config.yaml
GEOAPIFY_KEY=$(grep -E '^geoapify_api_key:' /app/config.yaml 2>/dev/null | sed -E 's/^geoapify_api_key:[[:space:]]*["'\'']*([^"'\''#]+)["'\'']*.*/\1/' | tr -d ' ')

# If key exists and is not placeholder, create the Infinity datasource
if [ -n "$GEOAPIFY_KEY" ] && [ "$GEOAPIFY_KEY" != "$PLACEHOLDER" ]; then
  echo "Provisioning Infinity datasource with Geoapify API key..."

  cat > "$DATASOURCES_DIR/infinity.yaml" <<EOF
apiVersion: 1

datasources:
  - name: Geoapify (Infinity)
    type: yesoreyeram-infinity-datasource
    uid: infinity_DS
    access: proxy
    isDefault: false
    editable: true
    jsonData:
      allowedHosts:
        - 'https://api.geoapify.com'
      tlsSkipVerify: false
    secureJsonData:
      apiKey: "$GEOAPIFY_KEY"
EOF

  echo "✓ Infinity datasource configured"
else
  echo "ℹ No valid Geoapify API key found - Infinity datasource not configured"
fi

echo "Starting Grafana..."
# Start Grafana with the original entrypoint
exec /run.sh "$@"
