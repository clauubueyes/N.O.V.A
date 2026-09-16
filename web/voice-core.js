"use strict";
/* ============================================================
   N.O.V.A. — voice-core
   Lógica pura del sistema de voz (sin DOM ni Web Audio):
   - VoiceActivityDetector (VAD) con parámetros centralizados
   - VoiceStateMachine (fuente de verdad del modo llamada)
   - flujo de transcripción de mensaje de voz (sin auto-envío)
   - scope del atajo Space / Enter (teclado)
   - suavizado de niveles para los visualizadores

   Este módulo se ejecuta en el navegador (window.VoiceCore) y
   en Node para pruebas (module.exports).
   ============================================================ */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiceCore = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /* ------------------------------------------------------------------
     Parámetros centralizados del VAD. Todos ajustables y fáciles de
     afinar desde un único sitio.
     ------------------------------------------------------------------ */
  const VAD_PARAMS = Object.freeze({
    frameMs: 25,           // duración de cada "tick" de análisis (ms)
    threshold: 0.022,      // RMS por debajo del cual se considera silencio (0..1)
    minSpeechMs: 350,      // voz mínima para aceptar un turno (evita ráfagas)
    minSilenceMs: 850,     // silencio trasero que cierra el turno (end of utterance)
    maxUtteranceMs: 20000, // tope de un turno, aunque siga hablando
    minSpeechGateMs: 120,  // voz mínima para reportar "speechOnset" (barge-in)
  });

  const VOICE_STATES = Object.freeze({
    IDLE: "idle",
    CONNECTING: "connecting",
    LISTENING: "listening",
    PROCESSING: "processing",
    SPEAKING: "speaking",
    ENDING: "ending",
    ERROR: "error",
  });

  /* Transiciones permitidas. La máquina rechaza estados imposibles
     (p. ej. speaking -> idle sin pasar por ending) y es la fuente de
     verdad para toda la UI del modo llamada. */
  const TRANSITIONS = Object.freeze({
    idle: ["connecting"],
    connecting: ["listening", "ending", "error"],
    listening: ["processing", "ending", "error"],
    processing: ["speaking", "listening", "ending", "error"],
    speaking: ["listening", "ending", "error"],
    ending: ["idle"],
    error: ["ending", "idle", "listening"],
  });

  function isState(s) {
    return Object.prototype.hasOwnProperty.call(VOICE_STATES, s.toUpperCase()) ||
      Object.values(VOICE_STATES).includes(s);
  }

  /* ------------------------------------------------------------------
     VoiceStateMachine — máquina de estados del modo llamada.
     onChange(prev, next, state). dispose() bloquea transiciones
     posteriores (salvo volver a idle) para limpiar en callbacks
     asíncronos que sobrevivan al desmontaje.
     ------------------------------------------------------------------ */
  function createVoiceMachine(onChange) {
    let state = VOICE_STATES.IDLE;
    let disposed = false;
    const stateMachine = {
      to(next) {
        if (disposed) return next === VOICE_STATES.IDLE;
        if (state === next) return true;
        const allowed = TRANSITIONS[state] || [];
        if (!allowed.includes(next)) return false;
        const prev = state;
        state = next;
        if (onChange) onChange(prev, next, state);
        return true;
      },
      is(next) { return state === next; },
      get state() { return state; },
      reset() { disposed = false; state = VOICE_STATES.IDLE; },
      dispose() { disposed = true; },
      get disposed() { return disposed; },
    };
    return stateMachine;
  }

  /* ------------------------------------------------------------------
     Flujo de transcripción de mensaje de voz.

     Grabación -> Detener -> Transcribir -> Mostrar -> Editar -> Enviar

     El paso "Enviar" lo ejecuta el pipeline normal del composer:
     aquí NO se auto-envía nada. Las transiciones invalidadas
     devuelven false (máquina de estados, evita estados imposibles
     como transcribing + enviado simultáneos).
     ------------------------------------------------------------------ */
  const FLOW_STATES = Object.freeze({
    IDLE: "idle",
    RECORDING: "recording",
    TRANSCRIBING: "transcribing",
    TRANSCRIBED: "transcribed",
    SENDING: "sending",
  });

  function createTranscriptionFlow(report) {
    let state = FLOW_STATES.IDLE;
    let generation = 0;
    let lastText = "";
    const ok = (expected) => state === expected;
    const flow = {
      get state() { return state; },
      get generation() { return generation; },
      get text() { return lastText; },
      start() {
        if (!ok(FLOW_STATES.IDLE)) return false;
        generation += 1; state = FLOW_STATES.RECORDING;
        if (report) report("start", generation);
        return true;
      },
      stop() {
        if (!ok(FLOW_STATES.RECORDING)) return false;
        state = FLOW_STATES.TRANSCRIBING;
        if (report) report("stop", generation);
        return true;
      },
      complete(text) {
        if (!ok(FLOW_STATES.TRANSCRIBING)) return false;
        lastText = String(text || "").trim();
        state = FLOW_STATES.TRANSCRIBED;
        if (report) report("transcribed", lastText, generation);
        return true;
      },
      empty() {
        if (!ok(FLOW_STATES.TRANSCRIBING)) return false;
        state = FLOW_STATES.IDLE;
        if (report) report("empty", generation);
        return true;
      },
      send() {
        if (!ok(FLOW_STATES.TRANSCRIBED)) return false;
        state = FLOW_STATES.SENDING;
        if (report) report("send", lastText, generation);
        return true;
      },
      done() {
        if (!ok(FLOW_STATES.SENDING)) return false;
        state = FLOW_STATES.IDLE;
        lastText = "";
        return true;
      },
      /* Descarta cualquier estado pendiente (recording, transcribing,
         transcribed) y vuelve a idle. Usado por Escape, cancelar y al
         enviar para que una transcripción tardía no se cuele. */
      clear() {
        if (ok(FLOW_STATES.IDLE)) return false;
        const was = state;
        state = FLOW_STATES.IDLE; generation += 1; lastText = "";
        if (report) report("clear", was, generation);
        return true;
      },
      isRecording() { return ok(FLOW_STATES.RECORDING); },
      isTranscribing() { return ok(FLOW_STATES.TRANSCRIBING); },
      isTranscribed() { return ok(FLOW_STATES.TRANSCRIBED); },
      isActive() { return state !== FLOW_STATES.IDLE && state !== FLOW_STATES.SENDING; },
      dispose() { generation += 1; state = FLOW_STATES.IDLE; lastText = ""; report = null; },
    };
    return flow;
  }

  /* ------------------------------------------------------------------
     VoiceActivityDetector (VAD) — reducer puro.

     Cada llamada a feed(rms, elapsedMsAbs) entrega:
       { state, enteredSpeech, speechOnset, endedUtterance,
         utteranceMs, speechMs }
     - speechOnset: primer frame con voz (arranca barge-in/captura)
     - enteredSpeech: el turno ha superado minSpeechMs (inicio real)
     - endedUtterance: se alcanzó minSilenceMs (o el tope) tras un
       turno válido -> "END OF UTTERANCE"
     Falsos positivos: ráfagas < minSpeechMs se descartan.
     Cortes demasiado rápidos: minSilenceMs evita acabar por pausas
     de respiración.
     ------------------------------------------------------------------ */
  function createVadSession(params) {
    const p = Object.assign({}, VAD_PARAMS, params || {});
    let state = "silence";                 // silence | speech | trailing
    let speechMs = 0;                      // voz acumulada del turno actual
    let silentMs = 0;                      // silencio trasero acumulado
    let lastElapsed = null;
    let utterance = false;                 // turno válido ya iniciado
    function feed(rms, elapsedMsAbs) {
      const elapsed = Number(elapsedMsAbs);
      let delta = p.frameMs;
      if (lastElapsed === null) lastElapsed = elapsed;
      else if (elapsed > lastElapsed) delta = Math.min(100, Math.max(0, elapsed - lastElapsed));
      else delta = 0;
      if (delta <= 0) return { state, enteredSpeech: false, speechOnset: false, endedUtterance: false, utteranceMs: 0, speechMs };
      const isSpeech = rms >= p.threshold;
      const ev = {
        state,
        enteredSpeech: false,
        speechOnset: false,
        endedUtterance: false,
        utteranceMs: 0,
        speechMs,
      };
      if (state === "speech" || state === "trailing") {
        if (isSpeech) {
          speechMs += delta; silentMs = 0; state = "speech";
          if (!ev.speechOnset && speechMs >= p.minSpeechGateMs) ev.speechOnset = true;
          if (!utterance && speechMs >= p.minSpeechMs) { utterance = true; ev.enteredSpeech = true; }
        } else {
          silentMs += delta;
          if (silentMs >= p.minSilenceMs) {
            if (utterance && speechMs >= p.minSpeechMs) {
              ev.endedUtterance = true;
              ev.utteranceMs = speechMs;
            }
            reset();
          } else {
            state = "trailing";
          }
        }
      } else if (isSpeech) {
        speechMs += delta; silentMs = 0; state = "speech";
        if (speechMs >= p.minSpeechGateMs) ev.speechOnset = true;
        if (speechMs >= p.minSpeechMs) {
          utterance = true;
          ev.enteredSpeech = true;
        }
      }
      if (utterance && speechMs >= p.maxUtteranceMs) {
        ev.endedUtterance = true;
        ev.utteranceMs = speechMs;
        reset();
      }
      ev.state = state;
      ev.speechMs = speechMs;
      return ev;
    }
    function reset() {
      state = "silence"; speechMs = 0; silentMs = 0; utterance = false; lastElapsed = null;
    }
    function active() { return state === "speech" || state === "trailing"; }
    feed.reset = reset;
    feed.active = active;
    feed.current = () => ({ state, speechMs, silentMs, utterance });
    return feed;
  }

  /* ------------------------------------------------------------------
     Niveles y visualización (lógica pura).
     ------------------------------------------------------------------ */
  const SMOOTH_FACTOR = 0.42;               // suavizado de barras (0..1)
  function smoothLevel(prev, next, factor) {
    const f = factor === undefined ? SMOOTH_FACTOR : factor;
    return prev + (next - prev) * f;
  }
  /* Convierte RMS en un valor de nivel 0..1 con curva suave. */
  function levelFromRms(rms, scale) {
    const s = scale === undefined ? 18 : scale;
    const v = rms * s;
    return v < 0 ? 0 : v > 1 ? 1 : v;
  }
  /* Suelo mínimo para que en silencio las barras no queden muertas. */
  function idleFloor(level, floor) {
    const f = floor === undefined ? 0.06 : floor;
    return level < f ? f : level;
  }
  /* Escala una magnitud de banda FFT (0..1) con curva perceptiva. */
  function bandLevel(magnitude01) {
    const v = Math.pow(magnitude01 <= 0 ? 0 : (magnitude01 > 1 ? 1 : magnitude01), 1.6);
    return idleFloor(v, 0.05);
  }

  /* ------------------------------------------------------------------
     Teclado (lógica pura).

     Space: solo detiene la grabación en el contexto apropiado, para no
     interferir con escritura normal, botones, modales, inputs/selects
     ni accesibilidad. El resto de casos conserva el comportamiento
     nativo del navegador.
     ------------------------------------------------------------------ */
  const SPACE_SCOPES = Object.freeze({
    TYPING_TARGET: "textarea",    // composer de mensajes
    BODY: "body",
    BUTTON: "button",
    FORM_FIELD: "input",          // inputs, selects, contenteditable...
    MODAL: "modal",               // cualquier <dialog>
    OTHER: "other",
  });

  function classifyTarget(target) {
    if (!target || target === document) return SPACE_SCOPES.BODY;
    if (target.closest && target.closest("dialog")) return SPACE_SCOPES.MODAL;
    const t = target;
    if (t.closest && t.closest("button, select")) return SPACE_SCOPES.BUTTON;
    if (t.closest && t.closest("input, textarea, [contenteditable='true']")) {
      return t.tagName && t.tagName.toLowerCase() === "textarea" ? SPACE_SCOPES.TYPING_TARGET : SPACE_SCOPES.FORM_FIELD;
    }
    if (t === document.body) return SPACE_SCOPES.BODY;
    return SPACE_SCOPES.OTHER;
  }

  function shouldInterceptSpace(flowState, target) {
    if (flowState !== FLOW_STATES.RECORDING) return false;
    const scope = typeof target === "string" ? target : classifyTarget(target);
    // El tipo del composer y el fondo permiten detener; botones, modales
    // y campos de formulario conservan su comportamiento nativo.
    return scope === SPACE_SCOPES.TYPING_TARGET || scope === SPACE_SCOPES.BODY || scope === SPACE_SCOPES.OTHER;
  }

  /* Enter envía solo en el composer, sin Shift y sin composición IME. */
  function shouldSubmit(key, shiftKey, isComposing) {
    return key === "Enter" && !shiftKey && !isComposing;
  }

  /* ------------------------------------------------------------------
     Utilidades.
     ------------------------------------------------------------------ */
  function mergeTranscription(existing, transcribed) {
    const text = String(transcribed || "").trim();
    if (!text) return String(existing || "");
    const base = String(existing || "").trim();
    return base ? base + " " + text : text;
  }

  function runWithTimeout(promise, ms, message) {
    const timer = new Promise((_, reject) =>
      setTimeout(() => reject(Object.assign(new Error(message || "La operación tardó demasiado."), { name: "TimeoutError" })), ms)
    );
    return Promise.race([promise, timer]);
  }

  return {
    VAD_PARAMS,
    VOICE_STATES,
    FLOW_STATES,
    TRANSITIONS,
    SPACE_SCOPES,
    createVoiceMachine,
    createTranscriptionFlow,
    createVadSession,
    smoothLevel,
    levelFromRms,
    idleFloor,
    bandLevel,
    classifyTarget,
    shouldInterceptSpace,
    shouldSubmit,
    mergeTranscription,
    runWithTimeout,
  };
});