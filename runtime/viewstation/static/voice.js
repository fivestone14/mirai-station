/* mirai-voice :: voice.js — the browser half of the SNDK talk button.
 *
 * All voice logic lives HERE; index.html only renders a widget and calls
 * window.MiraiVoice.toggle(). State flows back through a 'mirai-voice'
 * CustomEvent on document — the SNDK side panel repaints from
 * MiraiVoice.state whenever it fires (paintSide re-renders via innerHTML,
 * so nothing here may hold a DOM reference across paints).
 *
 * Transport: ws://<host>:8788 (MIRAI_VOICE_PORT). Mic frames go up as
 * PCM16 mono 16 kHz binary; TTS comes back as PCM16 binary bracketed by
 * audio_start/audio_end JSON. Turn-taking and barge-in are SERVER-side
 * (Silero VAD) — this file just ships audio both ways.
 *
 * Mic capture needs a secure context: localhost qualifies, a bare LAN IP
 * does not — the widget shows a "needs HTTPS" state on the iPad until the
 * cert phase ships. ScriptProcessorNode is deliberate v1 tech: deprecated
 * but universal; the AudioWorklet upgrade is mechanical when wanted.
 */
(function () {
  'use strict';

  const VERSION = 5;      // keep in step with index.html's /voice.js?v= tag
  const PORT = 8788;
  console.log('[mirai-voice] v' + VERSION + ' loaded');
  const V = {
    state: 'off',          // off|unsupported|connecting|listening|thinking|speaking
    lastYou: '', lastMirai: '', lastTool: '', err: '',
    _ws: null, _ctx: null, _stream: null, _proc: null, _src: null,
    _playQ: [], _playing: null, _rate: 24000, _pcm: [], _inAudio: false,
    _amp: 0, _raf: 0,
  };

  function emit() { document.dispatchEvent(new CustomEvent('mirai-voice')); }
  function setState(s) { if (V.state !== s) { V.state = s; emit(); } }

  // ---- audio out --------------------------------------------------------
  function playNext() {
    if (V._playing || !V._playQ.length || !V._ctx || V.state === 'off') return;
    const { pcm, rate } = V._playQ.shift();
    const f32 = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;
    const buf = V._ctx.createBuffer(1, f32.length, rate);
    buf.getChannelData(0).set(f32);
    const src = V._ctx.createBufferSource();
    src.buffer = buf; src.connect(V._ctx.destination);
    V._playing = src;
    setState('speaking');
    src.onended = function () {
      V._playing = null;
      if (V._playQ.length) playNext();
      else if (V.state === 'speaking') setState(micOn() ? 'listening' : 'off');
    };
    src.start();
  }
  function stopPlayback() {
    V._playQ.length = 0;
    if (V._playing) { try { V._playing.stop(); } catch (e) {} V._playing = null; }
  }
  function chime(freq) {
    if (!V._ctx) return;
    const o = V._ctx.createOscillator(), g = V._ctx.createGain();
    o.frequency.value = freq || 660; g.gain.value = 0.06;
    o.connect(g); g.connect(V._ctx.destination);
    o.start(); o.stop(V._ctx.currentTime + 0.12);
  }

  // ---- socket -----------------------------------------------------------
  function connect() {
    // Two transports for one sidecar: plain ws://host:8788 on the LAN/localhost,
    // wss://host:8443 when the page rides the Tailscale HTTPS proxy (443→8787,
    // 8443→8788 — tailscale serve terminates TLS and forwards to localhost).
    const url = location.protocol === 'https:'
      ? 'wss://' + location.hostname + ':8443'
      : 'ws://' + location.hostname + ':' + PORT;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    ws.onmessage = function (ev) {
      if (ev.data instanceof ArrayBuffer) {
        if (V._inAudio && V.state !== 'off') V._pcm.push(new Int16Array(ev.data));
        return;
      }
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      // after the button stopped everything, late frames from the cancelled
      // turn still drain out of the socket — swallow them, never play them
      if (V.state === 'off' &&
          ['sentence', 'tool', 'audio_start', 'audio_end', 'partial',
           'final', 'turn_end'].indexOf(m.type) !== -1) {
        V._inAudio = false; V._pcm = [];
        return;
      }
      if (m.type === 'final') {
        V.lastYou = m.text; V.lastMirai = ''; V.lastTool = '';
        setState('thinking'); emit();
      } else if (m.type === 'sentence') {
        V.lastMirai = (V.lastMirai ? V.lastMirai + ' ' : '') + m.text; emit();
      } else if (m.type === 'tool') {
        V.lastTool = m.name; emit();
      } else if (m.type === 'audio_start') {
        V._inAudio = true; V._pcm = []; V._rate = m.rate || 24000;
      } else if (m.type === 'audio_end') {
        V._inAudio = false;
        if (m.cancelled) { stopPlayback(); return; }
        let n = 0; V._pcm.forEach(function (c) { n += c.length; });
        const all = new Int16Array(n); let off = 0;
        V._pcm.forEach(function (c) { all.set(c, off); off += c.length; });
        V._pcm = [];
        if (all.length) { V._playQ.push({ pcm: all, rate: V._rate }); playNext(); }
      } else if (m.type === 'turn_end') {
        V.lastTool = ''; if (V.state === 'thinking') setState('listening');
        emit();
      } else if (m.type === 'timeout') {
        chime(440); off();
      } else if (m.type === 'error') {
        V.err = m.text || 'error'; emit();
      }
    };
    ws.onclose = function () { if (micOn()) off(); };
    return ws;
  }

  // ---- mic --------------------------------------------------------------
  function micOn() { return !!V._stream; }

  // 60fps voice-level painter: the widget is rebuilt by paintSide's innerHTML
  // swaps, so we re-query per frame (getElementById is nanoseconds) and write
  // a CSS var + a .hot class — all motion lives in CSS off --amp
  function ampLoop() {
    cancelAnimationFrame(V._raf);
    (function tick() {
      const w = document.getElementById('snk-voice');
      if (w) {
        if (micOn()) {
          w.style.setProperty('--amp', V._amp.toFixed(3));
          w.classList.toggle('hot', V._amp > 0.07);
        } else {
          w.style.removeProperty('--amp');
          w.classList.remove('hot');
        }
      }
      if (micOn()) V._raf = requestAnimationFrame(tick);
    }());
  }

  function downsampleTo16k(f32, fromRate) {
    const ratio = fromRate / 16000, n = Math.floor(f32.length / ratio);
    const out = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const v = f32[Math.floor(i * ratio)];
      out[i] = Math.max(-1, Math.min(1, v)) * 32767;
    }
    return out;
  }

  async function on() {
    V.err = '';
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      // the one non-obvious cause: a page NOT on localhost/HTTPS has no
      // mediaDevices at all (mirai.local and bare LAN IPs are not secure
      // contexts). Say so on the widget instead of dying silently.
      V.err = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
        ? 'this browser blocks mic APIs here'
        : 'mic needs localhost or HTTPS — you are on ' + location.hostname;
      console.warn('[mirai-voice]', V.err);
      setState('unsupported'); return;
    }
    setState('connecting');
    try {
      // no-mic machines (a bare Mac mini has NO built-in microphone) make
      // Safari throw a misleading "Invalid constraint" — check for actual
      // input hardware first and say the true reason (08-03 diagnosis)
      try {
        const devs = await navigator.mediaDevices.enumerateDevices();
        if (!devs.some(function (d) { return d.kind === 'audioinput'; })) {
          V.err = 'no microphone on this machine — plug one in (USB mic, ' +
                  'AirPods, iPhone continuity) or talk from the iPad/Air ' +
                  'via the tailscale address';
          console.warn('[mirai-voice]', V.err);
          off(); emit(); return;
        }
      } catch (ee) { /* enumeration blocked — let getUserMedia speak */ }
      V._ctx = V._ctx || new (window.AudioContext || window.webkitAudioContext)();
      await V._ctx.resume();                       // the tap IS the unlock
      try {
        V._stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true } });
      } catch (ce) {
        // Safari rejects constraint dictionaries ("Invalid constraint" /
        // OverconstrainedError). Retry with the plainest possible ask for
        // ANYTHING except an explicit permission denial — EC/NS are browser
        // defaults anyway. If the plain ask fails too, the error message is
        // version-stamped so a stale cached voice.js can never masquerade
        // as this code.
        if (ce && String(ce.name || '').match(/NotAllowed|Security/i)) throw ce;
        console.warn('[mirai-voice] constrained ask failed (' +
                     (ce && ce.name) + '), retrying plain {audio:true}');
        try {
          V._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (pe) {
          pe._plainRetryFailed = true;
          throw pe;
        }
      }
      V._ws = (V._ws && V._ws.readyState <= 1) ? V._ws : connect();
      await new Promise(function (res, rej) {
        if (V._ws.readyState === 1) return res();
        V._ws.addEventListener('open', res, { once: true });
        V._ws.addEventListener('error', rej, { once: true });
      });
      V._ws.send(JSON.stringify({ type: 'start' }));
      console.log('[mirai-voice] session open — mic streaming');
      V._src = V._ctx.createMediaStreamSource(V._stream);
      V._proc = V._ctx.createScriptProcessor(4096, 1, 1);
      V._proc.onaudioprocess = function (e) {
        if (!micOn() || !V._ws || V._ws.readyState !== 1) return;
        const f32 = e.inputBuffer.getChannelData(0);
        // voice level for the widget: RMS with fast attack / slow decay, so
        // the ring jumps when you start talking and relaxes when you stop
        let sum = 0;
        for (let i = 0; i < f32.length; i += 4) sum += f32[i] * f32[i];
        const rms = Math.sqrt(sum / (f32.length / 4));
        const target = Math.min(1, rms * 7);
        V._amp = target > V._amp ? V._amp * 0.4 + target * 0.6
                                 : V._amp * 0.88;
        V._ws.send(downsampleTo16k(f32, V._ctx.sampleRate).buffer);
      };
      V._src.connect(V._proc); V._proc.connect(V._ctx.destination);
      ampLoop();
      chime(880);
      setState('listening');
    } catch (e) {
      V.err = String(e && e.message || e);
      if ((e && String(e.name || '').match(/NotAllowed|Security/i)) ||
          V.err.match(/denied|permission/i)) {
        V.err = 'mic permission denied — check the address-bar mic icon AND ' +
                'System Settings › Privacy › Microphone for this browser';
      } else if (e && e._plainRetryFailed) {
        V.err = 'v' + VERSION + ': mic failed even with a plain request — ' +
                (e.name || '') + ': ' + (e.message || e);
      } else if (V.err.match(/WebSocket|error/i) &&
                 (!V._ws || V._ws.readyState > 1)) {
        V.err = 'voice sidecar not reachable — is the service up?';
      } else {
        V.err = 'v' + VERSION + ': ' + V.err;
      }
      console.warn('[mirai-voice] start failed:', V.err, e);
      off(); setState(V.err.match(/denied|permission/i) ? 'unsupported' : 'off');
      emit();
    }
  }

  function off() {
    // the button is a full stop in EVERY state: cancel the in-flight model
    // turn server-side, halt local playback, drop anything still arriving
    if (V._ws && V._ws.readyState === 1) {
      try { V._ws.send(JSON.stringify({ type: 'cancel' })); } catch (e) {}
      try { V._ws.send(JSON.stringify({ type: 'stop' })); } catch (e) {}
    }
    stopPlayback();
    V._inAudio = false; V._pcm = [];
    if (V._proc) { try { V._proc.disconnect(); } catch (e) {} V._proc = null; }
    if (V._src) { try { V._src.disconnect(); } catch (e) {} V._src = null; }
    if (V._stream) {
      V._stream.getTracks().forEach(function (t) { t.stop(); });
      V._stream = null;
    }
    V._amp = 0; cancelAnimationFrame(V._raf);
    const w = document.getElementById('snk-voice');
    if (w) { w.style.removeProperty('--amp'); w.classList.remove('hot'); }
    setState('off');
  }

  window.MiraiVoice = {
    get state() { return V.state; },
    get err() { return V.err; },
    get lastYou() { return V.lastYou; },
    get lastMirai() { return V.lastMirai; },
    get lastTool() { return V.lastTool; },
    supported: function () {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    },
    toggle: function () { (micOn() || V.state === 'connecting') ? off() : on(); },
    debug: function () {   // paste MiraiVoice.debug() in the console when stuck
      return { version: VERSION, state: V.state, err: V.err,
               secure: window.isSecureContext,
               host: location.hostname,
               mediaDevices: !!navigator.mediaDevices,
               ws: V._ws ? V._ws.readyState : 'none',
               ctx: V._ctx ? V._ctx.state : 'none' };
    },
  };
}());
