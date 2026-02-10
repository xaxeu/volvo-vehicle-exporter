#!/bin/bash
set -e

# Extract geoapify_api_key from config.yaml
GEOAPIFY_KEY=$(grep -E '^geoapify_api_key:' /app/config.yaml | sed 's/geoapify_api_key:[[:space:]]*"\?\([^"]*\)"\?/\1/' | tr -d ' ')

# If key exists and is not placeholder, create the datasource provisioning file
if [ -n "$GEOAPIFY_KEY" ] && [ "$GEOAPIFY_KEY" != "YOUR_GEOAPIFY_API_KEY_HERE" ]; then
  echo "Provisioning Infinity datasource with Geoapify API key..."
  
  # Create the infinity datasource file
  cat > /etc/grafana/provisioning/datasources/infinity.yaml <<EOF
apiVersion: 1

datasources:
  - name: Geoapify (Infinity)
    type: yesoreyeram-infinity-datasource
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
