"use strict";
/* Tests de la lógica pura del sistema de voz (voice-core.js).
   Ejecutar con: node --test tests/voice/core.test.js */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const VoiceCore = require("../../web/voice-core.js");

/* ================================================================
   1. Máquina de estados del modo llamada (VoiceStateMachine)
   ================================================================ */
test("FSM: ciclo completo de estados válido", () => {
  const seen = [];
  const sm = VoiceCore.createVoiceMachine((prev, next) => seen.push([prev, next]));
  assert.equal(sm.state, VoiceCore.VOICE_STATES.IDLE);
  assert.ok(sm.to(VoiceCore.VOICE_STATES.CONNECTING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.LISTENING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.PROCESSING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.SPEAKING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.LISTENING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.ENDING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.IDLE));
  assert.equal(seen.length, 7);
  assert.deepEqual(seen[0], [VoiceCore.VOICE_STATES.IDLE, VoiceCore.VOICE_STATES.CONNECTING]);
});

test("FSM: rechaza transiciones imposibles sin cambiar de estado", () => {
  const sm = VoiceCore.createVoiceMachine();
  sm.to(VoiceCore.VOICE_STATES.CONNECTING);
  assert.equal(sm.to(VoiceCore.VOICE_STATES.IDLE), false);   // necesita ending primero
  assert.equal(sm.state, VoiceCore.VOICE_STATES.CONNECTING);
  assert.equal(sm.to(VoiceCore.VOICE_STATES.PROCESSING), false);
  assert.equal(sm.state, VoiceCore.VOICE_STATES.CONNECTING);
});

test("FSM: estados de error son recuperables", () => {
  const sm = VoiceCore.createVoiceMachine();
  sm.to(VoiceCore.VOICE_STATES.CONNECTING);
  sm.to(VoiceCore.VOICE_STATES.LISTENING);
  assert.ok(sm.to(VoiceCore.VOICE_STATES.ERROR));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.LISTENING));   // desde error se puede volver a escuchar
  assert.ok(sm.to(VoiceCore.VOICE_STATES.ENDING));
  assert.ok(sm.to(VoiceCore.VOICE_STATES.IDLE));
});

test("FSM: dispose bloquea transiciones excepto volver a idle; reset recupera", () => {
  const sm = VoiceCore.createVoiceMachine();
  sm.to(VoiceCore.VOICE_STATES.CONNECTING);
  sm.to(VoiceCore.VOICE_STATES.LISTENING);
  sm.dispose();
  assert.equal(sm.to(VoiceCore.VOICE_STATES.PROCESSING), false);  // bloqueado
  assert.ok(sm.to(VoiceCore.VOICE_STATES.IDLE));                 // volver a idle siempre permitido
  assert.equal(sm.state, VoiceCore.VOICE_STATES.LISTENING);      // pero no cambia internamente
  sm.reset();
  assert.equal(sm.state, VoiceCore.VOICE_STATES.IDLE);
  assert.ok(sm.to(VoiceCore.VOICE_STATES.CONNECTING));
});

test("FSM: transición duplicada retorna true sin efecto secundario", () => {
  const sm = VoiceCore.createVoiceMachine();
  sm.to(VoiceCore.VOICE_STATES.CONNECTING);
  assert.ok(sm.to(VoiceCore.VOICE_STATES.CONNECTING));
  assert.equal(sm.state, VoiceCore.VOICE_STATES.CONNECTING);
});

/* ================================================================
   2. Flujo de transcripción (VoiceMessageMode)
   ================================================================ */
test("Flujo: ruta completa happy-path sin auto-envío", () => {
  const flow = VoiceCore.createTranscriptionFlow();
  assert.ok(flow.start());
  assert.ok(flow.isRecording());
  assert.equal(flow.state, VoiceCore.FLOW_STATES.RECORDING);

  assert.ok(flow.stop());
  assert.ok(flow.isTranscribing());
  assert.equal(flow.state, VoiceCore.FLOW_STATES.TRANSCRIBING);

  assert.ok(flow.complete("hola mundo"));
  assert.ok(flow.isTranscribed());
  assert.equal(flow.text, "hola mundo");

  assert.ok(flow.send());     // solo marca SENDING, NO auto-envía
  assert.equal(flow.state, VoiceCore.FLOW_STATES.SENDING);
  assert.ok(flow.done());
  assert.equal(flow.state, VoiceCore.FLOW_STATES.IDLE);
});

test("Flujo: transición empty vuelve a idle cuando no hay voz clara", () => {
  const flow = VoiceCore.createTranscriptionFlow();
  flow.start(); flow.stop();
  assert.ok(flow.empty());
  assert.equal(flow.state, VoiceCore.FLOW_STATES.IDLE);
});

test("Flujo: clear cancela grabación/transcripción y vuelve a idle", () => {
  const flow = VoiceCore.createTranscriptionFlow();
  flow.start();
  assert.ok(flow.clear());
  assert.equal(flow.state, VoiceCore.FLOW_STATES.IDLE);
  assert.equal(flow.clear(), false);   // ya está idle
});

test("Flujo: rechaza comandos fuera de estado (sin estados imposibles)", () => {
  const flow = VoiceCore.createTranscriptionFlow();
  assert.equal(flow.stop(), false);
  assert.equal(flow.complete("x"), false);
  assert.equal(flow.send(), false);
  assert.ok(flow.start());
  assert.equal(flow.complete("x"), false);    // grabando, no transcribiendo
  assert.equal(flow.send(), false);           // solo transcribido permite send
});

test("Flujo: dispose invalida callbacks tardíos", () => {
  const flow = VoiceCore.createTranscriptionFlow();
  assert.ok(flow.start());
  assert.ok(flow.stop());
  flow.dispose();
  assert.equal(flow.complete("tarde"), false);
  assert.equal(flow.state, VoiceCore.FLOW_STATES.IDLE);
});

/* ================================================================
   3. VAD (VoiceActivityDetector)
   ================================================================ */
test("VAD: ráfaga corta < minSpeechMs no se acepta como turno", () => {
  const vad = VoiceCore.createVadSession({
    threshold: 0.02, frameMs: 100, minSpeechMs: 300, minSilenceMs: 400,
    minSpeechGateMs: 100, maxUtteranceMs: 10000,
  });
  const loud = (ms) => vad(0.2, 1000 + ms);
  const silent = (ms) => vad(0.001, 1000 + ms);

  loud(0);          // speechMs 100, speechOnset true
  silent(100); silent(200); silent(300); silent(400);
  const ev = silent(500);
  assert.equal(ev.endedUtterance, false);   // utterance nunca fue true
});

test("VAD: turno completo — voz válida + silencio cierra con endedUtterance", () => {
  const vad = VoiceCore.createVadSession({
    threshold: 0.02, frameMs: 100, minSpeechMs: 300, minSilenceMs: 400,
    minSpeechGateMs: 100, maxUtteranceMs: 10000,
  });
  vad(0.2, 1000);   // speechMs 100
  vad(0.2, 1100);   // 200
  const entered = vad(0.2, 1200);  // 300 → enteredSpeech
  assert.equal(entered.enteredSpeech, true);
  assert.equal(vad.current().utterance, true);

  let ev;
  for (let i = 0; i < 4; i++) ev = vad(0.001, 1300 + i * 100);
  assert.equal(ev.endedUtterance, true);
  assert.ok(ev.utteranceMs >= 300);
  assert.equal(vad.current().state, "silence");
});

test("VAD: speechOnset se emite antes de enteredSpeech (barge-in rápido)", () => {
  const vad = VoiceCore.createVadSession({
    threshold: 0.02, frameMs: 100, minSpeechMs: 300, minSilenceMs: 400,
    minSpeechGateMs: 100, maxUtteranceMs: 10000,
  });
  const ev1 = vad(0.2, 1000);
  assert.equal(ev1.speechOnset, true);
  assert.equal(ev1.enteredSpeech, false);
  const ev2 = vad(0.2, 1100);
  assert.equal(ev2.enteredSpeech, false);
  const ev3 = vad(0.2, 1200);
  assert.equal(ev3.enteredSpeech, true);
});

test("VAD: maxUtteranceMs fuerza fin del turno aunque haya voz continua", () => {
  const vad = VoiceCore.createVadSession({
    threshold: 0.02, frameMs: 100, minSpeechMs: 300, minSilenceMs: 10000,
    minSpeechGateMs: 100, maxUtteranceMs: 500,
  });
  let sawEnd = false;
  for (let i = 0; i < 5; i++) {
    const ev = vad(0.2, 1000 + i * 100);
    if (ev.endedUtterance) sawEnd = true;
  }
  assert.ok(sawEnd);
});

/* ================================================================
   4. Teclado (scope de Space y Enter)
   ================================================================ */
test("Space: solo intercepta en grabación cuando el foco está en textarea/cuerpo/otro", () => {
  // shouldInterceptSpace acepta strings de scope directamente (evita document en Node)
  // sin grabación → nunca intercepta
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.IDLE, VoiceCore.SPACE_SCOPES.TYPING_TARGET), false);
  // grabando: textarea sí, body sí, other sí; button/input/modal no
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.TYPING_TARGET), true);
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.BODY), true);
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.OTHER), true);
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.BUTTON), false);
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.FORM_FIELD), false);
  assert.equal(VoiceCore.shouldInterceptSpace(VoiceCore.FLOW_STATES.RECORDING, VoiceCore.SPACE_SCOPES.MODAL), false);
});

test("Enter: shouldSubmit verifica Enter sin Shift y sin composición IME", () => {
  assert.equal(VoiceCore.shouldSubmit("Enter", false, false), true);
  assert.equal(VoiceCore.shouldSubmit("Enter", true, false), false);
  assert.equal(VoiceCore.shouldSubmit("Enter", false, true), false);
  assert.equal(VoiceCore.shouldSubmit("a", false, false), false);
});

/* ================================================================
   5. Niveles y suavizado (lógica pura)
   ================================================================ */
test("Niveles: smoothLevel converge, levelFromRms clamps, bandLevel e idleFloor", () => {
  assert.equal(VoiceCore.smoothLevel(0, 1, 1), 1);
  assert.ok(VoiceCore.smoothLevel(0, 1, 0.5) === 0.5);

  assert.equal(VoiceCore.levelFromRms(0, 18), 0);
  assert.equal(VoiceCore.levelFromRms(0.06, 18), 1);    // clamp arriba
  assert.ok(VoiceCore.levelFromRms(0.02, 18) > 0 && VoiceCore.levelFromRms(0.02, 18) < 1);

  assert.equal(VoiceCore.idleFloor(0.001, 0.06), 0.06);
  assert.equal(VoiceCore.idleFloor(0.5, 0.06), 0.5);

  assert.ok(VoiceCore.bandLevel(0) > 0);     // suelo no-cero
  assert.equal(VoiceCore.bandLevel(2), 1);    // clamp arriba
});

/* ================================================================
   6. mergeTranscription
   ================================================================ */
test("mergeTranscription: concatena, trima, preserva existente", () => {
  assert.equal(VoiceCore.mergeTranscription("", "hola"), "hola");
  assert.equal(VoiceCore.mergeTranscription("hola", "mundo"), "hola mundo");
  assert.equal(VoiceCore.mergeTranscription("hola", "  "), "hola");
  assert.equal(VoiceCore.mergeTranscription("", "  "), "");
});

/* ================================================================
   7. runWithTimeout
   ================================================================ */
test("runWithTimeout: rechaza si la promesa tarda más de lo permitido", async () => {
  const never = new Promise(() => {});
  await assert.rejects(
    () => VoiceCore.runWithTimeout(never, 30, "Timeout de prueba"),
    /Timeout de prueba/
  );
});

test("runWithTimeout: resuelve normalmente si termina a tiempo", async () => {
  const fast = Promise.resolve(42);
  const result = await VoiceCore.runWithTimeout(fast, 1000, "nunca");
  assert.equal(result, 42);
});

/* ================================================================
   8. Escenario de integración: barge-in en modo llamada
   ================================================================ */
test("Integración: barge-in corta el habla de N.O.V.A. y vuelve a escuchar", () => {
  const sm = VoiceCore.createVoiceMachine();
  sm.to(VoiceCore.VOICE_STATES.CONNECTING);
  sm.to(VoiceCore.VOICE_STATES.LISTENING);
  sm.to(VoiceCore.VOICE_STATES.PROCESSING);
  sm.to(VoiceCore.VOICE_STATES.SPEAKING);

  // Simular usuario hablando mientras N.O.V.A. habla (barge-in)
  const vad = VoiceCore.createVadSession({ minSpeechMs: 20, maxUtteranceMs: 60000, minSpeechGateMs: 150, threshold: 0.02, frameMs: 100, minSilenceMs: 850 });
  let onset = false;
  for (let i = 0; i < 10; i++) {
    const ev = vad(0.3, 1000 + i * 100);
    if (ev.speechOnset) { onset = true; break; }
  }
  assert.ok(onset, "speechOnset se detectó durante barge-in");

  // Tras barge-in, máquina vuelve a LISTENING
  assert.ok(sm.to(VoiceCore.VOICE_STATES.LISTENING));
  assert.equal(sm.state, VoiceCore.VOICE_STATES.LISTENING);
});

/* ================================================================
   9. Sanity: constantes y estructura expuesta
   ================================================================ */
test("Estructura: constantes VAD_PARAMS, VOICE_STATES y TRANSITIONS coherentes", () => {
  const p = VoiceCore.VAD_PARAMS;
  assert.ok(p.frameMs > 0);
  assert.ok(p.minSilenceMs > p.minSpeechMs);
  assert.ok(p.threshold >= 0);
  assert.equal(VoiceCore.VOICE_STATES.IDLE, "idle");
  assert.ok(VoiceCore.TRANSITIONS[VoiceCore.VOICE_STATES.IDLE].includes(VoiceCore.VOICE_STATES.CONNECTING));
  assert.ok(Object.values(VoiceCore.TRANSITIONS).every(list => Array.isArray(list)));
  assert.ok(typeof VoiceCore.createVadSession === "function");
  assert.ok(typeof VoiceCore.createTranscriptionFlow === "function");
  assert.ok(typeof VoiceCore.createVoiceMachine === "function");
});
