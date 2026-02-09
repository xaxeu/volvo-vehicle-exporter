#!/usr/bin/env python3

import requests
import time
import yaml
import os
import sys
import re
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from prometheus_client import start_http_server, Gauge, Counter, Histogram, CollectorRegistry

from auth import VolvoAuth, VolvoAPI

LOG_LEVEL = os.getenv('LOG_LEVEL', 'info').lower()

def log(msg, level='info'):
    ts = datetime.now().isoformat()
    if LOG_LEVEL == 'debug' or level == 'info':
        print(f"[{ts}] [{level.upper()}] {msg}")

def safe_float(value):
    """Convert to float safely, return 0.0 for non-numeric"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0

def window_state(value):
    """Convert window states: CLOSED=0, OPEN/OPENING=1"""
    if isinstance(value, str):
        return 1.0 if value.upper() in ['OPEN', 'OPENING'] else 0.0
    return safe_float(value)

def load_config(config_path="config.yaml"):
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        log("Config loaded OK", 'debug')
        return config
    except FileNotFoundError:
        log("config.yaml not found", 'error')
        sys.exit(1)
    except Exception as e:
        log(f"Config error: {e}", 'error')
        sys.exit(1)

REGISTRY = CollectorRegistry()

# Global label names list used for all metrics and dynamic metrics
LABEL_NAMES = ['vin', 'model', 'modelYear', 'fuelType', 'gearbox', 'upholstery', 'batteryCapacityKWH']

# Cache for last known address per VIN to prevent cardinality explosion
last_known_addresses = {}

# HTTP Metrics - defined globally
HTTP_REQUESTS_TOTAL = Counter(
    'http_requests_total',
    'Total HTTP requests made',
    ['method', 'endpoint', 'status_code'],
    registry=REGISTRY
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint', 'status_code'],
    registry=REGISTRY
)

def sanitize_endpoint(url):
    """
    Sanitize URL to avoid high cardinality in Prometheus labels.
    Removes query parameters, replaces VINs and IDs with placeholders.
    """
    try:
        parsed = urlparse(url)
        path = parsed.path
        
        # Replace VINs (typically 17 alphanumeric characters)
        path = re.sub(r'/[A-HJ-NPR-Z0-9]{17}(/.*)?$', '/<VIN>\1', path)
        
        # Replace UUIDs and long alphanumeric IDs
        path = re.sub(r'/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/', '/<UUID>/', path)
        path = re.sub(r'/[a-f0-9]{24,}/', '/<ID>/', path)
        path = re.sub(r'/\d{5,}/', '/<ID>/', path)
        
        # Construct sanitized endpoint with domain and path (no query parameters)
        endpoint = f"{parsed.scheme}://{parsed.netloc}{path}"
        
        return endpoint
    except Exception as e:
        log(f"Error sanitizing endpoint: {e}", 'debug')
        return url

def track_http_request(func):
    """
    Decorator to track HTTP requests with Prometheus metrics.
    Wraps requests library methods to capture metrics and log payloads.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        method = func.__name__.upper()
        url = args[0] if args else kwargs.get('url', 'unknown')
        
        # Sanitize URL for logging (remove API keys)
        sanitized_url = re.sub(r'[?&]apiKey=[^&]*', '?apiKey=***', url)
        sanitized_url = re.sub(r'[?&]appid=[^&]*', '?appid=***', sanitized_url)
        
        start_time = time.time()
        status_code = 'unknown'
        response_data = None
        
        try:
            response = func(*args, **kwargs)
            status_code = str(response.status_code)
            
            # Capture response data for logging
            try:
                response_data = response.json()
            except:
                response_data = response.text[:500]  # First 500 chars of text response
            
            return response
        except requests.exceptions.RequestException as e:
            status_code = 'error'
            raise
        finally:
            duration = time.time() - start_time
            endpoint = sanitize_endpoint(url)
            
            # Record metrics
            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code
            ).inc()
            
#            HTTP_REQUEST_DURATION_SECONDS.labels(
#                method=method,
#                endpoint=endpoint,
#                status_code=status_code
#            ).observe(duration)
            
            log(f"HTTP {method} {endpoint} -> {status_code} ({duration:.3f}s)", 'debug')
            
            # Log request/response details for external APIs when debug mode
            if LOG_LEVEL == 'debug' and ('geoapify' in url or 'openweathermap' in url or 'volvo' in url):
                log(f"  Request: {method} {sanitized_url}", 'debug')
                if response_data:
                    log(f"  Response: {response_data}", 'debug')
    
    return wrapper

# Monkey-patch requests library to track all HTTP calls
original_get = requests.get
original_post = requests.post
original_put = requests.put
original_delete = requests.delete
original_patch = requests.patch
original_head = requests.head
original_options = requests.options

requests.get = track_http_request(original_get)
requests.post = track_http_request(original_post)
requests.put = track_http_request(original_put)
requests.delete = track_http_request(original_delete)
requests.patch = track_http_request(original_patch)
requests.head = track_http_request(original_head)
requests.options = track_http_request(original_options)

# Also patch Session methods if used
original_session_request = requests.Session.request

@wraps(original_session_request)
def tracked_session_request(self, method, url, **kwargs):
    start_time = time.time()
    status_code = 'unknown'
    response_data = None
    
    # Sanitize URL for logging (remove API keys)
    sanitized_url = re.sub(r'[?&]apiKey=[^&]*', '?apiKey=***', url)
    sanitized_url = re.sub(r'[?&]appid=[^&]*', '?appid=***', sanitized_url)
    
    try:
        response = original_session_request(self, method, url, **kwargs)
        status_code = str(response.status_code)
        
        # Capture response data for logging
        try:
            response_data = response.json()
        except:
            response_data = response.text[:500]  # First 500 chars of text response
        
        return response
    except requests.exceptions.RequestException as e:
        status_code = 'error'
        raise
    finally:
        duration = time.time() - start_time
        endpoint = sanitize_endpoint(url)
        
        HTTP_REQUESTS_TOTAL.labels(
            method=method.upper(),
            endpoint=endpoint,
            status_code=status_code
        ).inc()
        
#        HTTP_REQUEST_DURATION_SECONDS.labels(
#            method=method.upper(),
#            endpoint=endpoint,
#            status_code=status_code
#        ).observe(duration)
        
        log(f"HTTP {method.upper()} {endpoint} -> {status_code} ({duration:.3f}s)", 'debug')
        
        # Log request/response details for external APIs when debug mode
        if LOG_LEVEL == 'debug' and ('geoapify' in url or 'openweathermap' in url or 'volvo' in url):
            log(f"  Request: {method.upper()} {sanitized_url}", 'debug')
            if response_data:
                log(f"  Response: {response_data}", 'debug')

requests.Session.request = tracked_session_request

def create_labeled_metrics():
    labels = LABEL_NAMES

    # Core metrics
    global VOLVO_BATTERY_LEVEL, VOLVO_ODOMETER_KM, VOLVO_RANGE_KM
    global CHARGE_STATE, PLUG_STATE, LOCK_STATE, POWER_STATUS, CHARGING_POWER

    VOLVO_BATTERY_LEVEL = Gauge('volvo_battery_level_percent', 'Battery level %', labels, registry=REGISTRY)

    VOLVO_ODOMETER_KM = Gauge(
        'volvo_odometer_km',
        'Odometer (km)',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )

    VOLVO_RANGE_KM = Gauge('volvo_range_km', 'Remaining range km', labels, registry=REGISTRY)

    CHARGE_STATE = Gauge('volvo_charge_state', 'Charging (1=charging, 0=idle)', labels, registry=REGISTRY)
    PLUG_STATE = Gauge('volvo_plug_state', 'Plug connected (1=yes, 0=no)', labels, registry=REGISTRY)
    LOCK_STATE = Gauge('volvo_lock_state', 'Locked (1=locked, 0=unlocked)', labels, registry=REGISTRY)
    POWER_STATUS = Gauge('volvo_power_status', 'Charger power status (e.g:PROVIDING_POWER)', labels, registry=REGISTRY)
    CHARGING_POWER = Gauge('volvo_charging_power', 'Charging power in Watt', labels, registry=REGISTRY)

    # Engine
    global ENGINE_STATUS
    ENGINE_STATUS = Gauge('volvo_engine_status', 'Engine status', labels, registry=REGISTRY)

    # Warnings (25 lights)
    global BRAKE_CENTER, BRAKE_LEFT, BRAKE_RIGHT, FOG_FRONT, FOG_REAR
    global POS_FRONT_L, POS_FRONT_R, POS_REAR_L, POS_REAR_R
    global HIGH_L, HIGH_R, LOW_L, LOW_R, DAY_L, DAY_R
    global TURN_F_L, TURN_F_R, TURN_R_L, TURN_R_R
    global PLATE_LIGHT, SIDE_MARK, HAZARD_LIGHT, REVERSE_LIGHT

    BRAKE_CENTER = Gauge('volvo_brake_center_warning', 'Brake center warning', labels, registry=REGISTRY)
    BRAKE_LEFT   = Gauge('volvo_brake_left_warning',   'Brake left warning',   labels, registry=REGISTRY)
    BRAKE_RIGHT  = Gauge('volvo_brake_right_warning',  'Brake right warning',  labels, registry=REGISTRY)
    FOG_FRONT    = Gauge('volvo_fog_front_warning',    'Fog front warning',    labels, registry=REGISTRY)
    FOG_REAR     = Gauge('volvo_fog_rear_warning',     'Fog rear warning',     labels, registry=REGISTRY)

    POS_FRONT_L = Gauge('volvo_pos_front_left_warning',  'Position front left warning',  labels, registry=REGISTRY)
    POS_FRONT_R = Gauge('volvo_pos_front_right_warning', 'Position front right warning', labels, registry=REGISTRY)
    POS_REAR_L  = Gauge('volvo_pos_rear_left_warning',   'Position rear left warning',   labels, registry=REGISTRY)
    POS_REAR_R  = Gauge('volvo_pos_rear_right_warning',  'Position rear right warning',  labels, registry=REGISTRY)

    HIGH_L = Gauge('volvo_high_left_warning',  'High beam left warning',  labels, registry=REGISTRY)
    HIGH_R = Gauge('volvo_high_right_warning', 'High beam right warning', labels, registry=REGISTRY)
    LOW_L  = Gauge('volvo_low_left_warning',   'Low beam left warning',   labels, registry=REGISTRY)
    LOW_R  = Gauge('volvo_low_right_warning',  'Low beam right warning',  labels, registry=REGISTRY)

    DAY_L = Gauge('volvo_day_left_warning',  'Daytime left warning',  labels, registry=REGISTRY)
    DAY_R = Gauge('volvo_day_right_warning', 'Daytime right warning', labels, registry=REGISTRY)

    TURN_F_L = Gauge('volvo_turn_front_left_warning',  'Turn front left warning',  labels, registry=REGISTRY)
    TURN_F_R = Gauge('volvo_turn_front_right_warning', 'Turn front right warning', labels, registry=REGISTRY)
    TURN_R_L = Gauge('volvo_turn_rear_left_warning',   'Turn rear left warning',   labels, registry=REGISTRY)
    TURN_R_R = Gauge('volvo_turn_rear_right_warning',  'Turn rear right warning',  labels, registry=REGISTRY)

    PLATE_LIGHT  = Gauge('volvo_plate_light_warning',   'Plate light warning',       labels, registry=REGISTRY)
    SIDE_MARK    = Gauge('volvo_side_mark_warning',     'Side marker warning',       labels, registry=REGISTRY)
    HAZARD_LIGHT = Gauge('volvo_hazard_warning',        'Hazard warning',            labels, registry=REGISTRY)
    REVERSE_LIGHT= Gauge('volvo_reverse_warning',       'Reverse light warning',     labels, registry=REGISTRY)


    # Tyres (enum severity)
    global TYRE_FL, TYRE_FR, TYRE_RL, TYRE_RR
    TYRE_FL = Gauge('volvo_tyre_front_left', 'Front left tyre status', labels, registry=REGISTRY)
    TYRE_FR = Gauge('volvo_tyre_front_right', 'Front right tyre status', labels, registry=REGISTRY)
    TYRE_RL = Gauge('volvo_tyre_rear_left', 'Rear left tyre status', labels, registry=REGISTRY)
    TYRE_RR = Gauge('volvo_tyre_rear_right', 'Rear right tyre status', labels, registry=REGISTRY)

    # Diagnostics (with unit label)
    global SERVICE_WARN, SERVICE_TRIGGER, ENGINE_HRS, DIST_SERVICE, WASHER_FLUID, TIME_SERVICE

    SERVICE_WARN = Gauge(
        'volvo_service_warning',
        'Service warning',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )
    SERVICE_TRIGGER = Gauge(
        'volvo_service_trigger',
        'Service trigger',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )
    ENGINE_HRS = Gauge(
        'volvo_engine_hours_service',
        'Engine hours to service',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )
    DIST_SERVICE = Gauge(
        'volvo_distance_service',
        'Distance to service',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )
    WASHER_FLUID = Gauge(
        'volvo_washer_fluid_warning',
        'Washer fluid warning',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )
    TIME_SERVICE = Gauge(
        'volvo_time_service',
        'Time to service',
        LABEL_NAMES + ['unit'],
        registry=REGISTRY,
    )

    # Windows
    global WIN_FL, WIN_FR, WIN_RL, WIN_RR, SUNROOF
    WIN_FL = Gauge('volvo_window_front_left', 'Front left window', labels, registry=REGISTRY)
    WIN_FR = Gauge('volvo_window_front_right', 'Front right window', labels, registry=REGISTRY)
    WIN_RL = Gauge('volvo_window_rear_left', 'Rear left window', labels, registry=REGISTRY)
    WIN_RR = Gauge('volvo_window_rear_right', 'Rear right window', labels, registry=REGISTRY)
    SUNROOF = Gauge('volvo_sunroof', 'Sunroof position', labels, registry=REGISTRY)

    # Location
    global LOCATION_LATITUDE, LOCATION_LONGITUDE, LOCATION_ALTITUDE
    LOCATION_LATITUDE = Gauge('volvo_location_latitude', 'Last known latitude', labels + ['address'], registry=REGISTRY)
    LOCATION_LONGITUDE = Gauge('volvo_location_longitude', 'Last known longitude', labels + ['address'], registry=REGISTRY)
    LOCATION_ALTITUDE = Gauge('volvo_location_altitude', 'Last known altitude', labels + ['address'], registry=REGISTRY)

    # Weather from OpenWeatherMap API (uses car coordinates)
    global WEATHER_TEMP, WEATHER_FEELS_LIKE, WEATHER_TEMP_MIN, WEATHER_TEMP_MAX
    global WEATHER_PRESSURE, WEATHER_HUMIDITY

    WEATHER_TEMP = Gauge('weather_temperature_celsius', 'Current temperature from OpenWeatherMap', labels, registry=REGISTRY)
    WEATHER_FEELS_LIKE = Gauge('weather_feels_like_celsius', 'Feels like temperature', labels, registry=REGISTRY)
    WEATHER_TEMP_MIN = Gauge('weather_temp_min_celsius', 'Temperature minimum', labels, registry=REGISTRY)
    WEATHER_TEMP_MAX = Gauge('weather_temp_max_celsius', 'Temperature maximum', labels, registry=REGISTRY)
    WEATHER_PRESSURE = Gauge('weather_pressure_hpa', 'Atmospheric pressure (hPa)', labels, registry=REGISTRY)
    WEATHER_HUMIDITY = Gauge('weather_humidity_percent', 'Relative humidity (%)', labels, registry=REGISTRY)

def get_vehicle_labels(status):
    return {
        'vin': status.get('vin', 'unknown'),
        'model': status.get('descriptions', {}).get('model', 'unknown'),
        'modelYear': str(status.get('modelYear', 'unknown')),
        'fuelType': status.get('fuelType', 'unknown'),
        'gearbox': status.get('gearbox', 'unknown'),
        'upholstery': status.get('descriptions', {}).get('upholstery', 'unknown'),
        'batteryCapacityKWH': str(status.get('batteryCapacityKWH', 'unknown')),
    }

def poll_all_metrics(api, labels):
    log("Poll start", 'debug')

    # Status / battery
    try:
        status = api.get_vehicle_data('status')
        battery = safe_float(status.get('batteryCapacityKWH'))
        VOLVO_BATTERY_LEVEL.labels(**labels).set(battery)
        log(f"Battery: {battery} kWh", 'info')
    except Exception as e:
        log(f"Battery error: {e}", 'debug')

    # Odometer with unit label
    try:
        odometer_data = api.get_vehicle_data('odometer')
        odo_obj = odometer_data.get('odometer', {})
        odometer = safe_float(odo_obj.get('value', 0.0))
        odo_unit = odo_obj.get('unit', 'km')
        VOLVO_ODOMETER_KM.labels(**labels, unit=odo_unit).set(odometer)
        log(f"Odometer: {odometer} {odo_unit}", 'info')
    except Exception as e:
        log(f"Odometer error: {e}", 'debug')

    # Statistics – dynamic metrics + range
    try:
        stats = api.get_vehicle_data('statistics')
        if LOG_LEVEL == 'debug':
            log(f"[statistics] raw keys: {list(stats.keys())}", 'debug')

        if not hasattr(REGISTRY, '_stats_metrics'):
            REGISTRY._stats_metrics = {}

        for key, data in stats.items():
            if not isinstance(data, dict) or 'value' not in data:
                continue

            metric_name = f"volvo_stats_{key.replace(' ', '_').lower()}_value"
            metric_desc = f"Statistics {key} ({data.get('unit', 'unknown')})"
            value = safe_float(data.get('value'))
            unit = data.get('unit', 'unknown')

            if metric_name not in REGISTRY._stats_metrics:
                REGISTRY._stats_metrics[metric_name] = Gauge(
                    metric_name,
                    metric_desc,
                    LABEL_NAMES + ['unit'],      # timestamp removed here
                    registry=REGISTRY,
                )
                log(f"Created stats metric: {metric_name}", 'debug')

            REGISTRY._stats_metrics[metric_name].labels(
                **labels,
                unit=unit,                     # only unit label now
            ).set(value)

        if 'distanceToEmptyBattery' in stats and 'value' in stats['distanceToEmptyBattery']:
            range_km = safe_float(stats['distanceToEmptyBattery']['value'])
            VOLVO_RANGE_KM.labels(**labels).set(range_km)
            log(f"Range: {range_km} km", 'info')

    except Exception as e:
        log(f"Stats error: {e}", 'debug')


    # Energy / charging – dynamic metrics
    try:
        energy = api.get_vehicle_data('energy')

        charging_status = energy.get('chargingStatus', {}).get('value', '').upper()
        charge_state = 1.0 if charging_status == 'CHARGING' else 0.0
        CHARGE_STATE.labels(**labels).set(charge_state)

        plug_status = energy.get('chargerConnectionStatus', {}).get('value', '').upper()
        plug_state = 1.0 if plug_status == 'CONNECTED' else 0.0
        PLUG_STATE.labels(**labels).set(plug_state)

        power_state = energy.get('chargerPowerStatus', {}).get('value', '').upper()
        power_status = 1.0 if power_state == 'PROVIDING_POWER' else 0.0
        POWER_STATUS.labels(**labels).set(power_status)

        charging_power = safe_float(energy.get('chargingPower', {}).get('value', ''))
        CHARGING_POWER.labels(**labels).set(charging_power)

        if not hasattr(REGISTRY, '_energy_metrics'):
            REGISTRY._energy_metrics = {}

        for key, data in energy.items():
            if isinstance(data, dict) and 'value' in data:
                metric_name = f"volvo_energy_{key.replace('Status', '').replace('Level', '').lower()}_value"
                metric_desc = f"Energy {key} value"
                status_label = data.get('status', 'UNKNOWN').upper()
                value = safe_float(data['value'])

                # Handle electricRange with both status and unit labels
                if key == 'electricRange':
                    unit_label = data.get('unit', 'unknown')
                    if metric_name not in REGISTRY._energy_metrics:
                        REGISTRY._energy_metrics[metric_name] = Gauge(
                            metric_name,
                            metric_desc,
                            LABEL_NAMES + ['status', 'unit'],
                            registry=REGISTRY,
                        )
                        log(f"Created energy metric: {metric_name}", 'debug')
                    REGISTRY._energy_metrics[metric_name].labels(**labels, status=status_label, unit=unit_label).set(value)
                else:
                    # Handle other energy metrics with status label
                    if metric_name not in REGISTRY._energy_metrics:
                        REGISTRY._energy_metrics[metric_name] = Gauge(
                            metric_name,
                            metric_desc,
                            LABEL_NAMES + ['status'],
                            registry=REGISTRY,
                        )
                        log(f"Created energy metric: {metric_name}", 'debug')

                    REGISTRY._energy_metrics[metric_name].labels(**labels, status=status_label).set(value)

        log(f"Energy state: {'CHARGING' if charge_state else 'IDLE'}", 'info')
    except Exception as e:
        log(f"Energy error: {e}", 'debug')

    # Engine
    try:
        engine = api.get_vehicle_data('engine-status')
        engine_status_str = engine.get('engineStatus', {}).get('value', 'STOPPED').upper()
        engine_status = 1.0 if engine_status_str == 'RUNNING' else 0.0
        ENGINE_STATUS.labels(**labels).set(engine_status)
        log(f"Engine: {engine_status_str}", 'info')
    except Exception as e:
        log(f"Engine error: {e}", 'debug')

    # Warnings
    try:
        warnings = api.get_vehicle_data('warnings')

        BRAKE_CENTER.labels(**labels).set(safe_float(warnings.get('brakeLightCenterWarning', {}).get('value')))
        BRAKE_LEFT.labels(**labels).set(safe_float(warnings.get('brakeLightLeftWarning', {}).get('value')))
        BRAKE_RIGHT.labels(**labels).set(safe_float(warnings.get('brakeLightRightWarning', {}).get('value')))
        FOG_FRONT.labels(**labels).set(safe_float(warnings.get('fogLightFrontWarning', {}).get('value')))
        FOG_REAR.labels(**labels).set(safe_float(warnings.get('fogLightRearWarning', {}).get('value')))

        POS_FRONT_L.labels(**labels).set(safe_float(warnings.get('positionLightFrontLeftWarning', {}).get('value')))
        POS_FRONT_R.labels(**labels).set(safe_float(warnings.get('positionLightFrontRightWarning', {}).get('value')))
        POS_REAR_L.labels(**labels).set(safe_float(warnings.get('positionLightRearLeftWarning', {}).get('value')))
        POS_REAR_R.labels(**labels).set(safe_float(warnings.get('positionLightRearRightWarning', {}).get('value')))

        HIGH_L.labels(**labels).set(safe_float(warnings.get('highBeamLeftWarning', {}).get('value')))
        HIGH_R.labels(**labels).set(safe_float(warnings.get('highBeamRightWarning', {}).get('value')))
        LOW_L.labels(**labels).set(safe_float(warnings.get('lowBeamLeftWarning', {}).get('value')))
        LOW_R.labels(**labels).set(safe_float(warnings.get('lowBeamRightWarning', {}).get('value')))

        DAY_L.labels(**labels).set(safe_float(warnings.get('daytimeRunningLightLeftWarning', {}).get('value')))
        DAY_R.labels(**labels).set(safe_float(warnings.get('daytimeRunningLightRightWarning', {}).get('value')))

        TURN_F_L.labels(**labels).set(safe_float(warnings.get('turnIndicationFrontLeftWarning', {}).get('value')))
        TURN_F_R.labels(**labels).set(safe_float(warnings.get('turnIndicationFrontRightWarning', {}).get('value')))
        TURN_R_L.labels(**labels).set(safe_float(warnings.get('turnIndicationRearLeftWarning', {}).get('value')))
        TURN_R_R.labels(**labels).set(safe_float(warnings.get('turnIndicationRearRightWarning', {}).get('value')))

        PLATE_LIGHT.labels(**labels).set(safe_float(warnings.get('registrationPlateLightWarning', {}).get('value')))
        SIDE_MARK.labels(**labels).set(safe_float(warnings.get('sideMarkLightsWarning', {}).get('value')))
        HAZARD_LIGHT.labels(**labels).set(safe_float(warnings.get('hazardLightsWarning', {}).get('value')))
        REVERSE_LIGHT.labels(**labels).set(safe_float(warnings.get('reverseLightsWarning', {}).get('value')))

        log("Warnings ok", 'info')
    except Exception as e:
        log(f"Warnings error: {e}", 'debug')

    # Tyres (enum)
    try:
        tyres = api.get_vehicle_data('tyres')

        def tyre_status_to_float(status_str):
            status_str = status_str.upper() if status_str else 'UNSPECIFIED'
            status_map = {
                'NO_WARNING': 0.0,
                'VERY_LOW_PRESSURE': 1.0,
                'LOW_PRESSURE': 2.0,
                'HIGH_PRESSURE': 3.0,
                'UNSPECIFIED': 0.0,
            }
            return status_map.get(status_str, 0.0)

        TYRE_FL.labels(**labels).set(
            tyre_status_to_float(tyres.get('frontLeft', {}).get('value'))
        )
        TYRE_FR.labels(**labels).set(
            tyre_status_to_float(tyres.get('frontRight', {}).get('value'))
        )
        TYRE_RL.labels(**labels).set(
            tyre_status_to_float(tyres.get('rearLeft', {}).get('value'))
        )
        TYRE_RR.labels(**labels).set(
            tyre_status_to_float(tyres.get('rearRight', {}).get('value'))
        )

        log("Tyres ok", 'info')
    except Exception as e:
        log(f"Tyres error: {e}", 'debug')


    # Diagnostics with unit label
    try:
        diag = api.get_vehicle_data('diagnostics')

        def value_and_unit(obj_name):
            obj = diag.get(obj_name, {})
            return safe_float(obj.get('value')), obj.get('unit', 'unknown')

        service_warn_val, service_warn_unit = value_and_unit('serviceWarning')
        service_trigger_val, service_trigger_unit = value_and_unit('serviceTrigger')
        engine_hrs_val, engine_hrs_unit = value_and_unit('engineHoursToService')
        dist_service_val, dist_service_unit = value_and_unit('distanceToService')
        washer_fluid_val, washer_fluid_unit = value_and_unit('washerFluidLevelWarning')
        time_service_val, time_service_unit = value_and_unit('timeToService')

        SERVICE_WARN.labels(**labels, unit=service_warn_unit).set(service_warn_val)
        SERVICE_TRIGGER.labels(**labels, unit=service_trigger_unit).set(service_trigger_val)
        ENGINE_HRS.labels(**labels, unit=engine_hrs_unit).set(engine_hrs_val)
        DIST_SERVICE.labels(**labels, unit=dist_service_unit).set(dist_service_val)
        WASHER_FLUID.labels(**labels, unit=washer_fluid_unit).set(washer_fluid_val)
        TIME_SERVICE.labels(**labels, unit=time_service_unit).set(time_service_val)

        log("Diagnostics ok", 'info')
    except Exception as e:
        log(f"Diagnostics error: {e}", 'debug')

    # Location (cache coordinates for weather API and reverse geocoding)
    try:
        location = api.get_vehicle_data('location')
        data = location.get('data', location)
        coordinates = data.get('geometry', {}).get('coordinates', [])
        if len(coordinates) >= 3:
            lat = safe_float(coordinates[1])
            lon = safe_float(coordinates[0])
            alt = safe_float(coordinates[2])
            
            # Fetch address from Geoapify
            config = load_config()  # Reload for geoapify_api_key
            vin = labels.get('vin', 'unknown')
            address = last_known_addresses.get(vin, 'unknown')  # Start with cached address
            geoapify_key = config.get('geoapify_api_key')
            if geoapify_key:
                geoapify_url = f"https://api.geoapify.com/v1/geocode/reverse?lat={lat}&lon={lon}&apiKey={geoapify_key}"
                try:
                    resp = requests.get(geoapify_url, timeout=10)
                    if resp.status_code == 200:
                        geoapify_data = resp.json()
                        features = geoapify_data.get('features', [])
                        if features and len(features) > 0:
                            new_address = features[0].get('properties', {}).get('formatted', 'unknown')
                            if new_address != address:  # Only update if address changed
                                address = new_address
                                last_known_addresses[vin] = address
                                log(f"Address updated: {address}", 'info')
                    else:
                        log(f"Geoapify API error: {resp.status_code}", 'debug')
                except Exception as e:
                    log(f"Geoapify fetch error: {e}", 'debug')
            
            # Set location metrics with last known address
            LOCATION_LATITUDE.labels(**labels, address=address).set(lat)
            LOCATION_LONGITUDE.labels(**labels, address=address).set(lon)
            LOCATION_ALTITUDE.labels(**labels, address=address).set(alt)
            log(f"Location: {lat:.4f}, {lon:.4f}, {alt:.0f}m", 'info')

            # Weather API call using car coordinates
            weather_key = config.get('weather_api_key')
            if weather_key:
                weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={weather_key}"
                try:
                    resp = requests.get(weather_url, timeout=10)
                    if resp.status_code == 200:
                        weather_data = resp.json()
                        main = weather_data.get('main', {})

                        WEATHER_TEMP.labels(**labels).set(safe_float(main.get('temp')))
                        WEATHER_FEELS_LIKE.labels(**labels).set(safe_float(main.get('feels_like')))
                        WEATHER_TEMP_MIN.labels(**labels).set(safe_float(main.get('temp_min')))
                        WEATHER_TEMP_MAX.labels(**labels).set(safe_float(main.get('temp_max')))
                        WEATHER_PRESSURE.labels(**labels).set(safe_float(main.get('pressure')))
                        WEATHER_HUMIDITY.labels(**labels).set(safe_float(main.get('humidity')))

                        log(f"Weather: {main.get('temp')}°C, feels {main.get('feels_like')}°C, {main.get('humidity')}% RH", 'info')
                    else:
                        log(f"Weather API error: {resp.status_code}", 'debug')
                except Exception as e:
                    log(f"Weather fetch error: {e}", 'debug')
        else:
            log("Location invalid or missing coordinates", 'debug')
    except Exception as e:
        log(f"Location error: {e}", 'debug')

def main():
    log("Volvo Exporter v2.0 starting (with HTTP metrics)", 'info')
    config = load_config()

    auth = VolvoAuth("config.yaml")
    if not auth.authenticate():
        log("Authentication failed", 'error')
        sys.exit(1)

    api = VolvoAPI(auth, "")
    vins = api.get_vehicle_list()
    if not vins:
        log("No vehicles found", 'error')
        sys.exit(1)

    api.vin = vins[0]
    log(f"Using VIN: {api.vin}", 'info')

    status = api.get_vehicle_data('status')
    vehicle_labels = get_vehicle_labels(status)

    create_labeled_metrics()
    listen_addr = config.get('exporter_listen_addr', '127.0.0.1')
    listen_port = config.get('exporter_listen_port', 9101)
    start_http_server(listen_port, addr=listen_addr, registry=REGISTRY)
    log(f"Exporter ready → http://{listen_addr}:{listen_port}/metrics", 'info')
    log("HTTP metrics: http_requests_total, http_request_duration_seconds", 'info')

    while True:
        try:
            poll_all_metrics(api, vehicle_labels)
            time.sleep(config.get('scrape_interval', 60))
        except KeyboardInterrupt:
            log("Exiting", 'info')
            break
        except Exception as e:
            log(f"Poll error: {e}", 'error')
            time.sleep(10)

if __name__ == "__main__":
    main()
