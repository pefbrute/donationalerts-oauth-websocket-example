import asyncio
import json
import logging
import secrets
import httpx
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError, InvalidStatusCode

# Import the application config settings
import config as app_config

# Import other necessary modules AFTER config is loaded
from token_storage import load_token, save_token, is_token_valid, needs_refresh, clear_token
from websocket_manager import websocket_manager

# --- Configuration ---
# Load settings from the central config module
settings = app_config.settings
logger = logging.getLogger(__name__)

# Use constants defined in config.py via the loaded settings dictionary
APP_ID = settings["APP_ID"]
API_KEY = settings["API_KEY"]
REDIRECT_URI = settings["REDIRECT_URI"]
SCOPES = settings["DA_SCOPES"]
AUTHORIZATION_URL = settings["DA_AUTHORIZATION_URL"]
TOKEN_URL = settings["DA_TOKEN_URL"]
API_BASE_URL = settings["DA_API_BASE_URL"]
CENTRIFUGO_URL = settings["DA_CENTRIFUGO_URL"]

# --- Helper Functions ---
async def _make_api_request(method: str, endpoint: str, access_token: str, **kwargs):
    """Helper to make authenticated requests to the DA API."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{API_BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            if not response.content:
                 logger.warning(f"Empty response received from {url}")
                 return None
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Error during API request to {url}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                 logger.error("Token might be expired or invalid.")
            raise
        except httpx.RequestError as e:
            logger.error(f"Network error during API request to {url}: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from {url}: {e}")
            logger.error(f"Response content was: {response.text}")
            raise

# --- OAuth Functions ---
def get_authorization_url():
    """Generates the DonationAlerts authorization URL."""
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    request = httpx.Request('GET', AUTHORIZATION_URL, params=params)
    return str(request.url), state

async def exchange_code_for_token(code: str):
    """Exchanges the authorization code for access and refresh tokens."""
    data = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": API_KEY,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(TOKEN_URL, data=data)
            response.raise_for_status()
            token_data = response.json()
            save_token(token_data)
            logger.info("Successfully exchanged code for token and saved.")
            return token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Error exchanging code for token: {e.response.status_code} - {e.response.text}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Network error exchanging code for token: {e}")
            return None
        except json.JSONDecodeError as e:
             logger.error(f"Failed to decode JSON token response: {e}")
             return None

async def refresh_access_token():
    """Refreshes the access token using the stored refresh token."""
    token_data = load_token()
    if not token_data or 'refresh_token' not in token_data:
        logger.error("Cannot refresh: No token data or refresh token found.")
        clear_token()
        return None

    data = {
        "grant_type": "refresh_token",
        "client_id": APP_ID,
        "client_secret": API_KEY,
        "refresh_token": token_data['refresh_token'],
        "redirect_uri": REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        try:
            logger.info("Attempting to refresh access token...")
            response = await client.post(TOKEN_URL, data=data)
            response.raise_for_status()
            new_token_data = response.json()
            if 'refresh_token' not in new_token_data and 'refresh_token' in token_data:
                 new_token_data['refresh_token'] = token_data['refresh_token']
            save_token(new_token_data)
            logger.info("Access token refreshed and saved successfully.")
            return new_token_data
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Error refreshing token: {e.response.status_code} - {e.response.text}")
            if e.response.status_code in [400, 401]:
                logger.error("Refresh token likely invalid or revoked. Clearing token.")
                clear_token()
            return None
        except httpx.RequestError as e:
            logger.error(f"Network error refreshing token: {e}")
            return None
        except json.JSONDecodeError as e:
             logger.error(f"Failed to decode JSON refresh token response: {e}")
             return None

# --- Centrifugo WebSocket Client ---

class DonationAlertsListener:
    def __init__(self):
        self._ws = None
        self._client_id = None
        self._user_id = None
        self._listen_task = None
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._is_running = False
        self._connect_lock = asyncio.Lock()

    async def start(self):
        async with self._connect_lock:
            if self._is_running:
                logger.info("Listener is already running.")
                return
            self._is_running = True
            logger.info("Starting DonationAlerts Listener...")
            if self._listen_task:
                 logger.warning("Listen task already exists, cancelling previous.")
                 self._listen_task.cancel()
                 try:
                     await self._listen_task
                 except asyncio.CancelledError:
                     pass
                 self._listen_task = None
            self._listen_task = asyncio.create_task(self._connection_loop())

    async def stop(self):
        async with self._connect_lock:
            if not self._is_running:
                logger.info("Listener is not running.")
                return
            self._is_running = False
            logger.info("Stopping DonationAlerts Listener...")
            if self._listen_task:
                self._listen_task.cancel()
                try:
                    await self._listen_task
                except asyncio.CancelledError:
                    logger.info("Listen task cancelled.")
                self._listen_task = None
            if self._ws and self._ws.open:
                await self._ws.close()
                logger.info("WebSocket connection closed.")
            self._ws = None
            self._client_id = None
            self._user_id = None
            self._reconnect_delay = 5

    async def _connection_loop(self):
        while self._is_running:
            token_data = load_token()
            if not token_data:
                logger.error("No token found. Listener cannot start. Stopping.")
                self._is_running = False
                break

            if needs_refresh(token_data):
                logger.info("Token needs refresh before connecting.")
                token_data = await refresh_access_token()
                if not token_data:
                    logger.error("Failed to refresh token. Stopping listener.")
                    self._is_running = False
                    break

            if not is_token_valid(token_data):
                 logger.error("Token is invalid even after potential refresh. Stopping listener.")
                 self._is_running = False
                 break

            access_token = token_data['access_token']

            try:
                logger.info("Attempting to get user info and socket token...")
                user_info = await _make_api_request('GET', '/user/oauth', access_token)
                if user_info is None or 'data' not in user_info:
                     logger.error("Failed to get valid user info from API (empty or missing 'data').")
                     raise ValueError("Invalid user info response")

                self._user_id = user_info.get('data', {}).get('id')
                socket_token = user_info.get('data', {}).get('socket_connection_token')

                if not self._user_id or not socket_token:
                    logger.error("Failed to get user_id or socket_connection_token from API response.")
                    raise ValueError("Missing critical connection info in API response")

                logger.info(f"Got user_id: {self._user_id}, proceeding to WebSocket connect.")
                await self._connect_and_listen(socket_token, access_token)

                self._reconnect_delay = 5
                logger.info("WebSocket connection closed normally. Will attempt reconnect.")

            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, ConnectionClosedError, InvalidStatusCode) as e:
                logger.error(f"Connection loop error: {e}. Attempting reconnect after {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            except ConnectionClosedOK:
                 logger.info("WebSocket connection closed by server (OK). Attempting reconnect...")
                 self._reconnect_delay = 5
                 await asyncio.sleep(self._reconnect_delay)
            except Exception as e:
                logger.exception(f"Unexpected error in connection loop: {e}. Stopping listener.")
                self._is_running = False
                break

            if self._is_running:
                 await asyncio.sleep(1)

        logger.info("Connection loop finished.")

    async def _connect_and_listen(self, socket_token, access_token):
        async with websockets.connect(CENTRIFUGO_URL) as ws:
            self._ws = ws
            logger.info(f"WebSocket connected to {CENTRIFUGO_URL}")

            auth_payload = {"params": {"token": socket_token}, "id": 1}
            await ws.send(json.dumps(auth_payload))
            logger.info("Sent WebSocket authentication request.")

            auth_response_str = await ws.recv()
            auth_response = json.loads(auth_response_str)
            logger.debug(f"Received auth response: {auth_response_str}")

            if auth_response.get("id") == 1 and "result" in auth_response and "client" in auth_response["result"]:
                self._client_id = auth_response["result"]["client"]
                logger.info(f"WebSocket authenticated successfully. Client ID: {self._client_id}")
            else:
                logger.error(f"WebSocket authentication failed. Response: {auth_response_str}")
                raise ValueError("WebSocket authentication failed")

            channel_name = f"$alerts:donation_{self._user_id}"
            logger.info(f"Requesting subscription token for channel: {channel_name}")
            sub_token_payload = {"client": self._client_id, "channels": [channel_name]}
            sub_response = await _make_api_request('POST', '/centrifuge/subscribe', access_token, json=sub_token_payload)

            if sub_response is None or "channels" not in sub_response or not isinstance(sub_response["channels"], list):
                 logger.error(f"Invalid or missing 'channels' in subscription token response: {sub_response}")
                 raise ValueError("Failed to get valid subscription token response")

            subscription_token = None
            for channel_info in sub_response["channels"]:
                if channel_info.get("channel") == channel_name and "token" in channel_info:
                    subscription_token = channel_info["token"]
                    break

            if not subscription_token:
                 logger.error(f"Could not find subscription token for {channel_name} in response: {sub_response}")
                 raise ValueError("Failed to get subscription token")

            logger.info("Successfully obtained subscription token.")

            subscribe_payload = {
                "id": 2,
                "method": 1,
                "params": {"channel": channel_name, "token": subscription_token}
            }
            await ws.send(json.dumps(subscribe_payload))
            logger.info(f"Sent subscribe request for channel {channel_name}")

            logger.info("Listener is now waiting for donation messages...")
            self._reconnect_delay = 5

            async for message_str in ws:
                logger.debug(f"RAW MESSAGE RECEIVED: {message_str}")
                try:
                    message = json.loads(message_str)
                    if "result" in message and "channel" in message["result"] and message["result"]["channel"] == channel_name:
                         if "data" in message["result"] and "data" in message["result"]["data"]:
                             donation_data = message["result"]["data"]["data"]
                             logger.info(f"Received donation: ID={donation_data.get('id')}, User={donation_data.get('username') or donation_data.get('name')}, Amount={donation_data.get('amount')} {donation_data.get('currency')}")
                             await websocket_manager.broadcast({"type": "donation", "data": donation_data})
                         elif "type" in message["result"] and message["result"]["type"] == 1 and "data" in message["result"] and "info" in message["result"]["data"]:
                              logger.info("Subscription confirmation message received. Broadcasting connected status.")
                              await websocket_manager.broadcast({"type": "listener_status", "status": "connected"})
                         else:
                              logger.warning(f"Received message on channel, but 'data.data' missing: {message_str}")
                    elif "id" in message:
                        if message.get("id") == 2 and "result" in message:
                             logger.info("Successfully subscribed to channel (received subscribe ack). Broadcasting connected status.")
                             await websocket_manager.broadcast({"type": "listener_status", "status": "connected"})
                        else:
                            logger.debug(f"Received message with ID (likely ping or subscribe ack): {message_str}")
                    else:
                         logger.warning(f"Received unhandled message format: {message_str}")

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON message: {message_str}")
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")

# Global listener instance
listener = DonationAlertsListener() 