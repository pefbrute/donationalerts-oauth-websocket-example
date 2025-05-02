import os
import logging
import secrets
from dotenv import dotenv_values

ENV_FILE = ".env"
logger = logging.getLogger(__name__)

# --- Constants ---
# These will be written to .env if it doesn't exist
DEFAULT_APP_ID = "YOUR_DONATIONALERTS_APP_ID"
DEFAULT_API_KEY = "YOUR_DONATIONALERTS_API_KEY"
DEFAULT_REDIRECT_URI = "http://localhost:8000/api/auth/donationalerts/callback"
DEFAULT_SCOPES = "oauth-user-show oauth-donation-subscribe"
DEFAULT_DA_AUTHORIZATION_URL = "https://www.donationalerts.com/oauth/authorize"
DEFAULT_DA_TOKEN_URL = "https://www.donationalerts.com/oauth/token"
DEFAULT_DA_API_BASE_URL = "https://www.donationalerts.com/api/v1"
DEFAULT_DA_CENTRIFUGO_URL = "wss://centrifugo.donationalerts.com/connection/websocket"

# Variables required for the application to run
REQUIRED_VARS = [
    "APP_ID",
    "API_KEY",
    "REDIRECT_URI",
    "SESSION_SECRET_KEY",
    "DA_SCOPES",
    "DA_AUTHORIZATION_URL",
    "DA_TOKEN_URL",
    "DA_API_BASE_URL",
    "DA_CENTRIFUGO_URL",
]


def _get_credentials_instructions() -> str:
    """Returns detailed instructions for getting DonationAlerts credentials."""
    return f"""
INSTRUCTIONS TO GET YOUR APP_ID and API_KEY:

1.  Go to the DonationAlerts OAuth Applications page: https://www.donationalerts.com/application/clients
2.  Log in using your streamer account (Twitch, YouTube, etc.).
3.  On the "OAuth API Applications" page, click the "+ CREATE NEW APP" button. This will open the "New Application" form.
4.  Fill in the "New Application" form:
    *   **App name:** Choose any name, e.g., "My Donation Monitor".
        (This is displayed to users when authorizing your application).
    *   **Redirect URL:** **Crucial:** Enter EXACTLY: {DEFAULT_REDIRECT_URI}
        (This is the allowed redirect URL after authorization. It must match the one used by this application).
5.  Click the orange "CREATE" button.
6.  After creating the app, you will be returned to the "OAuth API Applications" list. Find your application by the name you just entered.
7.  You will see the following details for your app:
    *   **App ID:** This is your `APP_ID`. Copy this value.
    *   **API Key:** This is your `API_KEY`. Copy this value.
8.  **IMPORTANT:** Keep your `API_KEY` confidential! Do not share it.

Now, please enter the values you copied below.
"""

def _prompt_and_create_env():
    """Prompts user for credentials, generates secret, and creates .env file."""
    print("-" * 60)
    print(f"Configuration file '{ENV_FILE}' not found.")
    print(_get_credentials_instructions())
    print("-" * 60)

    app_id = ""
    while not app_id:
        app_id = input("Enter your APP_ID: ").strip()
        if not app_id:
            print("APP_ID cannot be empty. Please enter it.")

    api_key = ""
    while not api_key:
        api_key = input("Enter your API_KEY: ").strip()
        if not api_key:
            print("API_KEY cannot be empty. Please enter it.")

    # Generate a secure session secret key
    session_secret = secrets.token_hex(32)
    print(f"\nGenerated a session secret key (SESSION_SECRET_KEY).")

    env_content = f"""# DonationAlerts Application Credentials
APP_ID="{app_id}"
API_KEY="{api_key}"

# OAuth Settings (Ensure this matches the value in DonationAlerts App settings)
REDIRECT_URI="{DEFAULT_REDIRECT_URI}"
DA_SCOPES="{DEFAULT_SCOPES}"

# Session Secret Key (Keep this secret!)
SESSION_SECRET_KEY="{session_secret}"

# DonationAlerts API Endpoints (Usually no need to change)
DA_AUTHORIZATION_URL="{DEFAULT_DA_AUTHORIZATION_URL}"
DA_TOKEN_URL="{DEFAULT_DA_TOKEN_URL}"
DA_API_BASE_URL="{DEFAULT_DA_API_BASE_URL}"
DA_CENTRIFUGO_URL="{DEFAULT_DA_CENTRIFUGO_URL}"
"""
    try:
        with open(ENV_FILE, "w") as f:
            f.write(env_content)
        print(f"\nFile '{ENV_FILE}' created successfully with your details.")
        print("You can now run the application again.")
        print("-" * 60)
    except IOError as e:
        logger.error(f"Failed to create file '{ENV_FILE}': {e}")
        raise


def load_app_config() -> dict:
    """
    Ensures .env file exists (prompts/creates if not), loads it,
    validates required variables, and returns the config dictionary.
    """
    if not os.path.exists(ENV_FILE):
        try:
            _prompt_and_create_env()
        except Exception as e:
            logger.critical(f"Critical error during .env file creation: {e}. Exiting.")
            exit(1)

    config = dotenv_values(ENV_FILE)

    missing_vars = [var for var in REQUIRED_VARS if var not in config or not config[var]]
    if missing_vars:
        error_msg = f"Critical environment variables missing or empty in '{ENV_FILE}': {', '.join(missing_vars)}. Please check the file or delete '{ENV_FILE}' to recreate."
        logger.critical(error_msg)
        raise ValueError(error_msg)

    if config.get("APP_ID") == DEFAULT_APP_ID or config.get("API_KEY") == DEFAULT_API_KEY:
         logger.warning(f"Default placeholder values found for APP_ID or API_KEY in '{ENV_FILE}'. Ensure you replaced them with your actual credentials.")

    logger.info(f"Configuration successfully loaded from '{ENV_FILE}'.")
    return config

# Load config once on import to be used by other modules
settings = load_app_config() 