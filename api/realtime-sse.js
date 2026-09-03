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
  const seen = new Set();

  const send = (type, data = {}) => {
    if (closed || res.writableEnded) return;
    try {
      res.write(`event: ${type}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    } catch (_) {}
  };

  const parseMaybeJson = value => {
    if (typeof value !== 'string') return value;
    const s = value.trim();
    if (!s) return value;
    if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
      try { return JSON.parse(s); } catch (_) { return value; }
    }
    return value;
  };

  const unwrap = raw => {
    let value = parseMaybeJson(raw);
    for (let i = 0; i < 4; i++) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) break;
      const next = value.data ?? value.payload ?? value.event ?? value.message;
      if (next === undefined || next === value) break;
      value = parseMaybeJson(next);
    }
    return value || {};
  };

  const normalize = (raw, eventName = 'unknown') => {
    let event = unwrap(raw) || {};
    if (typeof event === 'string') event = parseMaybeJson(event);
    if (!event || typeof event !== 'object' || Array.isArray(event)) event = {};

    let text = event.text || event.transcript || event.raw_text || event.rawText || event.sentence || '';
    text = parseMaybeJson(text);
    if (text && typeof text === 'object') text = text.text || text.raw_text || text.rawText || text.transcript || '';

    let speaker = event.speaker_name || event.speakerName || event.speaker?.name || event.speaker || 'Beszélő';
    if (speaker && typeof speaker === 'object') speaker = speaker.name || speaker.display_name || 'Beszélő';

    return {
      transcript_id: event.transcript_id || event.transcriptId || transcriptId,
      chunk_id: event.chunk_id || event.chunkId || event.id || `${eventName}-${Date.now()}-${transcriptEvents}`,
      text: typeof text === 'string' ? text : '',
      speaker_name: typeof speaker === 'string' ? speaker : 'Beszélő',
      start_time: event.start_time ?? event.startTime ?? null,
      end_time: event.end_time ?? event.endTime ?? null
    };
  };

  const forwardTranscript = (raw, eventName) => {
    const normalized = normalize(raw, eventName);
    if (!normalized.text.trim()) return false;
    const fingerprint = `${normalized.chunk_id}|${normalized.text}`;
    if (seen.has(fingerprint)) return true;
    seen.add(fingerprint);
    if (seen.size > 500) seen.delete(seen.values().next().value);
    transcriptEvents += 1;
    send('transcript', normalized);
    send('diagnostic', {
      stage:'transcript', eventName, count:transcriptEvents,
      hasText:true, speaker:normalized.speaker_name,
      rawType:typeof raw,
      rawKeys:(raw && typeof raw === 'object') ? Object.keys(raw) : []
    });
    return true;
  };

  send('proxy', { ok:true, transcriptId, at:Date.now() });

  const fireflies = io('wss://api.fireflies.ai', {
    path: '/ws/realtime',
    transports: ['websocket'],
    upgrade: false,
    auth: { token: `Bearer ${token}`, transcriptId },
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    timeout: 20000
  });

  fireflies.on('connect', () => send('socket', { status:'connected', socketId:fireflies.id, transport:fireflies.io.engine?.transport?.name || 'websocket' }));
  fireflies.on('auth.success', data => send('auth', { ok:true, data }));
  fireflies.on('auth.failed', err => send('fatal', { stage:'auth', error: err?.message || String(err || 'Fireflies hitelesítési hiba') }));
  fireflies.on('connection.established', () => send('ready', { transcriptId }));
  fireflies.on('connection.error', err => send('fatal', { stage:'connection', error: err?.message || String(err || 'Fireflies kapcsolati hiba') }));
  fireflies.on('connect_error', err => send('fatal', { stage:'socket', error: err?.message || String(err || 'Fireflies WebSocket kapcsolódási hiba') }));
  fireflies.on('disconnect', reason => send('diagnostic', { stage:'disconnect', reason }));

  fireflies.on('transcription.broadcast', rawEvent => {
    const ok = forwardTranscript(rawEvent, 'transcription.broadcast');
    if (!ok) {
      const unwrapped = unwrap(rawEvent);
      send('diagnostic', {
        stage:'empty-transcript-event',
        eventName:'transcription.broadcast',
        rawType:typeof rawEvent,
        rawPreview:typeof rawEvent === 'string' ? rawEvent.slice(0,300) : null,
        rawKeys:(rawEvent && typeof rawEvent === 'object') ? Object.keys(rawEvent) : [],
        nestedType:typeof unwrapped,
        nestedKeys:(unwrapped && typeof unwrapped === 'object') ? Object.keys(unwrapped) : []
      });
    }
  });

  fireflies.onAny((eventName, ...args) => {
    if (['transcription.broadcast','auth.success','auth.failed','connection.established','connection.error'].includes(eventName)) return;
    let forwarded = false;
    for (const arg of args) {
      if (forwardTranscript(arg, eventName)) { forwarded = true; break; }
    }
    if (!forwarded) {
      const first = args?.[0];
      const unwrapped = unwrap(first);
      send('diagnostic', {
        stage:'event', eventName,
        argCount:args.length,
        arg0Type:typeof first,
        arg0Preview:typeof first === 'string' ? first.slice(0,300) : null,
        arg0Keys:(first && typeof first === 'object') ? Object.keys(first) : [],
        nestedType:typeof unwrapped,
        nestedKeys:(unwrapped && typeof unwrapped === 'object') ? Object.keys(unwrapped) : []
      });
    }
  });

  const heartbeat = setInterval(() => send('ping', {
    at: Date.now(), connected: fireflies.connected, transcriptEvents,
    transport: fireflies.io.engine?.transport?.name || null
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
