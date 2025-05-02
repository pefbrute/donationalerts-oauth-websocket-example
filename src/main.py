import logging
import asyncio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
# from fastapi.staticfiles import StaticFiles # Keep if you add static files later
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketState

# Import the new config loader
import config as app_config # Use the loaded settings directly

# Import other modules
import token_storage
import donationalerts_client # Imports need to happen after config potentially creates .env
from websocket_manager import websocket_manager


# --- Basic Configuration ---
# Setup logging after config ensures .env potentially exists
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# --- FastAPI App Setup ---
# Use settings loaded in config.py
settings = app_config.settings # Get the loaded config

app = FastAPI(title="DonationAlerts OAuth Example")

# Session Middleware (for OAuth state)
# Use the key loaded by config.py
app.add_middleware(SessionMiddleware, secret_key=settings["SESSION_SECRET_KEY"], https_only=False) # Set https_only=True in production

# Templates
templates = Jinja2Templates(directory="templates")


# --- Event Handlers ---
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup...")
    # Try to start listener if token exists and is valid/refreshable
    token_data = token_storage.load_token()
    if token_data and (token_storage.is_token_valid(token_data) or token_storage.needs_refresh(token_data)):
        logger.info("Valid or refreshable token found on startup, attempting to start listener.")
        # Pass necessary config to the start method if needed, or rely on donationalerts_client loading its own config
        await donationalerts_client.listener.start() # Assuming donationalerts_client now uses config.settings
    else:
        logger.info("No valid token found on startup, listener will not start automatically.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown...")
    await donationalerts_client.listener.stop()
    logger.info("Listener stopped.")


# --- HTTP Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the main HTML page."""
    token_data = token_storage.load_token()
    is_authenticated = token_storage.is_token_valid(token_data) or token_storage.needs_refresh(token_data)
    context = {"request": request, "is_authenticated": is_authenticated}
    # Pass app_id only if needed by the template, currently not used
    # context["app_id"] = settings["APP_ID"]
    return templates.TemplateResponse("index.html", context)

@app.get("/api/auth/donationalerts/login")
async def login_donationalerts(request: Request):
    """Initiates the OAuth2 flow by redirecting the user to DonationAlerts."""
    # Call the function which now uses config.settings
    authorization_url, state = donationalerts_client.get_authorization_url()
    request.session['oauth_state'] = state # Store state in session
    logger.info(f"Redirecting user to DonationAlerts for authorization. State: {state}")
    return RedirectResponse(authorization_url)

@app.get("/api/auth/donationalerts/callback")
async def auth_donationalerts_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handles the callback from DonationAlerts after user authorization."""
    if error:
        logger.error(f"DonationAlerts OAuth Error: {error}")
        # Ensure 'is_authenticated' status reflects the failure
        return templates.TemplateResponse("index.html", {"request": request, "error": f"OAuth failed: {error}", "is_authenticated": False})

    stored_state = request.session.pop('oauth_state', None)
    if not state or state != stored_state:
        logger.error(f"OAuth State mismatch. Received: {state}, Expected: {stored_state}")
        return templates.TemplateResponse("index.html", {"request": request, "error": "Invalid OAuth state. CSRF attack possible.", "is_authenticated": False})

    if not code:
         logger.error("OAuth callback missing authorization code.")
         return templates.TemplateResponse("index.html", {"request": request, "error": "Missing authorization code from DonationAlerts.", "is_authenticated": False})

    logger.info("OAuth state verified. Exchanging code for token...")
    # Call the function which now uses config.settings
    token_data = await donationalerts_client.exchange_code_for_token(code)

    if token_data:
        logger.info("Token received successfully. Starting listener...")
        # Ensure listener is started/restarted with new token
        await donationalerts_client.listener.stop() # Stop if running
        await asyncio.sleep(0.1) # Short pause
        # Pass necessary config if needed, or rely on it using config.settings
        await donationalerts_client.listener.start()
        return RedirectResponse(url="/?status=success", status_code=303) # Redirect to homepage
    else:
        logger.error("Failed to exchange code for token.")
        return templates.TemplateResponse("index.html", {"request": request, "error": "Failed to obtain token from DonationAlerts.", "is_authenticated": False})

@app.post("/api/auth/donationalerts/logout")
async def logout_donationalerts():
    """Clears the stored token and stops the listener."""
    logger.info("Logging out user, clearing token and stopping listener.")
    await donationalerts_client.listener.stop()
    token_storage.clear_token()
    return {"message": "Logged out successfully"}


@app.get("/api/status")
async def get_status():
    """Checks if the backend has a potentially valid token."""
    token_data = token_storage.load_token()
    is_authenticated = bool(token_data and (token_storage.is_token_valid(token_data) or token_storage.needs_refresh(token_data)))
    listener_running = donationalerts_client.listener._is_running
    listener_connected = donationalerts_client.listener._ws is not None and donationalerts_client.listener._ws.open
    return {
        "authenticated": is_authenticated,
        "listener_running": listener_running,
        "listener_connected": listener_connected
        }


# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handles WebSocket connections from the frontend."""
    await websocket_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(10)
            if websocket.client_state != WebSocketState.CONNECTED:
                 logger.info(f"WebSocket {websocket.client} state is not CONNECTED, breaking loop.")
                 break
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"Error in WebSocket connection {websocket.client}: {e}", exc_info=True) # Add exc_info for better debugging
    finally:
        websocket_manager.disconnect(websocket)


# --- Uvicorn Runner (for local development) ---
if __name__ == "__main__":
    # The config check/creation now happens when config.py is imported/loaded
    # So by the time we get here, .env should exist.
    import uvicorn
    logger.info("Starting Uvicorn server...")
    # Use reload=False if you encounter issues with background tasks or config reloading
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 