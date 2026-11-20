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
  auto: "Automáticas", resolved: "Histórico",
};
const ROLE_LABEL = { client: "Cliente", assistant: "Assistente automático", operator: "Equipe" };

const state = {
  user: null, csrf: "", permissions: {}, view: "conversations", filter: "waiting", query: "",
  items: [], cursor: null, selected: null, detail: null, operators: [], events: null,
  draftKey: null, sending: false, auditBefore: null,
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

function toast(message, kind = "info") {
  const node = el("div", { className: `toast ${kind}`, text: message });
  $("toasts").append(node);
  setTimeout(() => node.remove(), 5000);
}

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
  if (response.status === 401 && !path.startsWith("/auth/login")) { showLogin("Sua sessão terminou. Entre novamente."); }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) throw new ApiError(response.status, (data && data.detail) || "Não foi possível concluir a ação.");
  return data;
}

// ------------------------------------------------------------- sessões
function showOnly(viewId) {
  for (const id of ["login-view", "password-view", "app-view"]) $(id).hidden = id !== viewId;
}

function showLogin(message = "") {
  stopEvents();
  state.user = null; state.csrf = "";
  showOnly("login-view");
  $("login-error").textContent = message;
  $("password-input").value = "";
  $("login-input").focus();
}

function applySession(data) {
  state.user = data.user; state.csrf = data.csrf_token; state.permissions = data.permissions || {};
  if (state.user.must_change_password) {
    showOnly("password-view");
    $("current-password").focus();
    return;
  }
  showOnly("app-view");
  document.body.classList.toggle("is-admin", state.user.role === "ADMIN");
  for (const node of document.querySelectorAll(".admin-only")) node.hidden = state.user.role !== "ADMIN";
  $("user-name").textContent = state.user.display_name;
  $("user-role").textContent = state.user.role === "ADMIN" ? "Admin" : "Atendente";
  startEvents();
  navigate(state.view, state.filter);
}

async function boot() {
  bindUi();
  try { applySession(await api("/auth/session")); }
  catch { showLogin(); }
}

// ------------------------------------------------------------ navegação
function navigate(view, filter) {
  state.view = view;
  if (filter) state.filter = filter;
  for (const id of ["overview-view", "conversations-view", "team-view", "audit-view", "settings-view", "knowledge-view"]) {
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
  refreshCounts();
}

function closeMenu() { $("sidebar").classList.remove("open"); $("menu-toggle").setAttribute("aria-expanded", "false"); }

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
  try {
    const page = await api(`/conversations?${params}`);
    state.items = reset ? page.items : state.items.concat(page.items);
    state.cursor = page.next_cursor;
    renderList();
  } catch (error) { if (error.status !== 401) toast(error.message, "error"); }
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
    el("span", { className: "conv-name", text: item.label }),
    el("span", { className: "conv-time", text: formatTime(item.last_message ? item.last_message.at : item.updated_at) }),
    el("span", { className: "conv-preview", text: item.last_client_text
      || (item.last_message ? `${ROLE_LABEL[item.last_message.role]}: ${item.last_message.text}` : "Sem mensagens") }),
    el("span", { className: "conv-tags" }, [
      el("span", { className: `badge ${stateKey}`, text: STATE_LABEL[stateKey] }),
      item.assigned ? el("span", { className: "tag", text: `Com ${item.assigned.name}` }) : null,
      item.unread ? el("span", { className: "unread", text: String(item.unread),
        attrs: { "aria-label": `${item.unread} mensagens não lidas` } }) : null,
    ]),
  ]);
  if (item.id === state.selected) button.setAttribute("aria-current", "true");
  button.addEventListener("click", () => openConversation(item.id));
  return el("li", {}, [button]);
}

async function openConversation(id, { focus = true } = {}) {
  state.selected = id;
  try {
    state.detail = await api(`/conversations/${encodeURIComponent(id)}`);
  } catch (error) {
    if (error.status === 404) { closeDetail(); toast("Esta conversa não está mais disponível para você."); scheduleListRefresh(); return; }
    toast(error.message, "error"); return;
  }
  renderDetail();
  $("conversations-view").classList.add("showing-detail");
  if (focus) $("detail-title").focus?.();
  api(`/conversations/${encodeURIComponent(id)}/read`, { method: "POST", body: {} }).then(scheduleListRefresh).catch(() => {});
  for (const node of document.querySelectorAll(".conv")) node.removeAttribute("aria-current");
}

function closeDetail() {
  state.selected = null; state.detail = null;
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

  const items = d.messages.map((m) => {
    const author = m.role === "operator" && m.author ? `${ROLE_LABEL.operator} · ${m.author}` : ROLE_LABEL[m.role];
    const foot = [el("time", { text: formatTime(m.at), attrs: { datetime: m.at } })];
    if (m.delivery) {
      foot.push(el("span", { className: `delivery ${m.delivery.key}`, text: DELIVERY_LABEL[m.delivery.key] || m.delivery.key }));
    }
    return el("li", { className: `msg ${m.role}` }, [
      el("div", { className: "msg-author", text: author }),
      el("p", { className: "msg-text", text: m.text }),
      el("div", { className: "msg-foot" }, foot),
    ]);
  });
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
  if (!canReply) hint = d.state === "HUMAN_ACTIVE" ? "Somente o responsável pode responder." : "Assuma o atendimento para responder.";
  else if (!d.window.last_inbound_at) hint = "Sem mensagem do cliente pelo WhatsApp: não é possível responder por este canal.";
  else if (!windowOpen) hint = "A janela de 24h terminou. Respostas livres não são permitidas.";
  else hint = `Janela de atendimento aberta até ${formatTime(new Date(windowEnd).toISOString())}. Envio simulado (nenhuma mensagem real).`;
  $("composer-hint").textContent = hint;
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

async function refreshCounts() {
  try {
    const data = await api("/overview");
    $("count-waiting").textContent = data.conversations.WAITING_HUMAN ? String(data.conversations.WAITING_HUMAN) : "";
    $("count-mine").textContent = data.assigned_to_me ? String(data.assigned_to_me) : "";
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
  source.addEventListener("ready", () => setConnection("live", "Ao vivo"));
  source.addEventListener("changes", (event) => {
    setConnection("live", "Ao vivo");
    let data = {};
    try { data = JSON.parse(event.data); } catch { return; }
    scheduleListRefresh(); refreshCounts();
    if (state.selected && (data.other || (data.conversations || []).includes(state.selected))) {
      openConversation(state.selected, { focus: false });
    }
    if ((data.conversations || []).length) announce("Conversas atualizadas.");
  });
  source.addEventListener("reset", () => { scheduleListRefresh(); if (state.selected) openConversation(state.selected, { focus: false }); });
  source.addEventListener("auth", () => { stopEvents(); showLogin("Sua sessão terminou. Entre novamente."); });
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
    const cards = [
      ["Aguardando atendimento", c.WAITING_HUMAN || 0], ["Em atendimento humano", c.HUMAN_ACTIVE || 0],
      ["Comigo agora", data.assigned_to_me], ["No automático", c.AUTO || 0], ["Encerradas", (c.RESOLVED || 0) + (c.CLOSED || 0)],
    ];
    if (data.outbox) cards.push(["Envios incertos (UNKNOWN)", data.outbox.UNKNOWN || 0], ["Envios com falha", data.outbox.FAILED || 0]);
    $("overview-cards").replaceChildren(...cards.map(([label, value]) => el("div", { className: "card" }, [
      el("p", { className: "card-value", text: String(value) }), el("p", { className: "card-label", text: label })])));
    $("overview-note").textContent = data.dispatch_halted
      ? `Envios interrompidos: ${data.dispatch_halted.reason}` : "Ambiente local: transporte simulado, nenhuma mensagem real é enviada.";
  } catch (error) { toast(error.message, "error"); }
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
      return el("tr", {}, [
        el("td", { text: u.display_name }), el("td", { text: u.login }),
        el("td", { text: u.role === "ADMIN" ? "Administrador" : "Atendente" }),
        el("td", { text: u.active ? (u.must_change_password ? "Ativo · senha temporária" : "Ativo") : "Desativado" }),
        el("td", { text: formatTime(u.last_login_at) || "—" }), el("td", {}, [toggle]),
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
  user_deactivated: "Desativou acesso", user_activated: "Reativou acesso", conversation_assumed: "Assumiu atendimento",
  conversation_transferred: "Transferiu atendimento", conversation_resolved: "Finalizou atendimento",
  automation_resumed: "Retomou automação", handoff_requested: "Enviou para a fila", operator_message_queued: "Enviou mensagem",
  password_changed: "Trocou a senha", password_reset: "Redefiniu senha", admin_access: "Acesso administrativo",
  dispatch_halted: "Interrompeu envios", dispatch_resumed: "Retomou envios",
};

async function loadAudit() {
  try {
    const rows = await api(`/audit${state.auditBefore ? `?before=${state.auditBefore}` : ""}`);
    $("audit-body").append(...rows.map((r) => el("tr", {}, [
      el("td", { text: formatTime(r.at) }), el("td", { text: r.actor || "—" }),
      el("td", { text: ACTION_LABEL[r.action] || r.action }),
      el("td", { text: r.entity_type === "conversation" && r.entity_id ? `Cliente ··${r.entity_id.slice(-6)}` : r.entity_type }),
      el("td", { text: { success: "Sucesso", denied: "Negado", failed: "Falhou" }[r.outcome] || r.outcome }),
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
    $("dispatch-status").textContent = data.dispatch_halted
      ? `Envios INTERROMPIDOS: ${data.dispatch_halted.reason}` : "Envios liberados (transporte simulado).";
    $("environment-note").textContent = `Ambiente: ${data.environment} · transporte: ${data.transport} (nenhum envio real nesta fase).`;
  } catch (error) { toast(error.message, "error"); }
}

// ------------------------------------------------------------------ conhecimento (ADMIN)
// Gestão das informações oficiais: rascunho → aprovação → (revogação). A IA nunca aciona
// estas rotas; o backend confere o papel ADMIN em cada chamada.
const KIND_FIELDS = {
  identity: ["text"], location: ["text"], hours: ["text"], professionals: ["items"], policy: ["items"],
  cancellation: ["items"], booksy: ["url"], service: ["key", "name", "aliases", "description"],
  price: ["key", "amount", "note"], faq: ["key", "questions", "answer"],
};
const KNOWLEDGE_STATUS = { DRAFT: "Rascunho", APPROVED: "Aprovado", REJECTED: "Rejeitado", ARCHIVED: "Revogado" };

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `R$ ${number.toFixed(2).replace(".", ",")}` : String(value);
}

function summarize(data) {
  if (["identity", "location", "hours"].includes(data.kind)) return data.text;
  if (["professionals", "policy", "cancellation"].includes(data.kind)) return data.items.join(" · ");
  if (data.kind === "booksy") return data.url;
  if (data.kind === "service") {
    const aliases = data.service.aliases && data.service.aliases.length ? ` (${data.service.aliases.join(", ")})` : "";
    return `${data.service.name}${aliases}${data.service.description ? ` — ${data.service.description}` : ""}`;
  }
  if (data.kind === "price") return `${data.service_id}: ${money(data.amount)}${data.note ? ` — ${data.note}` : ""}`;
  if (data.kind === "faq") return `${data.faq.questions[0]} → ${data.faq.answer}`;
  return "";
}

async function loadKnowledge() {
  try {
    const data = await api("/knowledge");
    state.knowledge.records = data.records; state.knowledge.labels = data.labels; state.knowledge.kinds = data.kinds;
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
      el("td", { text: KNOWLEDGE_STATUS[record.status] || record.status }),
      el("td", { text: record.in_use ? "Sim" : "Não" }), el("td", {}, [actions]),
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
  $("knowledge-key").previousElementSibling.textContent = kind === "faq" ? "Identificador da pergunta" : "Identificador do serviço";
}

function resetKnowledgeForm() {
  state.knowledge.editing = null;
  $("knowledge-form").reset();
  $("knowledge-kind").disabled = false; $("knowledge-key").disabled = false;
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
  }
  if (data.kind === "price") { $("knowledge-amount").value = String(data.amount).replace(".", ","); $("knowledge-note").value = data.note || ""; }
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
    box.replaceChildren(el("span", { text: result.reply }),
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
      applySession(await api("/auth/login", { method: "POST",
        body: { login: $("login-input").value, password: $("password-input").value } }));
    } catch (error) { $("login-error").textContent = error.message; $("password-input").value = ""; }
    finally { if (button) button.disabled = false; }
  });
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
    button.addEventListener("click", async () => { try { await api("/auth/logout", { method: "POST", body: {} }); } catch {} showLogin("Você saiu do painel."); });
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
  $("composer-input").addEventListener("input", () => { updateCount(); });
  $("composer-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("composer").requestSubmit();
  });
  $("menu-toggle").addEventListener("click", () => {
    const open = !$("sidebar").classList.contains("open");
    $("sidebar").classList.toggle("open", open); $("menu-toggle").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
  $("user-form").addEventListener("submit", createUser);
  $("knowledge-form").addEventListener("submit", submitKnowledge);
  $("knowledge-kind").addEventListener("change", () => showKnowledgeFields($("knowledge-kind").value));
  $("knowledge-cancel-edit").addEventListener("click", resetKnowledgeForm);
  $("knowledge-preview-form").addEventListener("submit", previewKnowledge);
  $("knowledge-filter").addEventListener("submit", (event) => { event.preventDefault(); renderKnowledge(); });
  for (const id of ["knowledge-search", "knowledge-kind-filter", "knowledge-status-filter"]) {
    $(id).addEventListener(id === "knowledge-search" ? "input" : "change", renderKnowledge);
  }
  $("audit-more").addEventListener("click", loadAudit);
  $("settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/settings", { method: "PUT", body: { operators_can_transfer: $("setting-transfer").checked,
        operators_can_resume: $("setting-resume").checked } });
      toast("Permissões salvas.");
    } catch (error) { toast(error.message, "error"); }
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
    if (document.visibilityState === "visible" && state.user) { scheduleListRefresh(); refreshCounts(); }
  });
}

boot();
