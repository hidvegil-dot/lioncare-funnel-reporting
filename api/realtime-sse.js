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

  // Követjük a Fireflies hivatalos mintáját: nem kényszerítjük a transportot.
  const fireflies = io('wss://api.fireflies.ai', {
    path: '/ws/realtime',
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

  fireflies.on('transcription.broadcast', rawEvent => {
    transcriptEvents += 1;
    const event = rawEvent?.data || rawEvent?.payload || rawEvent || {};
    const normalized = {
      transcript_id: event.transcript_id || event.transcriptId || transcriptId,
      chunk_id: event.chunk_id || event.chunkId || `rt-${Date.now()}-${transcriptEvents}`,
      text: event.text || event.transcript || event.raw_text || event.rawText || '',
      speaker_name: event.speaker_name || event.speakerName || event.speaker || 'Beszélő',
      start_time: event.start_time ?? event.startTime ?? null,
      end_time: event.end_time ?? event.endTime ?? null
    };
    send('transcript', normalized);
    send('diagnostic', {
      stage:'transcript',
      count:transcriptEvents,
      hasText:!!normalized.text,
      speaker:normalized.speaker_name,
      rawKeys:Object.keys(rawEvent || {}),
      normalizedKeys:Object.keys(normalized)
    });
  });

  fireflies.onAny((eventName, ...args) => {
    if (['transcription.broadcast','auth.success','auth.failed','connection.established','connection.error'].includes(eventName)) return;
    send('diagnostic', { stage:'event', eventName, arg0Keys:Object.keys(args?.[0] || {}) });
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

  res.on('close', cleanup);
  req.on('aborted', cleanup);
};
