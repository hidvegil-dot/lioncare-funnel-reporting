const { experimental_upgradeWebSocket } = require('@vercel/functions');
const { io } = require('socket.io-client');

module.exports = function handler(req, res) {
  return experimental_upgradeWebSocket((ws, request) => {
    const url = new URL(request?.url || req.url || '/', `https://${req.headers.host}`);
    const transcriptId = url.searchParams.get('transcriptId');
    const token = process.env.FIREFLIES_API_KEY;

    const send = (payload) => {
      try {
        ws.send(JSON.stringify(payload));
      } catch (_) {}
    };

    if (!token) {
      send({ type: 'error', error: 'FIREFLIES_API_KEY nincs beállítva' });
      try { ws.close(1011, 'Missing server configuration'); } catch (_) {}
      return;
    }

    if (!transcriptId) {
      send({ type: 'error', error: 'Hiányzó transcriptId' });
      try { ws.close(1008, 'Missing transcriptId'); } catch (_) {}
      return;
    }

    send({ type: 'proxy.connected', transcriptId });

    const fireflies = io('https://api.fireflies.ai', {
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

    fireflies.on('connect', () => send({ type: 'fireflies.socket.connected' }));
    fireflies.on('auth.success', (data) => send({ type: 'auth.success', data }));
    fireflies.on('auth.failed', (err) => send({ type: 'auth.failed', error: err?.message || String(err || 'Fireflies hitelesítési hiba') }));
    fireflies.on('connection.established', () => send({ type: 'connected', transcriptId }));
    fireflies.on('connection.error', (err) => send({ type: 'connection.error', error: err?.message || String(err || 'Fireflies kapcsolati hiba') }));
    fireflies.on('connect_error', (err) => send({ type: 'connection.error', error: err?.message || String(err || 'Fireflies kapcsolódási hiba') }));
    fireflies.on('disconnect', (reason) => send({ type: 'fireflies.socket.disconnected', reason }));
    fireflies.on('transcription.broadcast', (event) => send({ type: 'transcript', event }));

    const cleanup = () => {
      try { fireflies.disconnect(); } catch (_) {}
    };

    ws.on('close', cleanup);
    ws.on('error', cleanup);
    ws.on('message', (raw) => {
      let msg;
      try { msg = JSON.parse(String(raw)); } catch (_) { return; }
      if (msg?.type === 'ping') send({ type: 'pong', at: Date.now() });
    });
  });
};
