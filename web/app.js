"use strict";

/* N.O.V.A. web chat (ChatGPT-style). Plain JS, no build step.
   Works served by nova-api (relative paths) or on Vercel with ?api=<base>. */

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
let apiBase = (params.get("api") || localStorage.getItem("nova.apiBase") || "").replace(/\/+$/, "");
let token = params.get("token") || localStorage.getItem("nova.token") || "";
let sessionId = null;
let agentName = "";
let contributing = false;

function persist() {
  if (apiBase) localStorage.setItem("nova.apiBase", apiBase); else localStorage.removeItem("nova.apiBase");
  if (token) localStorage.setItem("nova.token", token); else localStorage.removeItem("nova.token");
}

function apiUrl(path) {
  return apiBase + path;
}

function authHeaders() {
  const headers = {};
  if (token) headers["Authorization"] = "Bearer " + token;
  return headers;
}

async function api(method, path, body) {
  const options = { method, headers: authHeaders() };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(apiUrl(path), options);
  if (response.status === 401) {
    token = "";
    $("token-input").value = "";
    persist();
    throw new Error("Sin autorización: introduce el token de la API y reintenta.");
  }
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail || ""; } catch (e) { /* ignore */ }
    throw new Error(detail || response.statusText);
  }
  return response.json();
}

/* ------------------------------------------------ rendering */

function append(kind, text) {
  const node = document.createElement("div");
  node.className = "msg " + (kind === "user" ? "user" : "assistant");
  if (kind === "assistant") {
    const label = document.createElement("span");
    label.className = "role-label";
    label.textContent = "N.O.V.A.";
    node.appendChild(label);
  }
  const body = document.createElement("span");
  body.textContent = text;
  node.appendChild(body);
  $("log").appendChild(node);
  scrollLog();
  return node;
}

function appendMeta(text, klass) {
  const node = document.createElement("div");
  node.className = "meta" + (klass ? " " + klass : "");
  node.textContent = text;
  $("log").appendChild(node);
  scrollLog();
}

function scrollLog() {
  const log = $("log");
  log.scrollTop = log.scrollHeight;
}

function setHeroVisible(visible) {
  $("hero").classList.toggle("hidden", !visible);
}

function renderSteps(steps) {
  for (const step of steps) {
    const args = JSON.stringify(step.args || {});
    const status = step.ok ? "ok" : "error";
    appendMeta("paso [" + status + "] " + step.tool + " " + args + " -> " + step.message, "step");
    if (step.data) appendMeta("  datos: " + JSON.stringify(step.data), "step");
  }
}

/* ------------------------------------------------ health + models */

async function updateHealth() {
  const dot = $("dot");
  try {
    const health = await api("GET", "/healthz");
    dot.className = "dot " + (health.status === "ok" ? "ok" : "bad");
    dot.title = "servidor: " + health.status;
  } catch (e) {
    dot.className = "dot bad";
    dot.title = "servidor no responde";
  }
}
setInterval(updateHealth, 15000);

async function loadModels() {
  try {
    const models = await api("GET", "/v1/models");
    const select = $("model");
    const current = $("model").value;
    select.innerHTML = '<option value="">modelo automático</option>';
    for (const m of models) {
      const option = document.createElement("option");
      option.value = m.name;
      option.textContent = m.name;
      select.appendChild(option);
    }
    if (current) select.value = current;
  } catch (e) { /* non-fatal */ }
}

/* ------------------------------------------------ sessions */

async function createSession() {
  const created = await api("POST", "/v1/sessions", { agent: agentName || null });
  sessionId = created.session_id;
  $("top-model").textContent = "N.O.V.A. · " + (created.agent || created.model);
  setHeroVisible(true);
  await refreshConversations();
  return created;
}

async function refreshConversations() {
  try {
    const sessions = await api("GET", "/v1/sessions");
    const list = $("conv-list");
    list.replaceChildren();
    if (!sessions.length) {
      const empty = document.createElement("div");
      empty.className = "conv-item";
      empty.style.cursor = "default";
      empty.textContent = "Sin conversaciones";
      list.appendChild(empty);
      return;
    }
    for (const s of sessions) {
      const btn = document.createElement("button");
      btn.className = "conv-item" + (s.session_id === sessionId ? " active" : "");
      btn.type = "button";
      btn.title = "Cargar conversación";
      btn.textContent = (s.agent ? s.agent + " · " : "") + (s.messages + " msg");
      btn.addEventListener("click", () => openSession(s.session_id));
      list.appendChild(btn);
    }
  } catch (e) { /* non-fatal */ }
}

async function openSession(id) {
  try {
    const data = await api("GET", "/v1/sessions/" + id + "/messages");
    sessionId = id;
    $("log").replaceChildren();
    for (const message of data.messages) {
      append(message.role === "user" ? "user" : "assistant", message.content);
    }
    setHeroVisible(!data.messages.length);
    await refreshConversations();
  } catch (e) {
    appendMeta("[error] " + e.message, "error");
  }
}

/* ------------------------------------------------ chat */

function sendMessage(message) {
  return api("POST", "/v1/sessions/" + sessionId + "/chat", {
    message,
    model: $("model").value || null,
  });
}

async function onSend(event) {
  event.preventDefault();
  const message = $("input").value.trim();
  if (!message || !sessionId || contributing) return;
  $("input").value = "";
  $("send").disabled = true;
  contributing = true;
  setHeroVisible(false);
  append("user", message);
  try {
    const data = await sendMessage(message);
    append("assistant", data.reply);
    if (data.context) appendMeta("contexto recuperado: " + data.context, "step");
    if (data.steps && data.steps.length) renderSteps(data.steps);
    $("input").focus();
    await refreshConversations();
  } catch (e) {
    appendMeta("[error] " + e.message, "error");
  } finally {
    $("send").disabled = false;
    contributing = false;
  }
}

$("compose").addEventListener("submit", onSend);

$("agent").addEventListener("change", () => {
  agentName = $("agent").value;
  $("log").replaceChildren();
  setHeroVisible(true);
  createSession().catch((e) => appendMeta("[error] " + e.message, "error"));
});

$("btn-new").addEventListener("click", async () => {
  $("log").replaceChildren();
  setHeroVisible(true);
  try {
    await createSession();
  } catch (e) {
    appendMeta("[error] " + e.message, "error");
  }
});

$("token-input").addEventListener("change", () => {
  token = $("token-input").value.trim();
  persist();
  if (token) location.reload();
});

$("btn-tools").addEventListener("click", async () => {
  try {
    const tools = await api("GET", "/v1/tools");
    appendMeta("Herramientas: " + tools.map((t) => t.name).join(", "), "step");
  } catch (e) {
    appendMeta("[error] " + e.message, "error");
  }
});

/* ------------------------------------------------ wizard */

const wizardEl = $("wizard");
let setupStatus = null;

function openWizard() {
  wizardEl.classList.remove("hidden");
  $("wiz-machine").textContent = "Detectando tu máquina...";
  loadSetupStatus();
}

$("wiz-close").addEventListener("click", () => wizardEl.classList.add("hidden"));
$("btn-install").addEventListener("click", openWizard);

function row(k, v, klass) {
  const div = document.createElement("div");
  div.className = "row";
  const key = document.createElement("span");
  key.className = "k";
  key.textContent = k;
  const val = document.createElement("span");
  val.className = "v" + (klass ? " " + klass : "");
  val.textContent = v;
  div.append(key, val);
  return div;
}

async function loadSetupStatus() {
  const rows = $("rows-connection");
  rows.replaceChildren();
  try {
    setupStatus = await api("GET", "/v1/setup/status");
    const m = setupStatus.machine;
    $("wiz-machine").textContent = m.os + " · " + m.cpu_count + " núcleos · " + m.ram_gb + " GB RAM" + (m.gpu_available ? " · GPU " + m.gpu_vram_gb + " GB" : " · sin GPU");

    rows.append(row("Sistema", m.os + " / " + m.python, "mono"));
    rows.append(row("CPU", m.cpu_count + " núcleo(s)", ""));
    rows.append(row("RAM", m.ram_gb + " GB", ""));
    rows.append(row("GPU VRAM", m.gpu_available ? m.gpu_vram_gb + " GB" : "ninguna detectada", m.gpu_available ? "ok" : "dim"));

    const o = setupStatus.ollama;
    rows.append(row("Ollama", o.installed ? (o.running ? "en ejecución" : "instalado pero apagado") : "no instalado", o.running ? "ok" : "bad"));
    if (o.running && o.models.length) rows.append(row("Modelos", o.models.join(", "), "mono"));

    const c = setupStatus.config;
    rows.append(row("config.yaml", c.exists ? (c.complete ? "completo" : "existe (incompleto)") : "no existe", c.complete ? "ok" : "bad"));

    rows.classList.add("filled");
    renderModels();
  } catch (e) {
    rows.append(row("Error", e.message, "bad"));
  }
}

function renderModels() {
  const rows = $("rows-models");
  rows.replaceChildren();
  if (!setupStatus) return;
  const have = new Set(setupStatus.ollama.models);
  const missing = setupStatus.missing_models;

  for (const rec of setupStatus.recommended_models || []) {
    const present = have.has(rec.model) || have.has(rec.model.split(":")[0]) || !missing.includes(rec.model);
    const div = document.createElement("div");
    div.className = "row";
    div.dataset.model = rec.model;
    const key = document.createElement("span");
    key.className = "k";
    key.textContent = rec.model;
    const val = document.createElement("span");
    val.className = "v " + (present ? "ok" : "bad");
    val.textContent = present ? "listo (" + rec.reason + ")" : "falta (" + rec.reason + ")";
    div.append(key, val);
    if (!present) {
      const bar = document.createElement("div");
      bar.className = "bar";
      bar.innerHTML = "<span></span>";
      div.append(bar);
    }
    rows.append(div);
  }

  const anyMissing = setupStatus.missing_models.length > 0;
  $("btn-pull-all").disabled = !anyMissing || !setupStatus.ollama.running;
}

async function provision() {
  const btn = $("btn-provision");
  btn.disabled = true;
  btn.textContent = "Guardando...";
  try {
    const data = await api("POST", "/v1/setup/provision", {
      voice: $("opt-voice").checked,
      web: $("opt-web").checked,
      plugins: $("opt-plugins").checked,
      automation: $("opt-automation").checked,
      autostart: $("opt-autostart").checked || null,
    });
    appendMetaToWizard("Configuración guardada: " + data.summary, "ok");
    appendMetaToWizard("Autostart: " + (data.autostart !== null ? (data.autostart ? "activado" : "desactivado") : "sin cambios"), "dim");
    await loadSetupStatus();
  } catch (e) {
    appendMetaToWizard("[error] " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Guardar configuración";
  }
}

function appendMetaToWizard(text, klass) {
  appendMeta(text, klass === "ok" || klass === "dim" || klass === "error" ? klass : "step");
}

function modelRow(model) {
  return document.querySelector('#rows-models .row[data-model="' + CSS.escape(model) + '"]');
}

async function pullModels(model) {
  const url = apiUrl("/v1/setup/pull" + (model ? "?model=" + encodeURIComponent(model) : ""));
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail || ""; } catch (e) { /* ignore */ }
    throw new Error(detail || response.statusText);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5).trim());
      onPullEvent(event);
    }
  }
}

function onPullEvent(event) {
  const target = event.model;
  const rowEl = target ? modelRow(target) : null;
  if (event.status === "done") {
    if (rowEl) {
      const val = rowEl.querySelector(".v");
      val.className = "v " + (event.ok ? "ok" : "bad");
      val.textContent = event.ok ? "listo" : "error: " + (event.error || "desconocido");
      const bar = rowEl.querySelector(".bar");
      if (bar) bar.style.display = "none";
      const barSpan = rowEl.querySelector(".bar span");
      if (barSpan) barSpan.style.width = event.ok ? "100%" : "0%";
    }
    appendMetaToWizard(target + ": " + (event.ok ? "descargado" : "falló"), event.ok ? "ok" : "error");
    if (!target) appendMetaToWizard(event.message || "sin modelos que descargar", "dim");
    return;
  }
  if (event.status === "start") {
    appendMetaToWizard("Descargando " + target + "...", "step");
    return;
  }
  if (rowEl) {
    const total = event.total || 0;
    const done = event.completed || 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const span = rowEl.querySelector(".bar span");
    if (span) span.style.width = pct + "%";
  }
}

$("btn-provision").addEventListener("click", provision);

$("btn-pull-all").addEventListener("click", () => {
  pullModels(null).catch((e) => appendMetaToWizard("[error] " + e.message, "error"));
});

for (const rowEl of document.querySelectorAll("#rows-models .row")) {
  rowEl.addEventListener("click", () => {
    const model = rowEl.dataset.model;
    if (!model || setupStatus.missing_models.includes(model)) {
      pullModels(model).catch((e) => appendMetaToWizard("[error] " + e.message, "error"));
    }
  });
}

$("btn-greet").addEventListener("click", async () => {
  try {
    const data = await api("POST", "/v1/setup/greeting");
    appendMetaToWizard(data.spoken ? "JARVIS: " + data.text : "Voz no disponible (instala el extra).", data.spoken ? "step" : "dim");
  } catch (e) {
    appendMetaToWizard("[error] " + e.message, "error");
  }
});

/* ------------------------------------------------ boot */

$("token-input").value = token;

async function boot() {
  updateHealth();
  loadModels();
  await refreshConversations();
  try {
    const agents = await api("GET", "/v1/agents");
    for (const agent of agents) {
      const option = document.createElement("option");
      option.value = agent.name;
      option.textContent = agent.name + " — " + agent.description;
      $("agent").appendChild(option);
    }
  } catch (e) {
    appendMeta("No se pudieron cargar los agentes: " + e.message, "error");
  }
  try {
    await createSession();
  } catch (e) {
    $("hero-sub").textContent = "No se pudo conectar con el servidor: " + e.message +
      " (si usas Vercel, abre la web con ?api=<http://IP:8000> y guarda el token).";
    setHeroVisible(true);
  }
}

boot();