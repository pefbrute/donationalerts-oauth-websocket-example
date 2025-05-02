import json
import os
import logging
from datetime import datetime, timedelta, timezone

TOKEN_FILE = "da_token.json"
logger = logging.getLogger(__name__)

def save_token(token_data):
    """Saves token data (including access, refresh, expires_in) to a file."""
    try:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_data.get('expires_in', 0)))
        token_data['expires_at'] = expires_at.isoformat()
        with open(TOKEN_FILE, 'w') as f:
            json.dump(token_data, f, indent=4)
        logger.info(f"Token saved successfully to {TOKEN_FILE}")
    except Exception as e:
        logger.error(f"Error saving token to {TOKEN_FILE}: {e}")

def load_token():
    """Loads token data from the file."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)
            # Ensure expires_at is present for consistency
            if 'expires_at' not in token_data and 'expires_in' in token_data:
                 # Estimate expires_at if missing (less accurate)
                 # This might happen if the app crashed before saving expires_at
                 logger.warning("Token file missing 'expires_at', estimating based on file mod time.")
                 file_mtime = datetime.fromtimestamp(os.path.getmtime(TOKEN_FILE), timezone.utc)
                 expires_at = file_mtime + timedelta(seconds=int(token_data.get('expires_in', 0)))
                 token_data['expires_at'] = expires_at.isoformat()

            logger.info(f"Token loaded successfully from {TOKEN_FILE}")
            return token_data
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        logger.error(f"Error loading token from {TOKEN_FILE}: {e}")
        # Attempt to delete corrupted file
        try:
            os.remove(TOKEN_FILE)
            logger.info(f"Corrupted token file {TOKEN_FILE} deleted.")
        except OSError as del_e:
            logger.error(f"Failed to delete corrupted token file {TOKEN_FILE}: {del_e}")
        return None

def clear_token():
    """Deletes the token file."""
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
            logger.info(f"Token file {TOKEN_FILE} deleted.")
    except OSError as e:
        logger.error(f"Error deleting token file {TOKEN_FILE}: {e}")

def is_token_valid(token_data):
    """Checks if the token data exists and the access token is not expired."""
    if not token_data or 'access_token' not in token_data or 'expires_at' not in token_data:
        return False
    try:
        # Add a small buffer (e.g., 60 seconds) to consider token expired slightly earlier
        buffer = timedelta(seconds=60)
        expires_at = datetime.fromisoformat(token_data['expires_at'])
        return datetime.now(timezone.utc) < (expires_at - buffer)
    except (ValueError, TypeError) as e:
        logger.error(f"Error parsing token expiration time: {e}")
        return False

def needs_refresh(token_data):
    """Checks if the token exists but is expired (or close to expiring)."""
    if not token_data or 'refresh_token' not in token_data or 'expires_at' not in token_data:
        return False # Cannot refresh if no refresh token or expiry info
    try:
        buffer = timedelta(seconds=60)
        expires_at = datetime.fromisoformat(token_data['expires_at'])
        # Needs refresh if current time is past expiry (minus buffer)
        return datetime.now(timezone.utc) >= (expires_at - buffer)
    except (ValueError, TypeError) as e:
        logger.error(f"Error parsing token expiration time for refresh check: {e}")
        return True # Assume refresh needed if parsing fails 