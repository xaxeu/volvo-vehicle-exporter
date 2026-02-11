#!/bin/bash
set -e

# Expected placeholder value from config.yaml
PLACEHOLDER="YOUR_GEOAPIFY_API_KEY_HERE"

# Extract geoapify_api_key from config.yaml
# This pattern handles both quoted and unquoted values
GEOAPIFY_KEY=$(grep -E '^geoapify_api_key:' /app/config.yaml 2>/dev/null | sed -E 's/^geoapify_api_key:[[:space:]]*["'\'']*([^"'\''#]+)["'\'']*.*/\1/' | tr -d ' ')

# If key exists and is not placeholder, create the datasource provisioning file
if [ -n "$GEOAPIFY_KEY" ] && [ "$GEOAPIFY_KEY" != "$PLACEHOLDER" ]; then
  echo "Provisioning Infinity datasource with Geoapify API key..."
  
  # Create the infinity datasource file
  cat > /etc/grafana/provisioning/datasources/infinity.yaml <<EOF
apiVersion: 1

datasources:
  - name: Geoapify (Infinity)
    type: yesoreyeram-infinity-datasource
    uid: infinity_DS # Unique identifier for the datasource
    access: proxy
    isDefault: false
    editable: true
    jsonData:
      allowedHosts:
        - 'api.geoapify.com'
      tlsSkipVerify: false
    secureJsonData:
      apiKey: "$GEOAPIFY_KEY"
EOF
else
  echo "No valid Geoapify API key found. Skipping Infinity datasource provisioning."
fi

# Start Grafana with the original entrypoint
exec /run.sh "$@"
