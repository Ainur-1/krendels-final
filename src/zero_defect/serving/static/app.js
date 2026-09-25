// Интерфейс Zero Defect: один экран вокруг графа линии. Без сборки и внешних библиотек —
// в закрытом контуре нет ни CDN, ни npm, и страница отдаётся тем же сервером, что и API.
import { CONTRACT } from "./contract.js";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const time = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
const mins = (s) => (s == null ? "—" : s >= 60 ? `${(s / 60).toFixed(1)} мин` : `${Math.round(s)} с`);
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)} %`);
const rub = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString("ru-RU")} ₽`);
const badge = (text, tone = "plain") => `<span class="badge ${tone}">${esc(text)}</span>`;

const L = {
  role: { controller: "контролёр ОТК", master: "мастер участка", technologist: "технолог", manager: "руководитель производства", admin: "администратор" },
  status: { conforming: ["годно", "ok"], nonconforming: ["несоответствие", "bad"], suspect: ["на рассмотрении", "warn"], not_assessable: ["оценка невозможна", "warn"], in_progress: ["в работе", "info"], unknown: ["нет данных", "plain"] },
  nc: { reported: ["сообщение о признаке", "warn"], under_review: ["рассматривается", "info"], recheck_requested: ["доп. проверка", "info"], confirmed: ["подтверждено", "bad"], rejected: ["отклонено", "plain"], closed: ["закрыто", "ok"] },
  stage: { incoming: "входной брак", operation: "возникло на операции", between_checks: "между проверками", unknown: "этап не установлен" },
  conf: { strong: ["основания сильные", "ok"], moderate: ["основания умеренные", "warn"], insufficient: ["сведений недостаточно", "bad"] },
  cause: { incoming_defect: "входной брак", equipment_problem: "проблема оборудования", operator_error: "ошибка оператора", process_issue: "техпроцесс", handling_damage: "повреждение при перемещении", not_established: "не установлена", other: "иное" },
  action: { start_review: "Начать рассмотрение", request_recheck: "Назначить доп. проверку", confirm: "Подтвердить несоответствие", reject: "Отклонить сигнал", close: "Закрыть (устранено)", reopen: "Открыть заново", confirm_cause: "Установить причину" },
  pathStatus: { pending: "ещё не прошёл", passed: "пройден", processing: "в работе", reworked: "доработка", not_assessable: "оценка невозможна", possible_origin: "возможное место возникновения", defect_detected: "дефект обнаружен здесь", defect_origin: "дефект возник здесь" },
  result: { no_defect_signs: ["признаков нет", "ok"], defect_signs_found: ["признаки дефекта", "bad"], not_assessable: ["оценка невозможна", "warn"] },
  event: { item_registered: "поступление", component_linked: "установка компонента", operation_started: "начало операции", operation_paused: "пауза", operation_resumed: "возобновление", operation_finished: "завершение операции", inspection_reported: "контроль", operator_action: "действие оператора", machine_state: "состояние станка" },
};

// Что каждая роль видит первым и как ей работать с системой.
const ROLES = {
  controller: {
    about: "Рассматривает сигналы анализатора, подтверждает или отклоняет несоответствия.",
    guide: ["Слева — очередь несоответствий: сначала критичные.", "Откройте карточку: сверху исходное сообщение, ниже разбор системы, внизу решения.", "Примите решение с обоснованием — оно ляжет отдельной записью, исходное сообщение не меняется.", "Красная рамка этапа на графе — там обнаружено то, что ждёт вашего решения."],
  },
  master: {
    about: "Следит за ходом линии: что в работе, где отклонения, что застряло.",
    guide: ["Граф показывает, где сейчас изделия: точки движутся по этапам.", "Слева — операции в работе, отклонения оборудования и пропуски сообщений.", "Нажмите этап — справа его статистика и изделия, прошедшие через него.", "Кнопки пуска и внесения дефекта управляют эмуляцией линии."],
  },
  technologist: {
    about: "Ищет, на каких этапах и при каких обстоятельствах возникают дефекты.",
    guide: ["Слева — где возникают дефекты по оценке системы: чем длиннее полоса, тем чаще.", "Таблица этапов под графом фильтруется по типу изделия, смене и периоду.", "Этап справа показывает дефекты, возникшие и обнаруженные на нём, и операторов.", "Установите причину подтверждённого несоответствия в его карточке."],
  },
  manager: {
    about: "Главная роль линии: показатели, экономика, создание новых линий и загрузка потоков.",
    guide: ["Слева — показатели линии и кнопки: экономика, новая линия, загрузка потока.", "«Экономика» считает потери и стоимость годного по вашим параметрам и измерениям.", "«Новая линия» — задайте этапы по порядку; граф построится сам.", "«Загрузить поток» — свой файл с этапами и изделиями проиграется на графе за заданное время."],
  },
  admin: {
    about: "Целостность журнала, ключи, аудит, интеграции; видит экраны всех ролей.",
    guide: ["«Смотреть как» в шапке показывает экран любой роли — удобно проверять права.", "Слева — проверка целостности журнала, ключи, журнал действий, интеграции.", "Любое вмешательство в журнал в обход приложения видно при проверке с номером записи.", "Создание линий и загрузка потоков доступны так же, как главной роли."],
  },
};

const S = {
  token: null, me: null, viewRole: null, lines: [], lineId: null, live: null, prevNodes: {}, stages: [],
  overview: null, selected: null, injectMode: null, filters: {}, itemFilters: { item_type: "", status: "", stage: "", q: "" },
  geometry: null, timers: [], lastRunFinished: false,
};

function notify(text, bad = false) {
  const toast = $("toast");
  toast.textContent = text;
  toast.className = bad ? "toast bad" : "toast";
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.add("hidden"), 4500);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(S.token ? { Authorization: `Bearer ${S.token}` } : {}) };
  const response = await fetch(path, { ...options, headers });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : await response.text();
  if (response.status === 401 && S.token) { logout(); throw new Error("сессия истекла, войдите снова"); }
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body));
  return body;
}
const remember = (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* без хранилища */ } };
const recall = (k) => { try { return sessionStorage.getItem(k); } catch { return null; } };
const forget = (k) => { try { sessionStorage.removeItem(k); } catch { /* без хранилища */ } };
const can = (permission) => S.me?.permissions.includes(permission);

// --- вход ------------------------------------------------------------------------------

async function showLogin() {
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  const options = await api("/api/auth/options");
  $("role-cards").innerHTML = options.users.map((u) => `<button class="role-card" data-user="${esc(u.user_id)}">
      <b>${esc(L.role[u.role] || u.role)}</b><span>${esc(u.name)} · ${esc(u.user_id)}</span><span>${esc(ROLES[u.role]?.about || "")}</span></button>`).join("")
    || `<p class="muted">Вход по имени пользователя и паролю.</p>`;
  $("role-cards").querySelectorAll("[data-user]").forEach((b) => b.addEventListener("click", async () => {
    const result = await api("/api/auth/demo-login", { method: "POST", body: JSON.stringify({ user_id: b.dataset.user }) });
    await enter(result.token);
  }));
}

$("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  try {
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ user_id: form.user_id.value, password: form.password.value }) });
    await enter(result.token);
  } catch (error) { notify(error.message, true); }
});

async function enter(token) {
  S.token = token;
  remember("zd-token", token);
  S.me = await api("/api/me");
  S.viewRole = S.me.role;
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  await boot();
}

function logout() {
  S.timers.forEach(clearInterval);
  S.timers = [];
  S.token = null;
  forget("zd-token");
  showLogin();
}
$("logout").addEventListener("click", logout);

// --- каркас ------------------------------------------------------------------------------

async function boot() {
  $("user-chip").innerHTML = `${esc(S.me.name)} · <b>${esc(L.role[S.me.role] || S.me.role)}</b>`;
  const viewAs = $("view-as");
  $("view-as-wrap").classList.toggle("hidden", !can("view_as"));
  viewAs.innerHTML = Object.keys(ROLES).map((r) => `<option value="${r}">${esc(L.role[r])}</option>`).join("");
  viewAs.value = S.viewRole;
  viewAs.onchange = () => { S.viewRole = viewAs.value; renderGuide(); renderRolePanel(); openDefault(); };
  await loadLines(recall("zd-line"));
  renderTopActions();
  renderGuide();
  S.timers.forEach(clearInterval);
  S.timers = [setInterval(refreshLive, 1200), setInterval(refreshOverview, 5000), setInterval(refreshStages, 9000)];
}

async function loadLines(prefer) {
  S.lines = await api("/api/lines");
  const select = $("line-select");
  select.innerHTML = S.lines.map((l) => `<option value="${esc(l.line_id)}">${esc(l.title)}</option>`).join("");
  S.lineId = S.lines.some((l) => l.line_id === prefer) ? prefer : S.lines[0]?.line_id;
  select.value = S.lineId;
  select.onchange = () => switchLine(select.value);
  await switchLine(S.lineId);
}

async function switchLine(lineId) {
  S.lineId = lineId;
  remember("zd-line", lineId);
  S.geometry = null;
  S.prevNodes = {};
  S.selected = null;
  const line = S.lines.find((l) => l.line_id === lineId);
  $("f-type").innerHTML = `<option value="">все типы изделий</option>` + (line?.item_types || []).map((t) => `<option>${esc(t)}</option>`).join("");
  S.filters = {};
  S.itemFilters = { item_type: "", status: "", stage: "", q: "" };
  await Promise.all([refreshLive(), refreshOverview(), refreshStages()]);
  openDefault();
}

function renderTopActions() {
  const actions = [];
  if (can("line_manage")) {
    actions.push(`<button class="btn small" data-open="flow">Загрузить поток</button>`, `<button class="btn small" data-open="editor">Новая линия</button>`);
  }
  actions.push(`<button class="btn small" data-open="economics">Экономика</button>`);
  $("top-actions").innerHTML = actions.join("");
  $("top-actions").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => OPEN[b.dataset.open]()));
}

function renderGuide() {
  const role = ROLES[S.viewRole];
  $("guide").innerHTML = `<details open><summary>Как работать · ${esc(L.role[S.viewRole])}</summary><ol>${role.guide.map((g) => `<li>${esc(g)}</li>`).join("")}</ol></details>`;
}

// --- граф линии ------------------------------------------------------------------------------

const NODE_W = 156, NODE_H = 68;
// Координаты линии растягиваются по горизонтали: между этапами остаётся место для стрелки
// и изделий, подписи не наезжают друг на друга.
const SX = 1.25, SY = 1.0;

function geometry(raw) {
  const nodes = raw.map((n) => ({ ...n, x: n.x * SX, y: n.y * SY }));
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs) - NODE_W / 2 - 60, maxX = Math.max(...xs) + NODE_W / 2 + 30;
  const minY = Math.min(...ys) - NODE_H / 2 - 40, maxY = Math.max(...ys) + NODE_H / 2 + 70;
  return { minX, minY, width: maxX - minX, height: maxY - minY, pos: Object.fromEntries(nodes.map((n) => [n.node_id, n])) };
}

function edgePath(a, b) {
  const x1 = a.x + NODE_W / 2, y1 = a.y, x2 = b.x - NODE_W / 2, y2 = b.y;
  if (Math.abs(y1 - y2) < 1) return `M${x1},${y1} L${x2},${y2}`;
  if (x2 <= x1 + 10) {
    const midY = (y1 + y2) / 2;
    return `M${a.x},${a.y + NODE_H / 2} C${a.x},${midY} ${b.x},${midY} ${b.x},${b.y - NODE_H / 2}`;
  }
  const mid = (x1 + x2) / 2;
  return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
}

function buildGraph(live) {
  const g = geometry(live.nodes);
  live = { ...live, nodes: live.nodes.map((n) => g.pos[n.node_id]) };
  S.geometry = g;
  const svg = $("graph");
  svg.setAttribute("viewBox", `${g.minX} ${g.minY} ${g.width} ${g.height}`);
  const edges = live.edges.map(([a, b]) => `<path class="edge" d="${edgePath(g.pos[a], g.pos[b])}" marker-end="url(#arrow)"/>`).join("");
  const nodes = live.nodes.map((n) => {
    const icon = n.kind === "operation" ? (n.assembly ? "⧉" : "⚙") : "◉";
    const sub = n.kind === "operation" ? (n.equipment_id || n.station_id) : ({ incoming: "входной контроль", after_operation: "контроль", final: "финальный контроль" }[n.checkpoint_kind] || "контроль");
    const title = n.title.length > 19 ? `${n.title.slice(0, 18)}…` : n.title;
    return `<g class="node ${n.kind}" data-node="${esc(n.node_id)}" transform="translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})">
      <title>${esc(n.title)}</title>
      <rect class="box" width="${NODE_W}" height="${NODE_H}" rx="12"/>
      <text class="icon" x="12" y="24">${icon}</text>
      <text x="32" y="24">${esc(title)}</text>
      <text class="sub" x="12" y="44">${esc(sub)}</text>
      <text class="sub" data-f="passed" x="12" y="60"></text>
      <g data-f="badges"></g>
    </g>`;
  }).join("");
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--flow)"/></marker></defs>
    <g id="edges">${edges}</g><g id="nodes">${nodes}</g><g id="fx"></g><g id="items"></g>`;
  svg.querySelectorAll(".node").forEach((el) => el.addEventListener("click", () => nodeClicked(el.dataset.node)));
}

function renderGraph(live) {
  if (!S.geometry || S.geometry.lineId !== live.line_id || S.geometry.version !== live.version) {
    buildGraph(live);
    S.geometry.lineId = live.line_id;
    S.geometry.version = live.version;
  }
  const svg = $("graph");
  const running = live.emulation.running || (live.emulation.run && !live.emulation.run.finished);
  svg.querySelectorAll(".edge").forEach((e) => e.classList.toggle("running", !!running));
  for (const raw of live.nodes) {
    const n = { ...raw, ...S.geometry.pos[raw.node_id], ...raw, x: S.geometry.pos[raw.node_id].x, y: S.geometry.pos[raw.node_id].y };
    const el = svg.querySelector(`.node[data-node="${CSS.escape(n.node_id)}"]`);
    if (!el) continue;
    el.classList.remove("state-ok", "state-warning", "state-alarm");
    el.classList.add(`state-${n.state}`);
    el.classList.toggle("selected", S.selected?.type === "node" && S.selected.id === n.node_id);
    el.classList.toggle("inject", !!S.injectMode);
    el.querySelector('[data-f="passed"]').textContent = `прошло ${n.passed}${n.in_work ? ` · в работе ${n.in_work}` : ""}`;
    const badges = [];
    let x = NODE_W - 8;
    const addBadge = (value, tone, title) => {
      const w = 12 + String(value).length * 7;
      x -= w;
      badges.push(`<g><title>${title}</title><rect class="count-bg ${tone}" x="${x}" y="-9" width="${w}" height="18" rx="9"/><text class="count light" x="${x + w / 2}" y="4" text-anchor="middle">${value}</text></g>`);
      x -= 4;
    };
    if (n.open_detections) addBadge(n.open_detections, "bad", "обнаружено здесь, ждёт решения");
    if (n.originated) addBadge(n.originated, "warn", "возникло здесь по оценке системы");
    el.querySelector('[data-f="badges"]').innerHTML = badges.join("");
    const before = S.prevNodes[n.node_id];
    if (before && n.detections > before.detections) flash(n);
    S.prevNodes[n.node_id] = n;
  }
  renderItems(live);
}

function flash(n) {
  const fx = $("graph").querySelector("#fx");
  const ring = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  ring.setAttribute("class", "flash");
  ring.setAttribute("x", n.x - NODE_W / 2 - 6);
  ring.setAttribute("y", n.y - NODE_H / 2 - 6);
  ring.setAttribute("width", NODE_W + 12);
  ring.setAttribute("height", NODE_H + 12);
  ring.setAttribute("rx", 16);
  fx.appendChild(ring);
  setTimeout(() => ring.remove(), 1700);
}

function renderItems(live) {
  const layer = $("graph").querySelector("#items");
  const g = S.geometry;
  const slots = {};
  const firstSource = g.pos[live.nodes[0].node_id];
  const seen = new Set();
  for (const item of live.items) {
    const node = g.pos[item.node_id] || null;
    const key = item.node_id || "__entry";
    const k = (slots[key] = (slots[key] ?? -1) + 1);
    if (k > 8) continue;
    const base = node || { x: firstSource.x - NODE_W / 2 - 34, y: firstSource.y - 20 };
    const x = node ? base.x - NODE_W / 2 + 12 + k * 17 : base.x;
    const y = node ? base.y + NODE_H / 2 + 14 : base.y + k * 17;
    seen.add(item.item_id);
    let el = layer.querySelector(`[data-item="${CSS.escape(item.item_id)}"]`);
    if (!el) {
      el = document.createElementNS("http://www.w3.org/2000/svg", "g");
      el.dataset.item = item.item_id;
      el.innerHTML = `<title></title><circle r="6"/>`;
      el.style.transform = `translate(${x}px, ${y}px)`;
      el.addEventListener("click", (e) => { e.stopPropagation(); openItem(el.dataset.item); });
      layer.appendChild(el);
    }
    el.setAttribute("class", `item s-${item.status || "in_progress"}${item.processing ? " processing" : ""}`);
    el.querySelector("title").textContent = `${item.item_id} · ${item.item_type} · ${(L.status[item.status] || [item.status])[0]}`;
    requestAnimationFrame(() => { el.style.transform = `translate(${x}px, ${y}px)`; });
  }
  layer.querySelectorAll("[data-item]").forEach((el) => { if (!seen.has(el.dataset.item)) el.remove(); });
}

async function nodeClicked(nodeId) {
  if (S.injectMode) {
    try {
      await api(`/api/lines/${S.lineId}/emulation/inject`, { method: "POST", body: JSON.stringify({ node_id: nodeId, kind: S.injectMode }) });
      notify(`${S.injectMode === "deviation" ? "Отклонение оборудования" : "Дефект"} будет внесён в следующее изделие на этапе «${S.geometry.pos[nodeId].title}».`);
    } catch (error) { notify(error.message, true); }
    S.injectMode = null;
    renderControls();
    return;
  }
  openNode(nodeId);
}

function renderControls() {
  const live = S.live;
  if (!live) return;
  const em = live.emulation;
  $("line-title").textContent = live.title;
  $("emulation-state").textContent = em.run && !em.run.finished ? `· прогон потока, ×${Math.round(em.speed)}` : em.running ? `· эмуляция идёт, ×${Math.round(em.speed)}` : "· эмуляция остановлена";
  const controls = [];
  if (can("emulate")) {
    controls.push(em.running ? `<button class="btn small" data-act="stop">■ Стоп</button>` : `<button class="btn small primary" data-act="start">▶ Пуск</button>`);
    controls.push(`<select data-act="speed" aria-label="Ускорение">${[30, 60, 120, 240].map((v) => `<option value="${v}" ${Math.round(em.speed) === v ? "selected" : ""}>×${v}</option>`).join("")}</select>`);
    controls.push(`<button class="btn small ${S.injectMode === "defect" ? "active" : ""}" data-act="inject-defect">Внести дефект</button>`);
    controls.push(`<button class="btn small ${S.injectMode === "deviation" ? "active" : ""}" data-act="inject-deviation">Отклонение станка</button>`);
  }
  const box = $("graph-controls");
  box.innerHTML = controls.join("");
  box.querySelector('[data-act="start"]')?.addEventListener("click", () => emulation("start"));
  box.querySelector('[data-act="stop"]')?.addEventListener("click", () => emulation("stop"));
  box.querySelector('[data-act="speed"]')?.addEventListener("change", (e) => emulation("start", Number(e.target.value)));
  box.querySelector('[data-act="inject-defect"]')?.addEventListener("click", () => { S.injectMode = S.injectMode === "defect" ? null : "defect"; renderControls(); if (S.injectMode) notify("Нажмите этап на графе, куда внести дефект."); });
  box.querySelector('[data-act="inject-deviation"]')?.addEventListener("click", () => { S.injectMode = S.injectMode === "deviation" ? null : "deviation"; renderControls(); if (S.injectMode) notify("Нажмите операцию на графе, где станок отклонится от режима."); });
  const run = em.run;
  const progress = $("run-progress");
  if (run && (!run.finished || run.elapsed_s < run.duration_s + 30)) {
    progress.classList.remove("hidden");
    progress.innerHTML = `<div class="row spread" style="margin-bottom:4px"><span>Прогон потока: ${run.items} изделий, ${run.events_delivered} из ${run.events_total} событий</span><span class="muted">${run.finished ? "завершён" : `${Math.round(run.elapsed_s)} с из ~${Math.round(run.duration_s)} с`}</span></div><div class="progress"><i style="width:${Math.round(run.progress * 100)}%"></i></div>`;
    if (run.finished && !S.lastRunFinished) { S.lastRunFinished = true; notify("Прогон завершён: итоги — в таблице этапов и в очереди несоответствий."); refreshStages(); refreshOverview(); }
    if (!run.finished) S.lastRunFinished = false;
  } else progress.classList.add("hidden");
}

async function emulation(action, speed) {
  try {
    await api(`/api/lines/${S.lineId}/emulation`, { method: "POST", body: JSON.stringify({ action, speed }) });
    await refreshLive();
  } catch (error) { notify(error.message, true); }
}

async function refreshLive() {
  if (!S.lineId || !S.token) return;
  try {
    S.live = await api(`/api/lines/${S.lineId}/live`);
    renderGraph(S.live);
    renderControls();
  } catch (error) { console.warn(error); }
}

// --- этапы ---------------------------------------------------------------------------------

function filterQuery() {
  const p = new URLSearchParams();
  const type = $("f-type").value, shift = $("f-shift").value, since = $("f-since").value, until = $("f-until").value;
  if (type) p.set("item_type", type);
  if (shift) p.set("shift", shift);
  if (since) p.set("since", new Date(since).toISOString());
  if (until) p.set("until", new Date(until).toISOString());
  return p.toString();
}
["f-type", "f-shift", "f-since", "f-until"].forEach((id) => $(id).addEventListener("change", () => { refreshStages(); if (S.selected?.type === "node") openNode(S.selected.id); }));
$("open-items").addEventListener("click", () => openItems());

async function refreshStages() {
  if (!S.lineId || !S.token) return;
  const rows = await api(`/api/lines/${S.lineId}/stages?${filterQuery()}`);
  S.stages = rows;
  const head = `<tr><th>этап</th><th class="num">изделий</th><th class="num">выполнений / проверок</th><th class="num">в работе</th><th class="num">доработок</th><th class="num" title="по данным источника, иначе измерено системой">длительность</th><th class="num">откл. станка</th><th class="num">обнаружено</th><th class="num">возникло</th><th class="num">без признаков</th></tr>`;
  const body = rows.map((r) => {
    const op = r.kind === "operation";
    return `<tr class="click ${S.selected?.type === "node" && S.selected.id === r.node_id ? "selected" : ""}" data-node="${esc(r.node_id)}">
      <td>${op ? "⚙" : "◉"} ${esc(r.title)}</td>
      <td class="num">${r.items}</td>
      <td class="num">${op ? r.runs : r.checks}</td>
      <td class="num">${op ? r.in_progress : "—"}</td>
      <td class="num">${op ? r.rework_runs : "—"}</td>
      <td class="num">${op ? mins(r.median_reported_s ?? r.median_active_s) : "—"}</td>
      <td class="num">${op ? r.deviations : "—"}</td>
      <td class="num">${op ? "—" : r.first_detections ? badge(r.first_detections, "bad") : 0}</td>
      <td class="num">${r.defects_originated ? badge(r.defects_originated, "warn") : 0}</td>
      <td class="num">${op ? "—" : pct(r.pass_rate)}</td></tr>`;
  }).join("");
  $("stages").innerHTML = `<table>${head}${body}</table>`;
  $("stages").querySelectorAll("tr[data-node]").forEach((tr) => tr.addEventListener("click", () => openNode(tr.dataset.node)));
}

// --- панель роли ------------------------------------------------------------------------------

async function refreshOverview() {
  if (!S.lineId || !S.token) return;
  S.overview = await api(`/api/lines/${S.lineId}/overview`);
  renderRolePanel();
}

const kpi = (value, label) => `<div class="kpi"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
const title = (id) => S.geometry?.pos[id]?.title || id;

function queueList(queue) {
  if (!queue.length) return `<div class="empty">Очередь пуста.</div>`;
  return `<ul class="list">${queue.map((n) => `<li class="click" data-nc="${esc(n.nc_id)}"><div class="row spread"><b>${esc(n.defect_type)}</b>${badge(n.severity || "—", n.severity === "critical" ? "bad" : n.severity === "major" ? "warn" : "plain")}</div>
    <div class="muted mono">${esc(n.item_id)} · ${esc(n.checkpoint_id)} · ${time(n.first_detected_at)}</div></li>`).join("")}</ul>`;
}

function renderRolePanel() {
  const o = S.overview;
  if (!o) return;
  const k = o.kpi;
  const role = S.viewRole;
  let html = "";
  if (role === "controller") {
    html = `<div class="card-head"><h2>Очередь на решение</h2>${badge(k.open_nonconformances, k.open_nonconformances ? "warn" : "ok")}</div>
      <div class="kpis" style="margin-bottom:10px">${kpi(k.open_nonconformances, "ждут решения")}${kpi(k.confirmed, "подтверждено")}</div>${queueList(o.queue)}`;
  } else if (role === "master") {
    html = `<div class="card-head"><h2>Сейчас на линии</h2></div>
      <div class="kpis">${kpi(k.in_progress_runs, "операций в работе")}${kpi(k.late_events, "событий опоздало")}</div>
      <h3 style="margin-top:12px">В работе</h3>${o.in_progress.length ? `<ul class="list">${o.in_progress.map((r) => `<li class="click" data-item="${esc(r.item_id)}"><b class="mono">${esc(r.item_id)}</b> · ${esc(title(r.node_id))}<div class="muted">${esc(r.operator_id || "")} · с ${time(r.started_at)}</div></li>`).join("")}</ul>` : `<div class="empty">Операций в работе нет.</div>`}
      <h3>Отклонения оборудования</h3>${o.deviations.length ? `<ul class="list">${o.deviations.map((d) => `<li class="click" data-node="${esc(d.node_id)}"><b>${esc(d.equipment_id)}</b> ${badge(d.state, "warn")}<div class="muted">${esc(d.message || "")} · ${time(d.at)}</div></li>`).join("")}</ul>` : `<div class="empty">Отклонений нет.</div>`}
      <h3>Пропуски сообщений</h3>${Object.keys(o.source_gaps).length ? `<ul class="plain">${Object.entries(o.source_gaps).map(([s, g]) => `<li class="mono">${esc(s)}: №${g.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}</li>`).join("")}</ul>` : `<div class="empty">Пропусков нет.</div>`}`;
  } else if (role === "technologist") {
    const entries = Object.entries(o.origins_by_node);
    const max = Math.max(1, ...entries.map(([, v]) => v));
    html = `<div class="card-head"><h2>Где возникают дефекты</h2></div>
      ${entries.length ? `<div class="bars">${entries.map(([node, v]) => `<div class="bar-row" data-node="${esc(node)}"><small>${esc(title(node))}</small><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><b>${v}</b></div>`).join("")}</div>` : `<div class="empty">Дефектов нет.</div>`}
      <h3 style="margin-top:14px">Гипотезы системы, не подтверждённые людьми</h3>${Object.keys(o.hypotheses).length ? `<ul class="plain">${Object.entries(o.hypotheses).map(([c, v]) => `<li>${esc(L.cause[c] || c)}: ${v}</li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <div class="row" style="margin-top:12px"><a class="btn small" href="#" data-ocel>Выгрузка OCEL 2.0</a></div>`;
  } else if (role === "manager") {
    html = `<div class="card-head"><h2>Показатели линии</h2></div>
      <div class="kpis">${kpi(k.items, "изделий")}${kpi(k.conforming, "годно")}${kpi(k.nonconforming, "несоответствий у изделий")}${kpi(k.rework_runs, "доработок")}${kpi(k.open_nonconformances, "ждут решения")}${kpi(k.confirmed, "подтверждено")}</div>
      <div class="stack" style="margin-top:12px"><button class="btn" data-open="economics">Экономика линии</button>
      ${can("line_manage") ? `<button class="btn" data-open="editor">Создать новую линию</button><button class="btn" data-open="flow">Загрузить поток и проиграть</button>` : ""}</div>`;
  } else if (role === "admin") {
    html = `<div class="card-head"><h2>Администрирование</h2></div>
      <div class="stack"><button class="btn" data-admin="integrity">Проверка целостности журнала</button><button class="btn" data-admin="keys">Ключи и профили</button>
      <button class="btn" data-admin="audit">Журнал критических действий</button><button class="btn" data-admin="integration">Интеграции</button><button class="btn" data-admin="users">Пользователи</button></div>
      <div class="kpis" style="margin-top:12px">${kpi(k.items, "изделий")}${kpi(k.open_nonconformances, "ждут решения")}</div>`;
  }
  const panel = $("role-panel");
  panel.innerHTML = html;
  panel.querySelectorAll("[data-nc]").forEach((el) => el.addEventListener("click", () => openNc(el.dataset.nc)));
  panel.querySelectorAll("[data-item]").forEach((el) => el.addEventListener("click", () => openItem(el.dataset.item)));
  panel.querySelectorAll("[data-node]").forEach((el) => el.addEventListener("click", () => openNode(el.dataset.node)));
  panel.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => OPEN[el.dataset.open]()));
  panel.querySelectorAll("[data-admin]").forEach((el) => el.addEventListener("click", () => openAdmin(el.dataset.admin)));
  panel.querySelector("[data-ocel]")?.addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      const data = await api("/api/export/ocel");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
      Object.assign(document.createElement("a"), { href: url, download: "zero-defect.ocel.json" }).click();
    } catch (error) { notify(error.message, true); }
  });
}

// --- контекстная панель -------------------------------------------------------------------------

function context(html) {
  $("context").innerHTML = html;
  const ctx = $("context");
  ctx.querySelectorAll("[data-nc]").forEach((el) => el.addEventListener("click", () => openNc(el.dataset.nc)));
  ctx.querySelectorAll("[data-item]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); openItem(el.dataset.item); }));
  ctx.querySelectorAll("[data-node]").forEach((el) => el.addEventListener("click", () => openNode(el.dataset.node)));
  ctx.scrollTop = 0;
}

function select(type, id) {
  S.selected = { type, id };
  if (S.live) renderGraph(S.live);
  $("stages").querySelectorAll("tr[data-node]").forEach((tr) => tr.classList.toggle("selected", type === "node" && tr.dataset.node === id));
}

function openDefault() {
  select(null, null);
  const k = S.overview?.kpi || {};
  context(`<div class="card-head"><h2>Детали</h2></div>
    <p class="muted">Нажмите этап на графе или в таблице — здесь появится его статистика. Точка на графе — изделие: по нажатию откроется его маршрут A → B → C с отметкой, где возник и где обнаружен дефект.</p>
    <div class="kpis">${kpi(k.items ?? "—", "изделий на линии")}${kpi(k.open_nonconformances ?? "—", "ждут решения")}${kpi(k.in_progress_runs ?? "—", "операций в работе")}${kpi(k.rework_runs ?? "—", "доработок")}</div>
    ${S.viewRole === "controller" && S.overview?.queue.length ? `<h3 style="margin-top:14px">Первое в очереди</h3>${queueList(S.overview.queue.slice(0, 3))}` : ""}`);
}

async function openNode(nodeId) {
  select("node", nodeId);
  const d = await api(`/api/lines/${S.lineId}/stages/${encodeURIComponent(nodeId)}?${filterQuery()}`);
  const s = d.stats;
  const op = s.kind === "operation";
  const stats = op
    ? `${kpi(s.runs, "выполнений")}${kpi(s.in_progress, "в работе")}${kpi(mins(s.median_active_s), "медиана, система")}${kpi(mins(s.median_reported_s), "медиана, источник")}${kpi(s.rework_runs, "доработок")}${kpi(s.deviations, "отклонений станка")}${kpi(s.defects_originated, "дефектов возникло")}${kpi(s.defects_confirmed, "из них подтверждено")}`
    : `${kpi(s.checks, "проверок")}${kpi(pct(s.pass_rate), "без признаков")}${kpi(s.found, "с признаками")}${kpi(s.not_assessable, "оценка невозможна")}${kpi(s.first_detections, "впервые обнаружено")}${kpi(s.open_detections, "ждут решения")}`;
  const operators = op && Object.keys(s.by_operator).length
    ? `<h3>Сопоставимые работы по операторам</h3><table><tr><th>оператор</th><th class="num">выполнений</th><th class="num">подтв. ошибок</th></tr>${Object.entries(s.by_operator).map(([who, r]) => `<tr><td class="mono">${esc(who)}</td><td class="num">${r.runs}</td><td class="num">${r.confirmed_errors}</td></tr>`).join("")}</table>` : "";
  const types = Object.keys(s.defect_types).length ? `<h3>Виды дефектов</h3><div class="row">${Object.entries(s.defect_types).map(([t, v]) => badge(`${t}: ${v}`, "warn")).join("")}</div>` : "";
  const ncs = (list, label) => list.length ? `<h3>${label}</h3><ul class="list">${list.slice(0, 8).map((n) => `<li class="click" data-nc="${esc(n.nc_id)}"><div class="row spread"><b>${esc(n.defect_type)}</b>${badge(...(L.nc[n.status] || [n.status]))}</div><div class="muted mono">${esc(n.item_id)} · ${time(n.first_detected_at)}</div></li>`).join("")}</ul>` : "";
  const items = d.items.length ? `<h3>Изделия на этапе</h3><table><tr><th>изделие</th><th>итог</th><th>когда</th></tr>${d.items.slice(0, 25).map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono">${esc(r.item_id)}</td><td>${op ? esc(r.outcome) : badge(...(L.result[r.outcome] || [r.outcome]))}</td><td class="muted">${time(r.at)}</td></tr>`).join("")}</table>` : "";
  const inject = can("emulate") ? `<div class="row" style="margin-top:10px"><button class="btn small" data-inject="defect">Внести дефект сюда</button>${op ? `<button class="btn small" data-inject="deviation">Отклонение станка</button>` : ""}</div>` : "";
  context(`<div class="card-head"><h2>${op ? "⚙" : "◉"} ${esc(s.title)}</h2>${badge(op ? "операция" : "контроль", "plain")}</div>
    <div class="muted mono" style="margin-bottom:10px">${esc(s.station_id)}${s.equipment_id ? ` · ${esc(s.equipment_id)}` : ""}${s.checkpoint_id ? ` · ${esc(s.checkpoint_id)}` : ""}</div>
    <div class="kpis">${stats}</div>${inject}${types}${operators}${ncs(d.detected, "Обнаружено на этапе")}${ncs(d.originated, "Возникло на этапе (оценка системы)")}${items}`);
  $("context").querySelectorAll("[data-inject]").forEach((b) => b.addEventListener("click", () => { S.injectMode = b.dataset.inject; nodeClicked(nodeId); }));
}

async function openItem(itemId) {
  select("item", itemId);
  let path;
  try { path = await api(`/api/items/${encodeURIComponent(itemId)}/path`); } catch (error) { notify(error.message, true); return; }
  const route = path.route.map((n, i) => `<div class="hop st-${n.status}"><div class="node-chip" data-node="${esc(n.node_id)}" title="${esc(L.pathStatus[n.status])}">
      <span class="muted">${String.fromCharCode(65 + i)}</span><b>${esc(n.title)}</b><span class="muted">${esc(L.pathStatus[n.status])}</span></div><div class="arrow"></div></div>`).join("");
  const steps = path.route.map((n, i) => {
    const runs = n.runs.map((r) => `<div>${r.rework ? badge("доработка", "info") + " " : ""}<span class="mono">${esc(r.item_id)}</span> · оператор <span class="mono">${esc(r.operator_id || "—")}</span> · ${time(r.started_at)} → ${time(r.finished_at)} · ${mins(r.active_s)}${r.deviations ? " " + badge("отклонение станка", "bad") : ""}</div>`).join("");
    const checks = n.checks.map((c) => `<div>${badge(...(L.result[c.result] || [c.result]))} ${time(c.at)} · уверенность ${c.confidence ?? "—"} ${c.reliable ? "" : badge(c.note || "недостоверно", "warn")} ${c.defects.length ? esc(c.defects.join(", ")) : ""}</div>`).join("");
    const ncs = n.nonconformances.map((x) => `<div class="click" data-nc="${esc(x.nc_id)}" style="cursor:pointer">${badge({ detected: "обнаружен здесь", origin: "возник здесь", possible_origin: "мог возникнуть здесь" }[x.role], x.role === "detected" ? "bad" : "warn")} ${esc(x.defect_type)} · <span class="mono">${esc(x.nc_id)}</span></div>`).join("");
    return `<div class="st-${n.status}"><b>${String.fromCharCode(65 + i)} · ${esc(n.title)}</b> <span class="muted">${esc(n.equipment_id || "")}</span>${runs}${checks}${ncs}${!runs && !checks ? `<div class="muted">${esc(L.pathStatus[n.status])}</div>` : ""}</div>`;
  }).join("");
  context(`<div class="card-head"><h2 class="mono">${esc(path.item_id)}</h2>${badge(...(L.status[path.status] || [path.status]))}</div>
    <div class="muted" style="margin-bottom:8px">${esc(path.item_type_id)}${path.parent_id ? ` · в составе <a href="#" data-item="${esc(path.parent_id)}" class="mono">${esc(path.parent_id)}</a>` : ""}${path.components.length ? ` · компоненты ${path.components.map((c) => `<a href="#" data-item="${esc(c)}" class="mono">${esc(c)}</a>`).join(", ")}` : ""}</div>
    <div class="route">${route}</div>
    <div class="legend" style="margin:0 0 10px"><span><i style="background:var(--ok)"></i>пройден</span><span><i style="background:var(--origin)"></i>дефект возник</span><span><i style="background:var(--bad)"></i>дефект обнаружен</span><span><i style="background:var(--warn)"></i>мог возникнуть / оценка невозможна</span></div>
    <h3>По этапам</h3><div class="steps">${steps}</div>
    <details style="margin-top:12px"><summary class="muted">Все события изделия</summary><div id="item-events" class="muted">загрузка…</div></details>`);
  $("context").querySelector("details").addEventListener("toggle", async (e) => {
    if (!e.target.open) return;
    const full = await api(`/api/items/${encodeURIComponent(itemId)}`);
    $("item-events").innerHTML = `<table>${full.timeline.map((ev) => `<tr><td class="mono">${time(ev.occurred_at)}</td><td>${esc(L.event[ev.event_type] || ev.event_type)}</td><td class="mono muted">${esc(ev.station_id || ev.equipment_id || "")}</td><td>${ev.flags.map((f) => badge(f, "warn")).join(" ")}</td></tr>`).join("")}</table>`;
  }, { once: true });
}

async function openNc(ncId) {
  select("nc", ncId);
  const card = await api(`/api/nonconformances/${encodeURIComponent(ncId)}`);
  const a = card.assessment || {};
  const causes = ["incoming_defect", "equipment_problem", "operator_error", "process_issue", "handling_damage", "other"];
  context(`<div class="card-head"><h2>${esc(card.defect_type)}</h2>${badge(...(L.nc[card.status] || [card.status]))}</div>
    <div class="muted"><a href="#" data-item="${esc(card.item_id)}" class="mono">${esc(card.item_id)}</a> · ${esc(card.nc_id)} · зона ${esc(card.area)} · ${esc(card.severity)}</div>
    ${card.signals_after_last_decision ? `<p>${badge(`новых сигналов после решения: ${card.signals_after_last_decision}`, "warn")}</p>` : ""}
    <div class="layer source"><div class="layer-title">1 · сообщения анализатора, как пришли</div>
      ${card.signals_detail.map((s) => `<div>${badge(...(L.result[s.inspection_result] || [s.inspection_result]))} ${time(s.occurred_at)} · ${esc(s.checkpoint_id)} · уверенность ${s.confidence ?? "—"} ${s.reliable ? "" : badge(s.reliability_note || "недостоверно", "warn")}
        <details><summary class="muted">исходное сообщение</summary><pre class="raw">${esc(JSON.stringify(s.raw, null, 2))}</pre></details></div>`).join("")}
      ${card.evidence.map((m) => `<div class="muted">${esc(m.kind)} ${esc(m.uri)} — ${m.available ? "доступно" : esc(m.note)}</div>`).join("")}</div>
    <div class="layer system"><div class="layer-title">2 · разбор — оценка системы, не решение</div>
      <div class="row"><b>${esc(L.stage[a.stage] || a.stage)}</b>${badge(...(L.conf[a.stage_confidence] || [a.stage_confidence]))}</div>
      <div class="muted">предполагаемая причина: ${esc(L.cause[a.presumed_cause] || a.presumed_cause)}</div>
      <h3>Основания</h3><ul class="plain">${(a.evidence || []).map((e) => `<li>${esc(e.text)}</li>`).join("") || "<li>нет</li>"}</ul>
      <h3>Что ещё могло привести к тому же</h3><ul class="plain">${(a.alternatives || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>нет</li>"}</ul>
      <h3>Каких сведений не хватает</h3><ul class="plain">${(a.missing || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>не отмечено</li>"}</ul>
      ${card.recheck_outcome ? `<p class="muted">Доп. проверка: ${esc(card.recheck_outcome)}</p>` : ""}</div>
    <div class="layer human"><div class="layer-title">3 · решения людей — отдельные записи журнала</div>
      ${card.decisions.length ? `<ul class="plain">${card.decisions.map((d) => `<li>${time(d.decided_at)} · <span class="mono">${esc(d.author_id)}</span> — ${esc(L.action[d.action] || d.action)}${d.cause_category ? `: ${esc(L.cause[d.cause_category])}` : ""}. <span class="muted">${esc(d.reason)}</span></li>`).join("")}</ul>` : `<div class="empty">Решений нет.</div>`}
      ${card.allowed_actions.length ? `<form id="decision" class="form" style="margin-top:8px"><select name="action">${card.allowed_actions.map((x) => `<option value="${x}">${esc(L.action[x])}</option>`).join("")}</select>
        <select name="cause" class="hidden">${causes.map((c) => `<option value="${c}">${esc(L.cause[c])}</option>`).join("")}</select>
        <textarea name="reason" rows="2" placeholder="Обоснование — обязательно" required></textarea><button class="btn primary">Записать решение</button></form>` : `<p class="muted">У роли «${esc(L.role[S.me.role])}» нет решений по этой карточке.</p>`}</div>`);
  const form = $("decision");
  if (form) {
    const sync = () => form.cause.classList.toggle("hidden", form.action.value !== "confirm_cause");
    form.action.addEventListener("change", sync);
    sync();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/api/nonconformances/${encodeURIComponent(ncId)}/decisions`, { method: "POST", body: JSON.stringify({ action: form.action.value, reason: form.reason.value, cause_category: form.action.value === "confirm_cause" ? form.cause.value : null }) });
        notify("Решение записано отдельной записью журнала.");
        openNc(ncId);
        refreshOverview();
      } catch (error) { notify(error.message, true); }
    });
  }
}

async function openItems() {
  select("items", null);
  const f = S.itemFilters;
  const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const data = await api(`/api/lines/${S.lineId}/items?${p}&limit=150`);
  const opt = (values, current, labels = {}) => `<option value="">все</option>` + values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
  context(`<div class="card-head"><h2>Изделия линии</h2><span class="muted">найдено ${data.total}${data.total > data.rows.length ? `, показано ${data.rows.length}` : ""}</span></div>
    <div class="table-wrap"><table>
      <tr><th>изделие</th><th>тип</th><th>статус</th><th>этап</th><th class="num">NC</th></tr>
      <tr class="filters"><th><input data-f="q" value="${esc(f.q)}" placeholder="номер"></th>
        <th><select data-f="item_type">${opt(data.facets.item_type, f.item_type)}</select></th>
        <th><select data-f="status">${opt(data.facets.status, f.status, Object.fromEntries(Object.entries(L.status).map(([k, v]) => [k, v[0]])))}</select></th>
        <th><select data-f="stage">${opt(data.facets.stage, f.stage, data.stage_titles)}</select></th><th></th></tr>
      ${data.rows.map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono">${esc(r.item_id)}</td><td class="mono">${esc(r.item_type_id)}</td>
        <td>${badge(...(L.status[r.status] || [r.status]))}</td><td>${esc(data.stage_titles[r.stage] || "—")}</td><td class="num">${r.open_nc || ""}</td></tr>`).join("")}
    </table></div>`);
  $("context").querySelectorAll("[data-f]").forEach((el) => {
    const apply = () => { S.itemFilters[el.dataset.f] = el.value; openItems(); };
    if (el.tagName === "INPUT") el.addEventListener("change", apply); else el.addEventListener("change", apply);
  });
}

async function openEconomics() {
  select("economics", null);
  const e = await api(`/api/lines/${S.lineId}/economics`);
  const p = e.parameters, m = e.measured, c = e.computed;
  const labels = { item_value_rub: "стоимость годного изделия, ₽", rework_cost_rub: "стоимость доработки, ₽", scrap_cost_rub: "потери на несоответствующем изделии, ₽", hour_cost_rub: "стоимость часа участка, ₽", shift_hours: "длительность смены, ч" };
  const losses = Object.entries(c.losses_by_node_rub);
  const max = Math.max(1, ...losses.map(([, v]) => v));
  context(`<div class="card-head"><h2>Экономика линии</h2></div>
    <p class="muted">${esc(e.origin)}.</p>
    <div class="kpis">${kpi(rub(c.output_value_rub), "выпуск годных")}${kpi(rub(c.rework_cost_rub + c.scrap_cost_rub), "потери от брака")}${kpi(c.losses_share == null ? "—" : pct(c.losses_share), "потери к выпуску")}${kpi(rub(c.cost_per_good_item_rub), "затраты на годное")}${kpi(c.capacity_per_shift ?? "—", "пропускная способность, изд./смену")}${kpi(pct(c.final_yield), "годных на ОТК")}</div>
    <h3>Где теряем деньги</h3>${losses.length ? `<div class="bars">${losses.map(([node, v]) => `<div class="bar-row" data-node="${esc(node)}"><small>${esc(title(node))}</small><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><b style="font-size:11px">${Math.round(v / 1000)}к</b></div>`).join("")}</div>` : `<div class="empty">Потерь с установленным местом возникновения нет.</div>`}
    <h3>Измерено</h3><dl class="kv"><dt>изделий-продуктов</dt><dd>${m.products}</dd><dt>годных</dt><dd>${m.finished_conforming}</dd><dt>с несоответствием</dt><dd>${m.nonconforming_items}</dd><dt>доработок</dt><dd>${m.rework_runs}</dd><dt>часов на участках</dt><dd>${m.station_hours}</dd><dt>узкое место</dt><dd>${esc(title(m.bottleneck_node))}, медиана ${mins(m.bottleneck_median_s)}</dd></dl>
    <h3>Параметры</h3>${can("line_manage") ? `<form id="econ" class="form">${Object.entries(labels).map(([key, label]) => `<label>${label}<input name="${key}" type="number" step="any" min="0" value="${p[key]}"></label>`).join("")}<button class="btn primary">Сохранить параметры</button></form>` : `<dl class="kv">${Object.entries(labels).map(([key, label]) => `<dt>${label}</dt><dd>${p[key]}</dd>`).join("")}</dl>`}
    <p class="muted">${esc(p.note)}</p>`);
  $("econ")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(ev.target).entries());
    try { await api(`/api/lines/${S.lineId}/economics`, { method: "PUT", body: JSON.stringify(body) }); notify("Параметры сохранены, изменение записано в журнал."); openEconomics(); } catch (error) { notify(error.message, true); }
  });
}

// --- новая линия и поток ---------------------------------------------------------------------

const STEP_TEMPLATE = [
  { title: "Входной контроль", kind: "inspection", duration_min: 2, defect_rate_pct: 2, defect_types: "INCLUSION" },
  { title: "Обработка", kind: "operation", duration_min: 10, defect_rate_pct: 5, defect_types: "BURR, SCRATCH" },
  { title: "Контроль после обработки", kind: "inspection", duration_min: 2, defect_rate_pct: 0, defect_types: "" },
  { title: "Финальный контроль", kind: "inspection", duration_min: 3, defect_rate_pct: 0, defect_types: "" },
];

function openEditor() {
  select("editor", null);
  const steps = STEP_TEMPLATE.map((s) => ({ ...s }));
  const render = () => {
    $("steps-edit").innerHTML = `<div class="step-edit muted" style="font-size:11px"><span>этап</span><span>вид</span><span>мин</span><span>дефект %</span><span>виды дефектов</span><span></span></div>` + steps.map((s, i) => `<div class="step-edit" data-i="${i}">
      <input data-k="title" value="${esc(s.title)}"><select data-k="kind"><option value="operation" ${s.kind === "operation" ? "selected" : ""}>операция</option><option value="inspection" ${s.kind === "inspection" ? "selected" : ""}>контроль</option></select>
      <input data-k="duration_min" type="number" min="0.1" step="0.1" value="${s.duration_min}"><input data-k="defect_rate_pct" type="number" min="0" max="100" step="0.5" value="${s.defect_rate_pct}">
      <input data-k="defect_types" value="${esc(s.defect_types)}" placeholder="BURR, CRACK"><button type="button" class="btn small" data-del="${i}">×</button></div>`).join("");
    $("steps-edit").querySelectorAll("[data-k]").forEach((el) => el.addEventListener("change", () => { steps[el.closest("[data-i]").dataset.i][el.dataset.k] = el.value; }));
    $("steps-edit").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { steps.splice(Number(b.dataset.del), 1); render(); }));
  };
  context(`<div class="card-head"><h2>Новая производственная линия</h2></div>
    <p class="muted">Задайте этапы по порядку процесса — граф линии построится сам. Первый этап — вход материала, последний — выход изделия.</p>
    <form id="line-form" class="form">
      <div class="two"><label>Код линии<input name="line_id" required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,31}" value="L${S.lines.length + 1}"></label><label>Тип изделия<input name="product_type_id" required value="PART-${S.lines.length + 1}"></label></div>
      <label>Название<input name="title" required value="Линия ${S.lines.length + 1}"></label>
      <div class="two"><label>Такт, мин<input name="takt_min" type="number" min="0.5" step="0.5" value="8"></label><label>Стоимость годного, ₽<input name="item_value_rub" type="number" min="0" value="0"></label></div>
      <div class="two"><label>Стоимость доработки, ₽<input name="rework_cost_rub" type="number" min="0" value="0"></label><label>Потери на браке, ₽<input name="scrap_cost_rub" type="number" min="0" value="0"></label></div>
      <h3>Этапы</h3><div id="steps-edit" class="stack"></div>
      <div class="row"><button type="button" class="btn small" id="add-op">+ операция</button><button type="button" class="btn small" id="add-qc">+ контроль</button></div>
      <button class="btn primary">Создать линию и запустить</button></form>`);
  render();
  $("add-op").addEventListener("click", () => { steps.splice(steps.length - 1, 0, { title: "Операция", kind: "operation", duration_min: 8, defect_rate_pct: 3, defect_types: "" }); render(); });
  $("add-qc").addEventListener("click", () => { steps.splice(steps.length - 1, 0, { title: "Контроль", kind: "inspection", duration_min: 2, defect_rate_pct: 0, defect_types: "" }); render(); });
  $("line-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const spec = {
      line_id: f.get("line_id"), title: f.get("title"), product_type_id: f.get("product_type_id"), takt_min: Number(f.get("takt_min")),
      steps: steps.map((s) => ({ title: s.title, kind: s.kind, duration_min: Number(s.duration_min), defect_rate_pct: Number(s.defect_rate_pct), defect_types: String(s.defect_types).split(",").map((x) => x.trim()).filter(Boolean) })),
      economics: { item_value_rub: Number(f.get("item_value_rub")), rework_cost_rub: Number(f.get("rework_cost_rub")), scrap_cost_rub: Number(f.get("scrap_cost_rub")) },
    };
    try {
      await api("/api/lines", { method: "POST", body: JSON.stringify(spec) });
      await api(`/api/lines/${spec.line_id}/emulation`, { method: "POST", body: JSON.stringify({ action: "start" }) });
      notify(`Линия ${spec.line_id} создана и запущена.`);
      await loadLines(spec.line_id);
    } catch (error) { notify(error.message, true); }
  });
}

function openFlow() {
  select("flow", null);
  let flow = null;
  context(`<div class="card-head"><h2>Загрузить поток</h2></div>
    <p class="muted">Файл <span class="mono">zd-flow/1</span> описывает линию (этапы, длительности, вероятности дефектов) и прогон: сколько изделий, за сколько секунд показать и куда внести дефекты. Система сама подберёт ускорение и проиграет процесс на графе.</p>
    <div class="row"><input id="flow-file" type="file" accept=".json,application/json"><button class="btn small" id="flow-example">Взять пример</button></div>
    <div id="flow-preview" style="margin-top:12px"></div>`);
  const preview = () => {
    const line = flow.line, run = flow.run;
    $("flow-preview").innerHTML = `<h3>${esc(line.title)}</h3><ol class="plain">${line.steps.map((s, i) => `<li>${esc(s.title)} · ${s.kind === "operation" ? "операция" : "контроль"} · ${s.duration_min} мин${s.defect_rate_pct ? ` · дефект ${s.defect_rate_pct} %` : ""}${run.defects.filter((d) => d.stage === i + 1).map((d) => " " + badge(`изделие ${d.item}: ${d.kind === "deviation" ? "отклонение станка" : "дефект"}`, "warn")).join("")}</li>`).join("")}</ol>
      <dl class="kv" style="margin:10px 0"><dt>изделий</dt><dd>${run.items}</dd><dt>показ</dt><dd>${run.duration_s} с</dd><dt>такт</dt><dd>${line.takt_min} мин</dd></dl>
      <button class="btn primary" id="flow-run">Проиграть на графе</button>`;
    $("flow-run").addEventListener("click", async () => {
      try {
        const result = await api("/api/flows", { method: "POST", body: JSON.stringify(flow) });
        notify(`Прогон запущен: ускорение ×${result.speed}.`);
        await loadLines(result.line_id);
      } catch (error) { notify(error.message, true); }
    });
  };
  $("flow-file").addEventListener("change", async (e) => {
    try { flow = JSON.parse(await e.target.files[0].text()); preview(); } catch (error) { notify(`Файл не читается: ${error.message}`, true); }
  });
  $("flow-example").addEventListener("click", async () => { flow = await api("/api/flows/example"); preview(); });
}

// --- администрирование ------------------------------------------------------------------------

async function openAdmin(section) {
  select("admin", section);
  if (section === "integrity") {
    context(`<div class="card-head"><h2>Целостность журнала</h2></div><p class="muted">Проверяются цепочка хешей, подписи пакетов и подписанная голова журнала, хранящаяся отдельно от базы.</p><button class="btn primary" id="verify">Проверить</button><div id="verify-out" style="margin-top:10px"></div>`);
    $("verify").addEventListener("click", async () => {
      const r = await api("/api/integrity");
      $("verify-out").innerHTML = r.ok ? `${badge("журнал цел", "ok")} записей ${r.checked}, якорь №${r.anchor_seq}` : `${badge("обнаружено вмешательство", "bad")}<ul class="plain">${r.problems.map((p) => `<li>запись №${p.seq} (${esc(p.kind || "")}): ${esc(p.problem)}</li>`).join("")}</ul>`;
    });
  } else if (section === "keys") {
    const k = await api("/api/keys");
    context(`<div class="card-head"><h2>Ключи и профили</h2></div><table><tr><th>ключ</th><th>профиль</th><th>статус</th><th>секрет</th></tr>${k.keys.map((x) => `<tr><td class="mono">${esc(x.key_id)}</td><td class="mono">${esc(x.profile_id)}</td><td>${badge(x.status, x.status === "active" ? "ok" : "plain")}</td><td>${x.secret_available ? "есть" : badge("недоступен", "warn")}</td></tr>`).join("")}</table>
      <div class="row" style="margin-top:10px"><select id="profile">${Object.entries(k.profiles).map(([id, m]) => `<option value="${id}">${esc(id)} — ${esc(m)}</option>`).join("")}</select><button class="btn" id="rotate">Выпустить ключ</button></div><p class="muted">Старые записи не перешифровываются и проверяются своим ключом.</p>`);
    $("rotate").addEventListener("click", async () => { const r = await api("/api/keys/rotate", { method: "POST", body: JSON.stringify({ profile_id: $("profile").value }) }); notify(`Выпущен ${r.key_id}.`); openAdmin("keys"); });
  } else if (section === "audit") {
    const rows = await api("/api/audit");
    context(`<div class="card-head"><h2>Журнал критических действий</h2></div><table><tr><th>№</th><th>когда</th><th>кто</th><th>действие</th></tr>${rows.slice(0, 80).map((r) => `<tr><td>${r.seq}</td><td>${time(r.at)}</td><td class="mono">${esc(r.user_id)}</td><td>${esc(r.action)} <span class="muted mono">${esc(JSON.stringify(r.details))}</span></td></tr>`).join("")}</table>`);
  } else if (section === "integration") {
    const d = await api("/api/integration");
    context(`<div class="card-head"><h2>Интеграции</h2><button class="btn small primary" id="sync">Синхронизировать</button></div>
      <p>Подключено: ${d.adapters.map((a) => badge(a, "info")).join(" ") || "—"}</p>${Object.entries(d.last_errors).filter(([, e]) => e).map(([a, e]) => `<p>${badge(a, "bad")} ${esc(e)}</p>`).join("")}
      <h3>Исходящая очередь</h3>${d.outbox.length ? `<table>${d.outbox.map((o) => `<tr><td class="mono">${esc(o.item_id)}</td><td>${badge(o.status, o.status === "delivered" ? "ok" : "warn")}</td><td>${o.attempts}</td><td class="muted">${esc(o.last_error || o.external_ref || "")}</td></tr>`).join("")}</table>` : `<div class="empty">Пусто.</div>`}`);
    $("sync").addEventListener("click", async () => { try { const r = await api("/api/integration/sync", { method: "POST" }); notify(JSON.stringify(r)); openAdmin("integration"); } catch (error) { notify(error.message, true); } });
  } else if (section === "users") {
    const users = await api("/api/admin/users");
    context(`<div class="card-head"><h2>Пользователи</h2></div><table><tr><th>пользователь</th><th>роль</th><th>пароль</th></tr>${users.map((u) => `<tr><td>${esc(u.name)} <span class="mono muted">${esc(u.user_id)}</span></td><td>${esc(L.role[u.role] || u.role)}</td><td>${u.has_password ? badge("задан", "ok") : badge("только демо-вход", "plain")}</td></tr>`).join("")}</table>
      <p class="muted">Пароль задаётся командой <span class="mono">uv run python scripts/keys.py password &lt;пользователь&gt;</span>; хранится только хеш scrypt.</p>`);
  }
}

const OPEN = { economics: openEconomics, editor: openEditor, flow: openFlow };

(async function start() {
  console.info("Контракт событий, версии:", CONTRACT.versions.join(", "));
  const saved = recall("zd-token");
  if (saved) {
    try { await enter(saved); return; } catch { forget("zd-token"); }
  }
  showLogin();
})();
