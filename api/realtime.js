const { createServer } = require('http');
const { WebSocketServer, WebSocket } = require('ws');
const { io } = require('socket.io-client');

const server = createServer((req, res) => {
  res.writeHead(200, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store'
  });
  res.end(JSON.stringify({
    ok: true,
    service: 'lioncare-fireflies-realtime-proxy'
  }));
});

const wss = new WebSocketServer({ server });

wss.on('connection', (client, req) => {
  const base = `http://${req.headers.host || 'localhost'}`;
  const url = new URL(req.url || '/', base);
  const transcriptId = url.searchParams.get('transcriptId');
  const token = process.env.FIREFLIES_API_KEY;

  const send = (payload) => {
    try {
      if (client.readyState === WebSocket.OPEN) {
        client.send(JSON.stringify(payload));
      }
    } catch (_) {}
  };

  if (!token) {
    send({ type: 'error', error: 'FIREFLIES_API_KEY nincs beállítva' });
    client.close(1011, 'Missing server configuration');
    return;
  }

  if (!transcriptId) {
    send({ type: 'error', error: 'Hiányzó transcriptId' });
    client.close(1008, 'Missing transcriptId');
    return;
  }

  send({ type: 'proxy.connected', transcriptId });

  const fireflies = io('wss://api.fireflies.ai', {
    path: '/ws/realtime',
    transports: ['websocket'],
    auth: {
      token: `Bearer ${token}`,
      transcriptId
    },
    reconnection: true,
    reconnectionAttempts: 10,
    reconnectionDelay: 1000,
    timeout: 15000
  });

  fireflies.on('connect', () => {
    send({ type: 'fireflies.socket.connected' });
  });

  fireflies.on('auth.success', (data) => {
    send({ type: 'auth.success', data });
  });

  fireflies.on('auth.failed', (err) => {
    send({
      type: 'auth.failed',
      error: err?.message || String(err || 'Fireflies hitelesítési hiba')
    });
  });

  fireflies.on('connection.established', () => {
    send({ type: 'connected', transcriptId });
  });

  fireflies.on('connection.error', (err) => {
    send({
      type: 'connection.error',
      error: err?.message || String(err || 'Fireflies kapcsolati hiba')
    });
  });

  fireflies.on('connect_error', (err) => {
    send({
      type: 'connection.error',
      error: err?.message || String(err || 'Fireflies kapcsolódási hiba')
    });
  });

  fireflies.on('disconnect', (reason) => {
    send({ type: 'fireflies.socket.disconnected', reason });
  });

  fireflies.on('transcription.broadcast', (event) => {
    send({ type: 'transcript', event });
  });

  client.on('message', (raw) => {
    try {
      const msg = JSON.parse(String(raw));
      if (msg?.type === 'ping') {
        send({ type: 'pong', at: Date.now() });
      }
    } catch (_) {}
  });

  const cleanup = () => {
    try { fireflies.disconnect(); } catch (_) {}
  };

  client.on('close', cleanup);
  client.on('error', cleanup);
});

module.exports = server;
