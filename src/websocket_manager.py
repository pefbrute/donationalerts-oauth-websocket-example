import logging
from typing import List, Dict
from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        logger.info("WebSocketManager initialized.")

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket accepted and connected: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected: {websocket.client}")
        # No need to explicitly close here, FastAPI handles it

    async def broadcast(self, data: Dict):
        # Check connection state before sending
        disconnected_sockets = []
        for connection in self.active_connections:
            if connection.client_state == WebSocketState.CONNECTED:
                try:
                    await connection.send_json(data)
                    logger.debug(f"Broadcasted message to {connection.client}")
                except Exception as e:
                    logger.warning(f"Failed to send message to {connection.client}, marking for disconnect: {e}")
                    # Don't remove immediately while iterating
                    disconnected_sockets.append(connection)
            else:
                 logger.warning(f"Found non-connected socket {connection.client}, marking for disconnect.")
                 disconnected_sockets.append(connection)

        # Remove disconnected sockets after iteration
        for socket in disconnected_sockets:
            self.disconnect(socket)

websocket_manager = WebSocketManager() 