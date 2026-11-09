import requests
import json
import yaml
import urllib.parse
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from typing import Dict, Optional, List
import time
import secrets
import hashlib
import base64
import os
from datetime import datetime

LOG_LEVEL = os.getenv('LOG_LEVEL', 'info').lower()


def log(msg, level='info'):
    ts = datetime.now().isoformat()
    if LOG_LEVEL == 'debug' or level == 'info':
        print(f"[{ts}] [{level.upper()}] {msg}")


class VolvoAuth:
    # OAuth2 endpoints of the new Volvo ID service (Keycloak). Used for applications whose
    # client_id starts with "vid-" (Volvo's announcement says "vid-dc-"; a real application
    # came with "vid-dcr-").
    AUTH_URL = "https://auth.volvocars.com/realms/volvocars/protocol/openid-connect/auth"
    TOKEN_URL = "https://auth.volvocars.com/realms/volvocars/protocol/openid-connect/token"

    # Legacy Volvo ID service, only for old "dc-" clients. Volvo shuts it down on
    # 2027-01-31: after that delete these constants, `legacy_client` and its branches.
    NEW_CLIENT_ID_PREFIX = "vid-"
    LEGACY_AUTH_URL = "https://volvoid.eu.volvocars.com/as/authorization.oauth2"
    LEGACY_TOKEN_URL = "https://volvoid.eu.volvocars.com/as/token.oauth2"

    # API Base URLs
    BASE_URL_CONNECTED_VEHICLE = "https://api.volvocars.com/connected-vehicle/v2"
    BASE_URL_ENERGY = "https://api.volvocars.com/energy/v2"
    BASE_URL_LOCATION = "https://api.volvocars.com/location/v1"

    # API constants
    PKCE_VERIFIER_LENGTH = 128
    TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 60
    REQUEST_TIMEOUT_SECONDS = 10

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.session = requests.Session()
        self.vin = None
        self.session.headers.update({
            'Vcc-Api-Key': self.config['api_key'],
            'Content-Type': 'application/json',
        })
        self.token_file = Path("volvo_token.json")
        self.legacy_client = not str(self.config['client_id']).startswith(self.NEW_CLIENT_ID_PREFIX)
        self.auth_url = self.LEGACY_AUTH_URL if self.legacy_client else self.AUTH_URL
        self.token_url = self.LEGACY_TOKEN_URL if self.legacy_client else self.TOKEN_URL
        if self.legacy_client:
            log("WARNING: client_id does not start with 'vid-', so the LEGACY Volvo ID service is used. "
                "It stops working on 2027-01-31: create a new application and migrate (see README).")
        self.code_verifier = secrets.token_urlsafe(64)[:self.PKCE_VERIFIER_LENGTH]
        self.code_challenge = self._pkce_challenge()
        self.state = secrets.token_urlsafe(32)

    def _pkce_challenge(self) -> str:
        digest = hashlib.sha256(self.code_verifier.encode('ascii')).digest()
        return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

    def _load_config(self, config_path: str) -> Dict:
        """Load and validate configuration file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)

            # Validate required fields
            required_fields = ['client_id', 'client_secret', 'api_key', 'redirect_uri', 'scope']
            missing_fields = [field for field in required_fields if not config.get(field)]

            if missing_fields:
                raise ValueError(f"Missing required configuration fields: {', '.join(missing_fields)}")

            return config
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")

    def invalidate_token(self) -> None:
        """SAFELY backup token instead of delete"""
        if self.token_file.exists():
            backup = self.token_file.with_suffix('.json.bak')
            try:
                self.token_file.rename(backup)
                log(f"Token backed up to {backup.name}")
            except Exception as e:
                log(f"Backup failed: {e}", 'error')

    def _token_matches_client(self, token_data: Dict) -> bool:
        """A token (and its refresh token) only works for the OAuth client it was issued to.

        Token files written before client_id was recorded came from a legacy client.
        """
        stored = token_data.get('client_id')
        if stored is None:
            return self.legacy_client
        return stored == self.config['client_id']

    def load_token(self) -> Optional[Dict]:
        if not self.token_file.exists():
            return None
        try:
            token_data = json.loads(self.token_file.read_text())
            if not self._token_matches_client(token_data):
                log("Token file was issued to a different OAuth client - backing it up, new login required")
                self.invalidate_token()
                return None
            if 'access_token' in token_data and time.time() < token_data.get('expires_at', 0):
                self.session.headers['Authorization'] = f"Bearer {token_data['access_token']}"
                log("Token loaded")
                return token_data
            elif 'refresh_token' in token_data:
                log("Token expired but refresh available", 'warning')
                return token_data
        except Exception as e:
            log(f"Token parse error: {e} - backing up", 'error')
            self.invalidate_token()
        return None

    def save_token(self, token_data: Dict) -> None:
        """Always use NEW refresh_token from API response"""
        token_data['expires_at'] = (
            time.time() +
            token_data['expires_in'] -
            self.TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS
        )
        # A refresh token only works for the client it was issued to (see load_token)
        token_data['client_id'] = self.config['client_id']
        self.token_file.write_text(json.dumps(token_data, indent=2))
        self.session.headers['Authorization'] = f"Bearer {token_data['access_token']}"
        lifetimes = f"access token {token_data['expires_in']}s"
        if 'refresh_expires_in' in token_data:
            lifetimes += f", refresh token {token_data['refresh_expires_in']}s"
        log(f"Token saved (new refresh_token stored; {lifetimes})")

    def _post_token(self, data: Dict) -> requests.Response:
        """POST a grant to the token endpoint.

        The new service wants the client credentials in an HTTP Basic header, not in the
        body. The legacy service keeps getting them in the body, exactly as before.
        """
        client_id = self.config['client_id']
        client_secret = self.config['client_secret']
        kwargs = {}
        if self.legacy_client:
            data = {**data, 'client_id': client_id, 'client_secret': client_secret}
        else:
            kwargs['auth'] = (str(client_id), str(client_secret))
        return self.session.post(
            self.token_url,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )

    def refresh_token(self) -> bool:
        """Single refresh attempt – caller may wrap with retries"""
        if not self.token_file.exists():
            log("No token file for refresh", 'error')
            return False
        try:
            token_data = json.loads(self.token_file.read_text())
            if 'refresh_token' not in token_data:
                log("No refresh_token available", 'error')
                return False

            log("Refreshing token...")
            refresh_data = {
                'grant_type': 'refresh_token',
                'refresh_token': token_data['refresh_token'],
            }

            response = self._post_token(refresh_data)

            if response.status_code == 200:
                new_token = response.json()
                self.save_token(new_token)
                log("Token refreshed (new refresh_token stored)")
                return True

            log(f"Refresh failed: {response.status_code} - {response.text[:200]}", 'error')
            if response.status_code in (400, 401):
                # Only invalidate on real auth errors, not 5xx/transient
                self.invalidate_token()
            return False

        except requests.exceptions.RequestException as e:
            # Catch all requests-related errors (includes ConnectionError, Timeout, etc.)
            log(f"Network error during refresh: {e}", 'error')
            # Do not invalidate token on transient network errors
            return False
        except json.JSONDecodeError as e:
            log(f"Invalid JSON response during refresh: {e}", 'error')
            # Invalid response might indicate API changes
            return False
        except KeyError as e:
            log(f"Missing expected field in token response: {e}", 'error')
            self.invalidate_token()
            return False

    def safe_refresh(self, max_retries: int = 2, delay: int = 5) -> bool:
        """Retry wrapper with simple backoff for transient failures."""
        for attempt in range(1, max_retries + 1):
            if self.refresh_token():
                return True
            log(f"Refresh attempt {attempt} of {max_retries} failed", 'warning')
            time.sleep(delay)
        return False

    def authenticate(self) -> bool:
        token = self.load_token()
        if token:
            return True

        log("Volvo C3 PKCE Auth")
        redirect_uri_raw = urllib.parse.unquote(self.config['redirect_uri'])
        auth_url = (
            f"{self.auth_url}?"
            f"response_type=code"
            f"&client_id={self.config['client_id']}"
            f"&scope={self.config['scope']}"
            f"&redirect_uri={self.config['redirect_uri']}"
            f"&state={self.state}"
            f"&code_challenge={self.code_challenge}"
            f"&code_challenge_method=S256"
        )

        if not self.legacy_client:
            log("Tip: tick 'Remember me' on the Volvo ID login page. Without it the refresh token is "
                "short-lived and the exporter loses access soon after login; with it the grant lasts "
                "up to 3 months while it keeps being refreshed.")
        log(f"Open browser URL: {auth_url}")
        callback_url = input("Paste FULL callback URL: ").strip()

        if self.state not in callback_url:
            log("State mismatch", 'error')
            return False

        # Parse URL properly using urllib.parse
        parsed_url = urlparse(callback_url)
        query_params = parse_qs(parsed_url.query)

        code = query_params.get('code', [None])[0]
        if not code or len(code) < 10:
            log("Invalid or missing authorization code", 'error')
            return False

        log("Exchanging code + verifier + secret")
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri_raw,
            'code_verifier': self.code_verifier,
        }

        response = self._post_token(token_data)

        if response.status_code == 200:
            token = response.json()
            self.save_token(token)
            log("PKCE auth complete")
            return True

        log(f"Auth failed: {response.status_code}", 'error')
        return False

    def get_vehicle_list(self) -> List[str]:
        url = "https://api.volvocars.com/connected-vehicle/v2/vehicles"
        headers = {
            'Accept': 'application/json;q=0.9,text/plain',
            'Vcc-Api-Key': self.config['api_key'],
            'Authorization': self.session.headers.get('Authorization'),
            'vcc-api-operationId': 'exporter-list-vehicles',
        }

        response = self.session.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT_SECONDS)
    
        if response.status_code == 401:
            log("401 on vehicle list - attempting refresh", 'warning')
            if self.safe_refresh():
                headers['Authorization'] = self.session.headers.get('Authorization')
                response = self.session.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT_SECONDS)
                log(f"Vehicle list retry: {response.status_code}", 'warning')
    
        if response.status_code != 200:
            log(f"Vehicle list failed: {response.status_code}", 'error')
            return []
    
        data = response.json()
        vins = [v['vin'] for v in data.get('data', [])]
        log(f"Found {len(vins)} vehicles: {vins}", 'info')
        return vins

    def get_vehicle_data(self, endpoint: str) -> Dict:
        if not self.vin:
            log("No VIN selected", 'error')
            return {}

        # Define endpoint patterns using base URL constants
        endpoint_map = {
            'vehicles': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles",
            'status': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}",
            'statistics': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/statistics",
            'energy': f"{self.BASE_URL_ENERGY}/vehicles/{self.vin}/state",
            'odometer': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/odometer",
            'engine-status': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/engine-status",
            'warnings': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/warnings",
            'tyres': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/tyres",
            'diagnostics': f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/diagnostics",
            'location': f"{self.BASE_URL_LOCATION}/vehicles/{self.vin}/location",
        }

        url = endpoint_map.get(
            endpoint,
            f"{self.BASE_URL_CONNECTED_VEHICLE}/vehicles/{self.vin}/{endpoint}",
        )
        headers = {
            'Accept': 'application/json;q=0.9,text/plain',
            'Vcc-Api-Key': self.config['api_key'],
            'Authorization': self.session.headers.get('Authorization'),
            'vcc-api-operationId': f"exporter-poll-{endpoint}",
        }

        response = self.session.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT_SECONDS)
    
        if response.status_code == 401:
            log(f"401 detected on {endpoint} - attempting refresh", 'warning')
            if self.safe_refresh():
                headers['Authorization'] = self.session.headers.get('Authorization')
                response = self.session.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT_SECONDS)
                log(f"Retry [{endpoint}]: {response.status_code}", 'warning')
    
        if response.status_code != 200:
            log(f"[{endpoint}] {response.status_code}", 'debug')
            return {}
    
        data = response.json()
        log(f"[{endpoint}] OK", 'info')
        return data.get('data', data)
    



class VolvoAPI:
    def __init__(self, auth, vin: str = ""):
        self.auth = auth
        self.vin = vin

    def get_vehicle_data(self, endpoint: str) -> Dict:
        self.auth.vin = self.vin
        return self.auth.get_vehicle_data(endpoint)

    def get_vehicle_list(self) -> List[str]:
        return self.auth.get_vehicle_list()

