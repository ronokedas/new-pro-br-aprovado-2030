import { useEffect, useRef, useState } from 'react';
import { getStoredToken } from '../lib/api';
import type { WsDashboardMessage } from '../types/api';

export type SocketStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

interface UseLiveSocketOptions {
  enabled?: boolean;
  onMessage: (msg: WsDashboardMessage) => void;
}

export function useLiveSocket({ enabled = true, onMessage }: UseLiveSocketOptions) {
  const [status, setStatus] = useState<SocketStatus>('disconnected');
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!enabled) {
      setStatus('disconnected');
      return;
    }

    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let pingTimer: number | null = null;
    let disposed = false;
    let attempt = 0;

    const cleanupTimers = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (pingTimer !== null) {
        window.clearInterval(pingTimer);
        pingTimer = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      const token = getStoredToken();
      if (!token) {
        setStatus('disconnected');
        return;
      }

      setStatus(attempt === 0 ? 'connecting' : 'reconnecting');

      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${window.location.host}/ws/dashboard?token=${encodeURIComponent(token)}`;

      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        if (disposed) {
          ws?.close();
          return;
        }
        attempt = 0;
        setStatus('connected');
        pingTimer = window.setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            try {
              ws.send(JSON.stringify({ type: 'ping' }));
            } catch {
              // ignore
            }
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        try {
          const parsed = JSON.parse(event.data) as WsDashboardMessage;
          setLastMessageAt(Date.now());
          onMessageRef.current(parsed);
        } catch {
          // ignore non-JSON frames
        }
      };

      ws.onerror = () => {
        // onclose handles reconnect
      };

      ws.onclose = () => {
        cleanupTimers();
        if (disposed) return;
        setStatus('reconnecting');
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      attempt += 1;
      const delay = Math.min(15000, 1000 * Math.pow(1.6, Math.min(attempt, 6)));
      reconnectTimer = window.setTimeout(connect, delay);
    };

    connect();

    return () => {
      disposed = true;
      cleanupTimers();
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      setStatus('disconnected');
    };
  }, [enabled]);

  return { status, lastMessageAt };
}
