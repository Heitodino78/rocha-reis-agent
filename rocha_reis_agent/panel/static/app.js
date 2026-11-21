// Painel de atendimento Rocha Reis — sem build, sem dependências externas.
// Segurança: todo conteúdo vindo do servidor é inserido com textContent (nunca innerHTML);
// o token CSRF fica só em memória; a autorização real é sempre do backend.

const API = "/api/panel";
const STATE_LABEL = {
  AUTO: "Automático", WAITING_HUMAN: "Aguardando atendimento", HUMAN_ACTIVE: "Em atendimento humano",
  RESOLVED: "Encerrado", CLOSED: "Sessão fechada",
};
const DELIVERY_LABEL = {
  pending: "Na fila de envio", sending: "Enviando", accepted: "Aceita pela plataforma (envio simulado)",
  sent: "Enviada", delivered: "Entregue", read: "Lida", failed: "Falhou", unknown: "Resultado incerto",
  cancelled: "Não enviada",
};
const FILTER_TITLE = {
  all: "Conversas", waiting: "Aguardando atendimento", mine: "Meus atendimentos", human_active: "Em atendimento",
  auto: "Automáticas", resolved: "Encerradas", others: "Com outros funcionários",
};
// Motivos técnicos traduzidos (nunca texto do provedor ou do cliente).
const OUTCOME_LABEL = {
  fake_rejected: "recusada pela plataforma (simulação)", fake_timeout: "sem confirmação da plataforma (simulação)",
  fake_ambiguous: "resposta ambígua da plataforma (simulação)", fake_throttle: "limite da plataforma (simulação)",
  conversation_deleted: "dados do cliente excluídos", conversation_missing: "conversa não encontrada",
  conversation_state_changed: "situação da conversa mudou antes do envio", insufficient_evidence: "sem mensagem do cliente que permita responder",
  outside_service_window: "janela de 24h encerrada", lease_expired_during_send: "envio interrompido no meio",
  lease_expired_max_attempts: "tentativas esgotadas",
};
const NOTIF_OPTIONAL = ["human_requested", "assigned_to_me", "new_message", "retired_contact"];
const STORAGE = {  // somente conveniências deste navegador; nada aqui é fonte de verdade
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* modo privado */ } },
};
const ROLE_LABEL = { client: "Cliente", assistant: "Assistente automático", operator: "Equipe" };

const state = {
  user: null, csrf: "", permissions: {}, view: "conversations", filter: "waiting", query: "",
  items: [], cursor: null, selected: null, detail: null, operators: [], events: null,
  draftKey: null, sending: false, auditBefore: null, older: [], composerFor: null, drafts: {},
  environmentLabel: null,
  notif: { items: [], announced: new Set(), read: new Set(), prefs: { sound: false, off: [] }, open: false },
  knowledge: { records: [], labels: {}, kinds: [], editing: null },
};

const $ = (id) => document.getElementById(id);

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
  for (const [key, value] of Object.entries(options.attrs || {})) node.setAttribute(key, value);
  for (const child of children) if (child) node.append(child);
  return node;
}

function announce(message) { $("live").textContent = message; }

const TOAST_LIMIT = 3;  // Fase 16: nunca mais que 3 avisos flutuantes cobrindo a tela
function toast(message, kind = "info") {
  const node = el("div", { className: `toast ${kind}`, text: message });
  const host = $("toasts");
  host.append(node);
  while (host.children.length > TOAST_LIMIT) host.firstElementChild.remove();
  setTimeout(() => node.remove(), 5000);
}

function initials(name) {
  const words = String(name || "").trim().split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w));
  if (!words.length) return "?";
  const first = [...words[0]].find((c) => /[\p{L}\p{N}]/u.test(c)) || "";
  const last = words.length > 1 ? ([...words[words.length - 1]].find((c) => /[\p{L}\p{N}]/u.test(c)) || "") : "";
  return (first + last).toUpperCase();
}

function pill(text, tone = "") { return el("span", { className: `cell-pill${tone ? ` ${tone}` : ""}`, text }); }

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay ? date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function newKey() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------- API
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = state.csrf; }
  let response;
  try {
    response = await fetch(API + path, { method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Sem conexão com o servidor local.");
  }
  if (response.status === 401 && !path.startsWith("/auth/login") && state.user) {
    sessionEnded();
  }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (response.status === 403 && data && typeof data.detail === "string" && data.detail.includes("duas etapas") && state.user) {
    showMfaSetupRequired();  // política do ambiente: cadastro obrigatório antes de qualquer outra coisa
  }
  if (!response.ok) {
    // Só mensagens do servidor em texto; nunca listas/objetos técnicos.
    const detail = data && typeof data.detail === "string" ? data.detail : null;
    const fallback = response.status >= 500
      ? "Ocorreu um erro inesperado no servidor local. Tente de novo; se continuar, avise o suporte."
      : "Não foi possível concluir a ação.";
    throw new ApiError(response.status, response.status >= 500 ? fallback : (detail || fallback));
  }
  return data;
}

// ------------------------------------------------------------- sessões
function showOnly(viewId) {
  for (const id of ["login-view", "password-view", "mfa-view", "mfa-setup-view", "app-view"]) $(id).hidden = id !== viewId;
}

function sessionEnded() {
  saveDraft();  // o rascunho e a conversa aberta sobrevivem à expiração da sessão
  const kept = Object.keys(state.drafts).length ? " Seu rascunho foi mantido." : "";
  showLogin(`Sua sessão terminou. Entre novamente.${kept}`);
}

function showLogin(message = "") {
  stopEvents();
  clearInterval(notifTimer);
  closeNotifications();
  state.user = null; state.csrf = "";
  showOnly("login-view");
  $("login-error").textContent = message;
  $("password-input").value = "";
  $("login-input").focus();
}

let sessionGeneration = 0;  // cada sessão aplicada recebe um número; verificações atrasadas não a afetam

function applySession(data) {
  sessionGeneration += 1;
  state.user = data.user; state.csrf = data.csrf_token; state.permissions = data.permissions || {};
  if (state.user.must_change_password) {
    showOnly("password-view");
    $("current-password").focus();
    return;
  }
  state.mfa = data.mfa || {};
  $("release-info").textContent = data.release ? `Versão: ${data.release}. Não é versão de produção.` : "";
  if (state.mfa.required && !state.mfa.enabled) { showMfaSetupRequired(); return; }
  showOnly("app-view");
  document.body.classList.toggle("is-admin", state.user.role === "ADMIN");
  for (const node of document.querySelectorAll(".admin-only")) node.hidden = state.user.role !== "ADMIN";
  $("user-name").textContent = state.user.display_name;
  $("user-avatar").textContent = initials(state.user.display_name);
  $("user-role").textContent = state.user.role === "ADMIN" ? "Admin" : "Atendente";
  state.environmentLabel = data.environment_label || null;
  $("env-banner").hidden = !state.environmentLabel;
  $("env-banner").textContent = state.environmentLabel ? `${state.environmentLabel} · dados fictícios · nenhuma mensagem real é enviada` : "";
  loadNotifPrefs();
  state.notif.synced = false;  // primeira leitura dos avisos desta sessão: resumir, não inundar a tela
  startEvents();
  const reopen = state.selected;
  navigate(state.view, state.filter);
  if (reopen && state.view === "conversations") openConversation(reopen, { focus: false });
  loadNotifications();
  clearInterval(notifTimer);
  notifTimer = setInterval(loadNotifications, 30000);  // interrupções e falhas de entrada não passam pelo feed
}

async function boot() {
  loadDrafts();
  bindUi();
  try { applySession(await api("/auth/session")); }
  catch { showLogin(); }
}

// ------------------------------------------------------------ navegação
function navigate(view, filter) {
  state.view = view;
  if (filter) state.filter = filter;
  for (const id of ["overview-view", "conversations-view", "team-view", "audit-view", "settings-view", "knowledge-view", "system-view", "help-view", "account-view"]) {
    $(id).hidden = id !== `${view}-view`;
  }
  for (const item of document.querySelectorAll(".nav-item")) {
    const active = item.dataset.view === view && (view !== "conversations" || item.dataset.filter === state.filter);
    if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  }
  closeMenu();
  if (view === "conversations") { refreshList(true); }
  if (view === "overview") loadOverview();
  if (view === "team") loadTeam();
  if (view === "audit") { state.auditBefore = null; $("audit-body").replaceChildren(); loadAudit(); }
  if (view === "settings") loadSettings();
  if (view === "knowledge") loadKnowledge();
  if (view === "system") loadSystem();
  if (view === "account") loadAccount();
  refreshCounts();
}

function setMenu(open) {
  $("sidebar").classList.toggle("open", open);
  $("menu-toggle").setAttribute("aria-expanded", String(open));
  $("scrim").hidden = !open;  // Fase 16: fundo escurecido; tocar fora fecha a gaveta (antes ficava aberta sobre a página)
}
function closeMenu() { setMenu(false); }

// -------------------------------------------------------------- conversas
let listTimer = null;
function scheduleListRefresh() { clearTimeout(listTimer); listTimer = setTimeout(() => refreshList(true), 150); }

async function refreshList(reset) {
  if (state.view !== "conversations") return;
  $("list-title").textContent = FILTER_TITLE[state.filter] || "Conversas";
  for (const chip of document.querySelectorAll(".chip")) chip.setAttribute("aria-pressed", String(chip.dataset.filter === state.filter));
  const params = new URLSearchParams({ filter: state.filter });
  if (state.query) params.set("q", state.query);
  if (!reset && state.cursor) params.set("cursor", state.cursor);
  const list = $("conversation-list");
  list.setAttribute("aria-busy", "true");
  if (!state.items.length) { $("list-loading").hidden = false; $("list-empty").hidden = true; }
  try {
    const page = await api(`/conversations?${params}`);
    state.items = reset ? page.items : state.items.concat(page.items);
    state.cursor = page.next_cursor;
    $("list-error").hidden = true;
    renderList();
  } catch (error) {
    if (error.status !== 401) {
      $("list-error-text").textContent = error.status === 0 ? "Sem conexão com o servidor local. Os dados exibidos podem estar desatualizados."
        : error.message;
      $("list-error").hidden = false;
    }
  } finally {
    list.setAttribute("aria-busy", "false"); $("list-loading").hidden = true;
  }
}

function waitedMinutes(iso) {
  const start = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(start) ? null : Math.max(0, Math.floor((Date.now() - start) / 60000));
}

function renderList() {
  const list = $("conversation-list");
  list.replaceChildren(...state.items.map(renderItem));
  $("list-empty").hidden = state.items.length > 0;
  $("load-more").hidden = !state.cursor;
}

function renderItem(item) {
  const stateKey = item.closed ? "CLOSED" : item.state;
  const button = el("button", { className: `conv${item.needs_human ? " needs-human" : ""}`, attrs: { type: "button" } }, [
    el("span", { className: "conv-avatar", text: initials(item.label), attrs: { "aria-hidden": "true" } }),
    el("span", { className: "conv-name", text: item.label }),
    el("span", { className: "conv-time", text: formatTime(item.last_message ? item.last_message.at : item.updated_at) }),
    el("span", { className: "conv-preview", text: item.last_client_text
      || (item.last_message ? `${ROLE_LABEL[item.last_message.role]}: ${item.last_message.text}` : "Sem mensagens") }),
    el("span", { className: "conv-tags" }, [
      el("span", { className: `badge ${stateKey}`, text: STATE_LABEL[stateKey] }),
      item.assigned ? el("span", { className: "tag", text: `Com ${item.assigned.name}` }) : null,
      item.urgent ? el("span", { className: "urgent-badge", text: `Urgente · ${waitedMinutes(item.waiting_since)} min` }) : null,
      item.needs_human && !item.urgent && item.waiting_since
        ? el("span", { className: "wait-time muted small", text: `espera ${waitedMinutes(item.waiting_since)} min` }) : null,
      item.last_send_status === "FAILED" || item.last_send_status === "UNKNOWN"
        ? el("span", { className: "send-alert", text: item.last_send_status === "FAILED" ? "Falha de envio" : "Envio incerto" }) : null,
      item.unread ? el("span", { className: "unread", text: String(item.unread),
        attrs: { "aria-label": `${item.unread} mensagens não lidas` } }) : null,
    ]),
  ]);
  if (item.urgent) button.classList.add("urgent");
  if (item.id === state.selected) button.setAttribute("aria-current", "true");
  button.addEventListener("click", () => openConversation(item.id));
  return el("li", {}, [button]);
}

async function openConversation(id, { focus = true } = {}) {
  if (id !== state.selected) { saveDraft(); state.older = []; delete $("messages").dataset.rendered; }
  state.selected = id;
  try {
    state.detail = await api(`/conversations/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error.status === 404) { closeDetail(); toast("Esta conversa não está mais disponível para você."); scheduleListRefresh(); return; }
    if (error.status !== 401) toast(error.message, "error");
    return;
  }
  // Páginas antigas já carregadas continuam visíveis após atualizações ao vivo.
  const first = state.detail.messages.length ? state.detail.messages[0].ordinal : Infinity;
  state.older = state.detail.has_more ? state.older.filter((m) => m.ordinal < first) : [];
  renderDetail();
  $("conversations-view").classList.add("showing-detail");
  // Carregamento assíncrono não "rouba" o foco de um painel aberto nesse meio-tempo.
  if (focus && !state.notif.open && !$("confirm-dialog").open) $("detail-title").focus?.();
  api(`/conversations/${encodeURIComponent(id)}/read`, { method: "POST", body: {} }).then(scheduleListRefresh).catch(() => {});
  for (const node of document.querySelectorAll(".conv")) node.removeAttribute("aria-current");
}

async function loadOlder() {
  const d = state.detail;
  if (!d) return;
  const before = state.older.length ? state.older[0].ordinal : d.before;
  if (before === null || before === undefined) return;
  const button = $("load-older"); if (button) { button.disabled = true; button.textContent = "Carregando…"; }
  try {
    const page = await api(`/conversations/${encodeURIComponent(state.selected)}/messages?before=${before}`);
    state.older = page.messages.concat(state.older);
    d.older_has_more = page.has_more;
    const list = $("messages"), height = list.scrollHeight;
    renderDetail();
    list.scrollTop = list.scrollHeight - height;  // mantém a posição de leitura
  } catch (error) { if (error.status !== 401) toast(error.message, "error"); }
}

function closeDetail() {
  saveDraft();
  state.selected = null; state.detail = null; state.older = [];
  $("detail").hidden = true; $("detail-empty").hidden = false;
  $("conversations-view").classList.remove("showing-detail");
}

function renderDetail() {
  const d = state.detail;
  if (!d) return;
  const me = state.user;
  const stateKey = d.closed ? "CLOSED" : d.state;
  const mine = d.assigned && d.assigned.id === me.id;
  const admin = me.role === "ADMIN";
  $("detail-empty").hidden = true; $("detail").hidden = false;
  $("detail-title").textContent = d.label;
  $("detail-title").setAttribute("tabindex", "-1");
  const badge = $("detail-state"); badge.className = `badge ${stateKey}`; badge.textContent = STATE_LABEL[stateKey];
  $("detail-automation").textContent = d.state === "AUTO" && !d.closed ? "· Automação ativa" : "· Automação suspensa";
  $("detail-owner").textContent = d.assigned ? `Responsável: ${d.assigned.name}` : "Sem responsável";

  const actions = [];
  const add = (label, handler, kind = "btn-ghost") => {
    const b = el("button", { className: `btn ${kind}`, text: label, attrs: { type: "button" } });
    b.addEventListener("click", handler); actions.push(b);
  };
  if (d.state === "WAITING_HUMAN" && !d.closed) add("Assumir atendimento", () => act("assume", "Atendimento assumido."), "btn-primary");
  if (d.state === "HUMAN_ACTIVE" && (mine || admin) && (admin || state.permissions.operators_can_transfer)) add("Transferir", transferDialog);
  if ((d.state === "HUMAN_ACTIVE" && (mine || admin)) || (d.state === "WAITING_HUMAN" && admin)) {
    add("Finalizar atendimento", () => confirmAction("Finalizar atendimento?",
      "O atendimento será encerrado. A automação continuará suspensa até uma retomada explícita.",
      () => act("resolve", "Atendimento finalizado.")));
  }
  if (d.state === "RESOLVED" && !d.closed && (admin || (mine && state.permissions.operators_can_resume))) {
    add("Retomar automação", () => confirmAction("Retomar a automação?",
      "O assistente automático voltará a responder este cliente.", () => act("resume", "Automação retomada.")));
  }
  if (d.state === "AUTO" && !d.closed && admin) {
    add("Enviar para a fila humana", () => confirmAction("Transferir para atendimento humano?",
      "A automação será suspensa e a conversa entrará na fila.", () => act("request-human", "Conversa enviada para a fila.")));
  }
  $("detail-actions").replaceChildren(...actions);

  const all = state.older.concat(d.messages);
  const items = all.map((m) => {
    const author = m.role === "operator" && m.author ? `${ROLE_LABEL.operator} · ${m.author}` : ROLE_LABEL[m.role];
    const foot = [el("time", { text: formatTime(m.at), attrs: { datetime: m.at } })];
    if (m.delivery) {
      const reason = m.delivery.outcome ? ` — ${OUTCOME_LABEL[m.delivery.outcome] || "motivo técnico registrado"}` : "";
      foot.push(el("span", { className: `delivery ${m.delivery.key}`,
        text: `${DELIVERY_LABEL[m.delivery.key] || m.delivery.key}${reason}` }));
    }
    return el("li", { className: `msg ${m.role}` }, [
      el("div", { className: "msg-author", text: author }),
      el("p", { className: "msg-text", text: m.text }),
      el("div", { className: "msg-foot" }, foot),
    ]);
  });
  const moreOlder = state.older.length ? Boolean(d.older_has_more) : Boolean(d.has_more);
  if (moreOlder) {
    const older = el("button", { className: "btn btn-ghost btn-small", text: "Carregar mensagens anteriores", attrs: { type: "button", id: "load-older" } });
    older.addEventListener("click", loadOlder);
    items.unshift(el("li", { className: "older" }, [older]));
  }
  const operatorStates = all.filter((m) => m.role === "operator" && m.delivery).map((m) => m.delivery.key);
  const alert = $("detail-alert");
  if (operatorStates.includes("unknown")) {
    alert.textContent = "Uma resposta tem resultado INCERTO: ela pode ter chegado ao cliente. Confira antes de escrever de novo — o sistema nunca reenvia sozinho.";
  } else if (operatorStates.includes("failed")) {
    alert.textContent = "Uma resposta NÃO foi enviada (falha). Se ainda for necessária, escreva-a novamente.";
  } else alert.textContent = "";
  alert.hidden = !alert.textContent;
  const list = $("messages");
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  list.replaceChildren(...(items.length ? items : [el("li", { className: "empty", text: "Sem mensagens registradas." })]));
  if (atBottom || !list.dataset.rendered) list.scrollTop = list.scrollHeight;
  list.dataset.rendered = "1";

  $("transitions").replaceChildren(...d.transitions.map((t) => el("li", {
    text: `${formatTime(t.recorded_at)} — ${STATE_LABEL[t.previous]} → ${STATE_LABEL[t.current]} (${t.action}${t.actor ? `, ${t.actor}` : ""})`,
  })));

  const canReply = d.state === "HUMAN_ACTIVE" && !d.closed && mine;
  const windowEnd = d.window.last_inbound_at ? new Date(d.window.last_inbound_at).getTime() + d.window.seconds * 1000 : 0;
  const windowOpen = windowEnd > Date.now();
  let hint = "";
  const canResume = admin || (mine && state.permissions.operators_can_resume);
  if (!canReply) {
    hint = {
      WAITING_HUMAN: "Assuma o atendimento para responder.",
      HUMAN_ACTIVE: `Somente o responsável${d.assigned ? ` (${d.assigned.name})` : ""} pode responder.`,
      AUTO: "O assistente automático está atendendo esta conversa."
        + (admin ? " Para responder, envie-a para a fila humana." : " Se precisar intervir, peça à administração."),
      RESOLVED: "Atendimento encerrado. A automação continua desligada para este cliente; "
        + (canResume ? "você pode retomá-la pelo botão acima." : "somente a administração pode retomá-la."),
    }[d.state] || "Não é possível responder nesta situação.";
    if (d.closed) hint = "Sessão fechada: não é possível responder.";
  }
  else if (!d.window.last_inbound_at) hint = "Sem mensagem do cliente pelo WhatsApp: não é possível responder por este canal.";
  else if (!windowOpen) hint = "A janela de 24h terminou. Respostas livres não são permitidas.";
  else hint = `Janela de atendimento aberta até ${formatTime(new Date(windowEnd).toISOString())}. Envio simulado (nenhuma mensagem real).`;
  $("composer-hint").textContent = hint;
  if (state.composerFor !== state.selected) {  // cada conversa tem seu próprio rascunho
    state.composerFor = state.selected;
    $("composer-input").value = state.drafts[state.selected] || "";
    state.draftKey = null; updateCount();
  }
  const enabled = canReply && windowOpen && !state.sending;
  $("composer-input").disabled = !(canReply && windowOpen);
  $("composer-send").disabled = !enabled;
}

async function act(action, success) {
  const id = state.selected;
  if (!id) return;
  for (const b of $("detail-actions").querySelectorAll("button")) b.disabled = true;
  try {
    await api(`/conversations/${encodeURIComponent(id)}/${action}`, { method: "POST", body: {} });
    toast(success); announce(success);
  } catch (error) { toast(error.message, "error"); }
  await openConversation(id, { focus: false });
  scheduleListRefresh(); refreshCounts();
}

function confirmAction(title, text, onConfirm, extra = null) {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-extra").replaceChildren(...(extra ? [extra] : []));
  dialog.returnValue = "";
  dialog.addEventListener("close", function handler() {
    dialog.removeEventListener("close", handler);
    if (dialog.returnValue === "ok") onConfirm();
  });
  dialog.showModal();
}

async function transferDialog() {
  try { state.operators = await api("/operators"); } catch (error) { toast(error.message, "error"); return; }
  const current = state.detail.assigned ? state.detail.assigned.id : null;
  const options = state.operators.filter((o) => o.id !== current);
  if (!options.length) { toast("Não há outro funcionário ativo para receber a conversa."); return; }
  const select = el("select", { attrs: { id: "transfer-target" } },
    options.map((o) => el("option", { text: `${o.display_name} (${o.role === "ADMIN" ? "Admin" : "Atendente"})`, attrs: { value: o.id } })));
  const wrap = el("div", {}, [el("label", { text: "Transferir para", attrs: { for: "transfer-target" } }), select]);
  confirmAction("Transferir atendimento", "O novo responsável passará a responder esta conversa.", async () => {
    const id = state.selected;
    try {
      await api(`/conversations/${encodeURIComponent(id)}/transfer`, { method: "POST", body: { target_user_id: select.value } });
      toast("Atendimento transferido."); announce("Atendimento transferido.");
    } catch (error) { toast(error.message, "error"); }
    await openConversation(id, { focus: false }); scheduleListRefresh();
  }, wrap);
}

async function sendReply(event) {
  event.preventDefault();
  if (state.sending || !state.selected) return;
  const input = $("composer-input");
  const text = input.value.trim();
  if (!text) { toast("Escreva a mensagem antes de enviar.", "error"); input.focus(); return; }
  state.sending = true; $("composer-send").disabled = true;
  // A mesma chave é reutilizada se o envio for repetido (falha de rede, clique duplo).
  state.draftKey = state.draftKey || newKey();
  try {
    const result = await api(`/conversations/${encodeURIComponent(state.selected)}/reply`, {
      method: "POST", body: { text, idempotency_key: state.draftKey } });
    input.value = ""; state.draftKey = null; updateCount();
    delete state.drafts[state.selected]; persistDrafts();
    announce(result.duplicate ? "Mensagem já registrada." : "Mensagem registrada para envio.");
  } catch (error) {
    toast(error.message, "error");
    if (error.status && error.status !== 0 && error.status !== 409) state.draftKey = null;
  } finally {
    state.sending = false;
    await openConversation(state.selected, { focus: false });
  }
}

function updateCount() { $("composer-count").textContent = `${$("composer-input").value.length}/2000`; }

// Rascunhos: memória + sessionStorage desta aba (some ao fechar a aba ou ao sair do painel).
function saveDraft() {
  const id = state.composerFor;
  if (!id) return;
  const value = $("composer-input").value;
  if (value.trim()) state.drafts[id] = value; else delete state.drafts[id];
  persistDrafts();
}
function persistDrafts() { try { sessionStorage.setItem("rr-drafts", JSON.stringify(state.drafts)); } catch { /* sem armazenamento */ } }
function loadDrafts() { try { state.drafts = JSON.parse(sessionStorage.getItem("rr-drafts") || "{}") || {}; } catch { state.drafts = {}; } }
function clearDrafts() { state.drafts = {}; try { sessionStorage.removeItem("rr-drafts"); } catch { /* idem */ } }

async function refreshCounts() {
  try {
    const data = await api("/overview");
    $("count-waiting").textContent = data.conversations.WAITING_HUMAN ? String(data.conversations.WAITING_HUMAN) : "";
    $("count-mine").textContent = data.assigned_to_me ? String(data.assigned_to_me) : "";
    if (data.outbox) {
      const problems = (data.outbox.UNKNOWN || 0) + (data.outbox.FAILED || 0) + (data.dispatch_halted ? 1 : 0);
      $("count-system").textContent = problems ? String(problems) : "";
    }
  } catch { /* ignorado: estado de conexão já é indicado */ }
}

// ------------------------------------------------------------ tempo real
let reconnectTimer = null;
function setConnection(kind, text) {
  // Mesmo estado no topo (tablet/celular) e na barra lateral (desktop); só o primeiro é anunciado.
  for (const c of [$("connection"), $("connection-side")]) { c.dataset.state = kind; c.textContent = text; }
}

function startEvents() {
  stopEvents();
  setConnection("connecting", "Conectando…");
  const source = new EventSource(`${API}/events`);
  state.events = source;
  source.addEventListener("ready", () => { setConnection("live", "Ao vivo"); scheduleNotifications(); });
  source.addEventListener("changes", (event) => {
    setConnection("live", "Ao vivo");
    let data = {};
    try { data = JSON.parse(event.data); } catch { return; }
    scheduleListRefresh(); refreshCounts(); scheduleNotifications();
    if (state.view === "overview") loadOverview();
    if (state.selected && (data.other || (data.conversations || []).includes(state.selected))) {
      openConversation(state.selected, { focus: false });
    }
    if ((data.conversations || []).length) announce("Conversas atualizadas.");
  });
  source.addEventListener("reset", () => {
    scheduleListRefresh(); scheduleNotifications();
    if (state.selected) openConversation(state.selected, { focus: false });
  });
  source.addEventListener("auth", async () => {
    stopEvents();
    const generation = sessionGeneration;
    // A sessão do stream pode ter sido apenas RENOVADA (senha/2 etapas): confere antes de deslogar.
    let valid = false;
    try {
      const response = await fetch(`${API}/auth/session`, { credentials: "same-origin", headers: { Accept: "application/json" } });
      valid = response.ok;
    } catch { /* sem conexão: trata como sessão encerrada */ }
    if (generation !== sessionGeneration || !state.user) return;  // outra sessão já foi aplicada/encerrada
    if (valid) startEvents(); else sessionEnded();
  });
  source.addEventListener("rotate", () => setConnection("connecting", "Reconectando…"));
  source.onerror = () => {
    if (!state.user) return;
    setConnection("offline", "Reconectando…");
    // EventSource reconecta sozinho com Last-Event-ID; se fechar de vez, recriamos e sincronizamos.
    if (source.readyState === EventSource.CLOSED) {
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(async () => {
        try { applySession(await api("/auth/session")); } catch { /* showLogin já tratado em 401 */ }
      }, 3000);
    }
  };
}

function stopEvents() { if (state.events) { state.events.close(); state.events = null; } }

// --------------------------------------------------------- administração
async function loadOverview() {
  try {
    const data = await api("/overview");
    const c = data.conversations;
    // [rótulo, valor, tela, filtro, tom, ícone]; o tom só "acende" o número quando há algo a fazer.
    const cards = [
      ["Aguardando sem responsável", data.waiting_unassigned, "conversations", "waiting", "warning", "clock"],
      ["Comigo agora", data.assigned_to_me, "conversations", "mine", "accent", "me"],
      ["Em atendimento humano", c.HUMAN_ACTIVE || 0, "conversations", "human_active", "accent", "people"],
      ["No automático", c.AUTO || 0, "conversations", "auto", "success", "bot"],
      ["Encerradas", (c.RESOLVED || 0) + (c.CLOSED || 0), "conversations", "resolved", "neutral", "done"],
    ];
    if (data.outbox) {
      cards.push(["Envios incertos", data.outbox.UNKNOWN || 0, "system", undefined, "danger", "question"],
        ["Envios com falha", data.outbox.FAILED || 0, "system", undefined, "danger", "x"],
        ["Envios na fila", (data.outbox.PENDING || 0) + (data.outbox.RETRY || 0) + (data.outbox.SENDING || 0), "system", undefined, "info", "send"]);
    }
    const alerting = new Set(["warning", "danger"]);
    $("overview-cards").replaceChildren(...cards.map(([label, value, view, filter, tone, icon]) => {
      const card = el("button", { className: "card card-link", attrs: { type: "button", "data-tone": tone, "data-icon": icon,
        "data-attention": String(alerting.has(tone) && Number(value) > 0) } }, [
        el("span", { className: "card-icon", attrs: { "aria-hidden": "true" } }),
        el("span", { className: "card-value", text: String(value) }), el("span", { className: "card-label", text: label })]);
      card.addEventListener("click", () => { closeDetail(); navigate(view, filter); });
      return card;
    }));
    $("overview-cards").setAttribute("aria-busy", "false");
    const attention = [];
    const push = (text, kind = "warning", go = null) => attention.push({ text, kind, go });
    if (data.dispatch_halted) push(`Envios INTERROMPIDOS: ${data.dispatch_halted.reason}`, "critical", ["settings"]);
    if (data.outbox && data.outbox.UNKNOWN) push(`${data.outbox.UNKNOWN} envio(s) com resultado incerto — conferir antes de qualquer reenvio.`, "critical", ["system"]);
    if (data.outbox && data.outbox.FAILED) push(`${data.outbox.FAILED} envio(s) com falha.`, "warning", ["system"]);
    if (data.waiting_unassigned) {
      const minutes = data.oldest_waiting_minutes;
      const urgent = minutes !== null && minutes >= data.urgent_after_minutes;
      push(`${data.waiting_unassigned} cliente(s) aguardando atendimento; o mais antigo há ${minutes} min${urgent ? " (URGENTE)" : ""}.`,
        urgent ? "critical" : "warning", ["conversations", "waiting"]);
    }
    if (data.inbox && data.inbox.FAILED) push(`${data.inbox.FAILED} mensagem(ns) recebida(s) não processada(s).`, "warning", ["system"]);
    if (data.retired_contact_attempts) push(`${data.retired_contact_attempts} tentativa(s) de contato de clientes com dados excluídos.`, "info", ["system"]);
    const list = $("attention-list");
    list.replaceChildren(...(attention.length ? attention.map((item) => {
      const node = el("li", { className: `attention-item ${item.kind}` }, [el("span", { text: item.text })]);
      if (item.go) {
        const go = el("button", { className: "btn btn-ghost btn-small", text: "Ver", attrs: { type: "button" } });
        go.addEventListener("click", () => { closeDetail(); navigate(item.go[0], item.go[1]); });
        node.append(go);
      }
      return node;
    }) : [el("li", { className: "muted", text: "Nada pendente agora." })]));
    $("overview-note").textContent = `Última atividade: ${formatTime(data.last_activity_at) || "—"}. `
      + "Números calculados do banco local; nenhuma mensagem real é enviada nesta fase.";
  } catch (error) { if (error.status !== 401) toast(error.message, "error"); }
}

// ------------------------------------------------------------ notificações (somente no painel)
let notifTimer = null, notifDebounce = null, audioContext = null;
function scheduleNotifications() { clearTimeout(notifDebounce); notifDebounce = setTimeout(loadNotifications, 250); }
function notifKey(name) { return `rr-notif-${name}:${state.user ? state.user.id : "anon"}`; }

function loadNotifPrefs() {
  state.notif.prefs = Object.assign({ sound: false, off: [] }, STORAGE.get(notifKey("prefs"), {}));
  state.notif.announced = new Set(STORAGE.get(notifKey("announced"), []));
  state.notif.read = new Set(STORAGE.get(notifKey("read"), []));
  $("notif-sound").checked = Boolean(state.notif.prefs.sound);
  for (const box of document.querySelectorAll("[data-notif-pref]")) box.checked = !state.notif.prefs.off.includes(box.dataset.notifPref);
}

function rememberKeys(name, set) {
  // Guarda só chaves de avisos ainda atuais (+ limite) para não crescer sem fim.
  const current = new Set(state.notif.items.map((n) => n.key));
  const kept = [...set].filter((k) => current.has(k)).slice(-500);
  STORAGE.set(notifKey(name), kept);
}

function visibleNotifs() {
  return state.notif.items.filter((n) => n.mandatory || !state.notif.prefs.off.includes(n.type));
}

async function loadNotifications() {
  if (!state.user) return;
  let data;
  try { data = await api("/notifications"); } catch { return; }
  state.notif.items = data.items;
  const fresh = visibleNotifs().filter((n) => !state.notif.announced.has(n.key));
  if (!state.notif.synced) {
    // Fase 16: logo após entrar, os avisos já pendentes viravam até 4 toasts por 5 s, cobrindo a fila e o
    // campo de resposta (no celular, quase a tela toda). Agora: um único resumo; o sino mostra o resto.
    state.notif.synced = true;
    if (fresh.length) {
      const critical = fresh.some((n) => n.severity === "critical");
      toast(`${fresh.length} aviso(s) pendente(s) — veja no sino de avisos.`, critical ? "error" : "info");
    }
  } else {
    for (const n of fresh.slice(0, 2)) toast(`${n.label ? `${n.label}: ` : ""}${n.text}`, n.severity === "critical" ? "error" : "info");
    if (fresh.length > 2) toast(`Mais ${fresh.length - 2} aviso(s) no painel de avisos.`);
  }
  if (fresh.length) {
    announce(`${fresh.length} aviso(s) novo(s).`);
    if (state.notif.prefs.sound) beep();
  }
  for (const n of fresh) state.notif.announced.add(n.key);
  rememberKeys("announced", state.notif.announced);
  rememberKeys("read", state.notif.read);
  renderNotifications();
}

function renderNotifications() {
  const items = visibleNotifs();
  const unread = items.filter((n) => !state.notif.read.has(n.key)).length;
  const critical = items.some((n) => n.severity === "critical" && !state.notif.read.has(n.key));
  for (const node of document.querySelectorAll("[data-notif-count]")) {
    node.textContent = unread ? String(unread) : "";
    node.classList.toggle("critical", critical);
  }
  for (const toggle of document.querySelectorAll("[data-notif-toggle]")) {
    toggle.setAttribute("aria-label", unread ? `Avisos: ${unread} não lido(s)` : "Avisos");
  }
  $("notif-list").replaceChildren(...items.map((n) => {
    const body = el("button", { className: `notif ${n.severity}${state.notif.read.has(n.key) ? "" : " unread-notif"}`, attrs: { type: "button" } }, [
      el("span", { className: "notif-text", text: n.text }),
      el("span", { className: "notif-meta muted small", text: [n.label, formatTime(n.at), n.mandatory ? "obrigatório" : ""].filter(Boolean).join(" · ") }),
    ]);
    body.addEventListener("click", () => {
      state.notif.read.add(n.key); rememberKeys("read", state.notif.read); renderNotifications();
      if (n.type === "retired_contact" && state.user.role === "ADMIN") { closeNotifications(); navigate("system"); }
      else if (n.conversation_id) { closeNotifications(); state.filter = "all"; navigate("conversations", "all"); openConversation(n.conversation_id); }
      else if (n.type === "dispatch_halted") { closeNotifications(); navigate(state.user.role === "ADMIN" ? "settings" : "overview"); }
      else if (state.user.role === "ADMIN") { closeNotifications(); navigate("system"); }
    });
    return el("li", {}, [body]);
  }));
  $("notif-empty").hidden = items.length > 0;
}

function openNotifications() {
  state.notif.open = true;
  $("notif-panel").hidden = false;
  for (const t of document.querySelectorAll("[data-notif-toggle]")) t.setAttribute("aria-expanded", "true");
  $("notif-close").focus();
  // Abrir o painel marca como vistos os avisos exibidos.
  for (const n of visibleNotifs()) state.notif.read.add(n.key);
  rememberKeys("read", state.notif.read);
  setTimeout(renderNotifications, 1500);
}

function closeNotifications() {
  if (!state.notif.open) return;
  state.notif.open = false;
  $("notif-panel").hidden = true;
  for (const t of document.querySelectorAll("[data-notif-toggle]")) t.setAttribute("aria-expanded", "false");
  renderNotifications();
}

function beep() {
  // Som local gerado no navegador (sem arquivo externo); só após um gesto do usuário.
  if (!audioContext) return;
  try {
    const oscillator = audioContext.createOscillator(), gain = audioContext.createGain();
    oscillator.frequency.value = 880; gain.gain.value = 0.08;
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start(); oscillator.stop(audioContext.currentTime + 0.18);
  } catch { /* áudio indisponível */ }
}

// ------------------------------------------------------------ sistema (ADMIN)
async function loadSystem() {
  try {
    const data = await api("/system");
    const mb = (data.database_bytes / 1024 / 1024).toFixed(1);
    // [rótulo, valor, chama atenção?]
    const rows = [
      ["Versão", data.release],
      ["Ambiente", `${data.environment}${data.environment_label ? ` (${data.environment_label})` : ""}`],
      ["Transporte e IA", `Transporte: ${data.transport} · IA: ${data.ai_provider}`],
      ["Envios", data.dispatch_halted ? `INTERROMPIDOS: ${data.dispatch_halted.reason}` : "Liberados", Boolean(data.dispatch_halted)],
      ["Banco", `Esquema v${data.schema_version}, ${mb} MB · ${data.counts.conversations} conversas (${data.archived} arquivadas, ${data.retired} excluídas)`],
      ["Volume", `Mensagens: ${data.counts.messages} · entrada: ${data.counts.channel_inbox} · saída: ${data.counts.channel_outbox} · auditoria: ${data.counts.panel_audit}`],
      ["Perfil do ambiente", `${data.storage.environment_profile} · dados: ${data.storage.data_classification === "synthetic" ? "somente FICTÍCIOS" : data.storage.data_classification}`],
      ["Proteção em repouso", data.storage.protection],
      ["Backup automático", data.backup
        ? `${data.backup.saudavel ? "Em dia" : "ATENÇÃO"} · último sucesso: ${formatTime(data.backup.ultimo_sucesso) || "nunca"}`
          + ` · falhas seguidas: ${data.backup.falhas_consecutivas} · cópias: ${data.backup.copias_primario ?? 0}`
          + ` (secundária simulada: ${data.backup.copias_secundario ?? 0})`
          + (data.backup.ultimo_teste_restauracao && data.backup.ultimo_teste_restauracao.resultado
            ? ` · último teste de restauração: ${data.backup.ultimo_teste_restauracao.resultado}` : "")
        : "Ainda não executado neste ambiente", Boolean(data.backup && !data.backup.saudavel)],
    ];
    $("system-status").replaceChildren(...rows.map(([key, value, warn]) => el("li", {}, [
      el("span", { className: "kv-key", text: key }), el("span", { className: `kv-value${warn ? " attention-text" : ""}`, text: value })])));
    const lockText = { meta_graph: "Envio real pela Meta (Graph API)", meta_homologation: "Homologação real Meta",
      gemini_real: "Google Gemini real", real_data: "Uso de dados reais de clientes" };
    $("system-locks").replaceChildren(...Object.entries(data.locks).map(([key, on]) => el("li", { className: on ? "lock-on" : "lock-off" }, [
      el("span", { text: lockText[key] || key }),
      el("span", { className: "lock-pill", text: on ? "LIBERADA NO CÓDIGO" : "bloqueada" })])));
    const retired = await api("/retired-contacts");
    $("retired-body").replaceChildren(...retired.map((r) => {
      const button = el("button", { className: "btn btn-ghost btn-small", text: "Reativar", attrs: { type: "button" } });
      button.addEventListener("click", () => reinstateDialog(r));
      return el("tr", {}, [el("td", { text: r.label }), el("td", { text: formatTime(r.retired_at) }),
        el("td", { text: String(r.attempts) }), el("td", { text: formatTime(r.last_attempt_at) || "—" }), el("td", {}, [button])]);
    }));
    $("retired-empty").hidden = retired.length > 0;
  } catch (error) { if (error.status !== 401) toast(error.message, "error"); }
}

function reinstateDialog(contact) {
  const code = `REATIVAR:${contact.id.slice(-6)}`;
  const input = el("input", { attrs: { id: "reinstate-confirm", maxlength: "64", autocomplete: "off", spellcheck: "false" } });
  const wrap = el("div", {}, [el("label", { text: `Digite ${code} para confirmar`, attrs: { for: "reinstate-confirm" } }), input]);
  confirmAction("Reativar contato?", "O cliente poderá conversar de novo, começando do zero. Os dados excluídos não voltam.", async () => {
    try {
      await api(`/retired-contacts/${encodeURIComponent(contact.id)}/reinstate`, { method: "POST", body: { confirmation: input.value.trim() } });
      toast("Contato reativado.");
    } catch (error) { toast(error.message, "error"); }
    loadSystem();
  }, wrap);
}

// ------------------------------------------------------------ segundo fator (Fase 13)
function showMfaStep(recovery = false) {
  showOnly("mfa-view");
  $("mfa-error").textContent = "";
  $("mfa-code-block").hidden = recovery; $("mfa-recovery-block").hidden = !recovery;
  $("mfa-use-recovery").textContent = recovery ? "Usar o código do aplicativo" : "Perdi o celular: usar código de recuperação";
  $("mfa-code").value = ""; $("mfa-recovery").value = "";
  (recovery ? $("mfa-recovery") : $("mfa-code")).focus();
}

function showMfaSetupRequired() {
  stopEvents();
  showOnly("mfa-setup-view");
  mountSetup($("mfa-setup-host"), (result) => applySession(result));
}

function mountSetup(host, onDone) {
  for (const old of document.querySelectorAll(".mfa-setup")) old.remove();  // um único componente por vez
  const box = $("mfa-setup-template").content.firstElementChild.cloneNode(true);
  host.append(box);
  const step = (name) => { for (const s of box.querySelectorAll(".mfa-step")) s.hidden = s.dataset.for !== name; };
  const error = box.querySelector(".mfa-setup-error");
  const [passwordForm, codeForm] = box.querySelectorAll("form.mfa-step");
  let finished = null;
  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault(); error.textContent = "";
    try {
      const setup = await api("/auth/mfa/setup", { method: "POST", body: { password: $("mfa-setup-password").value } });
      $("mfa-setup-password").value = "";
      box.querySelector(".mfa-account").textContent = `${setup.issuer} — ${setup.account}`;
      box.querySelector(".mfa-secret").textContent = setup.secret;
      box.querySelector(".mfa-uri").textContent = setup.uri;
      step("code"); $("mfa-setup-code").focus();
    } catch (err) { error.textContent = err.message; }
  });
  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault(); error.textContent = "";
    try {
      const result = await api("/auth/mfa/activate", { method: "POST", body: { code: $("mfa-setup-code").value.trim() } });
      state.csrf = result.csrf_token;  // a sessão é renovada ao ativar
      // O stream ao vivo ainda usa a sessão antiga (agora revogada): reabre com o cookie novo,
      // senão o servidor encerra o stream com "auth" e o painel deslogaria a pessoa.
      if (state.user && !$("app-view").hidden) startEvents();
      box.querySelector(".mfa-secret").textContent = ""; box.querySelector(".mfa-uri").textContent = "";
      box.querySelector(".recovery-codes").replaceChildren(...result.recovery_codes.map((c) => el("li", { text: c })));
      finished = result;
      step("codes"); box.querySelector(".mfa-done").focus();
    } catch (err) { error.textContent = err.message; }
  });
  box.querySelector(".mfa-done").addEventListener("click", () => {
    box.remove(); toast("Verificação em duas etapas ativada."); onDone(finished);
  });
  $("mfa-setup-password").focus();
}

async function loadAccount() {
  try {
    const mfa = await api("/auth/mfa/status");
    state.mfa = mfa;
    $("mfa-status").dataset.on = String(Boolean(mfa.enabled));
    $("mfa-status").textContent = mfa.enabled
      ? `Ativada. Códigos de recuperação restantes: ${mfa.recovery_codes_left}.${mfa.required ? " Obrigatória neste ambiente." : ""}`
      : (mfa.available ? "Desativada. Recomendada para proteger o acesso às conversas." : "Indisponível neste ambiente (chave de proteção não configurada).");
    const actions = [];
    const add = (label, handler, kind = "btn-ghost") => {
      const b = el("button", { className: `btn ${kind}`, text: label, attrs: { type: "button" } });
      b.addEventListener("click", handler); actions.push(b);
    };
    if (!mfa.enabled && mfa.available) {
      add("Ativar verificação em duas etapas", () => {
        $("mfa-account-actions").replaceChildren();  // durante o cadastro, só o passo a passo aparece
        mountSetup($("mfa-account-host"), () => loadAccount());
      }, "btn-primary");
    }
    if (mfa.enabled) add("Gerar novos códigos de recuperação", regenerateCodes);
    if (mfa.enabled && !mfa.required) add("Desativar", disableMfa, "btn-danger");
    $("mfa-account-actions").replaceChildren(...actions);
  } catch (error) { if (error.status !== 401) toast(error.message, "error"); }
}

function codeInput(id, label) {
  const input = el("input", { attrs: { id, inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7" } });
  return [input, el("div", {}, [el("label", { text: label, attrs: { for: id } }), input])];
}

function regenerateCodes() {
  const [input, wrap] = codeInput("regen-code", "Código atual do aplicativo");
  confirmAction("Gerar novos códigos de recuperação?", "Os códigos antigos deixam de valer imediatamente.", async () => {
    try {
      const result = await api("/auth/mfa/recovery-codes", { method: "POST", body: { code: input.value.trim() } });
      const list = el("ul", { className: "recovery-codes" }, result.recovery_codes.map((c) => el("li", { text: c })));
      $("mfa-account-host").replaceChildren(el("p", { text: "Guarde estes códigos agora; não serão mostrados de novo." }), list);
      loadAccount();
    } catch (error) { toast(error.message, "error"); }
  }, wrap);
}

function disableMfa() {
  const password = el("input", { attrs: { id: "disable-password", type: "password", autocomplete: "current-password" } });
  const [code, codeWrap] = codeInput("disable-code", "Código atual do aplicativo");
  const wrap = el("div", {}, [el("label", { text: "Sua senha", attrs: { for: "disable-password" } }), password, codeWrap]);
  confirmAction("Desativar a verificação em duas etapas?", "Sua conta ficará protegida só pela senha. Você precisará entrar de novo.", async () => {
    try {
      await api("/auth/mfa/disable", { method: "POST", body: { password: password.value, code: code.value.trim() } });
      showLogin("Verificação em duas etapas desativada. Entre novamente.");
    } catch (error) { toast(error.message, "error"); }
  }, wrap);
}

function resetMfaDialog(user) {
  const reason = el("textarea", { attrs: { id: "reset-reason", rows: "3", maxlength: "300" } });
  const confirm = el("input", { attrs: { id: "reset-confirm", autocomplete: "off", spellcheck: "false" } });
  const wrap = el("div", {}, [
    el("label", { text: "Justificativa (obrigatória; fica registrada)", attrs: { for: "reset-reason" } }), reason,
    el("label", { text: `Digite REDEFINIR:${user.login} para confirmar`, attrs: { for: "reset-confirm" } }), confirm]);
  confirmAction(`Redefinir as duas etapas de ${user.display_name}?`,
    "Use só depois de confirmar a identidade da pessoa (por exemplo, pessoalmente). As sessões dela serão encerradas e ela deverá cadastrar de novo.",
    async () => {
      try {
        await api(`/users/${user.id}/mfa-reset`, { method: "POST", body: { justification: reason.value.trim(), confirmation: confirm.value.trim() } });
        toast("Segundo fator redefinido.");
      } catch (error) { toast(error.message, "error"); }
      loadTeam();
    }, wrap);
}

async function loadTeam() {
  try {
    const users = await api("/users");
    $("users-body").replaceChildren(...users.map((u) => {
      const toggle = el("button", { className: "btn btn-ghost", text: u.active ? "Desativar" : "Reativar", attrs: { type: "button" } });
      toggle.disabled = u.id === state.user.id;
      toggle.addEventListener("click", () => confirmAction(u.active ? "Desativar acesso?" : "Reativar acesso?",
        u.active ? "As sessões abertas dessa pessoa serão encerradas imediatamente." : "A pessoa poderá entrar novamente.",
        async () => {
          try { await api(`/users/${u.id}/${u.active ? "deactivate" : "activate"}`, { method: "POST", body: {} }); toast("Acesso atualizado."); }
          catch (error) { toast(error.message, "error"); }
          loadTeam();
        }));
      // Fase 16: a célula era a própria caixa flexível (td.row-actions), o que a tirava do modelo de tabela
      // e desalinhava a linha; agora os botões ficam num contêiner dentro da célula.
      const buttons = el("div", { className: "row-actions" }, [toggle]);
      if (u.mfa_enabled && u.id !== state.user.id) {
        const reset = el("button", { className: "btn btn-ghost", text: "Redefinir duas etapas", attrs: { type: "button" } });
        reset.addEventListener("click", () => resetMfaDialog(u));
        buttons.append(reset);
      }
      toggle.classList.add("btn-small");
      for (const b of buttons.children) b.classList.add("btn-small");
      return el("tr", {}, [
        el("td", { text: u.display_name }), el("td", { text: u.login }),
        el("td", {}, [pill(u.role === "ADMIN" ? "Administrador" : "Atendente", u.role === "ADMIN" ? "accent" : "")]),
        el("td", {}, [pill(u.active ? (u.must_change_password ? "Ativo · senha temporária" : "Ativo") : "Desativado",
          u.active ? (u.must_change_password ? "warn" : "ok") : "")]),
        el("td", {}, [u.mfa_enabled ? pill("Ativa", "ok") : el("span", { className: "muted", text: "—" })]),
        el("td", { text: formatTime(u.last_login_at) || "—" }), el("td", { className: "actions" }, [buttons]),
      ]);
    }));
  } catch (error) { toast(error.message, "error"); }
}

async function createUser(event) {
  event.preventDefault();
  $("user-error").textContent = "";
  try {
    await api("/users", { method: "POST", body: {
      login: $("new-login").value, display_name: $("new-name").value, role: $("new-role").value,
      password: $("new-user-password").value } });
    $("user-form").reset(); toast("Acesso criado."); loadTeam();
  } catch (error) { $("user-error").textContent = error.message; }
}

const ACTION_LABEL = {
  login: "Entrou", logout: "Saiu", login_failed: "Falha de login", user_created: "Criou acesso",
  user_deactivated: "Desativou acesso", user_activated: "Reativou acesso", knowledge_drafted: "Criou rascunho de informação",
  knowledge_approved: "Aprovou informação", knowledge_rejected: "Rejeitou rascunho", knowledge_revoked: "Revogou informação", conversation_assumed: "Assumiu atendimento",
  conversation_transferred: "Transferiu atendimento", conversation_resolved: "Finalizou atendimento",
  automation_resumed: "Retomou automação", handoff_requested: "Enviou para a fila", operator_message_queued: "Enviou mensagem",
  password_changed: "Trocou a senha", password_reset: "Redefiniu senha", admin_access: "Acesso administrativo",
  dispatch_halted: "Interrompeu envios", dispatch_resumed: "Retomou envios",
  login_password_ok_mfa_pending: "Senha correta, aguardando 2ª etapa", mfa_verified: "Confirmou a 2ª etapa",
  mfa_failed: "Código de 2ª etapa inválido", mfa_setup_started: "Iniciou cadastro de 2 etapas", mfa_enabled: "Ativou 2 etapas",
  mfa_disabled: "Desativou 2 etapas", mfa_reset_by_admin: "Redefiniu 2 etapas de outra pessoa",
  mfa_recovery_code_used: "Usou código de recuperação", mfa_recovery_codes_regenerated: "Gerou novos códigos de recuperação",
  session_rotated_password: "Sessão renovada (senha)", session_rotated_mfa_enabled: "Sessão renovada (2 etapas)",
  contact_reinstated: "Reativou contato excluído", setting_urgent_after_minutes_changed: "Alterou o tempo de urgência",
  setting_retired_contact_policy_block: "Política de excluídos: bloquear",
  setting_retired_contact_policy_allow_new_conversation: "Política de excluídos: permitir nova conversa",
};

async function loadAudit() {
  try {
    const rows = await api(`/audit${state.auditBefore ? `?before=${state.auditBefore}` : ""}`);
    $("audit-body").append(...rows.map((r) => el("tr", {}, [
      el("td", { text: formatTime(r.at) }), el("td", { text: r.actor || "—" }),
      el("td", { text: ACTION_LABEL[r.action] || r.action }),
      el("td", { text: r.entity_type === "conversation" && r.entity_id ? `Cliente ··${r.entity_id.slice(-6)}` : r.entity_type }),
      el("td", {}, [pill({ success: "Sucesso", denied: "Negado", failed: "Falhou" }[r.outcome] || r.outcome,
        { success: "ok", denied: "warn", failed: "bad" }[r.outcome] || "")]),
    ])));
    state.auditBefore = rows.length ? rows[rows.length - 1].id : state.auditBefore;
    $("audit-more").hidden = rows.length < 100;
  } catch (error) { toast(error.message, "error"); }
}

async function loadSettings() {
  try {
    const data = await api("/settings");
    $("setting-transfer").checked = data.permissions.operators_can_transfer;
    $("setting-resume").checked = data.permissions.operators_can_resume;
    $("setting-urgent").value = String(data.permissions.urgent_after_minutes);
    $("setting-retired-policy").value = data.permissions.retired_contact_policy;
    $("settings-error").textContent = "";
    $("dispatch-status").textContent = data.dispatch_halted
      ? `Envios INTERROMPIDOS: ${data.dispatch_halted.reason}` : "Envios liberados (transporte simulado).";
    $("environment-note").textContent = `Ambiente: ${data.environment}${data.environment_label ? ` (${data.environment_label})` : ""} · transporte: ${data.transport} (nenhum envio real nesta fase).`;
  } catch (error) { toast(error.message, "error"); }
}

// ------------------------------------------------------------------ conhecimento (ADMIN)
// Gestão das informações oficiais: rascunho → aprovação → (revogação). A IA nunca aciona
// estas rotas; o backend confere o papel ADMIN em cada chamada.
const KIND_FIELDS = {
  identity: ["text"], location: ["text"], hours: ["text"], professionals: ["items"], policy: ["items"],
  cancellation: ["items"], booksy: ["url"],
  service: ["key", "name", "aliases", "description", "category", "duration", "team"],
  price: ["key", "pricing", "amount", "amount_max", "note"], faq: ["key", "questions", "answer"],
  category: ["key", "name", "active"], professional: ["key", "name", "active"],
};
const NAME_LABEL = { service: "Nome do serviço", category: "Nome da categoria", professional: "Nome de exibição do profissional" };
const KEY_LABEL = { faq: "Identificador da pergunta", category: "Identificador da categoria",
  professional: "Identificador do profissional" };
const KNOWLEDGE_STATUS = { DRAFT: "Rascunho", APPROVED: "Aprovado", REJECTED: "Rejeitado", ARCHIVED: "Revogado" };

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `R$ ${number.toFixed(2).replace(".", ",")}` : String(value);
}

// Registros aprovados, em uso e ativos (para escolher categoria/profissionais de um serviço).
function activeOf(kind) {
  return state.knowledge.records.filter((r) => r.kind === kind && r.status === "APPROVED" && r.in_use && r.data[kind].active)
    .map((r) => r.data[kind]);
}

function nameOf(kind, id) {
  const record = state.knowledge.records.find((r) => r.kind === kind && r.data[kind] && r.data[kind].id === id && r.in_use);
  return record ? record.data[kind].name : id;
}

function minutes(value) {
  const h = Math.floor(value / 60), m = value % 60;
  return h ? (m ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`) : `${m} min`;
}

function summarize(data) {
  if (data.kind === "category" || data.kind === "professional") {
    const item = data[data.kind];
    const field = data.kind === "category" ? "category_id" : "professional_ids";
    const users = state.knowledge.records.filter((r) => r.kind === "service" && r.in_use
      && [].concat(r.data.service[field] || []).includes(item.id)).map((r) => r.data.service.name);
    return `${item.name}${item.active ? "" : " (inativo)"}${users.length ? ` — serviços: ${users.join(", ")}` : ""}`;
  }
  if (["identity", "location", "hours"].includes(data.kind)) return data.text;
  if (["professionals", "policy", "cancellation"].includes(data.kind)) return data.items.join(" · ");
  if (data.kind === "booksy") return data.url;
  if (data.kind === "service") {
    const aliases = data.service.aliases && data.service.aliases.length ? ` (${data.service.aliases.join(", ")})` : "";
    const extra = [];
    if (data.service.category_id) extra.push(`categoria: ${nameOf("category", data.service.category_id)}`);
    const d = data.service.duration;
    if (d) extra.push(`duração: ${d.max_minutes ? `${minutes(d.min_minutes)}–${minutes(d.max_minutes)}` : `~${minutes(d.min_minutes)}`}`);
    if ((data.service.professional_ids || []).length) {
      extra.push(`profissionais: ${data.service.professional_ids.map((id) => nameOf("professional", id)).join(", ")}`);
    }
    return `${data.service.name}${aliases}${data.service.description ? ` — ${data.service.description}` : ""}`
      + (extra.length ? ` [${extra.join(" · ")}]` : "");
  }
  if (data.kind === "price") {
    const value = { on_evaluation: "mediante avaliação", starting: `a partir de ${money(data.amount)}`,
      range: `${money(data.amount)} a ${money(data.amount_max)}` }[data.pricing] || money(data.amount);
    return `${data.service_id}: ${value}${data.note ? ` — ${data.note}` : ""}`;
  }
  if (data.kind === "faq") return `${data.faq.questions[0]} → ${data.faq.answer}`;
  return "";
}

async function loadKnowledge() {
  try {
    const data = await api("/knowledge");
    state.knowledge.records = data.records; state.knowledge.labels = data.labels; state.knowledge.kinds = data.kinds;
    if ($("knowledge-pricing").options.length !== Object.keys(data.pricings || {}).length) {
      $("knowledge-pricing").replaceChildren(...Object.entries(data.pricings || {}).map(([value, label]) =>
        el("option", { text: label, attrs: { value } })));
    }
    $("knowledge-scope").textContent = data.scope === "OFFICIAL"
      ? "Base OFICIAL: somente informações entregues e aprovadas pelo salão."
      : "Base SINTÉTICA de testes: dados fictícios, nunca usar como oficiais.";
    fillKindSelects();
    const pending = $("knowledge-pending");
    pending.replaceChildren(...data.pending.map((item) => el("li", { text: `${item.label}: ${item.detail}` })));
    if (!data.pending.length) pending.append(el("li", { text: "Nenhuma pendência nas categorias acompanhadas." }));
    const drafts = data.records.filter((r) => r.status === "DRAFT").length;
    $("count-pending").textContent = drafts ? String(drafts) : "";
    renderKnowledge();
    if ($("knowledge-kind").value === "service") {  // opções sempre a partir do que está aprovado agora
      fillRelations($("knowledge-category").value,
        [...document.querySelectorAll("#knowledge-team input:checked")].map((b) => b.value));
    }
    $("knowledge-form").hidden = false; $("knowledge-preview-form").hidden = false;
  } catch (error) {
    $("knowledge-scope").textContent = error.message;
    $("knowledge-body").replaceChildren(); $("knowledge-pending").replaceChildren();
    $("knowledge-form").hidden = true; $("knowledge-preview-form").hidden = true;
  }
  loadAiUsage();
}

function fillKindSelects() {
  const { kinds, labels } = state.knowledge;
  const filter = $("knowledge-kind-filter"), form = $("knowledge-kind");
  if (form.options.length === kinds.length) return;
  filter.replaceChildren(el("option", { text: "Todos os tipos", attrs: { value: "" } }),
    ...kinds.map((k) => el("option", { text: labels[k] || k, attrs: { value: k } })));
  form.replaceChildren(...kinds.map((k) => el("option", { text: labels[k] || k, attrs: { value: k } })));
  showKnowledgeFields(form.value);
}

function renderKnowledge() {
  const query = $("knowledge-search").value.trim().toLowerCase();
  const kind = $("knowledge-kind-filter").value, status = $("knowledge-status-filter").value;
  const rows = state.knowledge.records.filter((r) => (!kind || r.kind === kind) && (!status || r.status === status)
    && (!query || `${r.fact_id} ${summarize(r.data)}`.toLowerCase().includes(query)));
  $("knowledge-body").replaceChildren(...rows.map((record) => {
    const actions = el("div", { className: "row-actions knowledge-actions" });
    const button = (label, handler, kindClass = "btn-ghost") => {
      const node = el("button", { className: `btn ${kindClass} btn-small`, text: label, attrs: { type: "button" } });
      node.addEventListener("click", handler); actions.append(node);
    };
    if (record.status === "DRAFT") {
      button("Aprovar", () => knowledgeAction("approve", record), "btn-primary");
      button("Rejeitar", () => knowledgeAction("reject", record));
    }
    if (record.status === "APPROVED") button("Revogar", () => knowledgeAction("revoke", record), "btn-danger");
    button("Nova versão", () => editRecord(record));
    button("Histórico", () => showHistory(record));
    return el("tr", {}, [
      el("td", { text: state.knowledge.labels[record.kind] || record.kind }), el("td", { text: record.fact_id }),
      el("td", { className: "wrap", text: summarize(record.data) }), el("td", { text: `v${record.version}` }),
      el("td", {}, [pill(KNOWLEDGE_STATUS[record.status] || record.status,
        { DRAFT: "warn", APPROVED: "ok", REJECTED: "bad" }[record.status] || "")]),
      el("td", { text: record.in_use ? "Sim" : "Não" }), el("td", { className: "actions" }, [actions]),
    ]);
  }));
  $("knowledge-empty").hidden = rows.length > 0;
}

function knowledgeAction(name, record) {
  const titles = { approve: "Aprovar como informação em uso?", reject: "Rejeitar este rascunho?", revoke: "Revogar esta informação?" };
  const texts = {
    approve: `"${summarize(record.data)}" passará a ser usado nas respostas automáticas. Confirme que a origem é confiável.`,
    reject: "O rascunho ficará no histórico e não será usado.",
    revoke: "A informação deixará de ser usada imediatamente; o histórico é preservado.",
  };
  confirmAction(titles[name], texts[name], async () => {
    try {
      await api(`/knowledge/${name}`, { method: "POST", body: { fact_id: record.fact_id, version: record.version } });
      toast({ approve: "Informação aprovada.", reject: "Rascunho rejeitado.", revoke: "Informação revogada." }[name]);
    } catch (error) { toast(error.message, "error"); }
    loadKnowledge();
  });
}

function showKnowledgeFields(kind) {
  const wanted = new Set(KIND_FIELDS[kind] || []);
  for (const block of document.querySelectorAll("#knowledge-form [data-fields]")) block.hidden = !wanted.has(block.dataset.fields);
  $("knowledge-key").previousElementSibling.textContent = KEY_LABEL[kind] || "Identificador do serviço";
  $("knowledge-name-label").textContent = NAME_LABEL[kind] || "Nome";
  if (kind === "service") fillRelations();
  if (kind === "price") updatePricingFields();
}

function fillRelations(selectedCategory = "", selectedTeam = []) {
  const select = $("knowledge-category");
  select.replaceChildren(el("option", { text: "Sem categoria", attrs: { value: "" } }),
    ...activeOf("category").map((c) => el("option", { text: c.name, attrs: { value: c.id } })));
  select.value = selectedCategory;
  const team = activeOf("professional");
  $("knowledge-team").replaceChildren(...team.map((p) => {
    const box = el("input", { attrs: { type: "checkbox", value: p.id, id: `team-${p.id}` } });
    box.checked = selectedTeam.includes(p.id);
    return el("label", { className: "switch", attrs: { for: `team-${p.id}` } }, [box, document.createTextNode(` ${p.name}`)]);
  }));
  $("knowledge-team-empty").hidden = team.length > 0;
}

function updatePricingFields() {
  const pricing = $("knowledge-pricing").value || "fixed";
  document.querySelector("#knowledge-form [data-fields='amount']").hidden = pricing === "on_evaluation";
  document.querySelector("#knowledge-form [data-fields='amount_max']").hidden = pricing !== "range";
  $("knowledge-amount-label").textContent = pricing === "range" ? "Valor mínimo (R$)" : "Valor (R$)";
}

function resetKnowledgeForm() {
  state.knowledge.editing = null;
  $("knowledge-form").reset();
  $("knowledge-kind").disabled = false; $("knowledge-key").disabled = false;
  $("knowledge-active").checked = true;
  $("knowledge-form-title").textContent = "Novo registro (rascunho)";
  $("knowledge-cancel-edit").hidden = true; $("knowledge-error").textContent = "";
  showKnowledgeFields($("knowledge-kind").value);
}

function editRecord(record) {
  resetKnowledgeForm();
  state.knowledge.editing = { fact_id: record.fact_id, version: record.version };
  const data = record.data;
  $("knowledge-kind").value = record.kind; $("knowledge-kind").disabled = true;
  showKnowledgeFields(record.kind);
  const key = record.fact_id.includes(":") ? record.fact_id.split(":")[1] : "";
  $("knowledge-key").value = key; $("knowledge-key").disabled = Boolean(key);
  if (data.text) $("knowledge-text").value = data.text;
  if (data.items) $("knowledge-items").value = data.items.join("\n");
  if (data.url) $("knowledge-url").value = data.url;
  if (data.service) {
    $("knowledge-name").value = data.service.name; $("knowledge-aliases").value = (data.service.aliases || []).join(", ");
    $("knowledge-description").value = data.service.description || "";
    fillRelations(data.service.category_id || "", data.service.professional_ids || []);
    $("knowledge-duration-min").value = data.service.duration ? String(data.service.duration.min_minutes) : "";
    $("knowledge-duration-max").value = data.service.duration && data.service.duration.max_minutes
      ? String(data.service.duration.max_minutes) : "";
  }
  for (const kind of ["category", "professional"]) {
    if (data[kind]) { $("knowledge-name").value = data[kind].name; $("knowledge-active").checked = data[kind].active; }
  }
  if (data.kind === "price") {
    $("knowledge-pricing").value = data.pricing || "fixed";
    $("knowledge-amount").value = data.amount === null || data.amount === undefined ? "" : String(data.amount).replace(".", ",");
    $("knowledge-amount-max").value = data.amount_max ? String(data.amount_max).replace(".", ",") : "";
    $("knowledge-note").value = data.note || "";
    updatePricingFields();
  }
  if (data.faq) { $("knowledge-questions").value = data.faq.questions.join("\n"); $("knowledge-answer").value = data.faq.answer; }
  $("knowledge-form-title").textContent = `Nova versão de ${record.fact_id} (a partir da v${record.version})`;
  $("knowledge-cancel-edit").hidden = false;
  $("knowledge-form").scrollIntoView({ behavior: "smooth", block: "start" });
  $("knowledge-source").focus();
}

async function submitKnowledge(event) {
  event.preventDefault();
  $("knowledge-error").textContent = "";
  const kind = $("knowledge-kind").value;
  const value = (id) => $(id).value;
  const fields = {
    text: value("knowledge-text"), items: value("knowledge-items"), url: value("knowledge-url").trim(),
    name: value("knowledge-name"), aliases: value("knowledge-aliases"), description: value("knowledge-description"),
    amount: value("knowledge-amount"), note: value("knowledge-note"), questions: value("knowledge-questions"),
    answer: value("knowledge-answer"),
    active: $("knowledge-active").checked ? "true" : "false", category: value("knowledge-category"),
    duration_min: value("knowledge-duration-min").trim(), duration_max: value("knowledge-duration-max").trim(),
    professionals: [...document.querySelectorAll("#knowledge-team input:checked")].map((b) => b.value).join(","),
    pricing: value("knowledge-pricing") || "fixed", amount_max: value("knowledge-amount-max"),
  };
  const editing = state.knowledge.editing;
  try {
    const result = await api("/knowledge/drafts", { method: "POST", body: {
      kind, key: value("knowledge-key"), fields, source: value("knowledge-source"),
      expected_version: editing ? editing.version : null } });
    toast(`Rascunho salvo: ${result.fact_id} v${result.version}. Aprove para usar no atendimento.`);
    resetKnowledgeForm();
    loadKnowledge();
  } catch (error) { $("knowledge-error").textContent = error.message; }
}

async function showHistory(record) {
  try {
    const rows = await api(`/knowledge/history?fact_id=${encodeURIComponent(record.fact_id)}`);
    $("knowledge-history-title").textContent = `Histórico de ${record.fact_id}`;
    $("knowledge-history").replaceChildren(...rows.slice().reverse().map((r) => el("li", {}, [
      el("strong", { text: `v${r.version} · ${KNOWLEDGE_STATUS[r.status] || r.status}` }),
      el("span", { className: "muted", text: ` · ${formatTime(r.updated_at)}${r.reviewed_by ? ` · revisado por ${r.reviewed_by}` : ""}` }),
      el("div", { text: summarize(r.data) }), el("div", { className: "muted small", text: `Origem: ${r.source}` }),
    ])));
    $("knowledge-history-card").hidden = false;
    $("knowledge-history-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) { toast(error.message, "error"); }
}

async function previewKnowledge(event) {
  event.preventDefault();
  const box = $("knowledge-preview");
  try {
    const result = await api("/knowledge/preview", { method: "POST", body: { question: $("knowledge-question").value } });
    box.replaceChildren(el("strong", { text: "Prévia SIMULADA (sem IA real e sem envio): " }), el("span", { text: result.reply }),
      el("span", { className: "muted small", text: ` — fontes: ${result.fact_references.join(", ") || "nenhuma"}` }));
  } catch (error) { box.textContent = error.message; }
}

async function loadAiUsage() {
  try {
    const data = await api("/ai/usage");
    $("ai-status").textContent = `Provedor: ${data.provider} · modelo configurado: ${data.model || "nenhum (a definir na homologação)"} · prompt: ${data.prompt_version} · `
      + (data.real_provider_code_lock ? "provedor real AUTORIZADO no código" : "provedor real BLOQUEADO no código (nenhuma chamada externa)");
    const rows = (data.usage_last_24h && data.usage_last_24h.rows) || [];
    $("ai-usage").replaceChildren(...(rows.length ? rows.map((r) => el("li", {
      text: `${r.provider}/${r.model} ${r.prompt_version} · ${r.outcome}: ${r.n} chamadas, ${r.input_tokens}+${r.output_tokens} tokens (raciocínio: ${r.reasoning_tokens || 0}), `
        + (r.cost_micros === null ? "custo não estimado" : `≈ US$ ${(r.cost_micros / 1e6).toFixed(4)} (estimativa)`) })) :
      [el("li", { text: `Sem chamadas nas últimas 24h. Limites: ${data.limits.requests_per_day} req/dia, US$ ${data.limits.cost_usd_per_day}/dia.` })]));
  } catch (error) { $("ai-status").textContent = error.message; }
}

// ------------------------------------------------------------------ eventos de UI
function bindUi() {
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter; if (button) button.disabled = true;
    $("login-error").textContent = "";
    try {
      const result = await api("/auth/login", { method: "POST",
        body: { login: $("login-input").value, password: $("password-input").value } });
      $("password-input").value = "";
      if (result.mfa_required) showMfaStep(false); else applySession(result);
    } catch (error) { $("login-error").textContent = error.message; $("password-input").value = ""; }
    finally { if (button) button.disabled = false; }
  });
  $("mfa-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("mfa-error").textContent = "";
    const recovery = !$("mfa-recovery-block").hidden;
    const body = recovery ? { recovery_code: $("mfa-recovery").value.trim() } : { code: $("mfa-code").value.trim() };
    try { applySession(await api("/auth/mfa", { method: "POST", body })); }
    catch (error) {
      $("mfa-error").textContent = error.message;
      if (error.status === 401 && error.message.includes("expirou")) setTimeout(() => showLogin(error.message), 1500);
    }
  });
  $("mfa-use-recovery").addEventListener("click", () => showMfaStep($("mfa-recovery-block").hidden));
  $("mfa-restart").addEventListener("click", () => showLogin(""));
  $("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("password-error").textContent = "";
    if ($("new-password").value !== $("confirm-password").value) { $("password-error").textContent = "As senhas não conferem."; return; }
    try {
      applySession(await api("/auth/password", { method: "POST",
        body: { current_password: $("current-password").value, new_password: $("new-password").value } }));
      $("password-form").reset(); toast("Senha atualizada.");
    } catch (error) { $("password-error").textContent = error.message; }
  });
  for (const button of document.querySelectorAll("[data-action='logout']")) {
    button.addEventListener("click", async () => {
      try { await api("/auth/logout", { method: "POST", body: {} }); } catch {}
      clearDrafts(); state.selected = null; state.composerFor = null; $("composer-input").value = "";
      showLogin("Você saiu do painel.");
    });
  }
  for (const item of document.querySelectorAll(".nav-item")) {
    item.addEventListener("click", () => { closeDetail(); navigate(item.dataset.view, item.dataset.filter); });
  }
  for (const chip of document.querySelectorAll(".chip")) {
    chip.addEventListener("click", () => { state.filter = chip.dataset.filter; navigate("conversations", chip.dataset.filter); });
  }
  $("search-form").addEventListener("submit", (event) => { event.preventDefault(); state.query = $("search-input").value.trim(); refreshList(true); });
  $("search-input").addEventListener("search", () => { state.query = $("search-input").value.trim(); refreshList(true); });
  $("load-more").addEventListener("click", () => refreshList(false));
  $("back-btn").addEventListener("click", () => { closeDetail(); renderList(); });
  $("composer").addEventListener("submit", sendReply);
  $("composer-input").addEventListener("input", () => { updateCount(); saveDraft(); });
  $("list-retry").addEventListener("click", () => refreshList(true));
  for (const toggle of document.querySelectorAll("[data-notif-toggle]")) {
    toggle.addEventListener("click", () => (state.notif.open ? closeNotifications() : openNotifications()));
  }
  $("notif-close").addEventListener("click", closeNotifications);
  $("notif-sound").addEventListener("change", () => {
    state.notif.prefs.sound = $("notif-sound").checked;
    if (state.notif.prefs.sound && !audioContext) {
      try { audioContext = new AudioContext(); } catch { audioContext = null; }
    }
    STORAGE.set(notifKey("prefs"), state.notif.prefs);
    if (state.notif.prefs.sound) beep();  // prévia do som
  });
  for (const box of document.querySelectorAll("[data-notif-pref]")) {
    box.addEventListener("change", () => {
      const off = new Set(state.notif.prefs.off);
      if (box.checked) off.delete(box.dataset.notifPref); else off.add(box.dataset.notifPref);
      state.notif.prefs.off = [...off].filter((type) => NOTIF_OPTIONAL.includes(type));
      STORAGE.set(notifKey("prefs"), state.notif.prefs); renderNotifications();
    });
  }
  $("composer-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("composer").requestSubmit();
  });
  $("menu-toggle").addEventListener("click", () => {
    const open = !$("sidebar").classList.contains("open");
    setMenu(open);
    if (open) { const current = document.querySelector(".sidebar .nav-item[aria-current='page']") || document.querySelector(".sidebar .nav-item"); if (current) current.focus(); }
  });
  $("scrim").addEventListener("click", () => { closeMenu(); $("menu-toggle").focus(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMenu(); closeNotifications(); } });
  $("user-form").addEventListener("submit", createUser);
  $("knowledge-form").addEventListener("submit", submitKnowledge);
  $("knowledge-kind").addEventListener("change", () => showKnowledgeFields($("knowledge-kind").value));
  $("knowledge-pricing").addEventListener("change", updatePricingFields);
  $("knowledge-cancel-edit").addEventListener("click", resetKnowledgeForm);
  $("knowledge-preview-form").addEventListener("submit", previewKnowledge);
  $("knowledge-filter").addEventListener("submit", (event) => { event.preventDefault(); renderKnowledge(); });
  for (const id of ["knowledge-search", "knowledge-kind-filter", "knowledge-status-filter"]) {
    $(id).addEventListener(id === "knowledge-search" ? "input" : "change", renderKnowledge);
  }
  $("audit-more").addEventListener("click", loadAudit);
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("settings-error").textContent = "";
    const minutes = Number($("setting-urgent").value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
      $("settings-error").textContent = "Informe um número inteiro de 1 a 1440 minutos."; $("setting-urgent").focus(); return;
    }
    const send = async () => {
      try {
        await api("/settings", { method: "PUT", body: { operators_can_transfer: $("setting-transfer").checked,
          operators_can_resume: $("setting-resume").checked, urgent_after_minutes: minutes,
          retired_contact_policy: $("setting-retired-policy").value } });
        toast("Configurações salvas."); state.permissions = (await api("/settings")).permissions;
      } catch (error) { $("settings-error").textContent = error.message; }
    };
    if ($("setting-retired-policy").value === "allow_new_conversation" && state.permissions.retired_contact_policy !== "allow_new_conversation") {
      confirmAction("Permitir que contatos excluídos voltem a conversar?",
        "Essa decisão é do responsável pelos dados. Uma nova mensagem desses contatos abrirá uma conversa nova e vazia.", send);
    } else send();
  });
  $("halt-btn").addEventListener("click", () => {
    const input = el("input", { attrs: { id: "halt-reason", maxlength: "200", required: "" } });
    confirmAction("Interromper todos os envios?", "Nenhuma mensagem sairá até a retomada. A fila é preservada.", async () => {
      try { await api("/dispatch/halt", { method: "POST", body: { reason: input.value || "Interrupção pelo painel" } }); toast("Envios interrompidos."); }
      catch (error) { toast(error.message, "error"); }
      loadSettings();
    }, el("div", {}, [el("label", { text: "Motivo", attrs: { for: "halt-reason" } }), input]));
  });
  $("resume-dispatch-btn").addEventListener("click", () => confirmAction("Retomar envios?",
    "Mensagens na fila voltarão a ser processadas (somente simuladas nesta fase).", async () => {
      try { await api("/dispatch/resume", { method: "POST", body: { confirmation: "RESUME_DISPATCH" } }); toast("Envios retomados."); }
      catch (error) { toast(error.message, "error"); }
      loadSettings();
    }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.user) { scheduleListRefresh(); refreshCounts(); scheduleNotifications(); }
  });
}

boot();
