import requests
import json
import yaml
import urllib.parse
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
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.session = requests.Session()
        self.vin = None
        self.session.headers.update({
            'Vcc-Api-Key': self.config['api_key'],
            'Content-Type': 'application/json',
        })
        self.token_file = Path("volvo_token.json")
        self.auth_url = "https://volvoid.eu.volvocars.com/as/authorization.oauth2"
        self.token_url = "https://volvoid.eu.volvocars.com/as/token.oauth2"
        self.code_verifier = secrets.token_urlsafe(64)[:128]
        self.code_challenge = self._pkce_challenge()
        self.state = secrets.token_urlsafe(32)

    def _pkce_challenge(self) -> str:
        digest = hashlib.sha256(self.code_verifier.encode('ascii')).digest()
        return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

    def _load_config(self, config_path: str) -> Dict:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def invalidate_token(self):
        """SAFELY backup token instead of delete"""
        if self.token_file.exists():
            backup = self.token_file.with_suffix('.json.bak')
            try:
                self.token_file.rename(backup)
                log(f"Token backed up to {backup.name}")
            except Exception as e:
                log(f"Backup failed: {e}", 'error')

    def load_token(self) -> Optional[Dict]:
        if not self.token_file.exists():
            return None
        try:
            token_data = json.loads(self.token_file.read_text())
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

    def save_token(self, token_data: Dict):
        """Always use NEW refresh_token from API response"""
        token_data['expires_at'] = time.time() + token_data['expires_in'] - 60
        self.token_file.write_text(json.dumps(token_data, indent=2))
        self.session.headers['Authorization'] = f"Bearer {token_data['access_token']}"
        log("Token saved (new refresh_token stored)")

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
                'client_id': self.config['client_id'],
                'client_secret': self.config['client_secret'],
                'refresh_token': token_data['refresh_token'],
            }

            response = self.session.post(
                self.token_url,
                data=refresh_data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10,
            )

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

        except requests.exceptions.ConnectionError as e:
            # Your observed error: ('Connection aborted.', RemoteDisconnected(...))
            log(f"Network error during refresh: {e}", 'error')
            # Do not invalidate token; let caller retry later
            return False

        except Exception as e:
            log(f"Unexpected refresh error: {e}", 'error')
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

        log(f"Open browser URL: {auth_url}")
        callback_url = input("Paste FULL callback URL: ").strip()

        if self.state not in callback_url:
            log("State mismatch", 'error')
            return False

        code = None
        if 'code=' in callback_url:
            code = callback_url.split('code=')[1].split('&')[0].split('#')[0]

        if not code or len(code) < 10:
            log("Invalid code", 'error')
            return False

        log("Exchanging code + verifier + secret")
        token_data = {
            'grant_type': 'authorization_code',
            'client_id': self.config['client_id'],
            'client_secret': self.config['client_secret'],
            'code': code,
            'redirect_uri': redirect_uri_raw,
            'code_verifier': self.code_verifier,
        }

        response = self.session.post(
            self.token_url,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )

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
    
        if LOG_LEVEL == 'debug':
            log(f"[request] vehicle_list GET {url} headers={{'Vcc-Api-Key': '***', 'Authorization': '***'}}", 'debug')
    
        response = self.session.get(url, headers=headers, timeout=10)
    
        if LOG_LEVEL == 'debug':
            body_preview = response.text[:1000]
            log(f"[response] vehicle_list {response.status_code} body={body_preview}", 'debug')
    
        if response.status_code == 401:
            log("401 on vehicle list - attempting refresh", 'warning')
            if self.safe_refresh():
                headers['Authorization'] = self.session.headers.get('Authorization')
                response = self.session.get(url, headers=headers, timeout=10)
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
    
        endpoint_map = {
            'vehicles': "https://api.volvocars.com/connected-vehicle/v2/vehicles",
            'status': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}",
            'statistics': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/statistics",
            'energy': f"https://api.volvocars.com/energy/v2/vehicles/{self.vin}/state",
            'odometer': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/odometer",
            'engine-status': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/engine-status",
            'warnings': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/warnings",
            'tyres': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/tyres",
            'diagnostics': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/diagnostics",
            'location': f"https://api.volvocars.com/location/v1/vehicles/{self.vin}/location",
             # 'doors': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/doors",
             # 'windows': f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/windows",
        }
    
        url = endpoint_map.get(
            endpoint,
            f"https://api.volvocars.com/connected-vehicle/v2/vehicles/{self.vin}/{endpoint}",
        )
        headers = {
            'Accept': 'application/json;q=0.9,text/plain',
            'Vcc-Api-Key': self.config['api_key'],
            'Authorization': self.session.headers.get('Authorization'),
            'vcc-api-operationId': f"exporter-poll-{endpoint}",
        }
    
        if LOG_LEVEL == 'debug':
            log(f"[request] {endpoint} GET {url} headers={{'Vcc-Api-Key': '***', 'Authorization': '***'}}", 'debug')
    
        response = self.session.get(url, headers=headers, timeout=10)
    
        if LOG_LEVEL == 'debug':
            body_preview = response.text[:2000]
            log(f"[response] {endpoint} {response.status_code} body={body_preview}", 'debug')
    
        if response.status_code == 401:
            log(f"401 detected on {endpoint} - attempting refresh", 'warning')
            if self.safe_refresh():
                headers['Authorization'] = self.session.headers.get('Authorization')
                response = self.session.get(url, headers=headers, timeout=10)
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

