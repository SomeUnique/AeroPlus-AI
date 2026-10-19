"""WebSocket connection hub: fan-out of validated envelopes to all clients."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from models.schemas import _Envelope, dumps

logger = logging.getLogger("aeropulse.hub")


class ConnectionHub:
    #: a stalled peer must not block fan-out for every other client
    SEND_TIMEOUT_S = 2.0

    def __init__(self) -> None:
        self._clients: Dict[WebSocket, int] = {}
        self._seq = 0
        self.frames_delivered = 0
        self.send_errors = 0

    # -- lifecycle ------------------------------------------------------------ #
    async def connect(self, websocket: WebSocket) -> int:
        await websocket.accept()
        client_id = id(websocket)
        self._clients[websocket] = client_id
        logger.info("client connected (%d total)", len(self._clients))
        return client_id

    def disconnect(self, websocket: WebSocket) -> None:
        if self._clients.pop(websocket, None) is not None:
            logger.info("client disconnected (%d total)", len(self._clients))

    @property
    def count(self) -> int:
        return len(self._clients)

    def next_seq(self) -> int:
        """Server-global monotonic sequence shared by every message type."""
        self._seq += 1
        return self._seq

    # -- delivery ------------------------------------------------------------- #
    async def send(self, websocket: WebSocket, message: _Envelope) -> bool:
        if websocket.client_state is not WebSocketState.CONNECTED:
            return False
        try:
            await asyncio.wait_for(websocket.send_json(dumps(message)), self.SEND_TIMEOUT_S)
            self.frames_delivered += 1
            return True
        except Exception:  # noqa: BLE001 - peer vanished, timed out, etc.
            self.send_errors += 1
            self.disconnect(websocket)
            return False

    async def broadcast(self, message: _Envelope) -> int:
        """Send to every connected client. Returns the delivery count."""
        if not self._clients:
            return 0
        payload = dumps(message)
        delivered = 0
        for websocket in list(self._clients):
            if websocket.client_state is not WebSocketState.CONNECTED:
                self.disconnect(websocket)
                continue
            try:
                await asyncio.wait_for(websocket.send_json(payload), self.SEND_TIMEOUT_S)
                self.frames_delivered += 1
                delivered += 1
            except Exception:  # noqa: BLE001
                self.send_errors += 1
                self.disconnect(websocket)
        return delivered

    def stats(self) -> dict:
        return {
            "clients": self.count,
            "frames_delivered": self.frames_delivered,
            "send_errors": self.send_errors,
            "seq": self._seq,
        }
