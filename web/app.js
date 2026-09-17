"use strict";
const $ = id => document.getElementById(id);
const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
const params = new URLSearchParams(location.search);
let apiBase = (params.get("api") || localStorage.getItem("nova.apiBase") || "").replace(/\/+$/, "");
let token = params.get("token") || sessionStorage.getItem("nova.token") || "";
if (params.get("token")) sessionStorage.setItem("nova.token", token);
localStorage.removeItem("nova.token");
const uiVersion = document.querySelector('meta[name="nova-version"]')?.content || "";
let current = null, busy = false, pending = [], status = null, panelView = "", prepareTimer = null, approvalId = null, stick = true;
const messagesEl = $("messages");
function syncSend() {
  const has = $("message").value.trim() !== "" || pending.length > 0;
  $("send").disabled = !has || busy;
}
function toggleSidebarCollapse() {
  const app = $("app");
  const collapsed = app.classList.toggle("sidebar-collapsed");
  localStorage.setItem("nova.sidebarCollapsed", collapsed ? "1" : "0");
}
function closeMobileSidebar() {
  $("sidebar").classList.remove("visible");
  const scrim = $("scrim"); if (scrim) scrim.hidden = true;
}
const isLocalPage = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const headers = () => Object.assign(token ? {Authorization: "Bearer " + token} : {}, uiVersion ? {"X-NOVA-UI-Version": uiVersion} : {});
const coreProbeBases = ["http://localhost:8000", "http://127.0.0.1:8000"];
async function probeLocalCore() {
  for (const base of coreProbeBases) {
    try {
      const response = await fetch(base + "/healthz", {headers: headers()});
      if (response.ok) return base;
    } catch (e) {}
  }
  return null;
}

async function api(method, path, body) {
  const options = {method, headers: headers()};
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch(apiBase + path, options); }
  catch (e) { throw new Error("No podemos conectar con N.O.V.A. Abre la aplicación en tu ordenador y reintenta."); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(response.status === 401 ? "Introduce tu clave de conexión e inténtalo de nuevo." : response.status >= 500 ? "N.O.V.A. no ha podido completar esta acción. Reintenta o abre Diagnóstico." : typeof data.detail === "string" ? data.detail : "Revisa los datos e inténtalo de nuevo.");
    error.details = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || response.status);
    throw error;
  }
  return response.json();
}
async function rawFetch(path, options) {
  let response;
  try { response = await fetch(apiBase + path, options); }
  catch (e) { throw new Error("No podemos conectar con N.O.V.A. Abre la aplicación en tu ordenador y reintenta."); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(response.status === 401 ? "Introduce tu clave de conexión e inténtalo de nuevo." : typeof data.detail === "string" ? data.detail : "Revisa los datos e inténtalo de nuevo.");
    error.details = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || response.status);
    throw error;
  }
  return response;
}
function toast(message, details) {
  const box = $("toast"); box.replaceChildren(el("span", message)); box.hidden = false;
  const close = el("button", "×"); close.setAttribute("aria-label", "Cerrar aviso"); close.onclick = () => box.hidden = true; box.append(close);
  if (details) { const disclosure = el("details"); disclosure.append(el("summary", "Ver detalles"), el("p", details)); box.append(disclosure); }
}
const fail = error => toast(error.message || "No hemos podido completar esta acción.", error.details);
function button(text, callback, className) { const b = el("button", text, className); b.type = "button"; b.onclick = () => Promise.resolve().then(callback).catch(fail); return b; }
function openPanel(title, view) { panelView = view; $("panel-title").textContent = title; $("panel-body").replaceChildren(); if (!$("panel").open) $("panel").showModal(); return $("panel-body"); }
function confirmAction(title, message) {
  return new Promise(resolve => {
    $("confirmation-title").textContent = title; $("confirmation-text").textContent = message; $("confirmation").showModal();
    const finish = value => { $("confirmation").close(); resolve(value); };
    $("confirm-yes").onclick = () => finish(true); $("confirm-no").onclick = () => finish(false);
    $("confirmation").oncancel = () => finish(false);
  });
}
function newConversation() {
  if (busy) return;
  current = null; $("messages").replaceChildren(); $("hero").hidden = false;
  $("conversation-title").textContent = "Tu espacio"; stick = true; $("scroll-to-bottom").hidden = true; syncSend(); $("message").focus(); refreshConversations();
}
async function refreshConversations() {
  const rows = await api("GET", "/v1/sessions"); $("conversations").replaceChildren();
  if (!rows.length) $("conversations").append(el("p", "Aquí empieza tu próxima idea.", "empty"));
  for (const row of rows) {
    const node = el("div", undefined, "conversation" + (row.session_id === current ? " active" : ""));
    const select = button(row.title || "Conversación", () => openConversation(row), "select-conversation"); select.title = row.title;
    const remove = button("×", async () => {
      if (busy) return;
      if (!await confirmAction("¿Eliminar conversación?", "Se borrará su historial local. Los recuerdos guardados se administran desde Memoria.")) return;
      await api("DELETE", "/v1/sessions/" + row.session_id); if (current === row.session_id) newConversation(); await refreshConversations();
    }, "remove"); remove.setAttribute("aria-label", "Eliminar " + row.title);
    node.append(select, remove); $("conversations").append(node);
  }
}
function renderMessage(role, content, cards = [], images = []) {
  if (role === "system" || role === "tool") return;
  const outer = el("article", undefined, "message " + role), wrap = el("div", undefined, "message-wrap");
  if (role === "assistant") wrap.append(el("div", "N.O.V.A.", "message-label"));
  wrap.append(el("div", content, "message-content"));
  for (const card of cards) { const chip = el("div", undefined, "message-attachment"); chip.append(el("span", card.name)); wrap.append(chip); }
  if (role === "assistant") {
    const speak = el("button", "Leer", "speak"); speak.type = "button"; speak._voiceKey = content;
    speak.onclick = () => Promise.resolve().then(() => NovaVoice.toggleSpeak(content)).catch(fail);
    speak.hidden = !NovaVoice.canSpeak();
    wrap.append(speak, button("Copiar", () => navigator.clipboard.writeText(content).then(() => toast("Respuesta copiada.")), "copy"));
  }
  outer.append(wrap); $("messages").append(outer); return outer;
}
function renderSources(steps, messageNode) {
  if (!steps?.length || !messageNode) return;
  const sources = [];
  for (const step of steps) {
    if (step.tool !== "web_search" || !step.ok || !step.data?.results?.length) continue;
    for (const item of step.data.results) {
      if (!item || typeof item.url !== "string" || !item.url) continue;
      sources.push({title: item.title || item.url, url: item.url, snippet: item.snippet || ""});
    }
  }
  if (!sources.length) return;
  const block = el("div", undefined, "sources");
  block.append(el("p", "Fuentes consultadas", "sources-label"));
  for (const source of sources.slice(0, 8)) {
    const card = el("a", undefined, "source-card");
    card.href = source.url; card.target = "_blank"; card.rel = "noopener noreferrer";
    card.append(el("span", source.title, "source-title"));
    if (source.snippet) card.append(el("span", source.snippet, "source-snippet"));
    card.append(el("span", source.url, "source-url"));
    block.append(card);
  }
  messageNode.querySelector(".message-wrap").append(block);
}
async function refreshVoice() {
  try { NovaVoice.setStatus(await api("GET", "/v1/voice/status")); }
  catch (e) { NovaVoice.setStatus(null); }
  NovaVoice.updateVisibility();
}
async function openConversation(row) {
  if (busy) return;
  const data = await api("GET", "/v1/sessions/" + row.session_id + "/messages");
  current = row.session_id; $("agent").value = row.agent || ""; $("messages").replaceChildren();
  for (const m of data.messages) renderMessage(m.role, m.content, m.attachments || [], m.images || []);
  $("hero").hidden = data.messages.length > 0; $("conversation-title").textContent = row.title || "Conversación";
  closeMobileSidebar(); await refreshConversations(); stick = true; scrollChat(); syncSend();
}
function scrollChat() { if (stick) messagesEl.scrollTop = messagesEl.scrollHeight; }
async function loadModels() {
  const previous = $("model").value; $("model").replaceChildren(new Option("Selección automática", ""));
  const models = await api("GET", "/v1/models");
  for (const model of models.filter(m => !/embed/i.test(m.name))) $("model").append(new Option(model.name, model.name));
  $("model").value = previous;
}
async function health() {
  status = await api("GET", "/v1/desktop/status");
  if (uiVersion && status.version !== uiVersion) {
    try { await fetch(apiBase + "/v1/desktop/exit", {method: "POST", headers: headers()}); } catch (e) {}
    throw new Error("La aplicación se está actualizando. Reinicia N.O.V.A. para continuar.");
  }
  $("privacy").replaceChildren(el("span", status.privacy === "cloud_allowed" ? "Servicios externos permitidos" : "Privado y local"));
  $("composer-note").textContent = status.privacy === "cloud_allowed" ? "Las funciones externas pueden recibir el contenido de la conversación. Los adjuntos se procesan localmente." : "Tus conversaciones se guardan en este ordenador.";
  const response = await api("GET", "/healthz");
  $("dot").className = "dot " + (response.status === "ok" && !status.paused ? "ok" : "bad");
  $("connection-status").textContent = status.paused ? "N.O.V.A. en pausa" : response.status === "ok" ? "En tu ordenador" : "Motor pendiente de preparar";
  return status;
}
const SLASH_COMMANDS = [
  ["help", "Lista los comandos disponibles"],
  ["new", "Nueva conversación"],
  ["clear", "Borra la conversación actual"],
  ["tools", "Abre el panel de herramientas"],
  ["status", "Diagnóstico de N.O.V.A."],
  ["models", "Catálogo de modelos"],
  ["route", "Averigua qué modelo usaría para un texto"],
  ["audit", "Actividad reciente de herramientas"],
  ["model", "Fija el modelo de la conversación"],
  ["call", "Llamada de voz con N.O.V.A."],
];
let slashIndex = -1;
function slashSuggest() {
  const box = $("slash-suggest"), input = $("message"), text = input.value;
  const match = /^\/(\S*)$/.exec(text);
  box.hidden = true; slashIndex = -1;
  if (!match) return '';
  const token = match[1].toLowerCase();
  const picks = SLASH_COMMANDS.filter(([cmd]) => cmd.startsWith(token));
  box.replaceChildren();
  if (!picks.length) return '';
  for (const [cmd, help] of picks) {
    const row = el("div", undefined, "suggest");
    row.setAttribute("role", "option");
    row.append(el("strong", "/" + cmd), el("span", help));
    row.onmousedown = e => e.preventDefault();
    row.onclick = () => { input.value = "/" + cmd + " "; input.focus(); slashSuggest(); };
    box.append(row);
  }
  box.hidden = false;
  return '/';
}
function highlightSuggest(rows) {
  rows.forEach((r, i) => r.classList.toggle("selected", i === slashIndex));
}
async function runSlash(raw) {
  const parts = (raw.slice(1).trim().split(/\s+/) || []);
  const cmd = (parts[0] || "").toLowerCase();
  const args = parts.slice(1).join(" ");
  const userNode = renderMessage("user", raw);
  let reply = "";
  try {
    switch (cmd) {
      case "help":
        reply = SLASH_COMMANDS.map(([c, d]) => `/${c} — ${d}`).join("\n");
        break;
      case "new":
        newConversation(); return;
      case "clear":
        if (!current) { toast("No hay conversación activa."); userNode.remove(); return; }
        if (!await confirmAction("¿Borrar esta conversación?", "Se eliminará su historial local.")) { userNode.remove(); return; }
        await api("DELETE", "/v1/sessions/" + current); newConversation(); return;
      case "tools":
        showTools(); userNode.remove(); return;
      case "status":
        showStatus(); userNode.remove(); return;
      case "models":
        showCatalog(); userNode.remove(); return;
      case "route":
        if (!args) { reply = "Uso: /route <texto>"; break; }
        {
          const d = await api("POST", "/v1/route", {messages: [{role: "user", content: args}]});
          reply = `Modelo: ${d.model}\nTarea: ${d.task_kind} · Rol: ${d.role} · Proveedor: ${d.provider}\nMotivo: ${d.reason}`;
        }
        break;
      case "audit": {
        const requested = parseInt(args, 10);
        const n = requested ? Math.max(1, Math.min(200, requested)) : 10;
        const data = await api("GET", "/v1/audit?limit=" + n);
        if (!data.entries.length) { reply = "Aún no hay actividad de herramientas registrada."; break; }
        reply = data.entries.map(e => `${(e.ts || "").slice(0, 19)} ${e.ok ? "✓" : "✗"} ${e.tool} ${e.action} → ${e.decision || ""}` + (e.message ? ` (${e.message})` : "")).join("\n");
        break;
      }
      case "model":
        if (!args) { reply = "Modelo actual: " + ($("model").value || "Selección automática"); break; }
        {
          const options = Array.from($("model").options).map(o => o.value).filter(Boolean);
          if (!options.includes(args)) { reply = `Modelo "${args}" no disponible. Instalados: ${options.join(", ") || "(ninguno)"}`; break; }
          $("model").value = args; reply = "Modelo fijado: " + args;
        }
        break;
      case "call":
        if (!NovaVoice.callAvailable()) { reply = "La llamada de voz no está disponible: activa la voz y asegúrate de tener STT y TTS (ver /status)."; break; }
        userNode.remove(); NovaVoice.startCall().catch(fail); return;
      default:
        reply = "Comando desconocido: /" + cmd + ". Escribe /help para ver los disponibles.";
    }
    if (reply) {
      $("hero").hidden = true;
      renderMessage("assistant", reply);
    }
    stick = true; scrollChat();
    $("message").value = ""; syncSend();
  } catch (error) { userNode.remove(); fail(error); }
}
async function send(event) {
  event.preventDefault(); if (busy) return;
  const message = $("message").value.trim(); if (!message && !pending.length) return;
  if (message.startsWith("/")) { await runSlash(message); return; }
  NovaVoice.beforeSend();
  busy = true; $("send").disabled = true; $("agent").disabled = true; $("attach").disabled = true; $("microphone").disabled = true; $("call-btn").disabled = true;
  let thinking, userNode;
  try {
    if (!current) { const created = await api("POST", "/v1/sessions", {agent: $("agent").value || null}); current = created.session_id; }
    $("hero").hidden = true;
    userNode = renderMessage("user", message, pending.map(p => p.meta));
    thinking = el("div", "N.O.V.A. está pensando…", "thinking"); $("messages").append(thinking); scrollChat();
    const data = await api("POST", "/v1/sessions/" + current + "/chat", {message, model: $("model").value || null, attachments: pending.map(p => p.meta.id)});
    thinking.remove(); const node = renderMessage("assistant", data.reply);
    if (data.steps?.length) { const details = el("details", undefined, "message"); details.append(el("summary", "Actividad de herramientas")); for (const step of data.steps) details.append(el("p", (step.ok ? "✓ " : "— ") + step.tool + ": " + step.message)); $("messages").append(details); }
    renderSources(data.steps, node);
    $("message").value = ""; pending.forEach(p => p.url && URL.revokeObjectURL(p.url)); pending = []; renderAttachments();
    $("conversation-title").textContent = message.slice(0,70) || "Conversación con archivos";
    await refreshConversations(); scrollChat();
  } catch (error) { thinking?.remove(); userNode?.remove(); fail(error); }
  finally { busy = false; syncSend(); NovaVoice.updateVisibility(); $("agent").disabled = false; $("attach").disabled = false; $("message").focus(); }
}
function renderAttachments() {
  $("attachments").replaceChildren();
  pending.forEach((item,index) => {
    const card = el("div", undefined, "attachment");
    if (item.url) { const image = el("img"); image.src = item.url; image.alt = "Vista previa de " + item.meta.name; card.append(image); card.classList.add("has-image"); }
    card.append(el("span", item.meta.name)); const remove = button("×", () => { if (busy) return; if(item.url) URL.revokeObjectURL(item.url); pending.splice(index,1); renderAttachments(); }); remove.setAttribute("aria-label", "Quitar " + item.meta.name); card.append(remove); $("attachments").append(card);
  });
  syncSend();
}
function readFile(file) { return new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("No se ha podido leer el archivo.")); reader.readAsDataURL(file); }); }
async function attachFiles(files) {
  if (busy) return;
  for (const file of files) {
    if (pending.length >= 4) { toast("Puedes adjuntar hasta 4 archivos por mensaje."); break; }
    if (file.size > 8*1024*1024) { toast(file.name + " supera los 8 MB."); continue; }
    const meta = await api("POST", "/v1/desktop/library", {name:file.name, data:await readFile(file)});
    pending.push({meta, url:file.type.startsWith("image/") ? URL.createObjectURL(file) : null}); renderAttachments();
    if(meta.truncated) toast("Se analizará el inicio de " + meta.name + " (hasta 40.000 caracteres).");
  }
}
function showOnboarding() {
  const body = openPanel("Bienvenido a N.O.V.A.", "onboarding");
  body.append(el("div", "◇", "onboarding-mark"), el("p", "Tu ordenador ya está preparado. ¿Cómo quieres utilizar N.O.V.A.?"));
  let mode = "private"; const options = el("div", undefined, "mode-options");
  for (const [value,title,description] of [["private","Privado","Modelos y herramientas locales. Tus datos se quedan contigo."],["balanced","Equilibrado","Combina capacidades locales y externas cuando hayas conectado un servicio. Te indicaremos cuándo se usa."],["advanced","Avanzado","Control sobre modelos y permisos. Empieza con la misma privacidad local."]]) {
    const item = button("", () => { mode=value; options.querySelectorAll("button").forEach(b=>b.classList.remove("selected")); item.classList.add("selected"); }, "mode-option" + (value===mode?" selected":""));
    item.append(el("strong", title), el("small", description)); options.append(item);
  }
  body.append(options, button("Empezar a conversar", async () => { await api("PATCH", "/v1/desktop/preferences", {mode,onboarding_complete:true}); $("panel").close(); await health(); $("message").focus(); }, "primary"));
}
function showPreparation() {
  const body = openPanel("Prepara tu N.O.V.A.", "prepare");
  body.append(el("div", "N.O.V.A.", "onboarding-mark"), el("p", "Tu asistente personal de IA. Privado. Local. Tuyo."), el("p", "Comprobaremos tu equipo y prepararemos un modelo apropiado. La primera descarga necesita Internet y puede tardar varios minutos."));
  body.append(button("Preparar N.O.V.A.", () => startPreparation(), "primary"));
}
async function startPreparation(model) {
  await api("POST", "/v1/desktop/prepare", {model:model || null});
  openPanel("Preparando N.O.V.A.", "prepare"); await pollPreparation();
}
async function pollPreparation() {
  clearTimeout(prepareTimer);
  const result = await api("GET", "/v1/desktop/preparation");
  if (panelView !== "prepare") return;
  const body = $("panel-body"); body.replaceChildren(el("p", result.message, result.status==="error"?"error-text":""));
  for (const check of result.checks || []) { const row = el("div", undefined, "check"); row.append(el("span",check.ok?"✓":"○"),el("span",check.label)); body.append(row); }
  if (result.recommendation) body.append(el("h3", "Modelo recomendado: " + result.recommendation.name), el("p", "Equilibrio entre calidad, velocidad y los recursos de tu ordenador."));
  if (result.status === "running") {
    const progress = el("progress", undefined, "progress"); if (result.total > 0) { progress.max=result.total; progress.value=result.completed; body.append(el("p", (result.completed/1024/1024).toFixed(0)+" / "+(result.total/1024/1024).toFixed(0)+" MB")); } body.append(progress);
    const stages = {system:"Comprobando sistema",components:"Instalando componentes",engine:"Preparando motor local",model:"Descargando modelo",verify:"Verificando descarga",config:"Guardando configuración"}; body.append(el("p", stages[result.stage] || "Preparando…", "muted"));
    prepareTimer = setTimeout(() => pollPreparation().catch(fail),700);
  } else if (result.status === "error") { body.append(button("Reintentar",()=>startPreparation(result.recommendation?.name),"primary")); if(result.details){const details=el("details");details.append(el("summary","Ver detalles"),el("pre",result.details));body.append(details);} }
  else if(result.status === "ready") { await health(); await loadModels(); body.append(button("Abrir N.O.V.A.", () => { if(!status.onboarding_complete) showOnboarding(); else $("panel").close(); }, "primary")); }
}
async function showSettings() {
  await health(); const body=openPanel("Ajustes", "settings");
  const field=el("label",undefined,"field");field.append(el("span","Modelo para conversar"));const select=el("select");
  for(const option of $("model").options) if(option.value) select.append(new Option(option.text,option.value));select.value=status.model;field.append(select);body.append(field);
  const modeField=el("label",undefined,"field");modeField.append(el("span","Cómo usas N.O.V.A."));const mode=el("select");for(const [v,t] of [["private","Privado"],["balanced","Equilibrado"],["advanced","Avanzado"]]) mode.append(new Option(t,v));mode.value=status.mode;modeField.append(mode);body.append(modeField);
  body.append(el("p","Equilibrado solo usa servicios externos si ya los has conectado. Los adjuntos permanecen en el procesamiento local.","muted"));
  body.append(el("h3","Proveedores de IA"));
  const routing=status.routing||{},routeField=el("label",undefined,"field"),routeSelect=el("select");
  routeField.append(el("span","Política de selección"));for(const [v,t]of[["local","Solo local"],["balanced","Equilibrada"],["performance","Rendimiento"],["custom","Personalizada"]])routeSelect.append(new Option(t,v));routeSelect.value=routing.policy||"local";routeField.append(routeSelect);body.append(routeField);
  const providerRows=[];
  function addProviderRow(info={}){const box=el("div",undefined,"provider-card"),enabled=el("input"),name=el("input"),url=el("input"),modelInput=el("input"),env=el("input"),secret=el("input"),isLocal=info.location==="local";enabled.type="checkbox";enabled.checked=Boolean(info.enabled);enabled.disabled=isLocal;name.value=info.name||"";name.placeholder="Nombre del proveedor";name.disabled=isLocal||["openai","gemini","opencode"].includes(info.name);url.value=info.base_url||"";url.placeholder="Base URL";url.disabled=isLocal;modelInput.value=info.model||"";modelInput.placeholder="Modelo";modelInput.disabled=isLocal;env.value=info.api_key_env||"";env.placeholder="Variable de entorno (opcional)";secret.type="password";secret.placeholder=info.credential_configured?"Clave guardada; deja vacío para conservar":"API key (se guarda en el almacén seguro)";const title=el("label",undefined,"setting-row");title.append(el("span",isLocal?`${info.name} · local`:info.name||"Compatible con OpenAI"),enabled);box.append(title,name,url,modelInput);if(!isLocal&&info.name!=="opencode")box.append(env,secret);if(info.healthy)box.append(el("small","Disponible","muted"));body.append(box);providerRows.push({box,enabled,name,url,modelInput,env,secret,location:info.location||"cloud"});}
  for(const info of(status.ai_providers||[]))addProviderRow(info);
  body.append(button("Añadir endpoint compatible",()=>addProviderRow({name:"",location:"cloud"})));
  const cloudProviderField=el("label",undefined,"field"),cloudProviderSelect=el("select"),cloudModelField=el("label",undefined,"field"),cloudModel=el("input"),contextField=el("label",undefined,"field"),maximumContext=el("input");cloudProviderField.append(el("span","Proveedor cloud preferido"));cloudProviderSelect.append(new Option("Automático",""));for(const info of(status.ai_providers||[]))if(info.location!=="local")cloudProviderSelect.append(new Option(info.name,info.name));cloudProviderSelect.value=routing.preferred_cloud_provider||"";cloudProviderField.append(cloudProviderSelect);cloudModelField.append(el("span","Modelo cloud preferido"));cloudModel.value=routing.preferred_cloud_model||"";cloudModel.placeholder="Automático";cloudModelField.append(cloudModel);contextField.append(el("span","Contexto máximo (tokens)"));maximumContext.type="number";maximumContext.min="1024";maximumContext.value=routing.maximum_context||"";maximumContext.placeholder="Sin límite configurado";contextField.append(maximumContext);body.append(cloudProviderField,cloudModelField,contextField);
  const cloudFallback=el("input"),cloudConfirm=el("input"),neverCloud=el("input"),neverSensitive=el("input"),redact=el("input");for(const input of[cloudFallback,cloudConfirm,neverCloud,neverSensitive,redact])input.type="checkbox";cloudFallback.checked=Boolean(routing.allow_cloud_fallback);cloudConfirm.checked=Boolean(routing.require_cloud_confirmation);neverCloud.checked=Boolean(routing.never_send_data_to_cloud);neverSensitive.checked=routing.never_send_sensitive_data_to_cloud!==false;redact.checked=routing.redact_cloud_requests!==false;
  for(const [label,input]of[["Permitir fallback cloud",cloudFallback],["Confirmar antes de usar cloud",cloudConfirm],["Nunca enviar datos a cloud",neverCloud],["Nunca enviar datos sensibles a cloud",neverSensitive],["Redactar secretos antes de cloud (heurístico)",redact]]){const row=el("label",undefined,"setting-row");row.append(el("span",label),input);body.append(row);}
  body.append(el("p","La redacción detecta patrones comunes, pero no garantiza anonimización. «Nunca enviar datos a cloud» tiene precedencia absoluta.","muted"));
  const start=el("label",undefined,"setting-row"),check=el("input");check.type="checkbox";start.append(el("span","Iniciar con Windows"),check);body.append(start);let autostartChanged=false;check.onchange=()=>autostartChanged=true;
  try{ const data=await api("GET","/v1/setup/status");check.checked=Boolean(data.autostart);}catch(e){}
  body.append(button("Guardar preferencias",async()=>{const providers=providerRows.filter(row=>row.location!=="local"&&row.name.value.trim()).map(row=>({name:row.name.value.trim(),enabled:row.enabled.checked,base_url:row.url.value.trim(),model:row.modelInput.value.trim(),api_key:row.secret.value||null,api_key_env:row.env.value.trim(),location:row.location}));await api("PATCH","/v1/desktop/preferences",{mode:mode.value,model:select.value||null,autostart:autostartChanged?check.checked:null,providers,routing:{policy:routeSelect.value,preferred_local_model:select.value||"",preferred_cloud_provider:cloudProviderSelect.value,preferred_cloud_model:cloudModel.value.trim(),maximum_context:maximumContext.value?Number(maximumContext.value):null,allow_cloud_fallback:cloudFallback.checked,require_cloud_confirmation:cloudConfirm.checked,never_send_data_to_cloud:neverCloud.checked,never_send_sensitive_data_to_cloud:neverSensitive.checked,redact_cloud_requests:redact.checked}});await health();toast("Preferencias guardadas.");},"primary"));
  const links=el("div",undefined,"help-links");links.append(button("Modelos",showCatalog),button("Permisos",showPermissions),button("Diagnóstico",showStatus));body.append(links);
  const remote=el("details");remote.append(el("summary","Compartir acceso"));
  if(apiBase&&token){
    remote.append(el("p","Enlace para abrir tu N.O.V.A. desde otro dispositivo. Comparte solo con quien confíes."));
    const shareUrl=location.origin+location.pathname+"?api="+encodeURIComponent(apiBase)+"&token="+encodeURIComponent(token);
    const urlInput=el("input");urlInput.readOnly=true;urlInput.value=shareUrl;urlInput.className="field";urlInput.setAttribute("aria-label","Enlace de acceso");remote.append(urlInput);
    remote.append(button("Copiar enlace",()=>navigator.clipboard.writeText(shareUrl).then(()=>toast("Enlace copiado."))));
    try{
      const tunnel=await api("GET","/v1/desktop/tunnel");
      if(tunnel.active&&tunnel.url){
        const publicUrl=tunnel.url+"/?api="+encodeURIComponent(tunnel.url)+"&token="+encodeURIComponent(token);
        remote.append(el("p","Acceso remoto público activo. Este enlace funciona desde cualquier dispositivo con internet.","muted"));
        const publicInput=el("input");publicInput.readOnly=true;publicInput.value=publicUrl;publicInput.className="field";publicInput.setAttribute("aria-label","Enlace público");remote.append(publicInput);
        remote.append(button("Copiar enlace público",()=>navigator.clipboard.writeText(publicUrl).then(()=>toast("Enlace público copiado."))));
        remote.append(button("Desactivar acceso público",async()=>{await api("POST","/v1/desktop/tunnel",{active:false});await showSettings();}));
      }else{
        remote.append(el("p","Mientras tu ordenador esté encendido, puedes crear un enlace público que funcione desde cualquier lugar.","muted"));
        remote.append(button("Crear enlace público",async()=>{try{await api("POST","/v1/desktop/tunnel",{active:true});await showSettings();}catch(e){fail(e);}},"primary"));
      }
    }catch(e){
      remote.append(el("p","Túnel no disponible. Verifica tu conexión e inténtalo de nuevo.","muted"));
    }
  }else{
    remote.append(el("p","Conéctate primero a tu N.O.V.A. desde la sección «Conexión avanzada» para generar un enlace de acceso.","muted"));
  }
  body.append(remote);
  const advanced=el("details");advanced.append(el("summary","Conexión avanzada"));const url=el("input");url.value=apiBase;url.placeholder="Dirección de tu Core";url.setAttribute("aria-label","Dirección del Core");const key=el("input");key.type="password";key.value=token;key.placeholder="Clave de conexión";key.setAttribute("aria-label","Clave de conexión");const group=el("div",undefined,"field");group.append(url,key,button("Conectar",()=>{const value=url.value.trim().replace(/\/+$/,"");if(value){const parsed=new URL(value);if(!["https:","http:"].includes(parsed.protocol)||parsed.username||parsed.password)throw new Error("Usa una dirección HTTP local o HTTPS sin credenciales en la dirección.");}apiBase=value;token=key.value.trim();localStorage.setItem("nova.apiBase",apiBase);sessionStorage.setItem("nova.token",token);location.reload();}));advanced.append(group);body.append(advanced);
}
async function showPermissions() {
  await health();const body=openPanel("Permisos", "permissions");body.append(el("p","Tú decides qué puede hacer N.O.V.A. Los comandos y cambios de archivos necesitan confirmación; las acciones administrativas y remotas están bloqueadas."));
  const categories={...status.categories},labels={reading:"Leer archivos",writing:"Crear o modificar archivos",applications:"Abrir aplicaciones",commands:"Ejecutar comandos",system:"Administrar el sistema",remote:"Recibir órdenes remotas"};
  for(const [key,label] of Object.entries(labels)){const row=el("label",undefined,"setting-row"),select=el("select");for(const [v,t]of [["ask","Preguntar"],["deny","Bloquear"],["allow","Permitir"]])select.append(new Option(t,v));select.value=categories[key]||"ask";if(["system","remote"].includes(key)){select.value="deny";select.disabled=true;}select.onchange=()=>categories[key]=select.value;row.append(el("span",label),select);body.append(row);}
  const field=el("label",undefined,"field");field.append(el("span","Carpetas permitidas (una ubicación completa por línea)"));const roots=el("textarea");roots.rows=3;roots.value=(status.roots||[]).join("\n");field.append(roots);body.append(field,el("p","Sin carpetas concedidas, las herramientas no pueden leer ni escribir tus archivos. Adjuntar un archivo al chat solo comparte esa copia.","muted"));
  body.append(button("Guardar permisos",async()=>{await api("PATCH","/v1/desktop/preferences",{categories:{categories},roots:roots.value.split("\n").map(x=>x.trim()).filter(Boolean)});toast("Permisos guardados. Las carpetas se aplican a nuevas conversaciones.");},"primary"));
}
async function showCatalog(){const body=openPanel("Modelos locales","models");body.append(el("p","Añade capacidades cuando las necesites. Solo podrás descargar modelos que encajen en los recursos disponibles."));const rows=await api("GET","/v1/desktop/catalog");for(const model of rows){const row=el("div",undefined,"list-row"),text=el("span",model.name);text.append(el("small",({general:"Conversación",vision:"Imágenes",coding:"Código",reasoning:"Razonamiento"}[model.role]||model.role)+" · "+model.size_gb+" GB"));const action=button(model.installed?"Instalado":model.compatible?"Descargar":"Sin recursos suficientes",()=>startPreparation(model.name));action.disabled=model.installed||!model.compatible;row.append(text,action);body.append(row);}}
async function showLibrary(){const body=openPanel("Biblioteca","library");body.append(el("p","Archivos que has compartido con N.O.V.A. Sus copias se guardan en este ordenador."));const rows=await api("GET","/v1/desktop/library");if(!rows.length)body.append(el("p","Adjunta tu primer archivo desde el chat.","muted"));for(const file of rows){const row=el("div",undefined,"list-row"),text=el("span",file.name);text.append(el("small",(file.size/1024).toFixed(0)+" KB · "+(file.kind==="image"?"Imagen":"Documento")));const actions=el("div",undefined,"actions");actions.style.margin="0";actions.append(button("Usar",()=>{if(pending.length>=4)throw new Error("Puedes adjuntar hasta 4 archivos.");pending.push({meta:file});renderAttachments();$("panel").close();}),button("Eliminar",async()=>{if(await confirmAction("¿Eliminar archivo?","Se borrará la copia de la biblioteca. El texto ya compartido seguirá en sus conversaciones hasta que las elimines.")){await api("DELETE","/v1/desktop/library/"+file.id);await showLibrary();}}));row.append(text,actions);body.append(row);}}
async function showMemory(){const body=openPanel("Memoria","memory");body.append(el("p","Lo que N.O.V.A. recuerda para ayudarte. Puedes eliminar cualquier recuerdo."));const rows=await api("GET","/v1/desktop/memory");if(!rows.length)body.append(el("p","Todavía no hay recuerdos guardados. Usa el modo Con herramientas y pide a N.O.V.A. que recuerde una preferencia.","muted"));for(const memory of rows){const row=el("div",undefined,"list-row");row.append(el("span",memory.content),button("Olvidar",async()=>{if(await confirmAction("¿Olvidar este recuerdo?",memory.content)){await api("DELETE","/v1/desktop/memory/"+memory.id);await showMemory();}}));body.append(row);}}
async function showTools(){const body=openPanel("Herramientas","tools");body.append(el("p","Activa Con herramientas en el chat para pedir acciones. Cada acción está sujeta a tus permisos."));const rows=await api("GET","/v1/tools");for(const tool of rows){const row=el("div",undefined,"list-row"),text=el("span",tool.name);text.append(el("small",tool.description));row.append(text);body.append(row);}body.append(button("Administrar permisos",showPermissions));const auto=await api("GET","/v1/automation");if(auto.workflows?.length){body.append(el("h3","Tareas guardadas"));for(const workflow of auto.workflows)body.append(button(workflow.name,async()=>{if(await confirmAction("¿Ejecutar tarea?",workflow.description||workflow.name)){const result=await api("POST","/v1/automation/workflows/"+encodeURIComponent(workflow.name)+"/run");toast(result.ok?"Tarea completada.":"La tarea no pudo completar todos los pasos.");}}));}}
async function showStatus(){const body=openPanel("Estado de N.O.V.A.","status");try{await health();body.append(el("p",status.paused?"N.O.V.A. está en pausa.":"Tu núcleo local está conectado."),el("p","Versión "+status.version+" · "+status.model,"muted"),button(status.paused?"Reanudar":"Pausar",async()=>{await api("POST","/v1/desktop/pause",{paused:!status.paused});await showStatus();}),button("Comprobar y reparar",showPreparation));const details=el("details");details.append(el("summary","Diagnóstico técnico"));const pre=el("pre","Cargando…");details.append(pre);details.ontoggle=async()=>{if(details.open){try{pre.textContent=JSON.stringify(await api("GET","/v1/setup/status"),null,2);}catch(e){pre.textContent=e.message;}}};body.append(details);}catch(error){body.append(el("p",error.message),button("Reintentar",showStatus),button("Configurar conexión",showConnection));}}
let corePollTimer = null;
function startCoreTap() {
  const statusLine = $("core-status");
  const frame = document.createElement("iframe"); frame.style.display = "none"; frame.setAttribute("aria-hidden", "true"); frame.src = "nova://start"; document.body.append(frame); setTimeout(() => frame.remove(), 2000);
  statusLine.textContent = "Iniciando el servidor en tu ordenador…"; statusLine.hidden = false;
  clearInterval(corePollTimer);
  const deadline = Date.now() + 45000;
  corePollTimer = setInterval(async () => {
    const base = await probeLocalCore();
    if (base) {
      clearInterval(corePollTimer);
      apiBase = base; localStorage.setItem("nova.apiBase", apiBase);
      statusLine.textContent = "Conectado."; try { $("panel").close(); await afterConnect(); } catch (e) { fail(e); }
      return;
    }
    if (Date.now() > deadline) {
      clearInterval(corePollTimer);
      statusLine.textContent = "No ha sido posible encenderlo. Si no ocurre nada, ejecuta «nova-setup protocol --enable 1» en tu ordenador y vuelve a intentarlo.";
    }
  }, 1500);
}
function showConnection(){
  const body = openPanel("Conecta tu N.O.V.A.","connection");
  body.append(el("p","N.O.V.A. vive en tu ordenador: modelos, memoria y archivos son locales. Esta web se conecta a él automáticamente cuando está encendido."));
  body.append(button("Encender N.O.V.A. en este equipo", startCoreTap, "primary"));
  const statusLine = el("p","", "muted"); statusLine.id = "core-status"; statusLine.hidden = true; body.append(statusLine);
  body.append(el("hr", undefined, "divider"));
  body.append(el("p","¿Te pasaron un enlace de acceso? Pégalo aquí para entrar."));
  const invite=el("div",undefined,"field"),inviteUrl=el("input"),inviteBtn=button("Entrar",()=>{try{const parsed=new URL(inviteUrl.value.trim());const params=new URLSearchParams(parsed.search);const base=params.get("api"),inviteToken=params.get("token");if(!base||!inviteToken)throw new Error("Este enlace no incluye dirección ni clave de acceso completa.");localStorage.setItem("nova.apiBase",base.replace(/\/+$/,""));sessionStorage.setItem("nova.token",inviteToken);location.reload();}catch(e){fail(e);}},"primary");inviteUrl.placeholder="Pega aquí el enlace de acceso completo";inviteUrl.setAttribute("aria-label","Enlace de acceso");invite.append(inviteUrl,inviteBtn);body.append(invite);
  body.append(el("hr", undefined, "divider"));
  body.append(el("p","Configuración avanzada: conoce la dirección y la clave de tu Core.","muted"));
  const group=el("div",undefined,"field"),url=el("input"),key=el("input");url.placeholder="Dirección del Core (configuración avanzada)";url.value=apiBase;url.setAttribute("aria-label","Dirección del Core");key.type="password";key.placeholder="Clave de conexión";key.setAttribute("aria-label","Clave de conexión");group.append(url,key,button("Conectar",()=>{const parsed=new URL(url.value);if(!["http:","https:"].includes(parsed.protocol)||parsed.username||parsed.password)throw new Error("Dirección no válida.");localStorage.setItem("nova.apiBase",url.value.replace(/\/+$/,""));sessionStorage.setItem("nova.token",key.value.trim());location.reload();}));body.append(group);}
async function checkApprovals(){try{if(!status?.desktop)return;const data=await api("GET","/v1/desktop/permissions"),row=data.pending[0];if(!row){if($("approval").open)$("approval").close();approvalId=null;return;}if(approvalId===row.id)return;approvalId=row.id;$("approval-description").textContent="N.O.V.A. solicita usar: "+row.tool;$("approval-args").textContent=JSON.stringify(row.args,null,2);$("approval-always").hidden=!row.remember_allowed;$("approval-always").textContent="Recordar durante esta sesión";$("approval").showModal();}catch(e){}}
async function resolveApproval(decision){if(!approvalId)return;await api("POST","/v1/desktop/permissions/"+approvalId,{decision});$("approval").close();approvalId=null;}
window.novaNavigate=async view=>{const routes={new:newConversation,settings:showSettings,library:showLibrary,memory:showMemory,tools:showTools,status:showStatus};try{await(routes[view]||newConversation)();}catch(e){fail(e);}};
$("compose").onsubmit = send;
$("new").onclick = newConversation;
$("agent").onchange = newConversation;
$("menu").onclick = () => { const open = $("sidebar").classList.toggle("visible"); $("scrim").hidden = !open; };
$("scrim").onclick = closeMobileSidebar;
$("collapse").onclick = toggleSidebarCollapse;
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("sidebar").classList.contains("visible")) closeMobileSidebar(); });
messagesEl.addEventListener("scroll", () => {
  const near = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 100;
  $("scroll-to-bottom").hidden = near;
  stick = near;
}, {passive: true});
$("scroll-to-bottom").onclick = () => { stick = true; messagesEl.scrollTo({top: messagesEl.scrollHeight, behavior: "smooth"}); $("scroll-to-bottom").hidden = true; };
document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>window.novaNavigate(b.dataset.view));
$("panel-close").onclick=()=>$("panel").close();$("panel").addEventListener("close",()=>{panelView="";clearTimeout(prepareTimer);});
$("message").addEventListener("keydown",e=>{
  const box=$("slash-suggest");
  if(!box.hidden){
    const rows=[...box.querySelectorAll(".suggest")];
    if(e.key==="ArrowDown"){e.preventDefault();slashIndex=(slashIndex+1)%rows.length;highlightSuggest(rows);return;}
    if(e.key==="ArrowUp"){e.preventDefault();slashIndex=(slashIndex-1+rows.length)%rows.length;highlightSuggest(rows);return;}
    if(e.key==="Enter"||e.key==="Tab"){e.preventDefault();if(slashIndex<0&&rows.length===1)slashIndex=0;if(slashIndex>=0&&rows[slashIndex])rows[slashIndex].click();else box.hidden=true;return;}
    if(e.key==="Escape"){box.hidden=true;slashIndex=-1;return;}
  }
  if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();$("compose").requestSubmit();}
});
$("message").addEventListener("input",()=>{$("message").style.height="auto";$("message").style.height=Math.min($("message").scrollHeight,200)+"px";syncSend();slashSuggest();});
$("message").addEventListener("blur",()=>setTimeout(()=>{const box=$("slash-suggest");if(!box.contains(document.activeElement))box.hidden=true;},120));
$("attach").onclick=()=>$("files").click();$("files").onchange=e=>{attachFiles([...e.target.files]).catch(fail);e.target.value="";};
$("suggest-image").onclick=()=>$("files").click();
$("suggest-file").onclick=()=>$("files").click();
$("suggest-task").onclick=()=>{$("agent").value="general";newConversation();$("message").value="Ayúdame con una tarea en mi ordenador: ";syncSend();$("message").focus();};
document.querySelectorAll("[data-suggest]").forEach(b=>b.onclick=()=>{$("message").value=b.dataset.suggest;syncSend();$("message").focus();});
document.addEventListener("paste",e=>{const files=[...(e.clipboardData?.files||[])];if(files.length){e.preventDefault();attachFiles(files).catch(fail);}});
let dragDepth=0;document.addEventListener("dragenter",e=>{if(e.dataTransfer?.types.includes("Files")){e.preventDefault();dragDepth++;$("drop-hint").hidden=false;}});document.addEventListener("dragover",e=>e.preventDefault());document.addEventListener("dragleave",()=>{dragDepth--;if(dragDepth<=0)$("drop-hint").hidden=true;});document.addEventListener("drop",e=>{e.preventDefault();dragDepth=0;$("drop-hint").hidden=true;attachFiles([...e.dataTransfer.files]).catch(fail);});
$("approval-once").onclick=()=>resolveApproval("once").catch(fail);$("approval-always").onclick=()=>resolveApproval("always").catch(fail);$("approval-cancel").onclick=()=>resolveApproval("cancel").catch(fail);$("approval").oncancel=e=>{e.preventDefault();resolveApproval("cancel").catch(fail);};
async function afterConnect() {
  try { await health(); await refreshVoice(); await refreshConversations(); await loadModels().catch(()=>{}); syncSend(); if(!status.prepared)showPreparation(); else if(!status.onboarding_complete)showOnboarding(); }
  catch(e){ $("connection-status").textContent="Sin conexión"; $("dot").className="dot bad"; $("hero-description").textContent="Abre N.O.V.A. en tu ordenador para empezar."; if(!token)showConnection(); else fail(e); }
}
async function boot(){
  if (localStorage.getItem("nova.sidebarCollapsed") === "1") $("app").classList.add("sidebar-collapsed");
  if (!isLocalPage && !apiBase) {
    apiBase = await probeLocalCore() || "";
    if (!apiBase) { $("connection-status").textContent="Conecta tu N.O.V.A."; showConnection(); return; }
    localStorage.setItem("nova.apiBase", apiBase);
  }
  await afterConnect(); }
setInterval(()=>{if(status)health().catch(()=>{$("connection-status").textContent="Reconectando…";});},15000);setInterval(checkApprovals,1000);boot();
