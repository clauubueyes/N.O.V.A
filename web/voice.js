"use strict";
/* ============================================================
   N.O.V.A. — voice.js · runtime de voz del navegador

   Arquitectura (módulos, dominio puro en web/voice-core.js):

     VoiceCore (web/voice-core.js)        → FSM, VAD params/reducer,
                                            flujo de transcripción,
                                            teclado, niveles. Sin DOM.
     AudioVisualizer (createWaveform)     → barras de audio reales
                                            (AnalyserNode + FFT) con
                                            suavizado y cleanup.
     MessageRecorder (startRecord)        → getUserMedia + MediaRecorder.
     messageMode (VoiceMessageMode)       → flujo del botón micrófono:
                                            grabar → detener → transcribir
                                            → mostrar → editar → Enter.
                                            NUNCA auto-envía.
     callMode (VoiceCallMode)             → Voice Call Mode completo:
                                            máquina de estados, VAD,
                                            bucle escuchar→procesar→hablar,
                                            barge-in, cleanup total.
     queue / NovaVoice                    → cola TTS compartida y fachada
                                            que usa web/app.js.

   Decisiones importantes:
   - El TTS/STT y los endpoints /v1/voice/* no cambian: se reutilizan
     tal cual. Esta es solo una mejora de UX/interacción/lógica cliente.
   - El "detener" del mensaje de voz NO envía el mensaje: el texto queda
     en el composer y Enter lo envía por el pipeline normal (send()).
   - Space solo detiene la grabación en el contexto apropiado (ver
     VoiceCore.shouldInterceptSpace) para no romper la escritura.
   - Un solo AudioContext de análisis, creado/reanudado dentro de un
     gesto de usuario, compartido por los visualizadores.
   - Cada modo de llamada incrementa un token de generación; cualquier
     callback asíncrono posterior con token distinto se ignora (esto
     garantiza que cerrar la llamada / desmontar lo limpie todo).
   - Barge-in: N.O.V.A. corta el TTS al detectar voz y pasa a escuchar;
     la captura arranca en el onset de la voz (sin pre-roll real).
   ============================================================ */
const VoiceCore = window.VoiceCore;

const NovaVoice = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  /* ------------------------------------------------------------------
     Estado de voz (status de /v1/voice/status) y disponibilidad.
     ------------------------------------------------------------------ */
  let status = null;
  const canSpeak = () => !!(status && status.enabled && status.tts && status.tts.available);
  const canStt = () => !!(status && status.enabled && status.stt && status.stt.available);
  const callAvailable = () => canSpeak() && canStt();

  /* ------------------------------------------------------------------
     AudioContext de análisis compartido (un solo contexto, reutilizado).
     Se crea y reanuda dentro del gesto del usuario (clic en mic/llamada).
     ------------------------------------------------------------------ */
  const analyserState = (() => {
    let ctx = null, node = null;
    function ensure() {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!ctx) {
        try { ctx = new Ctx(); node = ctx.createAnalyser(); node.fftSize = 1024; node.smoothingTimeConstant = 0.8; }
        catch (e) { ctx = null; node = null; return null; }
      }
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      return node;
    }
    function close() { if (ctx) { try { ctx.close(); } catch (e) {} } ctx = null; node = null; }
    return { ensure, close, get node() { return node; } };
  })();

  /* ------------------------------------------------------------------
     Cola de reproducción TTS (la misma infraestructura que lee las
     respuestas en el chat). Se conserva intacta.
     ------------------------------------------------------------------ */
  const queue = (() => {
    const audio = new Audio();
    let q = [], current = null, playing = false;
    const api = {
      play(key, url) { q.push({key, url}); if (!playing) next(); },
      stop() { q = []; cleanup(current && current.url); current = null; playing = false; api.onIdle = null; audio.pause(); audio.removeAttribute("src"); refresh(); },
      isSpeaking(key) { return playing && current !== null && current.key === key; },
      unlock() {
        if (api._unlocked || api._unlocking || playing) return;
        api._unlocking = true;
        const silent = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
        audio.muted = true;
        audio.src = silent;
        audio.play().then(() => { audio.pause(); audio.currentTime = 0; }).catch(() => {}).then(() => { audio.removeAttribute("src"); audio.muted = false; api._unlocked = true; api._unlocking = false; });
      },
      onIdle: null,
      dispose() { audio.onended = null; audio.onerror = null; api.stop(); }
    };
    function refresh() {
      for (const btn of document.querySelectorAll(".speak")) {
        btn.setAttribute("data-voice", playing && current && btn._voiceKey === current.key ? "playing" : "");
      }
    }
    function cleanup(url) { if (url && url.startsWith("blob:")) URL.revokeObjectURL(url); }
    function next() {
      if (!q.length) {
        playing = false; current = null; refresh();
        const idle = api.onIdle;
        if (idle) { api.onIdle = null; idle(); }
        return;
      }
      current = q.shift(); playing = true; refresh();
      audio.src = current.url;
      audio.play().catch(() => { cleanup(current.url); current = null; next(); });
    }
    audio.onended = () => { cleanup(current && current.url); next(); };
    audio.onerror = () => { cleanup(current && current.url); next(); };
    return api;
  })();

  async function speakText(text) {
    const response = await rawFetch("/v1/voice/speak", {
      method: "POST",
      headers: {...headers(), "Content-Type": "application/json"},
      body: JSON.stringify({text})
    });
    const blob = await response.blob();
    queue.unlock();
    queue.play(text, URL.createObjectURL(blob));
  }
  function toggleSpeak(text) {
    if (queue.isSpeaking(text)) { queue.stop(); return; }
    return speakText(text);
  }

  /* ------------------------------------------------------------------
     Helpers de audio (movidos desde app.js sin cambios de lógica).
     ------------------------------------------------------------------ */
  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    str(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    str(36, "data"); view.setUint32(40, samples.length * 2, true);
    let off = 44;
    for (let i = 0; i < samples.length; i++, off += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], {type: "audio/wav"});
  }
  function downsampleTo16k(samples, fromRate) {
    if (fromRate === 16000) return samples;
    const ratio = fromRate / 16000;
    const out = new Float32Array(Math.floor(samples.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const src = i * ratio, i0 = Math.floor(src), frac = src - i0;
      out[i] = samples[i0] + (samples[Math.min(i0 + 1, samples.length - 1)] - samples[i0]) * frac;
    }
    return out;
  }
  async function transcribeBlob(blob) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    try {
      const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
      let mono = buffer.numberOfChannels === 1 ? buffer.getChannelData(0) : null;
      if (!mono) {
        const frames = buffer.length, out = new Float32Array(frames);
        for (let c = 0; c < buffer.numberOfChannels; c++) {
          const ch = buffer.getChannelData(c);
          for (let i = 0; i < frames; i++) out[i] += ch[i];
        }
        for (let i = 0; i < frames; i++) out[i] /= buffer.numberOfChannels;
        mono = out;
      }
      if (mono.length < 3200) return "";
      const pcm = downsampleTo16k(mono, buffer.sampleRate);
      const response = await rawFetch("/v1/voice/transcribe", {
        method: "POST",
        headers: {...headers(), "Content-Type": "audio/wav"},
        body: encodeWav(pcm, 16000)
      });
      const data = await response.json();
      return (data.text || "").trim();
    } finally {
      ctx.close();
    }
  }
  function splitSentences(text) {
    const raw = (text.match(/[^.!?…]+[.!?…]+["”'»)]?\s*|[^.!?…]+$/g) || [text]).map(s => s.trim()).filter(Boolean);
    const out = [];
    for (const part of raw) {
      if (!out.length) { out.push(part); continue; }
      if (out[out.length - 1].length + part.length <= 170) out[out.length - 1] += " " + part;
      else out.push(part);
    }
    return out.map(s => s.slice(0, 240)).filter(Boolean);
  }

  /* ------------------------------------------------------------------
     AudioVisualizer — barras reales (MSEN FFT del micrófono) o un
     patrón suave para cuando habla N.O.V.A. Con suavizado, rAF y
     cleanup. Nada de animaciones CSS al azar: en modo mic lee el
     AnalyserNode de verdad.
     ------------------------------------------------------------------ */
  function createWaveform(hostId, opts) {
    const host = $(hostId);
    const count = (opts && opts.count) || 28;
    const bars = [];
    const levels = new Array(count).fill(0.06);
    let raf = null, mode = "off", source = null;
    let t0 = null;
    for (let i = 0; i < count; i++) {
      const bar = el("span", null, "bar");
      bar.style.transform = "scaleY(0.06)";
      host.append(bar);
      bars.push(bar);
    }
    function cancelRaf() { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }
    function disconnectSource() { if (source) { try { source.disconnect(); } catch (e) {} source = null; } }
    function tick(ts) {
      raf = requestAnimationFrame(tick);
      const node = analyserState.node;
      if (mode === "mic" && node) {
        const freq = new Uint8Array(node.frequencyBinCount);
        node.getByteFrequencyData(freq);
        const len = freq.length;
        const maxBin = Math.max(2, Math.min(110, Math.floor(len * 0.22)));
        for (let i = 0; i < count; i++) {
          const k = i / count;
          const low = 1 + Math.floor(Math.pow(k, 1.6) * (maxBin - 1));
          const high = 1 + Math.floor(Math.pow((i + 1) / count, 1.6) * (maxBin - 1));
          let sum = 0, n = 0;
          for (let b = low; b <= high && b < len; b++) { sum += freq[b]; n++; }
          const mag01 = n ? (sum / n) / 255 : 0;
          const target = VoiceCore.bandLevel(mag01);
          levels[i] = VoiceCore.smoothLevel(levels[i], target, 0.38);
        }
      } else if (mode === "talk") {
        if (t0 === null) t0 = ts;
        const t = (ts - t0) / 1000;
        for (let i = 0; i < count; i++) {
          const wave = 0.18 + 0.14 * Math.abs(Math.sin(t * 2.1 + i * 0.62));
          const breath = 0.05 + 0.05 * Math.sin(t * 0.9 + i * 0.35);
          const target = Math.min(1, wave + breath);
          levels[i] = VoiceCore.smoothLevel(levels[i], target, 0.25);
        }
      } else {
        for (let i = 0; i < count; i++) levels[i] = VoiceCore.smoothLevel(levels[i], 0.05, 0.2);
      }
      for (let i = 0; i < count; i++) bars[i].style.transform = "scaleY(" + levels[i].toFixed(3) + ")";
    }
    return {
      startFromStream(stream) {
        cancelRaf(); mode = "mic"; t0 = null;
        disconnectSource();
        const node = analyserState.ensure();
        if (node) {
          try { source = node.context.createMediaStreamSource(stream); source.connect(node); } catch (e) { source = null; }
        }
        raf = requestAnimationFrame(tick);
      },
      startFree() { cancelRaf(); mode = "mic"; t0 = null; raf = requestAnimationFrame(tick); },
      startTalk() { cancelRaf(); mode = "talk"; t0 = null; raf = requestAnimationFrame(tick); },
      stop() { cancelRaf(); mode = "off"; t0 = null; disconnectSource(); for (let i = 0; i < count; i++) bars[i].style.transform = "scaleY(0.06)"; },
      dispose() { this.stop(); for (const b of bars) b.remove(); bars.length = 0; }
    };
  }

  /* ------------------------------------------------------------------
     MessageRecorder — envoltorio de getUserMedia + MediaRecorder.
     startRecord(): Promise<{recorder, stream, stopped(datos), discard()}>
     - stopped se resuelve con el Blob cuando el recorder se detiene.
     - discard() corta los tracks sin consumir el resultado.
     ------------------------------------------------------------------ */
  function startRecord() {
    return navigator.mediaDevices.getUserMedia({audio: true}).then(stream => {
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
      const stopped = new Promise((resolve, reject) => {
        recorder.onstop = () => resolve({ blob: new Blob(chunks, {type: recorder.mimeType || "audio/webm"}), stream });
        recorder.onerror = () => reject(new Error("El grabador de audio no pudo capturar tu voz."));
      });
      recorder.start();
      return {
        recorder, stream, stopped,
        discard() { try { if (recorder.state === "recording") recorder.stop(); } catch (e) {} stream.getTracks().forEach(t => t.stop()); }
      };
    });
  }

  /* ------------------------------------------------------------------
     VoiceMessageMode — botón de micrófono (MODO 1).
     Flujo: grabar → detener → transcribir → mostrar → editar → Enter.
     El envío lo hace SIEMPRE send() (pipeline normal del chat).
     ------------------------------------------------------------------ */
  const messageMode = (() => {
    const flow = VoiceCore.createTranscriptionFlow();
    let rec = null, wave = null;

    function syncUi() {
      const mic = $("microphone");
      const recorder = flow.isRecording();
      const transcribing = flow.isTranscribing();
      mic.classList.toggle("recording", recorder);
      mic.classList.toggle("processing", transcribing);
      mic.disabled = transcribing || busy;
      mic.setAttribute("aria-label", recorder ? "Detener grabación y transcribir" : transcribing ? "Transcribiendo…" : "Hablar con N.O.V.A.");
      mic.setAttribute("title", recorder ? "Detener grabación y transcribir" : transcribing ? "Transcribiendo…" : "Hablar con N.O.V.A.");
      $("compose").classList.toggle("recording", recorder);
      const cancel = $("voice-cancel");
      cancel.hidden = !recorder;
    }
    function showWave(visible) {
      const box = $("voice-wave");
      if (visible) { box.hidden = false; $("compose").classList.add("recording"); }
      else { $("compose").classList.remove("recording"); box.hidden = true; }
    }
    function isRecording() { return flow.isRecording(); }
    function state() { return flow.state; }

    async function start() {
      if (busy || flow.state !== VoiceCore.FLOW_STATES.IDLE) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
        toast("Tu navegador no soporta la grabación de voz.");
        return;
      }
      flow.start();
      queue.unlock();
      analyserState.ensure(); // crear/reanudar dentro del gesto
      syncUi();
      try {
        rec = await startRecord();
        if (flow.state !== VoiceCore.FLOW_STATES.RECORDING) { rec.discard(); rec = null; return; }
        try { wave = createWaveform("voice-wave-bars", {count: 28}); wave.startFromStream(rec.stream); }
        catch (e) { wave = null; }
        showWave(true);
      } catch (e) {
        flow.clear(); rec = null; syncUi(); showWave(false);
        const denied = e && (e.name === "NotAllowedError" || e.name === "PermissionDeniedError");
        toast(denied ? "Permiso de micrófono denegado. Concede acceso para poder dictar." : "No se ha podido acceder al micrófono.");
      }
    }

    async function stop() {
      if (flow.state !== VoiceCore.FLOW_STATES.RECORDING || !rec) return;
      flow.stop(); syncUi();
      const current = rec;
      showWave(false);
      if (wave) { wave.dispose(); wave = null; }
      let blob;
      try {
        const result = await VoiceCore.runWithTimeout(current.stopped, 30000, "El micrófono tardó demasiado en detenerse.");
        blob = result.blob;
        current.stream.getTracks().forEach(t => t.stop());
      } catch (e) {
        current.discard();
        if (flow.state === VoiceCore.FLOW_STATES.TRANSCRIBING) { flow.clear(); syncUi(); toast("No se pudo detener la grabación.", e.message); }
        rec = null;
        return;
      }
      rec = null;
      if (flow.state !== VoiceCore.FLOW_STATES.TRANSCRIBING) return; // cancelado durante la transcripción
      try {
        const text = await VoiceCore.runWithTimeout(transcribeBlob(blob), 30000, "La transcripción superó el tiempo de espera.");
        if (flow.state !== VoiceCore.FLOW_STATES.TRANSCRIBING) return;
        if (!text.trim()) { flow.empty(); syncUi(); toast("No he escuchado palabras claras. Prueba de nuevo."); return; }
        flow.complete(text);
      } catch (e) {
        if (flow.state === VoiceCore.FLOW_STATES.TRANSCRIBING) { flow.clear(); syncUi(); toast("No he podido transcribir el audio.", e.message); }
        return;
      }
      syncUi();
      // Mostrar la transcripción en el composer (editable), SIN enviar.
      $("message").value = VoiceCore.mergeTranscription($("message").value, flow.text);
      syncSend();
      $("message").focus();
    }

    /* Cancela/descarta grabación o transcripción pendiente. */
    function cancel() {
      if (flow.state === VoiceCore.FLOW_STATES.IDLE) return;
      if (rec) { rec.discard(); rec = null; }
      flow.clear();
      if (wave) { wave.dispose(); wave = null; }
      syncUi(); showWave(false);
    }

    /* Enter con transcripción pendiente: consume el flujo y deja el
       envío en manos del pipeline normal (send()). */
    function consume() {
      if (flow.isTranscribed()) { flow.send(); flow.done(); syncUi(); }
    }

    function dispose() { cancel(); if (wave) { wave.dispose(); wave = null; } flow.dispose(); }

    return { start, stop, cancel, consume, isRecording, state, syncUi, dispose };
  })();

  /* ------------------------------------------------------------------
     VoiceCallMode — MODO 2 (conversación continua con VAD).
     Estados: idle → connecting → listening ⇄ processing → speaking →
     … → ending → idle, con error recuperable. Fuente de verdad:
     VoiceCore.createVoiceMachine.
     ------------------------------------------------------------------ */
  const callMode = (() => {
    const VOICES = VoiceCore.VOICE_STATES;
    const CALL_VAD = Object.assign({}, VoiceCore.VAD_PARAMS, {
      minSilenceMs: 850,
      minSpeechGateMs: 150,
    });
    const els = { dialog: $("call"), orb: $("call-orb"), status: $("call-status"), transcript: $("call-transcript"), mic: $("call-mic"), end: $("call-end"), wave: "call-wave" };
    const machine = VoiceCore.createVoiceMachine(refreshUi);
    let sessionId = null;
    let token = 0;
    let micStream = null, micSource = null;
    let wave = null;
    let recorder = null, recorderChunks = [];
    let vadSession = null, vadStart = 0, vadRaf = null, bargeRaf = null;
    const timeData = new Uint8Array(2048);

    function active() { return machine.state !== VOICES.IDLE; }

    function refreshUi(prev, next, cur) {
      const labels = {
        [VOICES.IDLE]: "Llamada con N.O.V.A.",
        [VOICES.CONNECTING]: "Llamando…",
        [VOICES.LISTENING]: "Escuchando…",
        [VOICES.PROCESSING]: "Procesando…",
        [VOICES.SPEAKING]: "Hablando…",
        [VOICES.ENDING]: "Finalizando…",
        [VOICES.ERROR]: "Algo no ha ido bien",
      };
      els.status.textContent = labels[cur] || "Llamada con N.O.V.A.";
      if (cur === VOICES.IDLE) { els.dialog.removeAttribute("data-phase"); els.orb.removeAttribute("data-phase"); }
      else { els.dialog.setAttribute("data-phase", cur); els.orb.setAttribute("data-phase", cur); }
      if (cur === VOICES.LISTENING) els.mic.setAttribute("data-active", "1");
      else els.mic.removeAttribute("data-active");
      if (wave) {
        if (cur === VOICES.SPEAKING) { stopRafs(); wave.startTalk(); }
        else if (cur === VOICES.LISTENING) { wave.startFree(); }
        else wave.stop();
      }
    }

    function addLine(role, text) {
      const line = el("div", null, "call-line " + role);
      line.append(el("span", role === "user" ? "Tú" : "N.O.V.A.", "call-line-label"));
      line.append(el("p", text, "call-line-text"));
      els.transcript.append(line);
      els.transcript.scrollTop = els.transcript.scrollHeight;
    }

    function stopRafs() {
      if (vadRaf !== null) { cancelAnimationFrame(vadRaf); vadRaf = null; }
      if (bargeRaf !== null) { cancelAnimationFrame(bargeRaf); bargeRaf = null; }
    }
    function clearRecorder() {
      if (recorder) {
        try { if (recorder.state !== "inactive") recorder.stop(); } catch (e) {}
        recorder = null; recorderChunks = [];
      }
    }
    function readRms() {
      const node = analyserState.node;
      if (!node) return 0;
      const arr = timeData.subarray(0, node.fftSize);
      node.getByteTimeDomainData(arr);
      let sum = 0;
      for (let i = 0; i < arr.length; i++) { const v = (arr[i] - 128) / 128; sum += v * v; }
      return Math.sqrt(sum / arr.length);
    }

    async function ensureSession() {
      if (sessionId) return sessionId;
      if (current) { sessionId = current; return sessionId; }
      const created = await api("POST", "/v1/sessions", {agent: $("agent").value || null});
      sessionId = created.session_id;
      return sessionId;
    }

    function startRecorderOn(m) {
      if (m !== token || !micStream) return;
      clearRecorder();
      recorder = new MediaRecorder(micStream);
      recorderChunks = [];
      recorder.ondataavailable = e => { if (e.data && e.data.size) recorderChunks.push(e.data); };
      recorder.onstop = () => onUtteranceBlob(new Blob(recorderChunks, {type: recorder.mimeType || "audio/webm"}), m);
      recorder.onerror = () => failCall("Error capturando tu voz.", m);
      recorder.start();
    }

    /* Equivale al bucle normal de escucha. */
    function beginListening(m) {
      if (m !== token || machine.state !== VOICES.LISTENING) return;
      stopRafs();
      startRecorderOn(m);
      vadSession = VoiceCore.createVadSession(CALL_VAD);
      vadStart = performance.now();
      const frame = () => {
        if (m !== token) return;
        const ev = vadSession(readRms(), performance.now() - vadStart);
        if (ev.endedUtterance) { stopRafs(); if (recorder && recorder.state === "recording") recorder.stop(); return; }
        vadRaf = requestAnimationFrame(frame);
      };
      vadRaf = requestAnimationFrame(frame);
    }

    /* Barge-in: mientras N.O.V.A. habla se vigila el micrófono; al
       detectar voz se corta el TTS y se empieza a escuchar/capturar. */
    function beginBargeWatch(m) {
      if (m !== token || machine.state !== VOICES.SPEAKING) return;
      stopRafs();
      vadSession = VoiceCore.createVadSession(Object.assign({}, CALL_VAD, { minSpeechMs: 20, maxUtteranceMs: 60000 }));
      vadStart = performance.now();
      const frame = () => {
        if (m !== token) return;
        const ev = vadSession(readRms(), performance.now() - vadStart);
        if (ev.speechOnset) {
          stopRafs();
          queue.stop();
          if (machine.to(VOICES.LISTENING)) beginListening(m);
          return;
        }
        bargeRaf = requestAnimationFrame(frame);
      };
      bargeRaf = requestAnimationFrame(frame);
    }

    function onUtteranceBlob(blob, m) {
      if (m !== token) return;
      if (!machine.to(VOICES.PROCESSING)) { clearRecorder(); return; }
      if (wave) wave.stop();
      handleTurn(blob, m);
    }

    async function handleTurn(blob, m) {
      try {
        const text = await VoiceCore.runWithTimeout(transcribeBlob(blob), 30000, "La transcripción superó el tiempo de espera.");
        if (m !== token) return;
        if (!text.trim()) {
          if (machine.state === VOICES.PROCESSING && machine.to(VOICES.LISTENING)) beginListening(m);
          return;
        }
        addLine("user", text);
        const data = await api("POST", "/v1/sessions/" + sessionId + "/chat", {message: text, model: $("model").value || null, task: "VOICE", latency: "low"});
        if (m !== token) return;
        const reply = (data.reply || "").trim();
        addLine("assistant", reply);
        await speakReply(reply, m);
      } catch (e) {
        if (m === token) failCall("He tenido un problema procesando tu mensaje.", m);
      }
    }

    async function speakReply(reply, m) {
      if (m !== token) return;
      if (!reply) { if (machine.to(VOICES.LISTENING)) beginListening(m); return; }
      if (!machine.to(VOICES.SPEAKING)) return;
      queue.onIdle = () => {
        if (m === token && machine.to(VOICES.LISTENING)) beginListening(m);
      };
      for (const chunk of splitSentences(reply)) {
        if (m !== token) { queue.stop(); return; }
        try {
          const response = await rawFetch("/v1/voice/speak", {
            method: "POST",
            headers: {...headers(), "Content-Type": "application/json"},
            body: JSON.stringify({text: chunk})
          });
          if (m !== token) { queue.stop(); return; }
          queue.play(chunk, URL.createObjectURL(await response.blob()));
        } catch (e) {
          if (m === token) failCall("No he podido leer la respuesta en voz.", m);
          return;
        }
      }
      beginBargeWatch(m);
    }

    function failCall(message, m) {
      if (m !== undefined && m !== token) return;
      addLine("system", message);
      if (machine.state !== VOICES.ERROR && !machine.to(VOICES.ERROR)) return;
      setTimeout(() => { if (m === token) end(); }, 1200);
    }

    const onDeviceChange = () => {
      if (micStream && (!navigator.mediaDevices.enumerateDevices || true)) {
        if (micStream.getAudioTracks().every(t => !t.enabled || t.readyState === "ended")) {
          failCall("El micrófono ha dejado de estar disponible.", token);
        }
      }
    };

    async function start() {
      if (machine.state !== VOICES.IDLE || busy) return;
      if (!callAvailable()) { toast("La llamada de voz no está disponible: activa la voz y asegúrate de tener STT y TTS (ver /status)."); return; }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined" || !(window.AudioContext || window.webkitAudioContext)) {
        toast("Tu navegador no soporta la llamada de voz.");
        return;
      }
      queue.unlock();
      analyserState.ensure(); // crear/reanudar dentro del gesto del clic
      if (!machine.to(VOICES.CONNECTING)) return;
      const m = ++token;
      els.transcript.replaceChildren();
      els.dialog.showModal();
      try {
        sessionId = await ensureSession();
        if (m !== token) return;
        micStream = await navigator.mediaDevices.getUserMedia({audio: true});
        if (m !== token) { micStream.getTracks().forEach(t => t.stop()); micStream = null; return; }
        micStream.getAudioTracks().forEach(t => { if (t.onended === null) t.onended = () => failCall("El micrófono se ha desconectado.", token); });
        try { navigator.mediaDevices.addEventListener("devicechange", onDeviceChange); } catch (e) {}
        const node = analyserState.node;
        if (node) { micSource = node.context.createMediaStreamSource(micStream); micSource.connect(node); }
        wave = createWaveform(els.wave, {count: 26});
        if (!machine.to(VOICES.LISTENING)) return;
        beginListening(m);
      } catch (e) {
        const denied = e && (e.name === "NotAllowedError" || e.name === "PermissionDeniedError");
        failCall(denied ? "Permiso de micrófono denegado. Concede acceso para usar la llamada." : "No se ha podido acceder al micrófono.", m);
      }
    }

    function end() {
      if (machine.state === VOICES.IDLE) return;
      token += 1;                            // invalida callbacks asíncronos pendientes
      stopRafs();
      queue.onIdle = null;
      queue.stop();
      clearRecorder();
      if (wave) { wave.dispose(); wave = null; }
      if (micSource) { try { micSource.disconnect(); } catch (e) {} micSource = null; }
      if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
      try { navigator.mediaDevices.removeEventListener("devicechange", onDeviceChange); } catch (e) {}
      machine.to(VOICES.ENDING);
      els.dialog.removeAttribute("data-phase");
      els.orb.removeAttribute("data-phase");
      els.mic.removeAttribute("data-active");
      if (els.dialog.open) els.dialog.close();
      machine.to(VOICES.IDLE);
      if (sessionId) {
        const finished = sessionId; sessionId = null;
        if (current === finished) openConversation({session_id: finished}).catch(() => {});
        else refreshConversations();
      }
    }

    els.mic.onclick = () => {
      if (machine.state === VOICES.LISTENING) {
        stopRafs();
        if (recorder && recorder.state === "recording") recorder.stop();
      } else if (machine.state === VOICES.SPEAKING) {
        token += 1;
        clearRecorder();
        queue.stop();
        machine.to(VOICES.LISTENING);
        beginListening(token);
      } else if (machine.state === VOICES.IDLE) {
        start().catch(fail);
      }
    };
    els.end.addEventListener("click", end);
    els.dialog.addEventListener("cancel", e => { e.preventDefault(); end(); });
    els.dialog.addEventListener("close", () => { if (machine.state !== VOICES.IDLE) end(); });

    return { active, start, end, get state() { return machine.state; } };
  })();

  /* ------------------------------------------------------------------
     Teclado global (registrado una sola vez).
     - Space: detiene la grabación solo cuando el flujo de mensaje está
       grabando y el foco está en el contexto apropiado
       (VoiceCore.shouldInterceptSpace).
     - Escape: cancela la grabación de mensaje o la llamada en curso.
     ------------------------------------------------------------------ */
  document.addEventListener("keydown", e => {
    if (e.repeat) return;
    const code = e.code || "";
    if (code === "Space" && VoiceCore.shouldInterceptSpace(messageMode.state(), e.target)) {
      e.preventDefault();
      messageMode.stop().catch(fail);
      return;
    }
    if (e.key === "Escape") {
      if (callMode.active() && $("call").open) { e.preventDefault(); callMode.end(); return; }
      if (messageMode.isRecording()) { e.preventDefault(); messageMode.cancel(); return; }
    }
  });

  /* ------------------------------------------------------------------
     Fachada N.O.V.A. usada por web/app.js.
     ------------------------------------------------------------------ */
  const nova = {
    setStatus(value) { status = value; },
    updateVisibility() {
      const mic = $("microphone"), callBtn = $("call-btn");
      const sttOk = canStt();
      mic.hidden = !sttOk;
      mic.disabled = messageMode.state() === VoiceCore.FLOW_STATES.TRANSCRIBING || busy;
      mic.setAttribute("title", sttOk ? "Hablar con N.O.V.A." : (status && status.enabled ? ((status.stt && status.stt.reason) || "Voz no disponible") : "La voz está desactivada en la configuración"));
      callBtn.hidden = !callAvailable();
      callBtn.disabled = busy || callMode.active();
      callBtn.setAttribute("title", callAvailable() ? "Llamada de voz con N.O.V.A." : "La voz completa (micrófono y lectura) no está disponible en esta configuración");
      for (const btn of document.querySelectorAll(".speak")) btn.hidden = !canSpeak();
    },
    canSpeak,
    callAvailable,
    isRecording: () => messageMode.isRecording(),
    /* Micrófono: alterna grabar ↔ detener (detener NO envía). */
    toggleMic() { return messageMode.isRecording() ? messageMode.stop() : messageMode.start(); },
    cancelRecording: () => messageMode.cancel(),
    /* Antes de enviar un mensaje: consume la transcripción pendiente y
       descarta cualquier grabación/transcripción en curso. */
    beforeSend() {
      messageMode.consume();
      messageMode.cancel();
    },
    speakText,
    toggleSpeak,
    startCall: () => callMode.start(),
    endCall: () => callMode.end(),
    isCalling: () => callMode.active(),
    callState: () => callMode.state,
    dispose() { messageMode.dispose(); queue.dispose(); analyserState.close(); },
  };

  /* Botones (el resto de listeners los registra app.js). */
  $("microphone").onclick = e => { e.preventDefault(); nova.toggleMic().catch(fail); };
  $("call-btn").onclick = e => { e.preventDefault(); nova.startCall().catch(fail); };
  $("voice-cancel").onclick = e => { e.preventDefault(); nova.cancelRecording(); };

  return nova;
})();

window.NovaVoice = NovaVoice;
