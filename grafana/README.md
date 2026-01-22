# Grafana Dashboard

This folder contains Grafana dashboards to visualize Volvo vehicle telemetry data collected by the Prometheus exporter.

## Setup

### 1. Start Grafana

Add Grafana to your `docker-compose.yml`:

```yaml
grafana:
  image: grafana/grafana:latest
  container_name: grafana
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
  volumes:
    - grafana_storage:/var/lib/grafana
    - ./grafana/provisioning:/etc/grafana/provisioning
  depends_on:
    - prometheus
```

### 2. Add Prometheus Data Source

1. Open Grafana at `http://localhost:3000`
2. Login with `admin` / `admin` (or your configured password)
3. Go to **Connections** → **Data Sources** → **Add data source**
4. Select **Prometheus**
5. Set URL to `http://prometheus:9090` (or `http://localhost:9090`)
6. Click **Save & test**

### 3. Import Dashboards

1. Go to **Dashboards** → **New** → **Import**
2. Upload `volvo-vehicle-dashboard.json` from this folder
3. Select the Prometheus data source
4. Click **Import**

## Available Dashboards

- **volvo-vehicle-dashboard.json** - Main vehicle telemetry dashboard showing:
  - Vehicle status (online/offline, battery charge, fuel level)
  - Door and window states
  - Tire pressure monitoring
  - Diagnostics and warnings
  - Trip statistics
  - Location data (if available)

## Dashboard Features

- Real-time vehicle metrics
- Historical trend graphs
- Alert indicators for warnings and diagnostics
- Filter by vehicle attributes (VIN, model, fuel type)
- Auto-refresh intervals

## Customization

Edit dashboard JSON to:
- Add custom panels
- Modify metric queries
- Change visualization styles
- Add alerts and thresholds

For more info, see [Grafana Dashboard Documentation](https://grafana.com/docs/grafana/latest/dashboards/).
