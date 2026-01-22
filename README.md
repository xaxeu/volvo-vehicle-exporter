# Volvo Vehicle Data Exporter

A Python-based tool that connects to Volvo's Connected Vehicle APIs to collect vehicle telemetry and export it as Prometheus metrics.

## Overview

This project authenticates with Volvo's API platform and periodically polls for vehicle data across multiple API specifications, including:
- **Connected Vehicle C3** - Core vehicle data (fuel, battery, doors, windows, locks, etc.)
- **Extended Vehicle C3** - Diagnostic and status information
- **Energy API** - EV-specific battery and charging data
- **Location API** - Vehicle location data

The collected data is exposed through a Prometheus-compatible HTTP endpoint for monitoring and time-series analysis.

## Project Structure

```
volvo/
├── auth.py                 # Volvo API authentication (OAuth2 with PKCE)
├── exporter.py             # Prometheus metrics exporter and data poller
├── config.yaml             # Configuration file with API credentials
├── requirements.txt        # Python dependencies
├── volvo_token.json        # Token storage (generated at runtime)
└── open-api/               # API specification files
    ├── connected-vehicle-c3-specification.*
    ├── extended-vehicle-c3-specification.*
    ├── energy-api-specification.*
    └── location-specification.*
```

## Features

- **OAuth2 Authentication** - Secure authentication with PKCE flow to Volvo ID platform
- **Multiple API Support** - Polls data from Connected Vehicle, Extended Vehicle, Energy, and Location APIs
- **Prometheus Integration** - Exposes metrics in Prometheus format on HTTP endpoint
- **Comprehensive Metrics** - Tracks vehicle attributes (VIN, model, fuel type, battery capacity) and real-time data
- **Error Tracking** - HTTP request metrics with status codes and duration
- **Configurable Polling** - Adjustable scrape intervals via config
- **Secure Token Management** - Automatic token refresh with backup preservation

## Installation

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/volvo-vehicle-exporter.git
cd volvo-vehicle-exporter
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure credentials in `config.yaml`:
   - `client_id` - Volvo API application ID
   - `client_secret` - Volvo API application secret
   - `api_key` - Volvo API key
   - `redirect_uri` - OAuth callback URL
   - `vin` - Vehicle VIN to monitor
   - `weather_api_key` - Optional weather service integration

## Dependencies

- **requests** (2.31.0) - HTTP client library
- **prometheus-client** (0.20.0) - Prometheus metrics client
- **pyyaml** (6.0.2) - YAML configuration parser

## Usage

1. **First-time Authentication**:
   ```bash
   python auth.py
   ```
   This initiates the OAuth2 flow and stores the authentication token in `volvo_token.json`.

2. **Start the Exporter**:
   ```bash
   python exporter.py
   ```
   The exporter will:
   - Load configuration from `config.yaml`
   - Authenticate with Volvo API
   - Expose Prometheus metrics on `http://localhost:8000/metrics`
   - Poll vehicle data at intervals specified in `scrape_interval`

## Configuration

Edit `config.yaml` to customize:
- **scrape_interval** - How often to poll vehicle data (default: 300 seconds)
- **scope** - Which API scopes to request (pre-configured with comprehensive vehicle data access)
- Vehicle monitoring targets and API credentials

## Metrics

The exporter provides:
- **Vehicle Attributes** - VIN, model year, fuel type, gearbox, battery capacity, etc.
- **Real-time Data** - Fuel levels, battery charge, door/window states, tire pressure, etc.
- **Diagnostics** - Engine status, warnings, maintenance indicators
- **HTTP Metrics** - Request count, duration, and status codes

All metrics are labeled with vehicle attributes for easy filtering and aggregation.

## Authentication Flow

Uses OAuth2 with PKCE (Proof Key for Code Exchange) for secure authentication without exposing client secrets directly. The implementation:
1. Generates a code verifier and challenge
2. Redirects user to Volvo ID authorization
3. Exchanges authorization code for access token
4. Stores token locally with automatic refresh handling

## Environment Variables

- `LOG_LEVEL` - Logging verbosity (`info` or `debug`, default: `info`)

## Files

| File | Purpose |
|------|---------|
| `auth.py` | OAuth2 authentication and token management |
| `exporter.py` | Prometheus metrics exporter and API poller |
| `config.yaml` | API credentials and configuration |
| `requirements.txt` | Python package dependencies |
| `open-api/` | Volvo API specification documentation |

## Notes

- Token files are backed up before invalidation to prevent data loss
- Metrics endpoint sanitizes URLs to prevent high cardinality issues
- Window and door states are normalized to numeric values for Prometheus
- The application includes comprehensive HTTP request tracking for debugging

## Status

This is an active development project for Volvo vehicle data monitoring via Prometheus.
