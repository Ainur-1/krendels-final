// Интерфейс Zero Defect: обзор производства → линия. Всё содержимое выводится на экране
// линии вокруг её графа: подробности — во вкладках под линией, этапы — в выпадающей сверху
// панели, время — на шкале. Без сборки и внешних библиотек: в закрытом контуре нет CDN.
import { CONTRACT } from "./contract.js?v=0.5.5";
import { $, L, api, badge, can, esc, login, logout, marqueeSvg, mins, modal, notify, pct, picker, poller, rub, session, store, time } from "./common.js?v=0.5.5";
import { openEditor } from "./editor.js?v=0.5.5";

const GUIDE = {
  controller: ["Слева расположена очередь текущих проблем линии: сначала критичные, решённые внизу списка. Очередь не зависит от шкалы времени.", "Выбор проблемы переносит линию в момент её обнаружения и выделяет этап, на котором она обнаружена.", "Во вкладке «Изделие и проблема» слева показано изделие, справа проблема. Решение принимается справа с обязательным обоснованием и записывается отдельной записью журнала."],
  master: ["Слева расположена очередь проблем линии, как у контролёра. Выбранная проблема поднимается наверх очереди.", "Мастер принимает решения по проблемам участка так же, как контролёр: подтверждает или отклоняет сигнал, назначает повторный контроль, закрывает несоответствие. По «Оценка невозможна» назначает повторный контроль или допускает изделие по ручному контролю.", "На шкале времени переключатель «Дефекты / Отклонения» выбирает вид отметок. Выбор в каждом виде сохраняется."],
  technologist: ["Кнопка «Оборудование» в шапке открывает справочник станков. Новый станок регистрируется по типу, который описал администратор.", "Слева расположена очередь сбоев оборудования, состояние станков и подтверждённые несоответствия, по которым технолог устанавливает причину. Красный круг над этапом показывает действующий сбой станка.", "На шкале времени отмечены сбои станков (красным) и их возврат в работу (зелёным).", "Выбор станка открывает во вкладке «Этап» его показатели и графики параметров режима с полосой допуска."],
  manager: ["Обзор производства показывает все линии и выпускаемые изделия. Карточка «＋ Новая линия» на экране производства открывает блочный редактор.", "Операция назначается на станок из справочника оборудования. Обработка и виды дефектов определяются типом станка.", "Вкладка «Экономика» под линией открывает расчёт потерь и затрат на годное изделие."],
  admin: ["Главный экран администратора: «Администрирование». Здесь описываются типы станков и виды дефектов, заводятся роли и пользователи.", "Тип станка является шаблоном: обработка, виды дефектов и параметры режима с допусками. Конкретный станок по шаблону регистрирует технолог.", "Раздел «Производство» открывает линии. Режим «Смотреть как» открывает базовый экран выбранной роли, права администратора при этом сохраняются. Экран запоминается для каждой роли отдельно."],
};

const S = {
  view: "plant", lineId: null, lines: [], plant: null, live: null, timeline: null, overview: null, at: null,
  itemType: "", viewRole: null, detail: null, prevItems: new Map(), prevNodes: {}, geometry: null, zoom: null, playing: null,
  playSpeed: 300, pollers: [], itemFilters: { item_type: "", status: "", stage: "", q: "" }, stageType: "", stageShift: "", leftOpen: false,
  range: { since: null, until: null }, windowMode: null, lane: "items", focusByLane: { items: null, machines: null }, selectedNode: null, queue: null,
  machineQueue: null, hideResolved: false, circle: null, pair: { item: null, problem: null },
};
const withAt = (path) => (S.at ? `${path}${path.includes("?") ? "&" : "?"}at=${encodeURIComponent(S.at)}` : path);

// --- вход ----------------------------------------------------------------------------------------

async function showLogin() {
  stopPollers();
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  const form = $("password-form");
  form.reset();
  const options = await api("/api/auth/options");
  demoRoles(options);
  form.user_id.focus();
}

// Демонстрационный вход: карточка роли подставляет логин и пароль в обычную форму и
// отправляет её. В демонстрационном режиме пароль совпадает с логином. При встраивании
// в систему предприятия удаляются эта функция и блок #demo-roles — вход не меняется.
function demoRoles(options) {
  const box = $("demo-roles");
  box.classList.toggle("hidden", !options.demo || !options.users.length);
  $("role-cards").innerHTML = options.users.map((u) => `<button type="button" class="role-card" data-user="${esc(u.user_id)}"><b>${esc(L.role[u.role] || u.role)}</b><span class="mono">${esc(u.user_id)} / ${esc(u.user_id)}</span></button>`).join("");
  $("role-cards").querySelectorAll("[data-user]").forEach((b) => b.addEventListener("click", () => {
    const form = $("password-form");
    form.user_id.value = b.dataset.user;
    form.password.value = b.dataset.user;
    form.requestSubmit();
  }));
}
$("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ user_id: e.target.user_id.value, password: e.target.password.value }) });
    await enter(r.token);
  } catch (error) { notify(error.message, true); }
});

async function enter(token) {
  await login(token);
  S.viewRole = session.me.screen;
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("user-chip").innerHTML = `<b>${esc(session.me.role_title)}</b>`;
  $("user-chip").title = `${session.me.name} (${session.me.user_id})`;
  $("view-as-wrap").classList.toggle("hidden", !can("view_as"));
  $("view-as").innerHTML = Object.keys(GUIDE).map((r) => `<option value="${r}">${esc(L.role[r])}</option>`).join("");
  $("view-as").value = S.viewRole;
  $("view-as").onchange = () => { S.viewRole = $("view-as").value; S.leftOpen = false; syncLeft(); openRoleScreen(); };
  S.lines = await api("/api/lines");
  openRoleScreen(true);
}

// Экран привязан к роли, а не к сайту: у каждой роли свой базовый экран и своя память о
// последнем открытом. При входе открывается то, где роль была в прошлый раз; при смене
// роли в «Смотреть как» — её базовый экран заново.
const BASE_SCREEN = { admin: "admin" };
const viewKey = () => `zd-view:${S.viewRole}`;
function openRoleScreen(restore = false) {
  const base = BASE_SCREEN[S.viewRole] || "plant";
  const saved = restore ? store.get(viewKey()) : null;
  const line = store.get(`zd-line:${S.viewRole}`);
  if (saved === "line" && line && S.lines.some((l) => l.line_id === line)) openLine(line);
  else if ((saved || base) === "admin" && can("admin")) showAdmin();
  else showPlant();
}
$("logout").addEventListener("click", () => { logout(); showLogin(); });
session.onExpired = () => showLogin();
$("open-guide").addEventListener("click", openGuide);

function stopPollers() { S.pollers.forEach((p) => p.stop()); S.pollers = []; }

// --- панель администратора: свёрнута в ☰ и при открытии сдвигает поле линии ----------------------

function syncLeft() {
  const burger = S.viewRole === "admin";
  $("left-toggle").classList.toggle("hidden", !burger);
  $("left-toggle").classList.toggle("active", burger && S.leftOpen);
  $("line-view").classList.toggle("left-closed", burger && !S.leftOpen);
}
function setLeft(open) { S.leftOpen = open; syncLeft(); }
$("left-toggle").addEventListener("click", () => setLeft(!S.leftOpen));

// Выплывающие панели закрываются нажатием вне их: этапы и панель администратора.
document.addEventListener("pointerdown", (e) => {
  const t = e.target;
  if (!(t instanceof Element) || t.closest(".modal-backdrop, .toast")) return;
  if (S.leftOpen && S.viewRole === "admin" && !t.closest("#role-panel, #left-toggle")) setLeft(false);
  if (S.circle && !t.closest("#circle-pop, [data-badge]")) closeCircle();
}, true);

function renderTop() {
  // У администратора центральная функция — администрирование: оно первое в навигации.
  const plant = `<a href="#" data-go="plant" class="${S.view === "plant" ? "current" : ""}">Производство</a>`;
  const admin = can("admin") ? `<a href="#" data-go="admin" class="${S.view === "admin" ? "current" : ""}">Администрирование</a>` : "";
  const crumbs = can("admin") ? [admin, `<span class="muted">|</span>`, plant] : [plant];
  if (S.view === "line") crumbs.push(`<span class="muted">/</span><select id="line-select" aria-label="Линия">${S.lines.map((l) => `<option value="${esc(l.line_id)}" ${l.line_id === S.lineId ? "selected" : ""}>${esc(l.title)}</option>`).join("")}</select>`);
  $("crumbs").innerHTML = crumbs.join("");
  $("crumbs").querySelector('[data-go="plant"]').addEventListener("click", (e) => { e.preventDefault(); showPlant(); });
  $("crumbs").querySelector('[data-go="admin"]')?.addEventListener("click", (e) => { e.preventDefault(); showAdmin(); });
  $("line-select")?.addEventListener("change", (e) => openLine(e.target.value));
  // В шапке — только переходы между экранами. Этапы, изделия и экономика линии открываются
  // вкладками под линией, новая линия — карточкой на экране производства.
  const actions = [];
  if (can("equipment_manage") && !can("admin")) actions.push(`<button class="btn small" data-act="equipment">Оборудование</button>`);
  if (can("emulate")) actions.push(`<a class="btn small" href="/emulator">Пульт эмулятора</a>`);
  $("top-actions").innerHTML = actions.join("");
  $("top-actions").querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => ACTIONS[b.dataset.act]()));
}

const ACTIONS = {
  "new-line": () => openEditor({ lines: S.lines, onSaved: afterSave }),
  "edit-line": async () => openEditor({ lines: S.lines, config: await api(`/api/lines/${S.lineId}`), onSaved: afterSave }),
  equipment: () => openEquipment(),
};
async function afterSave(lineId) { S.lines = await api("/api/lines"); openLine(lineId); }

// --- обзор производства ------------------------------------------------------------------------------

function showPlant() {
  S.view = "plant";
  store.set(viewKey(), "plant");
  stopPollers();
  stopPlaying();
  $("line-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("plant-view").classList.remove("hidden");
  renderTop();
  const p = poller(refreshPlant, 4000);
  S.pollers.push(p);
  p.now();
}

function miniGraph(card) {
  const xs = card.nodes.map((n) => n.x * 1.25), ys = card.nodes.map((n) => n.y);
  const minX = Math.min(...xs) - 70, maxX = Math.max(...xs) + 70, minY = Math.min(...ys) - 40, maxY = Math.max(...ys) + 40;
  const pos = Object.fromEntries(card.nodes.map((n) => [n.node_id, { ...n, x: n.x * 1.25 }]));
  const edges = card.edges.map(([a, b]) => `<line x1="${pos[a].x}" y1="${pos[a].y}" x2="${pos[b].x}" y2="${pos[b].y}" class="mini-edge"/>`).join("");
  const nodes = Object.values(pos).map((n) => `<rect x="${n.x - 60}" y="${n.y - 24}" width="120" height="48" rx="10" class="mini-node ${n.kind} ${card.alarm_nodes.includes(n.node_id) ? "alarm" : ""}"><title>${esc(n.title)}</title></rect>`).join("");
  return `<svg class="mini" viewBox="${minX} ${minY} ${maxX - minX} ${maxY - minY}" preserveAspectRatio="xMidYMid meet">${edges}${nodes}</svg>`;
}

async function refreshPlant() {
  S.plant = await api("/api/plant");
  const filter = S.itemType;
  const lines = S.plant.lines.filter((l) => !filter || l.item_types.includes(filter));
  $("plant-view").innerHTML = `
    <div class="plant-head"><div><h1>Производство</h1><p class="muted">Линии предприятия, изделия в работе и проблемы, ожидающие решения. Выберите линию или изделие.</p></div>
      <div class="products"><span class="muted">Изделие:</span><button class="chip-btn ${!filter ? "active" : ""}" data-product="">Все</button>${S.plant.products.map((p) => `<button class="chip-btn ${filter === p.item_type ? "active" : ""}" data-product="${esc(p.item_type)}">${esc(p.item_type)} <small>${esc(p.lines.join(", "))}</small></button>`).join("")}</div></div>
    <div class="plant-grid">${lines.map((l) => `
      <article class="line-card ${l.open_nonconformances ? "alarm" : ""}" data-line="${esc(l.line_id)}" tabindex="0">
        <div class="row spread"><b>${esc(l.title)}</b>${l.emulation.running || (l.emulation.run && !l.emulation.run.finished) ? badge("Работает", "info") : badge("Простой", "plain")}</div>
        ${miniGraph(l)}
        <div class="kpis four"><div class="kpi"><b>${l.items}</b><span>Изделий</span></div><div class="kpi"><b>${l.in_progress_runs}</b><span>Операций в работе</span></div>
          <div class="kpi"><b class="${l.open_nonconformances ? "bad-text" : ""}">${l.open_nonconformances}</b><span>Ждут решения</span></div><div class="kpi"><b>${pct(l.final_yield)}</b><span>Годных на ОТК</span></div></div>
        <div class="muted small">${esc(l.item_types.join(" · "))} · последнее событие: ${time(l.latest_event_at)}</div>
      </article>`).join("")}
      ${can("line_manage") ? `<button class="line-card add" id="plant-new"><b>＋ Новая линия</b><span class="muted">Блочный редактор этапов, станков и связей</span></button>` : ""}
    </div>`;
  $("plant-view").querySelectorAll("[data-product]").forEach((b) => b.addEventListener("click", () => { S.itemType = b.dataset.product; refreshPlant(); }));
  $("plant-view").querySelectorAll("[data-line]").forEach((c) => {
    c.addEventListener("click", () => openLine(c.dataset.line));
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") openLine(c.dataset.line); });
  });
  $("plant-new")?.addEventListener("click", ACTIONS["new-line"]);
}

// --- линия -------------------------------------------------------------------------------------------------

// Какие отметки видит роль на шкале. Контролёр работает с изделиями, технолог со станками;
// мастер, руководитель и администратор переключаются между ними, и выбор в каждой дорожке
// сохраняется при переключении.
const LANE_OF_ROLE = { controller: "items", technologist: "machines" };
const laneFixed = () => LANE_OF_ROLE[S.viewRole] || null;
const lane = () => laneFixed() || S.lane;
const focus = () => S.focusByLane[lane()];

async function openLine(lineId) {
  S.view = "line";
  S.lineId = lineId;
  S.range = { since: null, until: null };
  S.at = null;
  S.geometry = null;
  S.zoom = null;
  S.prevItems = new Map();
  S.prevNodes = {};
  S.focusByLane = { items: null, machines: null };
  S.selectedNode = null;
  S.circle = null;
  S.tlView = null;
  S.panelHtml = null;
  S.queue = null;
  S.machineQueue = null;
  stopPlaying();
  store.set(`zd-line:${S.viewRole}`, lineId);
  store.set(viewKey(), "line");
  stopPollers();
  $("plant-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("line-view").classList.remove("hidden");
  const types = S.lines.find((l) => l.line_id === lineId)?.item_types || [];
  if (!types.includes(S.itemType)) S.itemType = "";
  const options = (all) => `<option value="">${all}</option>` + types.map((t) => `<option ${t === S.itemType ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("product-filter").innerHTML = options("Все изделия");
  S.stageTypes = types;
  renderTop();
  syncLeft();
  renderLegend();
  detailsEmpty();
  await refreshTimeline();
  // Сначала граф: очередь и панель роли берут из него названия этапов.
  await refreshLive();
  await Promise.all([refreshOverview(), refreshQueue()]);
  S.pollers.push(
    poller(() => (S.at ? null : refreshLive()), 1500),
    poller(refreshQueue, 2000),
    poller(() => (S.at ? null : refreshOverview()), 5000),
    poller(refreshTimeline, 10000),
  );
}

$("product-filter").addEventListener("change", (e) => {
  S.itemType = e.target.value;
  S.stageType = S.itemType;
  if (S.live) renderGraph(S.live);
  if (S.detail?.tab === "stages") refreshStages();
});

async function refreshLive() {
  if (S.view !== "line") return;
  const since = S.timeline?.start ? `?since=${encodeURIComponent(new Date(sinceMs()).toISOString())}` : "";
  const live = await api(withAt(`/api/lines/${S.lineId}/live${since}`));
  if (live.line_id !== S.lineId) return;
  S.live = live;
  $("line-title").textContent = live.title;
  renderMoment();
  renderGraph(live);
}

async function refreshAll() {
  await Promise.all([refreshLive(), refreshOverview(), S.circle ? loadCircle() : null]);
  if (S.detail?.refresh) await S.detail.refresh();
}

// --- граф ---------------------------------------------------------------------------------------------------

const NODE_W = 160, NODE_H = 70, SX = 1.25, BADGE_R = 13;
// Цвета статусов изделий. Красным здесь помечено только несоответствие, оттенки критичности
// у списка проблем свои и с этими цветами не пересекаются.
// «На рассмотрении» и «несоответствие» — разные статусы по постановке: сигнал анализатора
// становится несоответствием только после решения контролёра. Поэтому и цвета разные.
const STATUS = [["in_progress", "var(--accent)", "В работе"], ["conforming", "var(--ok)", "Годно"], ["suspect", "var(--review)", "На рассмотрении"], ["nonconforming", "var(--bad)", "Несоответствие"], ["not_assessable", "var(--amber)", "Оценка невозможна"]];

// Счётчики под этапом — о самом этапе: проблема обнаружена здесь, оценка невозможна здесь,
// изделие ещё в работе или прошло этап без проблем. Красный и жёлтый сходятся с кругом.
const STAGE_COUNTS = [["in_progress", "var(--accent)", "В работе"], ["ok", "var(--ok)", "Без проблем на этапе"], ["problem", "var(--bad)", "Проблема на этапе"], ["not_assessable", "var(--amber)", "Оценка невозможна на этапе"]];

function renderLegend() {
  $("graph-legend").innerHTML = `<span class="muted">Под этапом — изделия за промежуток:</span>${STAGE_COUNTS.map(([, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")}
    <span class="legend-sep"><i style="background:var(--bad)"></i>Над этапом — действующие проблемы на выбранный момент. Нажатие открывает их список.</span>`;
}

function geometry(raw) {
  const nodes = raw.map((n) => ({ ...n, x: n.x * SX, y: n.y }));
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const box = { x: Math.min(...xs) - NODE_W / 2 - 60, y: Math.min(...ys) - NODE_H / 2 - 50 };
  box.w = Math.max(...xs) + NODE_W / 2 + 40 - box.x;
  box.h = Math.max(...ys) + NODE_H / 2 + 70 - box.y;
  return { box, pos: Object.fromEntries(nodes.map((n) => [n.node_id, n])), key: `${S.lineId}:${raw.map((n) => `${n.node_id}@${n.x},${n.y}:${n.title}:${n.equipment_id}:${n.processing}`).join(",")}` };
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

function setViewBox() {
  const z = S.zoom;
  $("graph").setAttribute("viewBox", `${z.x} ${z.y} ${z.w} ${z.h}`);
  if (S.circle) placeCircle();
}

function buildGraph(live) {
  const g = geometry(live.nodes);
  S.geometry = g;
  if (!S.zoom || S.zoom.key !== g.key) S.zoom = { ...g.box, key: g.key };
  const svg = $("graph");
  setViewBox();
  const edges = live.edges.map(([a, b]) => `<path class="edge" data-edge="${esc(a)}>${esc(b)}" d="${edgePath(g.pos[a], g.pos[b])}" marker-end="url(#arrow)"/>`).join("");
  const nodes = Object.values(g.pos).map((n) => {
    const icon = n.kind === "operation" ? (n.assembly ? "⧉" : "⚙") : "◉";
    const sub = n.kind === "operation" ? [n.equipment_id || n.station_id, n.processing].filter(Boolean).join(" · ") : ({ incoming: "Входной контроль", after_operation: "Контроль", final: "Финальный контроль" }[n.checkpoint_kind] || "Контроль");
    // Длинный текст не обрезается многоточием, а бежит строкой внутри блока (marqueeSvg).
    return `<g class="node ${n.kind}" data-node="${esc(n.node_id)}" transform="translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})">
      <rect class="focus-ring" x="-8" y="-8" width="${NODE_W + 16}" height="${NODE_H + 16}" rx="18"/>
      <rect class="box" width="${NODE_W}" height="${NODE_H}" rx="12"/>
      <text class="icon" x="12" y="24">${icon}</text><text x="32" y="24" data-fit="${NODE_W - 42}">${esc(n.title)}</text>
      <text class="sub" x="12" y="44" data-fit="${NODE_W - 22}">${esc(sub)}</text><text class="sub" data-f="passed" x="12" y="60"></text>
      <g data-f="badges"></g><g data-f="counts" transform="translate(0,${NODE_H + 22})"></g></g>`;
  }).join("");
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--flow)"/></marker></defs>
    <g id="edges">${edges}</g><g id="nodes">${nodes}</g><g id="moving"></g><g id="fx"></g>`;
  marqueeSvg(svg);
  svg.querySelectorAll(".node").forEach((el) => {
    el.addEventListener("click", (e) => {
      const badgeEl = e.target.closest("[data-badge]");
      selectNode(el.dataset.node);
      if (badgeEl) toggleCircle(el.dataset.node);
      else openNode(el.dataset.node);
    });
    el.addEventListener("mouseenter", () => hoverNode(el.dataset.node, true));
    el.addEventListener("mouseleave", () => hoverNode(el.dataset.node, false));
  });
}

function hoverNode(nodeId, on) {
  $("graph").querySelectorAll(".edge").forEach((e) => {
    const [a, b] = e.dataset.edge.split(">");
    e.classList.toggle("lit", on && (a === nodeId || b === nodeId));
  });
  const tip = $("graph-tip");
  if (!on || !S.live) { tip.classList.add("hidden"); return; }
  const n = S.live.nodes.find((x) => x.node_id === nodeId);
  if (!n) return;
  const p = n.problems;
  tip.innerHTML = `<b>${esc(n.title)}</b><div>${n.kind === "operation" ? `Станок ${esc(n.equipment_id || "не задан")}, норма ${mins(n.duration_s)}` : "Контрольная точка"}</div>
    <div>Изделий за промежуток: ${n.passed}</div>
    ${p.nonconformance ? `<div class="bad-text">Несоответствий: ${p.nonconformance}</div>` : ""}${p.not_assessable ? `<div class="amber-text">Оценка невозможна: ${p.not_assessable}</div>` : ""}${p.machine ? `<div class="bad-text">Сбой станка не устранён</div>` : ""}`;
  tip.classList.remove("hidden");
}

function selectNode(nodeId) {
  S.selectedNode = nodeId;
  if (S.live) renderGraph(S.live);
  $("stages")?.querySelectorAll("tr[data-node]").forEach((tr) => tr.classList.toggle("selected", tr.dataset.node === nodeId));
}

// Над этапом один красный круг: число проблем, действующих на выбранный момент. У технолога
// это сбои станка, у остальных — проблемы изделий (несоответствия и «оценка невозможна»).
// Под этапом — точка и число изделий по статусам за промежуток шкалы времени.
const activeProblems = (n) => (lane() === "machines" ? n.problems.machine : n.problems.nonconformance + n.problems.not_assessable);
const problemsHint = (n) => (lane() === "machines" ? "Сбой станка не устранён" : [n.problems.nonconformance && `несоответствий: ${n.problems.nonconformance}`, n.problems.not_assessable && `оценка невозможна: ${n.problems.not_assessable}`].filter(Boolean).join(", "));

function renderGraph(live) {
  if (!S.geometry || S.geometry.key !== geometry(live.nodes).key) buildGraph(live);
  const svg = $("graph");
  const f = focus();
  for (const raw of live.nodes) {
    const el = svg.querySelector(`.node[data-node="${CSS.escape(raw.node_id)}"]`);
    if (!el) continue;
    el.classList.remove("state-ok", "state-warning", "state-alarm");
    el.classList.add(`state-${raw.state}`);
    el.classList.toggle("selected", S.selectedNode === raw.node_id);
    el.classList.toggle("focused", !!f && f.node_id === raw.node_id);
    el.querySelector('[data-f="passed"]').textContent = `За промежуток: ${raw.passed}`;
    const n = activeProblems(raw);
    const text = n > 99 ? "99+" : String(n);
    el.querySelector('[data-f="badges"]').innerHTML = n
      ? `<g class="badge-g ${S.circle === raw.node_id ? "selected" : ""}" data-badge="problems" transform="translate(${NODE_W - BADGE_R + 4},-4)"><title>Действующих проблем: ${n} (${esc(problemsHint(raw))}). Нажмите, чтобы открыть список.</title><circle class="badge-ring" r="${BADGE_R + 4}"/><circle r="${BADGE_R}" fill="var(--bad)"/><text y="4" text-anchor="middle" class="count light">${text}</text></g>`
      : "";
    let cx = 2;
    el.querySelector('[data-f="counts"]').innerHTML = STAGE_COUNTS.filter(([s]) => raw.counts[s]).map(([s, color, label]) => {
      const value = String(raw.counts[s]);
      const out = `<g class="count-pill"><title>${label}: ${value}</title><circle cx="${cx + 6}" cy="0" r="6" fill="${color}"/><text x="${cx + 16}" y="4" class="count">${value}</text></g>`;
      cx += 26 + value.length * 7;
      return out;
    }).join("");
  }
  if (S.circle) {
    const node = live.nodes.find((x) => x.node_id === S.circle);
    if (node && activeProblems(node) !== S.circleCount) loadCircle();
    else placeCircle();
  }
  renderFocusNote();
  animateMoves(live);
}

// Список проблем круга: действующие на момент, в конце серым — решённые внутри промежутка.
function toggleCircle(nodeId) {
  if (S.circle === nodeId) { closeCircle(); return; }
  S.circle = nodeId;
  renderGraph(S.live);
  loadCircle();
}
function closeCircle() {
  S.circle = null;
  $("circle-pop").classList.add("hidden");
  if (S.live) renderGraph(S.live);
}
function placeCircle() {
  const pop = $("circle-pop");
  const anchor = $("graph").querySelector(`.node[data-node="${CSS.escape(S.circle)}"] [data-badge]`);
  if (!anchor) { pop.classList.add("hidden"); return; }
  // Список привязан к окну, а не к графу: высота графа его не обрезает.
  const r = anchor.getBoundingClientRect();
  const graph = $("graph").getBoundingClientRect();
  // Круг ушёл за край графа или окна — список прячется и вернётся вместе с кругом.
  const visible = r.bottom > Math.max(0, graph.top) && r.top < Math.min(innerHeight, graph.bottom) && r.right > graph.left && r.left < graph.right;
  pop.classList.toggle("hidden", !visible);
  const top = r.bottom + 6;
  pop.style.left = `${Math.max(8, Math.min(r.left - 150, innerWidth - pop.offsetWidth - 8))}px`;
  pop.style.top = `${top}px`;
  pop.style.maxHeight = `${Math.max(180, Math.min(420, innerHeight - top - 12))}px`;
}
window.addEventListener("scroll", () => { if (S.circle) placeCircle(); }, { passive: true });
window.addEventListener("resize", () => { if (S.circle) placeCircle(); });
async function loadCircle() {
  const nodeId = S.circle;
  if (!nodeId) return;
  const machines = lane() === "machines";
  const q = new URLSearchParams({ node: nodeId, lane: machines ? "machines" : "items", since: new Date(sinceMs()).toISOString() });
  if (S.at) q.set("at", S.at);
  const data = await api(`/api/lines/${S.lineId}/problems?${q}`);
  if (S.circle !== nodeId) return;
  S.circleCount = data.active.length;
  const pop = $("circle-pop");
  const row = (p, resolved) => `<li class="problem ${resolved ? "resolved" : machines ? "sev-major" : problemClass(p)} ${focus()?.problem_id === p.problem_id ? "focused" : ""}" data-pop="${esc(p.problem_id)}">
      <div class="row spread"><b class="small">${esc(p.title)}</b><span class="small">${resolved ? `Решено ${time(p.resolved_at, true)}` : esc(machines ? p.equipment_id : p.kind === "not_assessable" ? "Нужна повторная проверка" : SEVERITY[p.severity] || "")}</span></div>
      <div class="muted small">${machines ? `Станок <span class="mono">${esc(p.equipment_id)}</span>. ${esc(machineNote(p))}` : `Изделие <span class="mono">${esc(p.item_id)}</span>.`} Обнаружено ${time(p.at, true)}.</div></li>`;
  pop.innerHTML = `<div class="pop-head"><b>${esc(nodeTitle(nodeId))}</b><span class="muted small">На ${S.at ? time(S.at, true) : "текущий момент"}</span><button class="btn small" data-pop-close aria-label="Закрыть список">✕</button></div>
    <div class="pop-body">${data.active.length ? `<ul class="list">${data.active.map((p) => row(p, false)).join("")}</ul>` : `<div class="empty">Действующих проблем нет.</div>`}
    ${data.resolved.length ? `<div class="pop-sep">Решено за промежуток: ${data.resolved_total}</div><ul class="list">${data.resolved.map((p) => row(p, true)).join("")}</ul>` : ""}</div>`;
  pop.classList.remove("hidden");
  placeCircle();
  pop.querySelector("[data-pop-close]").addEventListener("click", closeCircle);
  const all = [...data.active, ...data.resolved];
  pop.querySelectorAll("[data-pop]").forEach((el) => el.addEventListener("click", () => {
    const p = all.find((x) => x.problem_id === el.dataset.pop);
    S.focusByLane[lane()] = { lane: lane(), problem_id: p.problem_id, node_id: p.node_id, item_id: p.item_id, at: p.at, title: p.title, kind: "problem" };
    pop.querySelectorAll("[data-pop]").forEach((x) => x.classList.toggle("focused", x === el));
    renderGraph(S.live);
    renderTimeline();
    if (machines) openNode(p.node_id);
    else openProblem(p.problem_id);
  }));
}

// Выделение выбранной проблемы держится при любом положении шкалы: кольцо вокруг этапа и
// подпись над графом. Смена числа проблем у этапа в любую сторону подсвечивается вспышкой.
function renderFocusNote() {
  const f = focus();
  const box = $("focus-note");
  if (!f) { box.classList.add("hidden"); return; }
  box.innerHTML = `<b>В фокусе:</b> ${esc(f.title)}${f.item_id ? `, <span class="mono">${esc(f.item_id)}</span>` : ""}, ${esc(nodeTitle(f.node_id))}, ${time(f.at, true)} <button class="btn small" id="focus-clear" title="Снять выделение">✕</button>`;
  box.classList.remove("hidden");
  box.querySelector("#focus-clear").addEventListener("click", () => { S.focusByLane[lane()] = null; renderGraph(S.live); renderTimeline(); });
}

function animateMoves(live) {
  const g = S.geometry;
  const layer = $("graph").querySelector("#moving");
  const next = new Map(live.items.map((i) => [i.item_id, i]));
  let started = 0;
  for (const [id, item] of next) {
    const before = S.prevItems.get(id);
    if (!before?.node_id || !item.node_id || before.node_id === item.node_id || started > 30) continue;
    if (S.itemType && item.item_type !== S.itemType) continue;
    const a = g.pos[before.node_id], b = g.pos[item.node_id];
    if (!a || !b) continue;
    const direct = live.edges.some(([x, y]) => x === before.node_id && y === item.node_id);
    const path = direct ? edgePath(a, b) : `M${a.x},${a.y + NODE_H / 2 + 22} L${b.x},${b.y + NODE_H / 2 + 22}`;
    const color = (STATUS.find(([s]) => s === item.status) || STATUS[0])[1];
    const ball = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ball.setAttribute("r", "8");
    ball.setAttribute("class", "ball");
    ball.setAttribute("fill", color);
    const motion = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
    Object.entries({ dur: "1.1s", fill: "freeze", path, begin: "indefinite" }).forEach(([k, v]) => motion.setAttribute(k, v));
    ball.appendChild(motion);
    layer.appendChild(ball);
    motion.beginElement();
    setTimeout(() => ball.remove(), 1200);
    started += 1;
  }
  for (const n of live.nodes) {
    const before = S.prevNodes[n.node_id];
    if (before && activeProblems(before) !== activeProblems(n)) flash(g.pos[n.node_id]);
  }
  S.prevNodes = Object.fromEntries(live.nodes.map((n) => [n.node_id, n]));
  S.prevItems = next;
}

function flash(n) {
  const ring = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  Object.entries({ class: "flash", x: n.x - NODE_W / 2 - 6, y: n.y - NODE_H / 2 - 6, width: NODE_W + 12, height: NODE_H + 12, rx: 16 }).forEach(([k, v]) => ring.setAttribute(k, v));
  $("graph").querySelector("#fx").appendChild(ring);
  setTimeout(() => ring.remove(), 1700);
}

(() => {
  const svg = $("graph");
  svg.addEventListener("wheel", (e) => {
    if (!S.zoom) return;
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const k = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    const px = S.zoom.x + ((e.clientX - rect.left) / rect.width) * S.zoom.w;
    const py = S.zoom.y + ((e.clientY - rect.top) / rect.height) * S.zoom.h;
    S.zoom = { ...S.zoom, x: px - (px - S.zoom.x) * k, y: py - (py - S.zoom.y) * k, w: S.zoom.w * k, h: S.zoom.h * k };
    setViewBox();
  }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (e) => { if (S.zoom && !e.target.closest(".node")) drag = { x: e.clientX, y: e.clientY, z: { ...S.zoom } }; });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    S.zoom = { ...S.zoom, x: drag.z.x - ((e.clientX - drag.x) / rect.width) * drag.z.w, y: drag.z.y - ((e.clientY - drag.y) / rect.height) * drag.z.h };
    setViewBox();
  });
  window.addEventListener("pointerup", () => { drag = null; });
  document.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
    if (!S.geometry) return;
    if (b.dataset.zoom === "fit") S.zoom = { ...S.geometry.box, key: S.geometry.key };
    else {
      const k = b.dataset.zoom === "in" ? 0.8 : 1.25;
      const cx = S.zoom.x + S.zoom.w / 2, cy = S.zoom.y + S.zoom.h / 2;
      S.zoom = { ...S.zoom, w: S.zoom.w * k, h: S.zoom.h * k, x: cx - (S.zoom.w * k) / 2, y: cy - (S.zoom.h * k) / 2 };
    }
    setViewBox();
  }));
})();

// --- шкала времени: промежуток с двумя ползунками -------------------------------------------------------------

// Промежуток задаёт и момент графа (его конец), и срез статистики этапов (от начала до
// конца). Конец «сейчас» означает текущее время: граф живой. Длина по умолчанию — из
// конфигурации: календарные сутки или смена.
const WINDOWS = { day: "Сутки", shift: "Смена", all: "Всё время" };

async function refreshTimeline() {
  if (S.view !== "line") return;
  const next = await api(`/api/lines/${S.lineId}/timeline`);
  const key = (t) => `${t.end}|${t.markers.length}|${t.markers[t.markers.length - 1]?.at}`;
  const same = S.timeline && S.timeline.start && key(S.timeline) === key(next);
  S.timeline = next;
  S.windowMode ??= S.timeline.window?.default || "day";
  if (!same) renderTimeline();
}

const tlStart = () => new Date(S.timeline.start).getTime();
const tlEnd = () => Math.max(new Date(S.timeline.end).getTime(), tlStart() + 60000);
const untilMs = () => (S.range.until ? new Date(S.range.until).getTime() : tlEnd());

function windowStart(endMs) {
  const w = S.timeline.window || { shift_hours: 8, timezone_offset_h: 3 };
  if (S.windowMode === "all") return tlStart();
  if (S.windowMode === "shift") return Math.max(tlStart(), endMs - w.shift_hours * 3600000);
  // Календарные сутки в поясе предприятия: полночь того дня, в который попадает конец.
  const offset = w.timezone_offset_h * 3600000;
  const local = new Date(endMs + offset);
  local.setUTCHours(0, 0, 0, 0);
  return Math.max(tlStart(), local.getTime() - offset);
}
const sinceMs = () => (S.range.since ? new Date(S.range.since).getTime() : windowStart(untilMs()));

// Масштаб шкалы: видимое окно внутри всей истории. Когда проблем в одной точке много,
// шкалу приближают колесом мыши или кнопками, и отметки расходятся. Окно, прижатое к
// правому краю, едет вместе с «сейчас».
const MIN_VIEW_MS = 2 * 60000;
const viewStart = () => (S.tlView ? Math.max(tlStart(), S.tlView.start) : tlStart());
const viewEnd = () => (S.tlView && !S.tlView.follow ? Math.min(tlEnd(), S.tlView.end) : tlEnd());
function setView(start, end) {
  const span = Math.max(MIN_VIEW_MS, end - start);
  const full = tlEnd() - tlStart();
  if (span >= full) { S.tlView = null; renderTimeline(); return; }
  const lo = Math.min(Math.max(tlStart(), start), tlEnd() - span);
  S.tlView = { start: lo, end: lo + span, follow: lo + span >= tlEnd() - 1000 };
  renderTimeline();
}
function zoomView(factor, centerMs) {
  const a = viewStart(), b = viewEnd();
  const c = centerMs ?? (a + b) / 2;
  setView(c - (c - a) * factor, c + (b - c) * factor);
}

function renderMoment() {
  $("moment-badge").innerHTML = S.at
    ? `<span class="moment">${badge("Прошлое", "warn")} на ${time(S.at, true)} <button class="btn small" id="to-live">К текущему моменту</button></span>`
    : `<span class="moment">${badge("Сейчас", "ok")}</span>`;
  $("to-live")?.addEventListener("click", goLive);
}

// На шкале три цвета: красный — проблема, жёлтый — оценка невозможна, зелёный — решено.
// Критичность на шкале не различается, она видна в очереди и в карточке.
const RESOLUTION = { closed: "Устранено", rejected: "Сигнал отклонён", rechecked: "Повторная проверка выполнена", accepted_manual: "Допущено по ручному контролю", restored: "Станок вернулся в работу" };
function markTitle(m) {
  const what = m.lane === "machines"
    ? `${m.title}, станок ${m.equipment_id}${m.note ? `: ${m.note}` : ""}`
    : `${m.problem_kind === "not_assessable" ? "Оценка невозможна" : `Несоответствие: ${m.title}`}, изделие ${m.item_id}`;
  return m.kind === "resolved" ? `${RESOLUTION[m.resolution] || "Решено"}. ${what}` : what;
}
const markerClass = (m) => (m.kind === "resolved" ? "m-ok" : m.problem_kind === "not_assessable" ? "m-amber" : "m-bad");

function renderTimeline() {
  const t = S.timeline;
  if (!t?.start) { $("timeline").innerHTML = `<div class="muted">Событий на линии ещё нет.</div>`; return; }
  const start = viewStart(), end = viewEnd();
  const lo = sinceMs(), hi = untilMs();
  const pos = (ms) => Math.min(100, Math.max(0, ((ms - start) / (end - start)) * 100));
  const current = lane();
  const f = focus();
  // В окно попадают только его отметки: при приближении DOM не растёт.
  // «Скрыть решённые» убирает решённую проблему целиком: и отметку обнаружения, и
  // отметку решения. Решённость — на настоящее время.
  const solved = S.hideResolved ? new Set(t.markers.filter((m) => m.kind === "resolved").map((m) => m.problem_id)) : null;
  const markers = t.markers.map((m, i) => ({ ...m, i, ms: new Date(m.at).getTime() })).filter((m) => m.lane === current && !(solved && solved.has(m.problem_id)) && m.ms >= start && m.ms <= end);
  const zoom = (tlEnd() - tlStart()) / (end - start);
  const tick = (k) => time(new Date(start + (end - start) * k).toISOString(), end - start < 3600000);
  const legend = current === "items"
    ? `<i class="m-bad"></i>Проблема <i class="m-amber"></i>Оценка невозможна <span class="${S.hideResolved ? "off" : ""}"><i class="m-ok"></i>Решено</span>`
    : `<i class="m-bad"></i>Сбой станка <span class="${S.hideResolved ? "off" : ""}"><i class="m-ok"></i>Станок в работе</span>`;
  // Возникновение и решение выбранной проблемы связаны линией: видно, сколько она действовала.
  const pair = f ? markers.filter((m) => m.problem_id === f.problem_id) : [];
  const link = pair.length === 2 ? `<div class="tl-link" style="left:${pos(new Date(pair[0].at).getTime())}%;width:${pos(new Date(pair[1].at).getTime()) - pos(new Date(pair[0].at).getTime())}%"></div>` : "";
  const laneSwitch = laneFixed() ? "" : `<div class="seg" role="group" aria-label="Вид отслеживания">${[["items", "Дефекты"], ["machines", "Отклонения"]].map(([k, l]) => `<button class="btn small ${current === k ? "active" : ""}" data-lane="${k}">${l}</button>`).join("")}</div>`;
  $("timeline").innerHTML = `
    <div class="tl-head">
      <div class="row">
        <button class="btn small" data-tl="back" title="Сдвинуть промежуток назад на 10 минут">−10 мин</button>
        <button class="btn small primary" data-tl="play">${S.playing ? "⏸ Пауза" : "▶ Воспроизвести"}</button>
        <button class="btn small" data-tl="fwd" title="Сдвинуть промежуток вперёд на 10 минут">+10 мин</button>
        <select data-tl="speed" title="Скорость воспроизведения">${[60, 300, 1800].map((v) => `<option value="${v}" ${S.playSpeed === v ? "selected" : ""}>×${v}</option>`).join("")}</select>
        <button class="btn small ${S.at ? "" : "active"}" data-tl="live">Сейчас</button>
        <div class="seg" role="group" aria-label="Длина промежутка">${Object.entries(WINDOWS).map(([k, l]) => `<button class="btn small ${S.windowMode === k && !S.range.since ? "active" : ""}" data-window="${k}">${l}</button>`).join("")}</div>
        ${laneSwitch}
        <button class="btn small ${S.hideResolved ? "active" : ""}" data-tl="hide" title="Скрыть решённые проблемы целиком: отметки обнаружения и решения">Скрыть решённые</button>
        <div class="seg" role="group" aria-label="Масштаб шкалы"><button class="btn small" data-tlzoom="in" title="Приблизить шкалу к промежутку или выбранной проблеме. Колесо мыши над шкалой приближает к курсору">＋</button><button class="btn small" data-tlzoom="out" title="Отдалить шкалу">－</button><button class="btn small ${S.tlView ? "" : "active"}" data-tlzoom="fit" title="Вся история">⤢</button></div>
        ${S.tlView ? `<span class="muted small">×${zoom < 10 ? zoom.toFixed(1) : Math.round(zoom)}, Shift + колесо сдвигает шкалу</span>` : ""}
      </div>
      <div class="muted small">Промежуток: <b>${time(new Date(lo).toISOString(), true)}</b> — <b>${S.range.until ? time(S.range.until, true) : "сейчас"}</b> · <span class="tl-key">${legend}</span></div>
    </div>
    <div class="tl-track" id="tl-track">
      <div class="tl-markers">${link}${markers.map((m) => {
        const focused = f && m.problem_id === f.problem_id;
        const outside = m.ms < lo || m.ms > hi;
        return `<button class="tl-mark ${markerClass(m)} ${focused ? "focused" : ""} ${outside ? "outside" : ""}" style="left:${pos(m.ms)}%" data-mark="${m.i}" title="${esc(`${time(m.at, true)}. ${markTitle(m)}`)}"></button>`;
      }).join("")}</div>
      <div class="tl-rail"><div class="tl-sel" title="Перетащите, чтобы сдвинуть промежуток целиком" style="left:${pos(lo)}%;width:${Math.max(0.3, pos(hi) - pos(lo))}%"></div>
        <button class="tl-handle" data-handle="since" style="left:${pos(lo)}%" aria-label="Начало промежутка" title="Начало промежутка"></button>
        <button class="tl-handle end" data-handle="until" style="left:${pos(hi)}%" aria-label="Конец промежутка" title="Конец промежутка: момент, который показывает граф"></button></div>
    </div>
    <div class="tl-axis">${[0, 0.25, 0.5, 0.75, 1].map((k) => `<span>${tick(k)}</span>`).join("")}</div>`;
  const box = $("timeline");
  bindHandles(box, start, end);
  box.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", () => selectMarker(t.markers[Number(b.dataset.mark)])));
  box.querySelector('[data-tl="back"]').addEventListener("click", () => shiftRange(-600000));
  box.querySelector('[data-tl="fwd"]').addEventListener("click", () => shiftRange(600000));
  box.querySelector('[data-tl="live"]').addEventListener("click", goLive);
  box.querySelector('[data-tl="hide"]').addEventListener("click", () => { S.hideResolved = !S.hideResolved; renderTimeline(); });
  box.querySelectorAll("[data-tlzoom]").forEach((b) => b.addEventListener("click", () => {
    const kind = b.dataset.tlzoom;
    if (kind === "fit") { S.tlView = null; renderTimeline(); return; }
    // Приближение — к выбранной проблеме, если она есть, иначе к середине промежутка.
    const center = f ? new Date(f.at).getTime() : (lo + hi) / 2;
    zoomView(kind === "in" ? 0.4 : 2.5, kind === "in" ? center : undefined);
  }));
  const track = box.querySelector("#tl-track");
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = track.getBoundingClientRect();
    const span = end - start;
    if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      const delta = ((e.shiftKey ? e.deltaY : e.deltaX) / rect.width) * span;
      setView(start + delta, end + delta);
      return;
    }
    const cursor = start + ((e.clientX - rect.left) / rect.width) * span;
    zoomView(e.deltaY > 0 ? 1.25 : 0.8, cursor);
  }, { passive: false });
  box.querySelector('[data-tl="speed"]').addEventListener("change", (e) => { S.playSpeed = Number(e.target.value); });
  box.querySelector('[data-tl="play"]').addEventListener("click", () => (S.playing ? stopPlaying() : startPlaying()));
  box.querySelectorAll("[data-window]").forEach((b) => b.addEventListener("click", () => { S.windowMode = b.dataset.window; S.range.since = null; renderTimeline(); refreshAll(); }));
  box.querySelectorAll("[data-lane]").forEach((b) => b.addEventListener("click", () => { S.lane = b.dataset.lane; renderTimeline(); if (S.circle) loadCircle(); if (S.live) renderGraph(S.live); }));
}

// Ползунки тянутся мышью; конец промежутка у правого края — это «сейчас». Сам промежуток
// перетаскивается целиком: длина сохраняется, сдвигаются обе границы.
function bindHandles(box, start, end) {
  const rail = box.querySelector(".tl-rail");
  const sel = rail.querySelector(".tl-sel");
  sel.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const rect = rail.getBoundingClientRect();
    const lo0 = sinceMs(), hi0 = untilMs(), width = hi0 - lo0;
    const span = end - start;
    let lo = lo0;
    sel.classList.add("dragging");
    const move = (ev) => {
      const delta = ((ev.clientX - e.clientX) / rect.width) * span;
      lo = Math.min(Math.max(start, lo0 + delta), end - width);
      const pct = (ms) => `${((ms - start) / span) * 100}%`;
      sel.style.left = pct(lo);
      rail.querySelector('[data-handle="since"]').style.left = pct(lo);
      rail.querySelector('[data-handle="until"]').style.left = pct(lo + width);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      sel.classList.remove("dragging");
      if (lo === lo0) return;
      S.range.since = new Date(lo).toISOString();
      S.range.until = lo + width >= tlEnd() - 1000 ? null : new Date(lo + width).toISOString();
      applyRange();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
  box.querySelectorAll(".tl-handle").forEach((h) => h.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const which = h.dataset.handle;
    const rect = rail.getBoundingClientRect();
    const toMs = (x) => start + Math.min(1, Math.max(0, (x - rect.left) / rect.width)) * (end - start);
    const move = (ev) => {
      const ms = toMs(ev.clientX);
      h.style.left = `${((ms - start) / (end - start)) * 100}%`;
      h.dataset.ms = ms;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const ms = Number(h.dataset.ms || NaN);
      if (Number.isNaN(ms)) return;
      if (which === "until") {
        S.range.until = ms >= tlEnd() - 1000 ? null : new Date(ms).toISOString();
        if (sinceMs() > untilMs()) S.range.since = null;
      } else S.range.since = new Date(Math.min(ms, untilMs() - 60000)).toISOString();
      applyRange();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }));
}

function applyRange() {
  S.at = S.range.until;
  renderTimeline();
  renderMoment();
  return refreshAll();
}

function shiftRange(deltaMs) {
  const end = tlEnd();
  const next = untilMs() + deltaMs;
  const width = untilMs() - sinceMs();
  S.range.until = next >= end ? null : new Date(Math.max(tlStart(), next)).toISOString();
  if (S.range.since) S.range.since = new Date(Math.max(tlStart(), untilMs() - width)).toISOString();
  applyRange();
}

// Переход к моменту события: конец промежутка ставится сразу после него, начало — по
// выбранной длине промежутка.
async function jumpTo(iso) {
  S.range.until = new Date(new Date(iso).getTime() + 1000).toISOString();
  S.range.since = null;
  await applyRange();
}

function goLive() {
  stopPlaying();
  S.range = { since: null, until: null };
  applyRange();
}

function startPlaying() {
  if (!S.timeline) return;
  if (!S.range.until) S.range.until = new Date(sinceMs()).toISOString();
  let busy = false;
  S.playing = setInterval(async () => {
    if (busy) return;
    const next = untilMs() + S.playSpeed * 1000;
    if (next >= tlEnd()) { goLive(); return; }
    S.range.until = new Date(next).toISOString();
    S.at = S.range.until;
    busy = true;
    try { renderTimeline(); await refreshLive(); } finally { busy = false; }
  }, 1000);
  renderTimeline();
}
function stopPlaying() {
  if (S.playing) clearInterval(S.playing);
  S.playing = null;
  if (S.view === "line" && S.timeline) renderTimeline();
}

// Отметка на шкале: переход к её моменту, фокус на проблеме, подробности под линией.
// Проблема изделия открывается вместе с изделием, сбой станка — во вкладке этапа.
async function selectMarker(m) {
  queueMicrotask(() => renderRolePanel());
  S.focusByLane[m.lane] = { lane: m.lane, problem_id: m.problem_id, node_id: m.node_id, item_id: m.item_id || null, at: m.at, title: m.title, kind: m.kind };
  selectNode(m.node_id);
  // Карточка открывается сразу, параллельно с переходом во времени: ждать пересборку
  // линии на момент, чтобы увидеть проблему, незачем.
  await Promise.all([jumpTo(m.at), m.lane === "machines" ? openNode(m.node_id) : openProblem(m.problem_id)]);
}

// Проблема из очереди: фокус и переход к моменту обнаружения выполняются сразу.
async function selectProblem(p) {
  queueMicrotask(() => renderRolePanel());
  const where = p.kind === "machine" ? "machines" : "items";
  S.focusByLane[where] = { lane: where, problem_id: p.problem_id, node_id: p.node_id, item_id: p.item_id, at: p.at, title: p.title, kind: "problem" };
  if (!laneFixed()) S.lane = where;
  selectNode(p.node_id);
  await Promise.all([jumpTo(p.at), where === "machines" ? openNode(p.node_id) : openProblem(p.problem_id)]);
}

// --- этапы процесса: вкладка под линией ---------------------------------------------------------------------

// Таблица этапов — вкладка под линией рядом с экономикой. Она считается за промежуток
// шкалы и пересчитывается при каждом его сдвиге.
function openStagesTab() {
  const opt = (values, current, all) => `<option value="">${all}</option>` + values.map(([v, l]) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(l)}</option>`).join("");
  details("stages", `<div class="card-head"><h2>Этапы процесса</h2><div class="row">
      <span id="stages-period" class="muted small"></span>
      <select id="f-type" aria-label="Тип изделия">${opt((S.stageTypes || []).map((t) => [t, t]), S.stageType, "Все типы изделий")}</select>
      <select id="f-shift" aria-label="Смена">${opt([["S1", "Смена S1"], ["S2", "Смена S2"]], S.stageShift, "Все смены")}</select></div></div>
    <div id="stages" class="table-wrap"><div class="empty">Загрузка.</div></div>`,
  { refresh: refreshStages, quiet: true }, (body) => {
    body.querySelector("#f-type").addEventListener("change", (e) => { S.stageType = e.target.value; refreshStages(); });
    body.querySelector("#f-shift").addEventListener("change", (e) => { S.stageShift = e.target.value; refreshStages(); });
    refreshStages();
  });
}

// Срез статистики: промежуток шкалы времени плюс тип изделия и смена.
function stageQuery({ total = false } = {}) {
  const p = new URLSearchParams();
  const type = S.stageType, shift = S.stageShift;
  if (type) p.set("item_type", type);
  if (shift) p.set("shift", shift);
  if (!total && S.timeline?.start) {
    p.set("since", new Date(sinceMs()).toISOString());
    if (S.range.until) { p.set("until", S.range.until); p.set("at", S.range.until); }
  }
  return p.toString();
}

// Значение за промежуток / значение на текущий момент.
const pair = (a, b) => `${a ?? "—"} <span class="muted">/ ${b ?? "—"}</span>`;

async function refreshStages() {
  if (!$("stages")) return;
  const [rows, totals] = await Promise.all([api(`/api/lines/${S.lineId}/stages?${stageQuery()}`), api(`/api/lines/${S.lineId}/stages?${stageQuery({ total: true })}`)]);
  const all = Object.fromEntries(totals.map((r) => [r.node_id, r]));
  $("stages-period").textContent = `Промежуток: ${time(new Date(sinceMs()).toISOString(), true)} — ${S.range.until ? time(S.range.until, true) : "сейчас"}`;
  if (!$("stages")) return;
  $("stages").innerHTML = `<p class="muted small">В каждой ячейке: значение за выбранный промежуток / значение на текущий момент.</p>
    <table><tr><th>Этап</th><th class="num">Изделий</th><th class="num">Выполнений, проверок</th><th class="num">В работе</th><th class="num">Доработок</th><th class="num">Длительность</th><th class="num">Отклонений станка</th><th class="num">Обнаружено</th><th class="num">Без признаков</th></tr>
    ${rows.map((r) => {
      const t = all[r.node_id] || {};
      const op = r.kind === "operation";
      const median = r.median_reported_s ?? r.median_active_s;
      const medianAll = t.median_reported_s ?? t.median_active_s;
      const dash = `<td class="num">—</td>`;
      return `<tr class="click ${S.selectedNode === r.node_id ? "selected" : ""}" data-node="${esc(r.node_id)}"><td>${op ? "⚙" : "◉"} ${esc(r.title)}</td><td class="num">${pair(r.items, t.items)}</td><td class="num">${pair(op ? r.runs : r.checks, op ? t.runs : t.checks)}</td><td class="num">${op ? pair(r.in_progress, t.in_progress) : "—"}</td>
        ${op ? gcell(pair(r.rework_runs, t.rework_runs), "rework", ratio(r.rework_runs, r.runs)) : dash}
        ${op ? gcell(pair(mins(median), mins(medianAll)), "duration", median == null ? null : ratio(median, r.norm_duration_s)) : dash}
        ${op ? gcell(pair(r.deviations, t.deviations), "deviations", ratio(r.deviations, r.runs)) : dash}
        ${op ? dash : gcell(pair(r.first_detections, t.first_detections), "found", ratio(r.found, r.checks))}
        ${op ? dash : gcell(pair(pct(r.pass_rate), pct(t.pass_rate)), "found", r.pass_rate == null ? null : 1 - r.pass_rate)}</tr>`;
    }).join("")}</table>${gradeLegend()}`;
  $("stages").querySelectorAll("tr[data-node]").forEach((tr) => tr.addEventListener("click", () => { selectNode(tr.dataset.node); openNode(tr.dataset.node); }));
}

// --- оценка показателей цветом ---------------------------------------------------------------------------------------

// Четыре уровня вместо одного цвета: хорошо, терпимо, плохо, очень плохо. Пороги — верхние
// границы первых трёх уровней, всё выше — «очень плохо». Пороги условные, для
// демонстрации: на предприятии их задают технолог и ОТК под свой техпроцесс.
const GRADES = [["good", "Хорошо"], ["fair", "Терпимо"], ["bad", "Плохо"], ["critical", "Очень плохо"]];
const RULES = {
  duration: { limits: [1.05, 1.2, 1.5], text: "Медиана длительности к норме", fmt: (v) => `×${v.toFixed(2)}` },
  rework: { limits: [0.02, 0.05, 0.1], text: "Доля доработок среди выполнений", fmt: pct },
  deviations: { limits: [0, 0.02, 0.05], text: "Отклонений станка на выполнение", fmt: pct },
  originated: { limits: [0.01, 0.03, 0.06], text: "Дефектов, возникших на этапе, на изделие", fmt: pct },
  found: { limits: [0.02, 0.05, 0.1], text: "Доля проверок с признаками дефекта", fmt: pct },
  unassessable: { limits: [0.01, 0.03, 0.06], text: "Доля проверок без оценки", fmt: pct },
  open: { limits: [0, 2, 5], text: "Несоответствий ждут решения", fmt: (v) => String(v) },
};
const ratio = (a, b) => (b ? a / b : null);

function grade(rule, value) {
  if (value == null || Number.isNaN(value)) return null;
  const { limits } = RULES[rule];
  return value <= limits[0] ? 0 : value <= limits[1] ? 1 : value <= limits[2] ? 2 : 3;
}

function gradeHint(rule, value) {
  const r = RULES[rule];
  const [a, b, c] = r.limits.map(r.fmt);
  return `${r.text}: ${r.fmt(value)}. Хорошо: до ${a}. Терпимо: до ${b}. Плохо: до ${c}. Выше: очень плохо. Пороги условные.`;
}

// Плитка показателя с оценкой: цвет полосы и подпись уровня, порог — во всплывающей подсказке.
function gkpi(value, label, rule = null, measure = null) {
  const g = rule ? grade(rule, measure) : null;
  if (g == null) return kpi(value, label);
  const [key, word] = GRADES[g];
  return `<div class="kpi graded g-${key}" title="${esc(gradeHint(rule, measure))}"><b>${value}</b><span>${esc(label)}</span><em>${word}</em></div>`;
}
const gradeLegend = () => `<div class="grade-legend">${GRADES.map(([k, w]) => `<span><i class="g-${k}"></i>${w}</span>`).join("")}<span class="muted">Порог показан в подсказке.</span></div>`;
const gcell = (content, rule, measure) => {
  const g = grade(rule, measure);
  return g == null ? `<td class="num">${content}</td>` : `<td class="num"><span class="gcell g-${GRADES[g][0]}" title="${esc(gradeHint(rule, measure))}">${content}</span></td>`;
};

// --- панель роли ----------------------------------------------------------------------------------------------------

async function refreshOverview() {
  if (S.view !== "line") return;
  S.overview = await api(withAt(`/api/lines/${S.lineId}/overview`));
  renderRolePanel();
}

// Очередь проблем всегда о настоящем: она не следует за шкалой времени. У технолога рядом
// с ней очередь сбоев оборудования.
async function refreshQueue() {
  if (S.view !== "line") return;
  const tech = S.viewRole === "technologist";
  const [queue, machines] = await Promise.all([api(`/api/lines/${S.lineId}/problems`), tech ? api(`/api/lines/${S.lineId}/problems?lane=machines`) : null]);
  S.queue = queue;
  S.machineQueue = machines;
  if (QUEUE_ROLES.has(S.viewRole) || tech) renderRolePanel();
}

// Очередь на решение одна у контролёра и мастера: мастер видит те же проблемы, решает по
// «оценка невозможна», а на шкале у него вдобавок отклонения станков.
const QUEUE_ROLES = new Set(["controller", "master"]);

const kpi = (value, label, tone = "") => `<div class="kpi"><b class="${tone}">${value}</b><span>${esc(label)}</span></div>`;
const nodeTitle = (id) => S.geometry?.pos[id]?.title || id;
const SEVERITY = { critical: "Критичное", major: "Значительное", minor: "Малозначительное" };
const problemClass = (p) => (p.kind === "not_assessable" ? "sev-amber" : `sev-${p.severity || "major"}`);

// Сообщение станка о сбое с заглавной буквы; без сообщения — сколько раз станок сообщил.
const machineNote = (p) => (p.note ? `${p.note[0].toUpperCase()}${p.note.slice(1)}.` : `Сообщений о сбое: ${p.reports}.`);

function queueList(q, { machines = false, resolved: withResolved = true } = {}) {
  if (!q) return `<div class="empty">Загрузка.</div>`;
  const f = S.focusByLane[machines ? "machines" : "items"];
  const what = (p, resolved) => {
    if (resolved) return RESOLUTION[p.resolution] || "Решено";
    if (machines) return "Не устранён";
    if (p.kind === "not_assessable") return p.status === "recheck_requested" ? "Повторный контроль назначен" : "Нужно решение";
    return SEVERITY[p.severity] || "";
  };
  const fresh = S.justDecided && Date.now() < S.justDecided.until ? S.justDecided.id : null;
  const row = (p, resolved = false) => `<li class="problem ${resolved ? "resolved" : machines ? "sev-major" : problemClass(p)} ${f?.problem_id === p.problem_id ? "focused" : ""} ${fresh === p.problem_id ? "just-decided" : ""}" data-problem="${esc(p.problem_id)}">
      <div class="row spread"><b>${esc(p.title)}</b><span class="small">${esc(what(p, resolved))}</span></div>
      <div class="small">${machines ? `${esc(nodeTitle(p.node_id))}, станок <span class="mono">${esc(p.equipment_id)}</span>. ${esc(machineNote(p))}` : `Изделие <span class="mono">${esc(p.item_id)}</span>. ${esc(nodeTitle(p.node_id))}.`}</div>
      <div class="muted small">Обнаружено ${time(p.at, true)}${resolved ? `. Решено ${time(p.resolved_at, true)}` : ""}.</div></li>`;
  // Выбранная проблема поднимается наверх очереди: сразу видно, что выбрано.
  const all = [...q.active.map((p) => [p, false]), ...q.resolved.map((p) => [p, true])];
  const picked = f && all.find(([p]) => p.problem_id === f.problem_id);
  const pinned = picked ? `<div class="pinned"><div class="pop-sep">Выбрано</div><ul class="list">${row(...picked)}</ul></div>` : "";
  const rest = q.active.filter((p) => p.problem_id !== f?.problem_id);
  const active = pinned + (rest.length ? `<ul class="list">${rest.map((p) => row(p)).join("")}</ul>` : picked ? "" : `<div class="empty">Действующих проблем нет.</div>`);
  const solved = q.resolved.filter((p) => p.problem_id !== f?.problem_id);
  const done = `<h3>Решено · ${q.resolved_total}</h3>${solved.length ? `<ul class="list">${solved.slice(0, 15).map((p) => row(p, true)).join("")}</ul>` : `<div class="empty">Нет.</div>`}`;
  return withResolved ? active + done : active;
}
const resolvedList = (q, machines) => (q ? queueList({ active: [], resolved: q.resolved, resolved_total: q.resolved_total }, { machines }).replace(`<div class="empty">Действующих проблем нет.</div>`, "") : "");

// Состояние станка цветом: работа — зелёный, отклонение режима — красный, предупреждение —
// жёлтый, остановка — серый. Полоса последних состояний показывает сбой без чтения чисел.
const MACHINE_STATE = { running: ["var(--ok)", "В работе"], idle: ["var(--line)", "Простой"], warning: ["var(--fair)", "Предупреждение"], deviation: ["var(--bad)", "Режим вне допуска"], stopped: ["var(--muted)", "Остановка"] };
const stateStrip = (states) => `<div class="strip">${states.map((st) => `<i style="background:${(MACHINE_STATE[st] || MACHINE_STATE.idle)[0]}" title="${esc((MACHINE_STATE[st] || [0, st])[1])}"></i>`).join("")}</div>`;

const plural = (n, one, few, many) => {
  const d = n % 10, h = n % 100;
  return d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
};

function machinesList(machines) {
  if (!machines.length) return `<div class="empty">Станков нет.</div>`;
  return `<ul class="list">${machines.map((m) => {
    const g = grade("deviations", ratio(m.deviations, m.runs));
    return `<li class="click" data-node="${esc(m.node_id)}"><div class="row spread"><b class="small">${esc(m.title)}</b>${m.deviations ? `<span class="gcell g-${GRADES[g ?? 3][0]}" title="${esc(gradeHint("deviations", ratio(m.deviations, m.runs)))}">${m.deviations} ${plural(m.deviations, "сбой", "сбоя", "сбоев")}</span>` : badge("Без сбоев", "ok")}</div>
      <div class="muted small">${esc(m.stage)}, ${esc(m.equipment_id)}${m.last_deviation_at ? `. Последний сбой ${time(m.last_deviation_at, true)}` : ""}</div>${stateStrip(m.strip)}</li>`;
  }).join("")}</ul>`;
}

function renderRolePanel(force = false) {
  const o = S.overview;
  if (!o) return;
  const k = o.kpi;
  const role = S.viewRole;
  let html;
  if (QUEUE_ROLES.has(role)) {
    const n = S.queue?.active.length ?? 0;
    html = `<div class="card-head"><h2>Очередь на решение</h2>${badge(n, n ? "bad" : "ok")}</div>
      <p class="muted small">Текущие проблемы линии. Шкала времени на очередь не влияет.</p>${queueList(S.queue)}`;
  } else if (role === "technologist") {
    // Технолог работает со станками: его очередь — сбои оборудования. Подтверждённые
    // несоответствия остаются отдельным списком, потому что причину по ним устанавливает он.
    const n = S.machineQueue?.active.length ?? 0;
    const confirmed = (S.queue?.active || []).filter((p) => p.kind === "nonconformance" && p.status === "confirmed");
    html = `<div class="card-head"><h2>Проблемы оборудования</h2>${badge(n, n ? "bad" : "ok")}</div>
      <p class="muted small">Сбои станков линии на текущий момент. Шкала времени на список не влияет.</p>${queueList(S.machineQueue, { machines: true, resolved: false })}
      <h3>Состояние станков</h3>${machinesList(o.machines || [])}
      <h3>Ждут установления причины · ${confirmed.length}</h3>${confirmed.length ? `<ul class="list">${confirmed.slice(0, 10).map((p) => `<li class="click" data-nc="${esc(p.problem_id)}"><b class="small">${esc(p.title)}</b><div class="muted small">Изделие <span class="mono">${esc(p.item_id)}</span>. ${esc(nodeTitle(p.node_id))}.</div></li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <button class="btn small" data-ocel style="margin-top:10px">Выгрузка OCEL 2.0</button>
      ${resolvedList(S.machineQueue, true)}`;
  } else if (role === "manager") {
    html = `<div class="card-head"><h2>Показатели линии</h2></div><div class="kpis">${kpi(k.items, "Изделий")}${kpi(k.conforming, "Годно")}${kpi(k.nonconforming, "С несоответствием", k.nonconforming ? "bad-text" : "")}${kpi(k.rework_runs, "Доработок")}${kpi(k.open_nonconformances, "Ждут решения")}${kpi(k.confirmed, "Подтверждено")}</div>
      <div class="stack" style="margin-top:12px">${can("line_manage") ? `<button class="btn" data-open="edit-line">Изменить линию</button>` : ""}${can("emulate") ? `<a class="btn" href="/emulator">Пульт эмулятора</a>` : ""}</div>`;
  } else {
    html = `<div class="card-head"><h2>Администрирование</h2></div><div class="stack">${ADMIN_SECTIONS.map(([s, t]) => `<button class="btn" data-admin="${s}">${t}</button>`).join("")}<button class="btn" data-open="edit-line">Изменить линию</button></div>
      <div class="kpis" style="margin-top:12px">${kpi(k.items, "Изделий")}${kpi(k.open_nonconformances, "Ждут решения")}</div>`;
  }
  const panel = $("role-panel");
  // Опросы приходят каждые 2–5 с; перерисовка сотен строк без изменений давала рывки и
  // сбрасывала прокрутку списка.
  if (!force && S.panelHtml === html) return;
  S.panelHtml = html;
  panel.innerHTML = html;
  // Выбрана другая проблема — очередь прокручивается наверх, к закреплённой строке.
  const picked = focus()?.problem_id || null;
  if (picked !== S.pinnedShown) {
    S.pinnedShown = picked;
    if (picked) panel.scrollTop = 0;
  }
  bindCommon(panel);
  panel.querySelectorAll("[data-problem]").forEach((el) => el.addEventListener("click", () => {
    const all = [S.queue, S.machineQueue].flatMap((q) => [...(q?.active || []), ...(q?.resolved || [])]);
    const p = all.find((x) => x.problem_id === el.dataset.problem);
    if (p) selectProblem(p);
  }));
  panel.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => ({ "edit-line": ACTIONS["edit-line"] })[b.dataset.open]()));
  panel.querySelectorAll("[data-admin]").forEach((b) => b.addEventListener("click", () => showAdmin(b.dataset.admin)));
  panel.querySelector("[data-ocel]")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/export/ocel");
      Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" })), download: "zero-defect.ocel.json" }).click();
    } catch (error) { notify(error.message, true); }
  });
}

function bindCommon(root) {
  root.querySelectorAll("[data-nc]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); openProblem(el.dataset.nc); }));
  root.querySelectorAll("[data-item]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); openItem(el.dataset.item); }));
  root.querySelectorAll("[data-node]").forEach((el) => el.addEventListener("click", () => { selectNode(el.dataset.node); openNode(el.dataset.node); }));
}

// --- подробности под линией: вкладки ---------------------------------------------------------------------------

// Вкладки подготовлены заранее и заполняются только по нажатию на событие. Изделие и
// проблема — одна вкладка из двух половин: слева изделие, справа проблема. Выбор проблемы
// сразу открывает и её изделие; выбор изделия справа показывает его проблемы.
const TABS = [
  ["pair", "Изделие и проблема", ""],
  ["stages", "Этапы процесса", "Таблица этапов за выбранный промежуток шкалы времени."],
  ["stage", "Этап", "Выберите этап на графе или в таблице этапов. Здесь появятся его показатели за выбранный промежуток, станок и изделия."],
  ["items", "Изделия линии", "Таблица изделий линии с фильтрами по столбцам."],
  ["economics", "Экономика", "Потери, затраты на годное изделие и параметры расчёта."],
];
const PAIR_HINT = {
  item: "Здесь появится изделие выбранной проблемы: его маршрут по этапам в хронологии, проблемный и повторный проходы отдельно.",
  problem: "Выберите проблему в очереди, отметку на шкале времени или проблему в круге над этапом. Здесь появятся сообщения анализатора, разбор системы и решения людей.",
};

function detailsFrame(active) {
  return `<div class="tabs" role="tablist">${TABS.map(([k, title]) => `<button class="tab ${k === active ? "active" : ""} ${S.filled?.[k] ? "filled" : ""}" data-tab="${k}" role="tab">${title}</button>`).join("")}</div><div class="tab-body" id="tab-body"></div>`;
}

function detailsEmpty() {
  S.detail = null;
  S.filled = {};
  S.tabHtml = {};
  S.tabBind = {};
  S.pair = { item: null, problem: null };
  renderPair({ quiet: true });
}

function showTab(tab) {
  const box = $("details");
  box.innerHTML = detailsFrame(tab);
  const body = box.querySelector("#tab-body");
  const [, , hint] = TABS.find(([k]) => k === tab);
  body.innerHTML = S.tabHtml?.[tab] || `<div class="tab-hint">${esc(hint)}</div>`;
  bindCommon(body);
  S.tabBind?.[tab]?.(body);
  box.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    // Таблица изделий и экономика загружаются нажатием на саму вкладку.
    if (b.dataset.tab === "items") openItems();
    else if (b.dataset.tab === "stages") openStagesTab();
    else if (b.dataset.tab === "economics") openEconomics();
    else showTab(b.dataset.tab);
  }));
}

function details(tab, html, state = {}, bind = null) {
  S.detail = { tab, ...state };
  S.filled = { ...(S.filled || {}), [tab]: true };
  S.tabHtml = { ...(S.tabHtml || {}), [tab]: html };
  S.tabBind = { ...(S.tabBind || {}), [tab]: bind };
  showTab(tab);
  if (!state.quiet) $("details").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPair({ quiet = false } = {}) {
  const side = (k) => `<div class="pair-side" data-side="${k}">${S.pair[k]?.html || `<div class="tab-hint">${esc(PAIR_HINT[k])}</div>`}</div>`;
  const html = `<div class="pair">${side("item")}${side("problem")}</div>`;
  const bind = (body) => ["item", "problem"].forEach((k) => S.pair[k]?.bind?.(body.querySelector(`[data-side="${k}"]`)));
  if (!S.pair.item && !S.pair.problem) {
    S.tabHtml = { ...(S.tabHtml || {}), pair: html };
    S.tabBind = { ...(S.tabBind || {}), pair: bind };
    showTab("pair");
    return;
  }
  details("pair", html, { pair: true, quiet }, bind);
}

async function openNode(nodeId) {
  const load = async () => {
    const [d, all] = await Promise.all([api(`/api/lines/${S.lineId}/stages/${encodeURIComponent(nodeId)}?${stageQuery()}`), api(`/api/lines/${S.lineId}/stages/${encodeURIComponent(nodeId)}?${stageQuery({ total: true })}`)]);
    const s = d.stats, t = all.stats;
    const op = s.kind === "operation";
    const median = s.median_reported_s ?? s.median_active_s;
    const stats = op
      ? `${kpi(pair(s.runs, t.runs), "Выполнений")}${kpi(pair(s.in_progress, t.in_progress), "В работе")}${gkpi(pair(mins(median), mins(t.median_reported_s ?? t.median_active_s)), `Длительность, медиана (норма ${mins(s.norm_duration_s)})`, "duration", median == null ? null : ratio(median, s.norm_duration_s))}${gkpi(pair(s.rework_runs, t.rework_runs), "Доработок", "rework", ratio(s.rework_runs, s.runs))}${gkpi(pair(s.deviations, t.deviations), "Отклонений станка", "deviations", ratio(s.deviations, s.runs))}${gkpi(pair(s.defects_originated, t.defects_originated), "Дефектов возникло", "originated", ratio(s.defects_originated, s.items))}`
      : `${kpi(pair(s.checks, t.checks), "Проверок")}${gkpi(pair(pct(s.pass_rate), pct(t.pass_rate)), "Без признаков", "found", s.pass_rate == null ? null : 1 - s.pass_rate)}${gkpi(pair(s.found, t.found), "С признаками", "found", ratio(s.found, s.checks))}${gkpi(pair(s.not_assessable, t.not_assessable), "Оценка невозможна", "unassessable", ratio(s.not_assessable, s.checks))}${kpi(pair(s.first_detections, t.first_detections), "Впервые обнаружено")}${gkpi(s.open_detections, "Ждут решения", "open", s.open_detections)}`;
    const operators = op && Object.keys(s.by_operator).length ? `<h3>Операторы</h3><table><tr><th>Оператор</th><th class="num">Выполнений</th><th class="num">Подтверждённых ошибок</th></tr>${Object.entries(s.by_operator).map(([w, r]) => `<tr><td class="mono">${esc(w)}</td><td class="num">${r.runs}</td><td class="num">${r.confirmed_errors}</td></tr>`).join("")}</table>` : "";
    details("stage", `<div class="card-head"><h2>${op ? "⚙" : "◉"} ${esc(s.title)}</h2>${badge(op ? "Операция" : "Контроль", "plain")}</div>
      <div class="muted small mono">${esc(s.station_id)}${s.equipment_id ? `, ${esc(s.equipment_id)}` : ""}${s.checkpoint_id ? `, ${esc(s.checkpoint_id)}` : ""}</div>
      <p class="muted small">Значения: за выбранный промежуток / на текущий момент.</p>
      <div class="detail-grid"><div><div class="kpis three">${stats}</div>${gradeLegend()}${operators}
        ${d.items.length ? `<h3>Изделия на этапе</h3><table>${d.items.slice(0, 20).map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono small">${esc(r.item_id)}</td><td>${op ? esc(L.runOutcome[r.outcome] || r.outcome) : badge(...(L.result[r.outcome] || [r.outcome]))}</td><td class="muted small">${time(r.at, true)}</td></tr>`).join("")}</table>` : ""}</div>
        <div>${d.machine ? machinePanel(d.machine) : ""}</div></div>`,
    { node: nodeId, refresh: () => load(), quiet: true });
  };
  await load();
}

// Станок этапа для технолога: полоса состояний и по графику на параметр режима. Зелёная
// полоса — допуск из справочника оборудования, точки вне её — красные.
function machinePanel(m) {
  const readings = m.events.filter((e) => Object.keys(e.parameters).length);
  const charts = Object.entries(m.parameters).map(([key, spec]) => {
    const points = readings.filter((e) => e.parameters[key] != null).map((e) => ({ at: e.at, v: e.parameters[key], state: e.state }));
    if (!points.length) return `<div class="param"><div class="row spread"><b class="small">${esc(spec.title)}</b><span class="muted small">Показаний нет</span></div></div>`;
    const values = points.map((p) => p.v);
    const lo = Math.min(spec.low, ...values), hi = Math.max(spec.high, ...values);
    const pad = (hi - lo) * 0.12 || 1;
    const W = 300, H = 64, y = (v) => H - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * H;
    const x = (i) => (points.length === 1 ? W / 2 : 6 + (i / (points.length - 1)) * (W - 12));
    const outside = points.filter((p) => p.v < spec.low || p.v > spec.high);
    const last = points[points.length - 1];
    const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    return `<div class="param"><div class="row spread"><b class="small">${esc(spec.title)}</b><span class="small ${last.v < spec.low || last.v > spec.high ? "bad-text" : ""}">Последнее значение: ${last.v} ${esc(spec.unit)}</span></div>
      <svg viewBox="0 0 ${W} ${H}" class="param-chart"><rect x="0" y="${y(spec.high)}" width="${W}" height="${Math.max(1, y(spec.low) - y(spec.high))}" class="band"/><path d="${line}" class="trace"/>
        ${points.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="${p.v < spec.low || p.v > spec.high ? 3.5 : 2}" class="${p.v < spec.low || p.v > spec.high ? "out" : "in"}"><title>${time(p.at, true)}: ${p.v} ${esc(spec.unit)}</title></circle>`).join("")}</svg>
      <div class="muted small">Допуск ${spec.low}–${spec.high} ${esc(spec.unit)}. Вне допуска: ${outside.length} из ${points.length}.</div></div>`;
  }).join("");
  const faults = m.events.filter((e) => e.state !== "running" && e.state !== "idle");
  return `<h3>Станок</h3><div class="machine-card"><b>${esc(m.title)}</b><span class="muted small">${esc(m.type_title)}${m.processing ? `, ${esc(m.processing)}` : ""}, ${esc(m.equipment_id)}</span></div>
    <div class="muted small" style="margin:8px 0 4px">Последние состояния: ${m.events.length}. ${Object.entries(MACHINE_STATE).map(([, [c, w]]) => `<span class="dot" style="background:${c}"></span> ${w}`).join(" ")}</div>
    ${stateStrip(m.events.map((e) => e.state))}
    <div class="params">${charts || `<div class="empty">У станка нет параметров в справочнике.</div>`}</div>
    ${faults.length ? `<h3>Сбои</h3><ul class="plain small">${faults.slice(-6).reverse().map((e) => `<li>${time(e.at, true)}. <b>${esc((MACHINE_STATE[e.state] || [0, e.state])[1])}</b>${e.message ? `: ${esc(e.message)}` : ""}${Object.entries(e.parameters).filter(([k]) => m.parameters[k] && (e.parameters[k] < m.parameters[k].low || e.parameters[k] > m.parameters[k].high)).map(([k, v]) => `. ${esc(m.parameters[k].title)} ${v} ${esc(m.parameters[k].unit)} при допуске ${m.parameters[k].low}–${m.parameters[k].high}`).join("")}.</li>`).join("")}</ul>` : ""}`;
}

// Маршрут изделия в хронологии: каждое прохождение этапа — отдельный шаг. Проблемный
// проход и повторный проход того же этапа не сливаются.
async function loadItemSide(itemId) {
  // История изделия всегда полная, на настоящее время: шкала её не обрезает, а только
  // переносит внимание на момент шага.
  const path = await api(`/api/items/${encodeURIComponent(itemId)}/path`);
  const visits = path.visits || [];
  const chain = visits.map((v, i) => `<div class="hop vt-${v.label}"><div class="node-chip click" data-visit="${i}" data-problems="${esc((v.problem_ids || []).join(" "))}" title="${esc(v.at ? `Перейти к моменту ${time(v.at, true)}${v.problem_id ? " и открыть проблему" : ""}` : L.visit[v.label] || v.label)}"><b>${esc(v.title)}</b><span class="small">${esc(L.visit[v.label] || v.label)}</span>${v.at ? `<span class="muted small">${time(v.at, true)}</span>` : ""}</div><div class="arrow"></div></div>`).join("");
  const steps = path.route.map((n) => {
    const runs = n.runs.map((r) => `<div class="small">${r.rework ? badge("Доработка", "info") + " " : ""}Оператор <span class="mono">${esc(r.operator_id || "не указан")}</span>, ${time(r.started_at, true)}, ${mins(r.active_s)}</div>`).join("");
    const checks = n.checks.map((c) => `<div class="small">${badge(...(L.result[c.result] || [c.result]))} ${time(c.at, true)}, уверенность ${c.confidence ?? "не указана"}${c.reliable ? "" : " " + badge(c.note || "Недостоверно", "warn")}</div>`).join("");
    const ncs = n.nonconformances.map((x) => `<div class="small"><a href="#" data-nc="${esc(x.nc_id)}">${esc(x.defect_type)}</a> ${badge({ detected: "Обнаружен здесь", origin: "Возник здесь", possible_origin: "Мог возникнуть здесь" }[x.role], x.role === "detected" ? "bad" : "warn")}</div>`).join("");
    return `<div class="st-${n.status}"><b>${esc(n.title)}</b> <span class="muted small">${esc(n.equipment_id || "")}</span>${runs}${checks}${ncs}${!runs && !checks ? `<div class="muted small">${esc(L.pathStatus[n.status])}</div>` : ""}</div>`;
  }).join("");
  S.pair.item = { id: itemId, html: `<div class="card-head"><h2>Изделие <span class="mono">${esc(path.item_id)}</span></h2>${badge(...(L.status[path.status] || [path.status]))}</div>
    <div class="muted small">${esc(path.item_type_id)}${path.parent_id ? `. В составе <a href="#" data-item="${esc(path.parent_id)}">${esc(path.parent_id)}</a>` : ""}${path.components.length ? `. Компоненты: ${path.components.map((c) => `<a href="#" data-item="${esc(c)}">${esc(c)}</a>`).join(", ")}` : ""}</div>
    <div class="route">${chain}</div>
    <div class="legend">${["passed", "possible_origin", "origin", "defect", "not_assessable", "rework", "ok", "pending"].map((k) => `<span class="vt-key vt-${k}"><i></i>${L.visit[k]}</span>`).join("")}</div>
    <h3>По этапам</h3><div class="steps">${steps}</div>`,
  bind: (side) => {
    // Шаг выбранной проблемы обведён: история та же, внимание — на моменте проблемы.
    const chosen = S.focusByLane.items?.problem_id;
    side.querySelectorAll("[data-visit]").forEach((el) => {
      el.classList.toggle("chosen", !!chosen && el.dataset.problems.split(" ").includes(chosen));
      el.addEventListener("click", () => selectVisit(itemId, visits[Number(el.dataset.visit)]));
    });
  } };
}

// Шаг маршрута: линия переходит к моменту шага, этап выделяется; у шага с проблемой она
// открывается справа — так же, как при выборе проблемы в очереди.
async function selectVisit(itemId, v) {
  queueMicrotask(() => renderRolePanel());
  selectNode(v.node_id);
  if (v.problem_id) {
    S.focusByLane.items = { lane: "items", problem_id: v.problem_id, node_id: v.node_id, item_id: itemId, at: v.at, title: v.problem_id.startsWith("NA-") ? "Оценка невозможна" : v.title, kind: "problem" };
    if (!laneFixed()) S.lane = "items";
  }
  await Promise.all([v.at ? jumpTo(v.at) : null, v.problem_id ? openProblem(v.problem_id) : null]);
  if (!v.problem_id && S.live) renderGraph(S.live);
}

// Проблемы изделия справа, пока ни одна из них не выбрана: действующие и решённые.
async function loadItemProblems(itemId) {
  const data = await api(`/api/lines/${S.lineId}/problems?item=${encodeURIComponent(itemId)}`);
  const all = [...data.active, ...data.resolved];
  const row = (p, resolved) => `<li class="problem ${resolved ? "resolved" : problemClass(p)}" data-pick="${esc(p.problem_id)}"><div class="row spread"><b class="small">${esc(p.title)}</b><span class="small">${resolved ? esc(RESOLUTION[p.resolution] || "Решено") : esc(p.kind === "not_assessable" ? "Нужна повторная проверка" : SEVERITY[p.severity] || "")}</span></div><div class="muted small">${esc(nodeTitle(p.node_id))}. Обнаружено ${time(p.at, true)}.</div></li>`;
  S.pair.problem = { id: null, itemId, html: `<div class="card-head"><h2>Проблемы изделия</h2>${badge(data.active.length, data.active.length ? "bad" : "ok")}</div>
    ${all.length ? `<ul class="list">${data.active.map((p) => row(p, false)).join("")}${data.resolved.map((p) => row(p, true)).join("")}</ul>` : `<div class="empty">У изделия проблем нет.</div>`}`,
  bind: (side) => side.querySelectorAll("[data-pick]").forEach((el) => el.addEventListener("click", () => {
    const p = all.find((x) => x.problem_id === el.dataset.pick);
    S.focusByLane.items = { lane: "items", problem_id: p.problem_id, node_id: p.node_id, item_id: p.item_id, at: p.at, title: p.title, kind: "problem" };
    if (S.live) renderGraph(S.live);
    renderTimeline();
    openProblem(p.problem_id);
  })) };
}

async function openItem(itemId) {
  try {
    await loadItemSide(itemId);
    if (S.pair.problem?.itemId !== itemId) await loadItemProblems(itemId);
    renderPair();
  } catch (error) { notify(error.message, true); }
}

// Проблема справа и её изделие слева: при выборе другой проблемы изделие обновляется.
async function openProblem(problemId) {
  try {
    const itemId = problemId.startsWith("NA-") ? await openUnassessable(problemId) : await openNc(problemId);
    if (!itemId) return;
    if (S.pair.item?.id !== itemId) {
      // Проблема показывается сразу, маршрут изделия догружается следом: он строится на
      // момент шкалы и может считаться дольше.
      S.pair.item = { id: itemId, html: `<div class="tab-hint">Загрузка изделия <span class="mono">${esc(itemId)}</span>.</div>` };
      renderPair();
      await loadItemSide(itemId);
    }
    renderPair();
  } catch (error) { notify(error.message, true); }
}

const NA_ACTIONS = { request_recheck: "Назначить повторный контроль", accept_manual: "Допустить по ручному контролю" };
const NA_RESOLUTION = { rechecked: "Повторный достоверный контроль выполнен", accepted_manual: "Допущено по ручному контролю" };

async function openUnassessable(problemId) {
  const data = await api(`/api/lines/${S.lineId}/problems?problem=${encodeURIComponent(problemId)}`);
  const p = [...data.active, ...data.resolved][0];
  if (!p) { notify("Проблема не найдена.", true); return null; }
  const done = !!p.resolved_at;
  const actions = !done && can("decide_unassessable") ? Object.keys(NA_ACTIONS).filter((a) => a !== "request_recheck" || p.status !== "recheck_requested") : [];
  const state = done ? ["Решено", "ok"] : p.status === "recheck_requested" ? ["Повторный контроль назначен", "info"] : ["Нужно решение", "warn"];
  const decisions = p.decisions || [];
  S.pair.problem = { id: problemId, itemId: p.item_id, html: `<div class="card-head"><h2>Оценка невозможна</h2>${badge(...state)}</div>
    <p class="small">${esc(nodeTitle(p.node_id))}. Обнаружено ${time(p.at, true)}.</p>
    <div class="layer source"><div class="layer-title">1. Сообщение анализатора</div><div class="small">${esc(p.note || "Анализатор сообщил, что оценка невозможна.")}</div></div>
    <div class="layer system"><div class="layer-title">2. Оценка системы</div><div class="small">Изделие не признаётся ни годным, ни бракованным. Отсутствие признаков при плохом наблюдении не подтверждает годность, поэтому этап возникновения по этому наблюдению не ограничивается.</div></div>
    <div class="layer human"><div class="layer-title">3. Решения людей (отдельные записи журнала)</div>
      ${decisions.length ? `<ul class="plain small">${decisions.map((d) => `<li>${time(d.decided_at, true)}, <span class="mono">${esc(d.author_id)}</span>: ${esc(NA_ACTIONS[d.action] || d.action)}. <span class="muted">${esc(d.reason)}</span></li>`).join("")}</ul>` : `<div class="empty">Решений нет.</div>`}
      ${done ? `<p class="small">${esc(NA_RESOLUTION[p.resolution] || "Решено")}: ${time(p.resolved_at, true)}.</p>` : ""}
      ${S.at && actions.length ? `<p class="muted small">Линия показана на ${time(S.at, true)}. Решение фиксируется текущим временем.</p>` : ""}
      ${actions.length ? `<form id="na-decision" class="form" style="margin-top:8px"><select name="action">${actions.map((a) => `<option value="${a}">${esc(NA_ACTIONS[a])}</option>`).join("")}</select><textarea name="reason" rows="2" placeholder="Обоснование (обязательно)" required></textarea><button class="btn primary">Записать решение</button></form>` : done ? "" : `<p class="muted small">Роль «${esc(session.me.role_title)}» не принимает решений по этой проблеме.</p>`}</div>`,
  bind: (side) => side.querySelector("#na-decision")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      await api(`/api/unassessable/${encodeURIComponent(problemId)}/decisions`, { method: "POST", body: JSON.stringify({ action: form.action.value, reason: form.reason.value }) });
      notify(form.action.value === "request_recheck" ? "Повторный контроль назначен. Решение записано отдельной записью журнала." : "Изделие допущено. Решение записано отдельной записью журнала.");
      await afterDecision(problemId);
    } catch (error) { notify(error.message, true); }
  }) };
  return p.item_id;
}

// После решения карточка обновляется, а проблема выделяется в очереди: список
// прокручивается к ней, чтобы сразу было видно, где она теперь — среди действующих или
// решённых.
async function afterDecision(problemId) {
  // Подсветка держится и при перерисовке очереди опросом; прокрутка мгновенная — плавную
  // сбивала следующая перерисовка.
  S.justDecided = { id: problemId, until: Date.now() + 3000 };
  await openProblem(problemId);
  await Promise.all([refreshQueue(), refreshOverview()]);
  renderRolePanel(true);
  const panel = $("role-panel");
  const row = panel.querySelector(`[data-problem="${CSS.escape(problemId)}"]`);
  // Прокручивается только очередь: страница остаётся на карточке решения.
  if (row) panel.scrollTop += row.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.clientHeight / 2;
  setTimeout(() => renderRolePanel(true), 3100);
}

async function openNc(ncId) {
  const card = await api(`/api/nonconformances/${encodeURIComponent(ncId)}`);
  const a = card.assessment || {};
  const causes = ["incoming_defect", "equipment_problem", "operator_error", "process_issue", "handling_damage", "other"];
  const f = S.focusByLane.items;
  if (f?.problem_id === ncId && card.defect_title && f.title !== card.defect_title) {
    f.title = card.defect_title;
    renderFocusNote();
  }
  S.pair.problem = { id: ncId, itemId: card.item_id, html: `<div class="card-head"><h2>${esc(card.defect_title || card.defect_type)}</h2>${badge(...(L.nc[card.status] || [card.status]))}</div>
    <div class="muted small">${esc(card.defect_type)}, ${esc(card.nc_id)}, зона ${esc(card.area)}, ${esc(SEVERITY[card.severity] || card.severity)}. Обнаружено ${time(card.first_detected_at, true)}.</div>
    <div class="layer source"><div class="layer-title">1. Сообщения анализатора в исходном виде</div>
      ${card.signals_detail.map((s) => `<div class="small">${badge(...(L.result[s.inspection_result] || [s.inspection_result]))} ${time(s.occurred_at, true)}, ${esc(s.checkpoint_id)}, уверенность ${s.confidence ?? "не указана"}${s.reliable ? "" : " " + badge(s.reliability_note || "Недостоверно", "warn")}<details><summary class="muted small">Исходное сообщение</summary><pre class="raw">${esc(JSON.stringify(s.raw, null, 2))}</pre></details></div>`).join("")}</div>
    <div class="layer system"><div class="layer-title">2. Разбор системы (оценка, не решение)</div>
      <div class="row"><b>${esc(L.stage[a.stage] || a.stage)}</b>${badge(...(L.conf[a.stage_confidence] || [a.stage_confidence]))}</div>
      <h3>Основания</h3><ul class="plain small">${(a.evidence || []).map((e) => `<li>${esc(e.text)}</li>`).join("") || "<li>Нет.</li>"}</ul>
      <h3>Альтернативные причины</h3><ul class="plain small">${(a.alternatives || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>Нет.</li>"}</ul>
      <h3>Недостающие сведения</h3><ul class="plain small">${(a.missing || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>Не отмечены.</li>"}</ul></div>
    <div class="layer human"><div class="layer-title">3. Решения людей (отдельные записи журнала)</div>
      ${card.decisions.length ? `<ul class="plain small">${card.decisions.map((d) => `<li>${time(d.decided_at, true)}, <span class="mono">${esc(d.author_id)}</span>: ${esc(L.action[d.action] || d.action)}${d.cause_category ? ` (${esc(L.cause[d.cause_category])})` : ""}. <span class="muted">${esc(d.reason)}</span></li>`).join("")}</ul>` : `<div class="empty">Решений нет.</div>`}
      ${S.at && card.allowed_actions.length ? `<p class="muted small">Линия показана на ${time(S.at, true)}. Решение фиксируется текущим временем.</p>` : ""}${card.allowed_actions.length ? `<form id="decision" class="form" style="margin-top:8px"><select name="action">${card.allowed_actions.map((x) => `<option value="${x}">${esc(L.action[x])}</option>`).join("")}</select><select name="cause" class="hidden">${causes.map((c) => `<option value="${c}">${esc(L.cause[c])}</option>`).join("")}</select><textarea name="reason" rows="2" placeholder="Обоснование (обязательно)" required></textarea><button class="btn primary">Записать решение</button></form>` : `<p class="muted small">Роль «${esc(session.me.role_title)}» не принимает решений по этой карточке.</p>`}</div>`,
  bind: (side) => {
    const form = side.querySelector("#decision");
    if (!form) return;
    const sync = () => form.cause.classList.toggle("hidden", form.action.value !== "confirm_cause");
    form.action.addEventListener("change", sync);
    sync();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/api/nonconformances/${encodeURIComponent(ncId)}/decisions`, { method: "POST", body: JSON.stringify({ action: form.action.value, reason: form.reason.value, cause_category: form.action.value === "confirm_cause" ? form.cause.value : null }) });
        notify("Решение записано отдельной записью журнала.");
        await afterDecision(ncId);
      } catch (error) { notify(error.message, true); }
    });
  } };
  return card.item_id;
}

async function openItems() {
  const load = async () => {
    const f = { ...S.itemFilters, item_type: S.itemFilters.item_type || S.itemType };
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    // По умолчанию — все изделия линии на настоящее время. Кнопка оставляет только
    // изделия с событиями внутри промежутка шкалы.
    if (S.itemsInRange) {
      p.set("since", new Date(sinceMs()).toISOString());
      if (S.range.until) p.set("until", S.range.until);
    }
    const data = await api(`/api/lines/${S.lineId}/items?${p}&limit=150`);
    const opt = (values, current, labels = {}) => `<option value="">Все</option>` + values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
    details("items", `<div class="card-head"><h2>Изделия линии</h2><button class="btn small ${S.itemsInRange ? "active" : ""}" data-range-filter title="Оставить изделия с событиями внутри промежутка шкалы">Только за промежуток</button><span class="muted small">Найдено: ${data.total}${data.total > data.rows.length ? `, показано: ${data.rows.length}` : ""}</span></div>
      <div class="table-wrap"><table><tr><th>Изделие</th><th>Тип</th><th>Статус</th><th>Этап</th></tr>
        <tr class="filters"><th><input data-f="q" value="${esc(f.q)}" placeholder="Номер"></th><th><select data-f="item_type">${opt(data.facets.item_type, f.item_type)}</select></th>
          <th><select data-f="status">${opt(data.facets.status, f.status, Object.fromEntries(Object.entries(L.status).map(([k, v]) => [k, v[0]])))}</select></th><th><select data-f="stage">${opt(data.facets.stage, f.stage, data.stage_titles)}</select></th></tr>
        ${data.rows.map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono small">${esc(r.item_id)}</td><td class="mono small">${esc(r.item_type_id)}</td><td>${badge(...(L.status[r.status] || [r.status]))}</td><td class="small">${esc(data.stage_titles[r.stage] || "—")}</td></tr>`).join("")}</table></div>`,
    { items: true, refresh: () => (S.itemsInRange ? load() : null) }, (body) => {
      body.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => { S.itemFilters[el.dataset.f] = el.value; load(); }));
      body.parentElement.querySelector("[data-range-filter]")?.addEventListener("click", () => { S.itemsInRange = !S.itemsInRange; load(); });
    });
  };
  await load();
}

async function openEconomics() {
  const e = await api(`/api/lines/${S.lineId}/economics`);
  const p = e.parameters, m = e.measured, c = e.computed;
  const labels = { item_value_rub: "Стоимость годного изделия, ₽", rework_cost_rub: "Стоимость доработки, ₽", scrap_cost_rub: "Потери на браке, ₽", hour_cost_rub: "Стоимость часа участка, ₽", shift_hours: "Длительность смены, ч" };
  const losses = Object.entries(c.losses_by_node_rub);
  const max = Math.max(1, ...losses.map(([, v]) => v));
  details("economics", `<div class="card-head"><h2>Экономика линии</h2></div><p class="muted small">${esc(e.origin)}.</p>
    <div class="detail-grid"><div><div class="kpis three">${kpi(rub(c.output_value_rub), "Выпуск годных")}${kpi(rub(c.rework_cost_rub + c.scrap_cost_rub), "Потери от брака")}${kpi(pct(c.losses_share), "Потери к выпуску")}${kpi(rub(c.cost_per_good_item_rub), "Затраты на годное")}${kpi(c.capacity_per_shift ?? "—", "Изделий за смену")}${kpi(pct(c.final_yield), "Годных на ОТК")}</div>
    <h3>Потери по этапам</h3>${losses.length ? `<div class="bars">${losses.map(([node, v]) => `<div class="bar-row" data-node="${esc(node)}"><small>${esc(nodeTitle(node))}</small><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><b class="small">${Math.round(v / 1000)} тыс</b></div>`).join("")}</div>` : `<div class="empty">Нет.</div>`}</div>
    <div><h3>Измерено</h3><dl class="kv"><dt>Изделий-продуктов</dt><dd>${m.products}</dd><dt>Годных</dt><dd>${m.finished_conforming}</dd><dt>Доработок</dt><dd>${m.rework_runs}</dd><dt>Часов на участках</dt><dd>${m.station_hours}</dd><dt>Узкое место</dt><dd>${esc(nodeTitle(m.bottleneck_node))}</dd></dl>
    <h3>Параметры</h3>${can("line_manage") ? `<form id="econ" class="form">${Object.entries(labels).map(([k, l]) => `<label>${l}<input name="${k}" type="number" step="any" min="0" value="${p[k]}"></label>`).join("")}<button class="btn primary">Сохранить</button></form>` : `<dl class="kv">${Object.entries(labels).map(([k, l]) => `<dt>${l}</dt><dd>${p[k]}</dd>`).join("")}</dl>`}<p class="muted small">${esc(p.note)}</p></div></div>`,
  { economics: true }, (body) => body.querySelector("#econ")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api(`/api/lines/${S.lineId}/economics`, { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(ev.target).entries())) }); notify("Параметры сохранены."); openEconomics(); } catch (error) { notify(error.message, true); }
  }));
}

// --- инструкция: как в cosmo-net — кнопка рядом с названием, окно поверх экрана, Esc закрывает ---------------------

function openGuide() {
  const role = S.viewRole || session.me?.role || "controller";
  modal(`<div class="modal-head"><h2>Руководство пользователя</h2><button class="btn small" data-close aria-label="Закрыть руководство">✕</button></div>
    <div class="guide-body">
      <section><h3>Роль «${esc(L.role[role])}»</h3><ol>${GUIDE[role].map((g) => `<li>${esc(g)}</li>`).join("")}</ol></section>
      <section><h3>Обзор производства</h3><p>Первый экран содержит все линии предприятия. Для каждой линии указаны схема, число изделий, операций в работе и проблем, ожидающих решения. Красная рамка обозначает линию с проблемами. Фильтр изделий сверху оставляет линии, выпускающие выбранное изделие.</p></section>
      <section><h3>Экран линии</h3><p>В центре расположен граф линии: этапы процесса и связи между ними. Колесо мыши изменяет масштаб, перетаскивание фона сдвигает граф, кнопка <b>⤢</b> вписывает граф целиком.</p>
        <ul><li>Под каждым этапом указано число изделий, прошедших его за выбранный промежуток: <span class="dot" style="background:var(--accent)"></span> в работе, <span class="dot" style="background:var(--ok)"></span> без проблем на этапе, <span class="dot" style="background:var(--bad)"></span> проблема на этапе, <span class="dot" style="background:var(--amber)"></span> оценка невозможна на этапе. Красный и жёлтый счётчики сходятся с кругом над этапом. Проблема, решённая в любой момент, в том числе позже конца промежутка, считается решённой.</li>
        <li>Красный круг над этапом показывает число проблем, действующих на выбранный момент: у технолога это сбои станка, у остальных ролей проблемы изделий. Нажатие открывает список: действующие проблемы сверху, решённые за промежуток серым в конце. Выбранный круг обведён.</li>
        <li>Выбранная проблема выделяется кольцом вокруг этапа и подписью над графом при любом положении шкалы времени.</li></ul></section>
      <section><h3>Шкала времени</h3><p>Шкала задаёт промежуток двумя ползунками, промежуток целиком сдвигается перетаскиванием. Колесо мыши над шкалой приближает её к курсору, Shift и колесо сдвигают, кнопки ＋, －, ⤢ меняют масштаб. Граф показывает состояние линии на конец промежутка, числа под этапами и таблица этапов считаются внутри промежутка. Длина промежутка выбирается кнопками «Сутки», «Смена», «Всё время»; значение по умолчанию задаётся в конфигурации.</p>
        <p>На шкале три цвета: красный означает проблему, жёлтый означает, что оценка невозможна, зелёный означает решение. Для станков красная отметка означает сбой, зелёная означает возврат в работу. У выбранной проблемы возникновение и решение соединены линией. Кнопка «Скрыть решённые» убирает решённые проблемы целиком: и отметку обнаружения, и отметку решения. Решение, принятое при просмотре прошлого, фиксируется текущим временем.</p></section>
      <section><h3>Подробности под линией</h3><p>Вкладки «Изделие и проблема», «Этап», «Изделия линии» и «Экономика» заполняются при выборе события. Во вкладке «Изделие и проблема» слева показано изделие, справа проблема; выбор другой проблемы обновляет изделие. Нажатие на шаг маршрута изделия переносит линию в момент этого шага и открывает его проблему. После решения проблема выделяется в очереди. Изделие, по которому принято решение, эмуляция ведёт дальше: после подтверждения дефекта операции — доработка и повторный контроль, после отклонения сигнала или закрытия — путь дальше по линии. Карточка несоответствия содержит три раздела: исходные сообщения анализатора, разбор системы и решения людей.</p></section>
      <section><h3>Этапы процесса</h3><p>Вкладка «Этапы процесса» под линией показывает таблицу этапов. В каждой ячейке указано значение за выбранный промежуток и значение на текущий момент. Выбор этапа в таблице и на графе синхронизирован.</p></section>
      <section><h3>Новая линия и оборудование</h3><p>Администратор описывает типы станков и виды дефектов. Технолог регистрирует по типу конкретный станок. Руководитель в блочном редакторе назначает операцию на станок из справочника; обработка и виды дефектов определяются типом станка.</p></section>
      <section><h3>Пульт эмулятора</h3><p>Пульт эмулятора открывается в том же окне кнопкой в шапке. В пульте запускается и останавливается эмуляция линии, вносится дефект в выбранный этап и проигрывается прогон по данным контракта. Система принимает события эмулятора как события обычных источников.</p></section>
      <section><h3>Часто задаваемые вопросы</h3><dl><dt>Почему изделие «на рассмотрении», а не «брак»?</dt><dd>Сигнал анализатора становится несоответствием только после подтверждения контролёром.</dd><dt>Почему «оценка невозможна» не считается браком?</dt><dd>Отсутствие признаков при плохом наблюдении не подтверждает ни годность, ни брак. Требуется повторная проверка.</dd><dt>Почему система не называет виновного?</dt><dd>Оператор является участником операции. Ошибка оператора фиксируется только решением человека.</dd></dl></section>
    </div>`);
}

// --- администрирование ---------------------------------------------------------------------------------------------------

// У администратора это главный экран. Администратор описывает шаблоны (типы станков и виды
// дефектов), заводит роли и пользователей и следит за журналом. Конкретный станок по
// шаблону заводит технолог: у него раздел «Оборудование» открывается окном.
const ADMIN_SECTIONS = [["machine_types", "Типы станков"], ["defects", "Виды дефектов"], ["equipment", "Оборудование"], ["roles", "Роли"], ["users", "Пользователи"], ["db", "База данных"], ["integrity", "Целостность журнала"], ["keys", "Ключи"], ["audit", "Журнал действий"], ["integration", "Интеграции"]];
const STATUS_EQ = { active: ["В работе", "ok"], maintenance: ["На обслуживании", "warn"], retired: ["Списан", "plain"] };

function showAdmin(section = S.adminSection || "machine_types") {
  S.view = "admin";
  S.adminSection = section;
  store.set(viewKey(), "admin");
  stopPollers();
  stopPlaying();
  $("plant-view").classList.add("hidden");
  $("line-view").classList.add("hidden");
  $("admin-view").classList.remove("hidden");
  renderTop();
  $("admin-view").innerHTML = `<div class="admin-page-head"><h1>Администрирование</h1><p class="muted">Шаблоны оборудования и дефектов, роли, пользователи и служебные функции системы.</p></div>
    <div class="admin card"><nav class="admin-nav">${ADMIN_SECTIONS.map(([k, t]) => `<button class="btn ${k === section ? "active" : ""}" data-sec="${k}">${t}</button>`).join("")}</nav><div id="admin-body" class="admin-body">Загрузка.</div></div>`;
  $("admin-view").querySelectorAll("[data-sec]").forEach((b) => b.addEventListener("click", () => showAdmin(b.dataset.sec)));
  renderAdminSection($("admin-body"), section);
}

async function renderAdminSection(body, key) {
  try { body.innerHTML = await ADMIN[key](); await ADMIN_BIND[key]?.(body); } catch (error) { body.innerHTML = `<p class="bad-text">${esc(error.message)}</p>`; }
}

// Раздел оборудования для технолога: тот же справочник станков, открытый окном.
function openEquipment() {
  const { dialog } = modal(`<div class="modal-head"><h2>Оборудование</h2><button class="btn small" data-close>✕</button></div><div class="admin-body" id="equipment-body">Загрузка.</div>`, { wide: true });
  renderAdminSection(dialog.querySelector("#equipment-body"), "equipment");
}

const defectOptions = (catalog) => catalog.defects.map((d) => ({ value: d.code, label: d.title, hint: d.code }));
const csv = (text) => text.split(",").map((x) => x.trim()).filter(Boolean);

const ADMIN = {
  async machine_types() {
    const rows = await api("/api/admin/machine-types");
    return `<div class="card-head"><h3>Типы станков</h3><button class="btn primary small" id="new-type">＋ Новый тип</button></div>
      <p class="muted small">Тип станка задаёт шаблон: выполняемую обработку, виды дефектов и параметры режима, которые станок передаёт в журнал станка, с допусками по умолчанию. Конкретный станок по шаблону регистрирует технолог.</p>
      <div id="type-form"></div>
      <div class="table-wrap"><table><tr><th>Тип</th><th>Обработка</th><th>Виды дефектов</th><th>Параметры режима</th><th class="num">Станков</th><th></th></tr>${rows.map((r) => `<tr>
        <td><b>${esc(r.title)}</b><div class="muted small mono">${esc(r.key)}</div></td><td class="small">${esc(r.processing.join(", "))}</td><td class="small mono">${esc(r.defect_types.join(", "))}</td>
        <td class="small">${Object.entries(r.parameters).map(([k, p]) => `${esc(p.title)}, ${esc(p.unit)}: ${p.low}–${p.high} <span class="muted mono">${esc(k)}</span>`).join("<br>")}</td><td class="num">${r.machines}</td>
        <td><button class="btn small" data-type="${esc(r.key)}">Изменить</button></td></tr>`).join("")}</table></div>`;
  },
  async defects() {
    const rows = await api("/api/admin/defects");
    return `<div class="card-head"><h3>Виды дефектов</h3><button class="btn primary small" id="new-defect">＋ Новый вид</button></div>
      <p class="muted small">Код дефекта передаётся анализатором и назначается этапам линий. Для каждого вида указан метод оценки и граница визуального метода.</p>
      <div id="defect-form"></div>
      <div class="table-wrap"><table><tr><th>Код</th><th>Название</th><th>Метод оценки</th><th>Этапы линий</th><th class="num">Найдено</th><th class="num">Подтверждено</th><th></th></tr>${rows.map((r) => `<tr><td class="mono">${esc(r.code)}</td><td>${esc(r.title)}</td><td class="small muted">${esc(r.method)}</td><td class="small">${esc(r.where.join("; ") || "—")}</td><td class="num">${r.found}</td><td class="num">${r.confirmed}</td><td><button class="btn small" data-defect="${esc(r.code)}">Изменить</button></td></tr>`).join("")}</table></div>`;
  },
  async equipment() {
    const rows = await api(can("admin") ? "/api/admin/equipment" : "/api/equipment");
    return `<div class="card-head"><h3>Оборудование</h3>${can("equipment_manage") ? `<button class="btn primary small" id="new-machine">＋ Новый станок</button>` : ""}</div>
      <p class="muted small">Станок регистрируется по типу из справочника. Обработка, параметры режима и виды дефектов определяются типом; у конкретного станка задаются код, инвентарный номер, состояние, перечень дефектов и допуски. Каждое изменение записывается в журнал критических действий.</p>
      <div id="machine-form"></div>
      <div class="table-wrap"><table><tr><th>Станок</th><th>Тип и обработка</th><th>Виды дефектов</th><th>Допуски</th><th>Установлен</th><th>Состояние</th><th class="num">Сбоев</th><th></th></tr>${rows.map((r) => `<tr>
        <td><b class="mono">${esc(r.equipment_id)}</b><div class="small">${esc(r.title)}</div><div class="muted small">Инв. № ${esc(r.inventory_no || "не указан")}</div></td>
        <td class="small">${esc(r.type_title)}<div class="muted">${esc(r.processing.join(", "))}</div></td>
        <td class="small mono">${esc(r.defect_types.join(", "))}</td>
        <td class="small">${Object.values(r.parameters).map((p) => `${esc(p.title)}: ${p.low}–${p.high} ${esc(p.unit)}`).join("<br>")}</td>
        <td class="small">${esc(r.used_in.join("; ") || "Не установлен")}</td>
        <td>${badge(...STATUS_EQ[r.status])}${r.state ? `<div class="muted small">${esc(MACHINE_STATE[r.state]?.[1] || r.state)}</div>` : ""}</td><td class="num">${r.deviations}</td>
        <td>${can("equipment_manage") ? `<button class="btn small" data-machine="${esc(r.equipment_id)}">Изменить</button>` : ""}</td></tr>`).join("")}</table></div>`;
  },
  async roles() {
    const d = await api("/api/admin/roles");
    S.adminRoles = d;
    const roles = Object.entries(d.roles);
    return `<div class="card-head"><h3>Роли</h3><button class="btn primary small" id="new-role">＋ Новая роль</button></div>
      <p class="muted small">Роль определяет права и экран, который видит пользователь. Новая роль выбирает один из готовых экранов и набор прав.</p>
      <div id="role-form"></div>
      <div class="table-wrap"><table><tr><th>Право</th>${roles.map(([code, r]) => `<th class="num">${esc(r.title)}<div><button class="btn small" data-role="${esc(code)}">Изменить</button></div></th>`).join("")}</tr>${d.permissions.map((p) => `<tr><td>${esc(p.title)}</td>${roles.map(([, r]) => `<td class="num">${r.permissions.includes(p.permission) ? "✓" : ""}</td>`).join("")}</tr>`).join("")}</table></div>`;
  },
  async users() {
    const d = await api("/api/admin/roles");
    S.adminRoles = d;
    return `<div class="card-head"><h3>Пользователи</h3><button class="btn primary small" id="new-user">＋ Новый пользователь</button></div>
      <p class="muted small">Логин не меняется после создания. Пароль хранится только в виде хеша scrypt.</p>
      <div id="user-form"></div>
      <table><tr><th>Логин</th><th>Имя</th><th>Роль</th><th>Пароль</th><th>Состояние</th><th></th></tr>${d.users.map((u) => `<tr><td class="mono">${esc(u.user_id)}</td><td>${esc(u.name)}</td><td>${esc(d.roles[u.role]?.title || u.role)}</td><td>${u.has_password ? badge("Задан", "ok") : badge("Не задан", "plain")}</td><td>${u.active ? badge("Активен", "ok") : badge("Отключён", "plain")}</td><td><button class="btn small" data-user-edit="${esc(u.user_id)}">Изменить</button></td></tr>`).join("")}</table>`;
  },
  async db() {
    const d = await api("/api/admin/db");
    return `<h3>База данных: ${esc(d.dialect)}</h3><p class="muted small">Только чтение. Шифротекст журнала, хеши паролей и полезная нагрузка очереди не показываются.</p>
      <table><tr><th>Таблица</th><th class="num">Строк</th><th>Столбцы</th><th>Скрыто</th><th></th></tr>${d.tables.map((t) => `<tr><td class="mono">${esc(t.table)}</td><td class="num">${t.rows}</td><td class="small mono">${esc(t.columns.join(", "))}</td><td class="small muted">${esc(t.hidden.join(", ") || "—")}</td><td><button class="btn small" data-table="${esc(t.table)}">Строки</button></td></tr>`).join("")}</table><div id="db-rows"></div>`;
  },
  async integrity() {
    return `<h3>Целостность журнала</h3><p class="muted">Проверяются цепочка хешей, подписи пакетов и подписанная голова журнала, хранящаяся отдельно от базы.</p><button class="btn primary" id="verify">Проверить</button><div id="verify-out" style="margin-top:10px"></div>`;
  },
  async keys() {
    const k = await api("/api/keys");
    return `<h3>Ключи и профили</h3><table><tr><th>Ключ</th><th>Профиль</th><th>Статус</th><th>Секрет</th></tr>${k.keys.map((x) => `<tr><td class="mono">${esc(x.key_id)}</td><td class="mono">${esc(x.profile_id)}</td><td>${badge(x.status, x.status === "active" ? "ok" : "plain")}</td><td>${x.secret_available ? "Доступен" : badge("Недоступен", "warn")}</td></tr>`).join("")}</table>
      <div class="row" style="margin-top:10px"><select id="profile">${Object.entries(k.profiles).map(([id, m]) => `<option value="${id}">${esc(id)}: ${esc(m)}</option>`).join("")}</select><button class="btn" id="rotate">Выпустить ключ</button></div>`;
  },
  async audit() {
    const rows = await api("/api/audit");
    return `<h3>Журнал критических действий</h3><table><tr><th>№</th><th>Время</th><th>Пользователь</th><th>Действие</th></tr>${rows.slice(0, 100).map((r) => `<tr><td>${r.seq}</td><td class="small">${time(r.at, true)}</td><td class="mono small">${esc(r.user_id)}</td><td>${esc(r.action)} <span class="muted mono small">${esc(JSON.stringify(r.details))}</span></td></tr>`).join("")}</table>`;
  },
  async integration() {
    const d = await api("/api/integration");
    return `<div class="card-head"><h3>Интеграции</h3><button class="btn small primary" id="sync">Синхронизировать</button></div><p>Подключено: ${d.adapters.map((a) => badge(a, "info")).join(" ") || "нет"}</p>
      ${d.outbox.length ? `<table>${d.outbox.map((o) => `<tr><td class="mono">${esc(o.item_id)}</td><td>${badge(o.status, o.status === "delivered" ? "ok" : "warn")}</td><td>${o.attempts}</td><td class="muted small">${esc(o.last_error || o.external_ref || "")}</td></tr>`).join("")}</table>` : `<div class="empty">Очередь пуста.</div>`}`;
  },
};

const ADMIN_BIND = {
  async machine_types(body) {
    const [catalog, rows] = await Promise.all([api("/api/catalog"), api("/api/admin/machine-types")]);
    const form = body.querySelector("#type-form");
    const open = (t = null) => {
      const editing = !!t;
      const v = t || { key: "", title: "", processing: [], defect_types: [], parameters: {} };
      let defects = [...v.defect_types];
      let params = Object.entries(v.parameters).map(([k, p]) => ({ key: k, ...p }));
      form.innerHTML = `<form class="form machine-form card"><h3>${editing ? `Тип ${esc(v.title)}` : "Новый тип станка"}</h3>
        <div class="form-row"><label>Код типа<input name="key" value="${esc(v.key)}" ${editing ? "readonly" : ""} required pattern="[a-z][a-z0-9_]{1,47}" placeholder="plasma_spray"></label>
          <label>Название<input name="title" value="${esc(v.title)}" required placeholder="Установка плазменного напыления"></label>
          <label>Обработка (через запятую)<input name="processing" value="${esc(v.processing.join(", "))}" required placeholder="напыление"></label></div>
        <div class="field"><span>Виды дефектов, которые возникают на станках этого типа</span><div data-picker></div></div>
        <h3>Параметры режима, которые передаёт станок</h3><div data-params></div>
        <div class="row"><button type="button" class="btn small" data-add-param>＋ Параметр</button></div>
        <div class="row"><button class="btn primary">${editing ? "Сохранить" : "Добавить тип"}</button><button type="button" class="btn" data-cancel>Отмена</button></div></form>`;
      const f = form.querySelector("form");
      picker(f.querySelector("[data-picker]"), { options: defectOptions(catalog), selected: defects, onChange: (x) => { defects = x; } });
      const drawParams = () => {
        f.querySelector("[data-params]").innerHTML = `<table><tr><th>Код</th><th>Название</th><th>Ед.</th><th class="num">Нижняя граница</th><th class="num">Верхняя граница</th><th></th></tr>${params.map((p, i) => `<tr><td><input data-p="key" data-i="${i}" value="${esc(p.key)}" style="width:140px"></td><td><input data-p="title" data-i="${i}" value="${esc(p.title)}"></td><td><input data-p="unit" data-i="${i}" value="${esc(p.unit)}" style="width:70px"></td><td class="num"><input data-p="low" data-i="${i}" type="number" step="any" value="${p.low}" style="width:90px"></td><td class="num"><input data-p="high" data-i="${i}" type="number" step="any" value="${p.high}" style="width:90px"></td><td><button type="button" class="btn small" data-del="${i}">×</button></td></tr>`).join("")}</table>`;
        f.querySelectorAll("[data-p]").forEach((el) => el.addEventListener("input", () => { params[Number(el.dataset.i)][el.dataset.p] = el.value; }));
        f.querySelectorAll("[data-del]").forEach((el) => el.addEventListener("click", () => { params.splice(Number(el.dataset.del), 1); drawParams(); }));
      };
      drawParams();
      f.querySelector("[data-add-param]").addEventListener("click", () => { params.push({ key: "", title: "", unit: "", low: 0, high: 1 }); drawParams(); });
      f.querySelector("[data-cancel]").addEventListener("click", () => { form.innerHTML = ""; });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const spec = { key: f.key.value, title: f.title.value, processing: csv(f.processing.value), defect_types: defects, parameters: Object.fromEntries(params.filter((p) => p.key).map((p) => [p.key, { title: p.title, unit: p.unit, low: Number(p.low), high: Number(p.high) }])) };
        try {
          await api(editing ? `/api/admin/machine-types/${encodeURIComponent(spec.key)}` : "/api/admin/machine-types", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
          notify(editing ? `Тип «${spec.title}» сохранён.` : `Тип «${spec.title}» добавлен. По нему можно регистрировать станки.`);
          renderAdminSection(body, "machine_types");
        } catch (error) { notify(error.message, true); }
      });
    };
    body.querySelector("#new-type").addEventListener("click", () => open());
    body.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => open(rows.find((r) => r.key === b.dataset.type))));
  },
  async defects(body) {
    const rows = await api("/api/admin/defects");
    const form = body.querySelector("#defect-form");
    const open = (d = null) => {
      const editing = !!d;
      const v = d || { code: "", title: "", method: "" };
      form.innerHTML = `<form class="form machine-form card"><h3>${editing ? `Вид дефекта ${esc(v.code)}` : "Новый вид дефекта"}</h3>
        <div class="form-row two-col"><label>Код<input name="code" value="${esc(v.code)}" ${editing ? "readonly" : ""} required pattern="[A-Z][A-Z0-9_]{1,47}" placeholder="DELAMINATION"></label>
          <label>Название<input name="title" value="${esc(v.title)}" required placeholder="Отслоение покрытия"></label></div>
        <label>Метод оценки<textarea name="method" rows="2" required placeholder="Каким методом обнаруживается и где граница визуального метода">${esc(v.method === "—" ? "" : v.method)}</textarea></label>
        <div class="row"><button class="btn primary">${editing ? "Сохранить" : "Добавить вид"}</button><button type="button" class="btn" data-cancel>Отмена</button></div></form>`;
      const f = form.querySelector("form");
      f.querySelector("[data-cancel]").addEventListener("click", () => { form.innerHTML = ""; });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const spec = { code: f.code.value.trim().toUpperCase(), title: f.title.value, method: f.method.value };
        try {
          await api(editing ? `/api/admin/defects/${encodeURIComponent(spec.code)}` : "/api/admin/defects", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
          notify(`Вид дефекта ${spec.code} сохранён.`);
          renderAdminSection(body, "defects");
        } catch (error) { notify(error.message, true); }
      });
    };
    body.querySelector("#new-defect").addEventListener("click", () => open());
    body.querySelectorAll("[data-defect]").forEach((b) => b.addEventListener("click", () => open(rows.find((r) => r.code === b.dataset.defect))));
  },
  async equipment(body) {
    if (!can("equipment_manage")) return;
    const [catalog, rows] = await Promise.all([api("/api/catalog"), api(can("admin") ? "/api/admin/equipment" : "/api/equipment")]);
    const form = body.querySelector("#machine-form");
    const types = Object.entries(catalog.machine_types);
    const open = (machine = null) => {
      const editing = !!machine;
      if (!types.length) { notify("В справочнике нет типов станков. Тип добавляет администратор.", true); return; }
      const m = machine || { equipment_id: "", title: "", machine_type: types[0][0], inventory_no: "", status: "active" };
      let defects = machine ? [...machine.defect_types] : [...catalog.machine_types[m.machine_type].defect_types];
      form.innerHTML = `<form class="form machine-form card"><h3>${editing ? `Станок ${esc(m.equipment_id)}` : "Новый станок"}</h3>
        <div class="form-row"><label>Код станка<input name="equipment_id" value="${esc(m.equipment_id)}" ${editing ? "readonly" : ""} required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]{0,47}" placeholder="CNC-03"></label>
          <label>Название<input name="title" value="${esc(m.title)}" required placeholder="Фрезерный центр ЧПУ №3"></label>
          <label>Инвентарный номер<input name="inventory_no" value="${esc(m.inventory_no)}"></label></div>
        <div class="form-row two-col"><label>Тип станка (шаблон администратора)<select name="machine_type" ${editing ? "disabled" : ""}>${types.map(([k, t]) => `<option value="${k}" ${k === m.machine_type ? "selected" : ""}>${esc(t.title)}</option>`).join("")}</select></label>
          <label>Состояние<select name="status">${Object.entries(STATUS_EQ).map(([k, [t]]) => `<option value="${k}" ${k === m.status ? "selected" : ""}>${t}</option>`).join("")}</select></label></div>
        <div class="muted small" data-processing></div>
        <div class="field"><span>Виды дефектов станка (из дефектов типа)</span><div data-picker></div></div>
        <div data-params></div>
        <div class="row"><button class="btn primary">${editing ? "Сохранить" : "Зарегистрировать станок"}</button><button type="button" class="btn" data-cancel>Отмена</button></div></form>`;
      const f = form.querySelector("form");
      const typeOf = () => f.machine_type.value;
      const params = () => (editing ? m.parameters : catalog.machine_types[typeOf()].parameters);
      const drawType = () => {
        const t = catalog.machine_types[typeOf()];
        f.querySelector("[data-processing]").textContent = `Обработка: ${t.processing.join(", ")}.`;
        picker(f.querySelector("[data-picker]"), { options: defectOptions(catalog).filter((o) => t.defect_types.includes(o.value)), selected: defects, onChange: (v) => { defects = v; } });
        f.querySelector("[data-params]").innerHTML = `<h3>Допуски параметров режима</h3><table><tr><th>Параметр</th><th>Ед.</th><th class="num">Нижняя граница</th><th class="num">Верхняя граница</th></tr>${Object.entries(params()).map(([k, p]) => `<tr><td>${esc(p.title)}</td><td>${esc(p.unit)}</td><td class="num"><input data-low="${k}" type="number" step="any" value="${p.low}" style="width:90px"></td><td class="num"><input data-high="${k}" type="number" step="any" value="${p.high}" style="width:90px"></td></tr>`).join("")}</table>`;
      };
      f.machine_type.addEventListener("change", () => { defects = [...catalog.machine_types[typeOf()].defect_types]; drawType(); });
      drawType();
      f.querySelector("[data-cancel]").addEventListener("click", () => { form.innerHTML = ""; });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const parameters = Object.fromEntries(Object.keys(params()).map((k) => [k, { low: Number(f.querySelector(`[data-low="${k}"]`).value), high: Number(f.querySelector(`[data-high="${k}"]`).value) }]));
        const spec = { equipment_id: f.equipment_id.value, title: f.title.value, inventory_no: f.inventory_no.value, machine_type: typeOf(), status: f.status.value, defect_types: defects, parameters };
        try {
          await api(editing ? `/api/equipment/${encodeURIComponent(spec.equipment_id)}` : "/api/equipment", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
          notify(editing ? `Станок ${spec.equipment_id} сохранён.` : `Станок ${spec.equipment_id} зарегистрирован. Его можно назначить на операцию линии.`);
          renderAdminSection(body, "equipment");
        } catch (error) { notify(error.message, true); }
      });
      f.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    body.querySelector("#new-machine")?.addEventListener("click", () => open());
    body.querySelectorAll("[data-machine]").forEach((b) => b.addEventListener("click", () => open(rows.find((r) => r.equipment_id === b.dataset.machine))));
  },
  roles(body) {
    const d = S.adminRoles;
    const form = body.querySelector("#role-form");
    const open = (code = null) => {
      const editing = !!code;
      const r = code ? { code, ...d.roles[code] } : { code: "", title: "", screen: "controller", permissions: ["read"] };
      form.innerHTML = `<form class="form machine-form card"><h3>${editing ? `Роль «${esc(r.title)}»` : "Новая роль"}</h3>
        <div class="form-row"><label>Код роли<input name="code" value="${esc(r.code)}" ${editing ? "readonly" : ""} required pattern="[a-z][a-z0-9_]{1,31}" placeholder="auditor"></label>
          <label>Название<input name="title" value="${esc(r.title)}" required placeholder="Аудитор"></label>
          <label>Экран роли<select name="screen">${d.screens.map((s) => `<option value="${s.screen}" ${s.screen === r.screen ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select></label></div>
        <div class="field"><span>Права</span><div class="checks">${d.permissions.map((p) => `<label class="check"><input type="checkbox" name="perm" value="${p.permission}" ${r.permissions.includes(p.permission) ? "checked" : ""}> ${esc(p.title)}</label>`).join("")}</div></div>
        <div class="row"><button class="btn primary">${editing ? "Сохранить" : "Добавить роль"}</button><button type="button" class="btn" data-cancel>Отмена</button></div></form>`;
      const f = form.querySelector("form");
      f.querySelector("[data-cancel]").addEventListener("click", () => { form.innerHTML = ""; });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const spec = { code: f.code.value, title: f.title.value, screen: f.screen.value, permissions: [...f.querySelectorAll("[name=perm]:checked")].map((x) => x.value) };
        try {
          await api(editing ? `/api/admin/roles/${encodeURIComponent(spec.code)}` : "/api/admin/roles", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
          notify(`Роль «${spec.title}» сохранена.`);
          renderAdminSection(body, "roles");
        } catch (error) { notify(error.message, true); }
      });
    };
    body.querySelector("#new-role").addEventListener("click", () => open());
    body.querySelectorAll("[data-role]").forEach((b) => b.addEventListener("click", () => open(b.dataset.role)));
  },
  users(body) {
    const d = S.adminRoles;
    const form = body.querySelector("#user-form");
    const open = (userId = null) => {
      const editing = !!userId;
      const u = userId ? d.users.find((x) => x.user_id === userId) : { user_id: "", name: "", role: "controller", active: true };
      form.innerHTML = `<form class="form machine-form card"><h3>${editing ? `Пользователь ${esc(u.user_id)}` : "Новый пользователь"}</h3>
        <div class="form-row"><label>Логин<input name="user_id" value="${esc(u.user_id)}" ${editing ? "readonly" : ""} required pattern="[a-z][a-z0-9_.\\-]{1,63}" placeholder="ivanov"></label>
          <label>Имя<input name="name" value="${esc(u.name)}" required></label>
          <label>Роль<select name="role">${Object.entries(d.roles).map(([code, r]) => `<option value="${code}" ${code === u.role ? "selected" : ""}>${esc(r.title)}</option>`).join("")}</select></label></div>
        <div class="form-row two-col"><label>${editing ? "Новый пароль (не менее 8 символов, пусто — без изменений)" : "Пароль (не менее 8 символов)"}<input name="password" type="password" minlength="8" ${editing ? "" : "required"} autocomplete="new-password"></label>
          <label>Состояние<select name="active"><option value="true" ${u.active ? "selected" : ""}>Активен</option><option value="false" ${u.active ? "" : "selected"}>Отключён</option></select></label></div>
        <div class="row"><button class="btn primary">${editing ? "Сохранить" : "Добавить пользователя"}</button><button type="button" class="btn" data-cancel>Отмена</button></div></form>`;
      const f = form.querySelector("form");
      f.querySelector("[data-cancel]").addEventListener("click", () => { form.innerHTML = ""; });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const spec = { user_id: f.user_id.value, name: f.name.value, role: f.role.value, active: f.active.value === "true", password: f.password.value || null };
        try {
          await api(editing ? `/api/admin/users/${encodeURIComponent(spec.user_id)}` : "/api/admin/users", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
          notify(`Пользователь ${spec.user_id} сохранён.`);
          renderAdminSection(body, "users");
        } catch (error) { notify(error.message, true); }
      });
    };
    body.querySelector("#new-user").addEventListener("click", () => open());
    body.querySelectorAll("[data-user-edit]").forEach((b) => b.addEventListener("click", () => open(b.dataset.userEdit)));
  },
  db(body) {
    body.querySelectorAll("[data-table]").forEach((b) => b.addEventListener("click", async () => {
      const d = await api(`/api/admin/db?table=${encodeURIComponent(b.dataset.table)}`);
      const cols = d.rows.length ? Object.keys(d.rows[0]) : [];
      body.querySelector("#db-rows").innerHTML = `<h3>${esc(d.table)}: последние ${d.rows.length}</h3><div class="table-wrap"><table><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${d.rows.map((r) => `<tr>${cols.map((c) => `<td class="small mono">${esc(r[c])}</td>`).join("")}</tr>`).join("")}</table></div>`;
    }));
  },
  integrity(body) {
    body.querySelector("#verify").addEventListener("click", async () => {
      const r = await api("/api/integrity");
      body.querySelector("#verify-out").innerHTML = r.ok ? `${badge("Журнал цел", "ok")} Проверено записей: ${r.checked}, якорь №${r.anchor_seq}.` : `${badge("Обнаружено вмешательство", "bad")}<ul class="plain">${r.problems.map((p) => `<li>Запись №${p.seq} (${esc(p.kind || "")}): ${esc(p.problem)}</li>`).join("")}</ul>`;
    });
  },
  keys(body) {
    body.querySelector("#rotate").addEventListener("click", async () => { const r = await api("/api/keys/rotate", { method: "POST", body: JSON.stringify({ profile_id: body.querySelector("#profile").value }) }); notify(`Выпущен ключ ${r.key_id}.`); });
  },
  integration(body) {
    body.querySelector("#sync").addEventListener("click", async () => { try { notify(JSON.stringify(await api("/api/integration/sync", { method: "POST" }))); } catch (error) { notify(error.message, true); } });
  },
};

// --- запуск ---------------------------------------------------------------------------------------------------------------

(async function start() {
  console.info("Контракт событий, версии:", CONTRACT.versions.join(", "));
  if (session.token) {
    try { await enter(session.token); return; } catch { logout(); }
  }
  showLogin();
})();
