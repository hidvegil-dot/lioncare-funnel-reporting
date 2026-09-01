const { io } = require('socket.io-client');

module.exports = async function handler(req, res) {
  const transcriptId = req.query?.transcriptId;
  const token = process.env.FIREFLIES_API_KEY;

  if (!token) return res.status(500).json({ ok:false, error:'FIREFLIES_API_KEY nincs beállítva' });
  if (!transcriptId) return res.status(400).json({ ok:false, error:'Hiányzó transcriptId' });

  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  if (res.flushHeaders) res.flushHeaders();

  const send = (type, data = {}) => {
    try {
      res.write(`event: ${type}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    } catch (_) {}
  };

  send('proxy', { ok:true, transcriptId });

  const fireflies = io('wss://api.fireflies.ai', {
    path: '/ws/realtime',
    transports: ['websocket'],
    auth: { token: `Bearer ${token}`, transcriptId },
    reconnection: true,
    reconnectionAttempts: 8,
    reconnectionDelay: 1000,
    timeout: 15000
  });

  fireflies.on('connect', () => send('socket', { status:'connected' }));
  fireflies.on('auth.success', data => send('auth', { ok:true, data }));
  fireflies.on('auth.failed', err => send('fatal', { error: err?.message || String(err || 'Fireflies hitelesítési hiba') }));
  fireflies.on('connection.established', () => send('ready', { transcriptId }));
  fireflies.on('connection.error', err => send('fatal', { error: err?.message || String(err || 'Fireflies kapcsolati hiba') }));
  fireflies.on('connect_error', err => send('fatal', { error: err?.message || String(err || 'Fireflies kapcsolódási hiba') }));
  fireflies.on('transcription.broadcast', event => send('transcript', event));

  const heartbeat = setInterval(() => send('ping', { at: Date.now() }), 15000);
  const cleanup = () => {
    clearInterval(heartbeat);
    try { fireflies.disconnect(); } catch (_) {}
    try { res.end(); } catch (_) {}
  };

  req.on('close', cleanup);
  req.on('aborted', cleanup);
};
