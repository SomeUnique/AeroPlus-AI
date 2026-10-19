"use client";

import { useEffect } from "react";

import { API_BASE, WS_URL } from "./config";
import { useTelemetryStore } from "./store";
import { parseMessage, type ClientFrame, type HealthResponse } from "./ws";

const MAX_BACKOFF_MS = 8000;
const HEALTH_POLL_MS = 3000;

/** Active socket, used by `sendClientFrame` to reach the server. */
let activeSocket: WebSocket | null = null;

/**
 * Send a client -> server frame over the shared mission-control socket.
 * Returns false when the socket is not open (caller surfaces a warning).
 */
export function sendClientFrame(frame: ClientFrame): boolean {
  if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) return false;
  try {
    activeSocket.send(JSON.stringify(frame));
    return true;
  } catch {
    return false;
  }
}

/**
 * Single socket for the whole app. Auto-reconnects with exponential backoff and
 * keeps /health polling so REST-only fields (faults, counters) stay fresh.
 */
class TelemetryClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private attempt = 0;
  private refs = 0;
  private stopped = true;

  start() {
    this.refs += 1;
    if (this.refs > 1) return;
    this.stopped = false;
    this.attempt = 0;
    window.addEventListener("online", this.onOnline);
    window.addEventListener("offline", this.onOffline);
    this.connect();
    this.pollHealth();
    this.healthTimer = setInterval(() => this.pollHealth(), HEALTH_POLL_MS);
  }

  stop() {
    this.refs = Math.max(0, this.refs - 1);
    if (this.refs > 0) return;
    this.stopped = true;
    window.removeEventListener("online", this.onOnline);
    window.removeEventListener("offline", this.onOffline);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.healthTimer) clearInterval(this.healthTimer);
    this.reconnectTimer = null;
    this.healthTimer = null;
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      this.socket.onopen = null;
      try {
        this.socket.close();
      } catch {
        /* ignore */
      }
      this.socket = null;
    }
    activeSocket = null;
  }

  private onOnline = () => {
    if (this.stopped) return;
    if (!this.socket || this.socket.readyState > WebSocket.OPEN) this.connect();
  };

  private onOffline = () => {
    useTelemetryStore.getState().setConnection("offline", this.attempt);
  };

  private connect() {
    if (this.stopped) return;
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const store = useTelemetryStore.getState();
    store.setConnection(this.attempt === 0 ? "connecting" : "reconnecting", this.attempt);

    let socket: WebSocket;
    try {
      socket = new WebSocket(WS_URL);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    activeSocket = socket;

    socket.onopen = () => {
      if (this.stopped) return;
      this.attempt = 0;
      useTelemetryStore.getState().setConnection("live", 0);
      this.pollHealth();
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const parsed = parseMessage(event.data);
      if (parsed) useTelemetryStore.getState().ingest(parsed);
    };

    socket.onerror = () => {
      /* onclose follows; handled there */
    };

    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      if (activeSocket === socket) activeSocket = null;
      if (this.stopped) return;
      if (!navigator.onLine) {
        useTelemetryStore.getState().setConnection("offline", this.attempt);
        return;
      }
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    this.attempt += 1;
    useTelemetryStore.getState().setConnection("reconnecting", this.attempt);
    const backoff = Math.min(800 * 2 ** (this.attempt - 1), MAX_BACKOFF_MS);
    const jitter = Math.random() * 300;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), backoff + jitter);
  }

  private async pollHealth() {
    try {
      const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
      if (!res.ok) return;
      const health = (await res.json()) as HealthResponse;
      useTelemetryStore.getState().setHealth(health);
    } catch {
      /* transient; socket state is the source of truth for connection */
    }
  }
}

const client = new TelemetryClient();

export function useTelemetry() {
  useEffect(() => {
    client.start();
    return () => client.stop();
  }, []);
}
