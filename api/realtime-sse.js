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

  let closed = false;
  let transcriptEvents = 0;

  const send = (type, data = {}) => {
    if (closed || res.writableEnded) return;
    try {
      res.write(`event: ${type}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    } catch (_) {}
  };

  send('proxy', { ok:true, transcriptId, at:Date.now() });

  const fireflies = io('wss://api.fireflies.ai', {
    path: '/ws/realtime',
    transports: ['websocket'],
    auth: { token: `Bearer ${token}`, transcriptId },
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    timeout: 20000
  });

  fireflies.on('connect', () => send('socket', { status:'connected', socketId:fireflies.id }));
  fireflies.on('auth.success', data => send('auth', { ok:true, data }));
  fireflies.on('auth.failed', err => send('fatal', { stage:'auth', error: err?.message || String(err || 'Fireflies hitelesítési hiba') }));
  fireflies.on('connection.established', () => send('ready', { transcriptId }));
  fireflies.on('connection.error', err => send('fatal', { stage:'connection', error: err?.message || String(err || 'Fireflies kapcsolati hiba') }));
  fireflies.on('connect_error', err => send('fatal', { stage:'socket', error: err?.message || String(err || 'Fireflies kapcsolódási hiba') }));
  fireflies.on('disconnect', reason => send('diagnostic', { stage:'disconnect', reason }));
  fireflies.on('transcription.broadcast', event => {
    transcriptEvents += 1;
    send('transcript', event);
    send('diagnostic', { stage:'transcript', count:transcriptEvents, hasText:!!event?.text, speaker:event?.speaker_name || null });
  });

  const heartbeat = setInterval(() => send('ping', {
    at: Date.now(),
    connected: fireflies.connected,
    transcriptEvents
  }), 15000);

  const rotate = setTimeout(() => {
    send('diagnostic', { stage:'rotate', message:'stream újranyitás', transcriptEvents });
    cleanup();
  }, 240000);

  function cleanup() {
    if (closed) return;
    closed = true;
    clearInterval(heartbeat);
    clearTimeout(rotate);
    try { fireflies.disconnect(); } catch (_) {}
    try { if (!res.writableEnded) res.end(); } catch (_) {}
  }

  // SSE esetén a kliens kapcsolatának lezárását a RESPONSE-on figyeljük.
  // A req 'close' eseménye már a bejövő GET kérés befejezésekor is elsülhet,
  // ami idő előtt megszakíthatná a Fireflies streamet.
  res.on('close', cleanup);
  req.on('aborted', cleanup);
};
