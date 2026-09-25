// Интерфейс Zero Defect: обзор производства → линия. Всё содержимое выводится на экране
// линии вокруг её графа: детали — в правой панели, этапы — в выпадающей сверху панели,
// время — на шкале внизу. Без сборки и внешних библиотек: в закрытом контуре нет CDN.
import { CONTRACT } from "./contract.js?v=0.3.4";
import { $, L, api, badge, can, esc, login, logout, mins, modal, notify, pct, poller, rub, session, store, time } from "./common.js?v=0.3.4";
import { openEditor } from "./editor.js?v=0.3.4";

const GUIDE = {
  controller: ["Откройте линию — слева очередь несоответствий, сначала критичные.", "«⟲ к моменту отказа» переносит линию в момент обнаружения: видно, что происходило на станках тогда.", "Откройте карточку и примите решение с обоснованием — оно ляжет отдельной записью, исходное сообщение не меняется."],
  master: ["Обзор производства показывает все линии; красная рамка — у линии есть несоответствия, ждущие решения.", "На линии под каждым станком — сколько изделий в каком статусе у него стоит.", "Слева — операции в работе, отклонения оборудования и пропуски сообщений."],
  technologist: ["Слева — где возникают дефекты по оценке системы.", "«Этапы процесса» в шапке раскрывает статистику по каждому этапу с фильтрами по изделию, смене и периоду.", "Оранжевая метка на станке — дефекты, возникшие на нём; нажмите, чтобы увидеть их."],
  manager: ["Обзор производства — все линии и изделия; «Новая линия» открывает блочный редактор.", "«Экономика» считает потери, затраты на годное и пропускную способность по вашим параметрам.", "«Пульт эмулятора» — отдельная страница: прогон по данным контракта и управление эмуляцией."],
  admin: ["«Администрирование» — оборудование, справочник дефектов, база данных, роли и права, целостность журнала.", "«Смотреть как» показывает экран любой роли — права при этом остаются вашими.", "«Добавить станок» в разделе оборудования открывает редактор линии с новым блоком."],
};

const S = {
  view: "plant", lineId: null, lines: [], plant: null, live: null, timeline: null, overview: null, at: null,
  itemType: "", viewRole: null, drawer: null, prevItems: new Map(), prevNodes: {}, geometry: null, zoom: null, playing: null,
  playSpeed: 300, pollers: [], itemFilters: { item_type: "", status: "", stage: "", q: "" }, stagesOpen: false,
};
const withAt = (path) => (S.at ? `${path}${path.includes("?") ? "&" : "?"}at=${encodeURIComponent(S.at)}` : path);

// --- вход ----------------------------------------------------------------------------------------

async function showLogin() {
  stopPollers();
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  const options = await api("/api/auth/options");
  $("role-cards").innerHTML = options.users.map((u) => `<button class="role-card" data-user="${esc(u.user_id)}"><b>${esc(L.role[u.role] || u.role)}</b><span>${esc(u.name)} · ${esc(u.user_id)}</span></button>`).join("") || `<p class="muted">Вход по имени пользователя и паролю.</p>`;
  $("role-cards").querySelectorAll("[data-user]").forEach((b) => b.addEventListener("click", async () => {
    const r = await api("/api/auth/demo-login", { method: "POST", body: JSON.stringify({ user_id: b.dataset.user }) });
    await enter(r.token);
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
  S.viewRole = session.me.role;
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("user-chip").innerHTML = `<b>${esc(L.role[session.me.role])}</b>`;
  $("user-chip").title = `${session.me.name} (${session.me.user_id})`;
  $("view-as-wrap").classList.toggle("hidden", !can("view_as"));
  $("view-as").innerHTML = Object.keys(GUIDE).map((r) => `<option value="${r}">${esc(L.role[r])}</option>`).join("");
  $("view-as").value = S.viewRole;
  $("view-as").onchange = () => { S.viewRole = $("view-as").value; renderRolePanel(); };
  S.lines = await api("/api/lines");
  const saved = store.get("zd-line");
  if (saved && S.lines.some((l) => l.line_id === saved) && store.get("zd-view") === "line") openLine(saved);
  else showPlant();
}
$("logout").addEventListener("click", () => { logout(); showLogin(); });
session.onExpired = () => showLogin();
$("open-guide").addEventListener("click", openGuide);

function stopPollers() { S.pollers.forEach((p) => p.stop()); S.pollers = []; }

function renderTop() {
  const crumbs = [`<a href="#" data-go="plant" class="${S.view === "plant" ? "current" : ""}">Производство</a>`];
  if (S.view === "line") crumbs.push(`<span class="muted">/</span><select id="line-select" aria-label="Линия">${S.lines.map((l) => `<option value="${esc(l.line_id)}" ${l.line_id === S.lineId ? "selected" : ""}>${esc(l.title)}</option>`).join("")}</select>`);
  $("crumbs").innerHTML = crumbs.join("");
  $("crumbs").querySelector('[data-go="plant"]').addEventListener("click", (e) => { e.preventDefault(); showPlant(); });
  $("line-select")?.addEventListener("change", (e) => openLine(e.target.value));
  // В шапке — только то, что относится к текущему экрану. Редактирование линии и
  // администрирование живут в панели роли и на обзоре производства: иначе у
  // администратора шапка разъезжалась на три строки.
  const actions = [];
  if (S.view === "line") {
    actions.push(`<button class="btn small ${S.stagesOpen ? "active" : ""}" data-act="stages">Этапы процесса ${S.stagesOpen ? "▲" : "▼"}</button>`, `<button class="btn small" data-act="items">Изделия</button>`, `<button class="btn small" data-act="economics">Экономика</button>`);
  } else {
    if (can("line_manage")) actions.push(`<button class="btn small" data-act="new-line">Новая линия</button>`);
    if (can("admin")) actions.push(`<button class="btn small" data-act="admin">Администрирование</button>`);
  }
  if (can("emulate")) actions.push(`<a class="btn small" href="/emulator" target="_blank" rel="noopener">Пульт эмулятора ↗</a>`);
  $("top-actions").innerHTML = actions.join("");
  $("top-actions").querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => ACTIONS[b.dataset.act]()));
}

const ACTIONS = {
  stages: () => toggleStages(),
  items: () => openItems(),
  economics: () => openEconomics(),
  "new-line": () => openEditor({ lines: S.lines, onSaved: afterSave }),
  "edit-line": async () => openEditor({ lines: S.lines, config: await api(`/api/lines/${S.lineId}`), onSaved: afterSave }),
  admin: () => openAdmin("equipment"),
};
async function afterSave(lineId) { S.lines = await api("/api/lines"); openLine(lineId); }

// --- обзор производства ------------------------------------------------------------------------------

function showPlant() {
  S.view = "plant";
  store.set("zd-view", "plant");
  stopPollers();
  stopPlaying();
  closeStages();
  $("line-view").classList.add("hidden");
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
    <div class="plant-head"><div><h1>Производство</h1><p class="muted">Все линии завода: где идут изделия и где несоответствия ждут решения. Выберите линию или изделие.</p></div>
      <div class="products"><span class="muted">Изделие:</span><button class="chip-btn ${!filter ? "active" : ""}" data-product="">все</button>${S.plant.products.map((p) => `<button class="chip-btn ${filter === p.item_type ? "active" : ""}" data-product="${esc(p.item_type)}">${esc(p.item_type)} <small>${esc(p.lines.join(", "))}</small></button>`).join("")}</div></div>
    <div class="plant-grid">${lines.map((l) => `
      <article class="line-card ${l.open_nonconformances ? "alarm" : ""}" data-line="${esc(l.line_id)}" tabindex="0">
        <div class="row spread"><b>${esc(l.title)}</b>${l.emulation.running || (l.emulation.run && !l.emulation.run.finished) ? badge("идёт", "info") : badge("простой", "plain")}</div>
        ${miniGraph(l)}
        <div class="kpis four"><div class="kpi"><b>${l.items}</b><span>изделий</span></div><div class="kpi"><b>${l.in_progress_runs}</b><span>операций в работе</span></div>
          <div class="kpi"><b class="${l.open_nonconformances ? "bad-text" : ""}">${l.open_nonconformances}</b><span>ждут решения</span></div><div class="kpi"><b>${pct(l.final_yield)}</b><span>годных на ОТК</span></div></div>
        <div class="muted small">${esc(l.item_types.join(" · "))} · последнее событие ${time(l.latest_event_at)}</div>
      </article>`).join("")}
      ${can("line_manage") ? `<button class="line-card add" id="plant-new"><b>＋ Новая линия</b><span class="muted">блочный редактор: этапы, станки, связи</span></button>` : ""}
    </div>`;
  $("plant-view").querySelectorAll("[data-product]").forEach((b) => b.addEventListener("click", () => { S.itemType = b.dataset.product; refreshPlant(); }));
  $("plant-view").querySelectorAll("[data-line]").forEach((c) => {
    c.addEventListener("click", () => openLine(c.dataset.line));
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") openLine(c.dataset.line); });
  });
  $("plant-new")?.addEventListener("click", ACTIONS["new-line"]);
}

// --- линия -------------------------------------------------------------------------------------------------

async function openLine(lineId) {
  S.view = "line";
  S.lineId = lineId;
  S.at = null;
  S.geometry = null;
  S.zoom = null;
  S.prevItems = new Map();
  S.prevNodes = {};
  stopPlaying();
  store.set("zd-line", lineId);
  store.set("zd-view", "line");
  stopPollers();
  $("plant-view").classList.add("hidden");
  $("line-view").classList.remove("hidden");
  const types = S.lines.find((l) => l.line_id === lineId)?.item_types || [];
  if (!types.includes(S.itemType)) S.itemType = "";
  const options = (all) => `<option value="">${all}</option>` + types.map((t) => `<option ${t === S.itemType ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("product-filter").innerHTML = options("все изделия");
  $("f-type").innerHTML = options("все типы изделий");
  renderTop();
  drawerDefault();
  await Promise.all([refreshLive(), refreshOverview(), refreshTimeline()]);
  S.pollers.push(poller(() => (S.at ? null : refreshLive()), 1500), poller(() => (S.at ? null : refreshOverview()), 5000), poller(() => (S.at ? null : refreshTimeline()), 10000));
}

$("product-filter").addEventListener("change", (e) => {
  S.itemType = e.target.value;
  $("f-type").value = S.itemType;
  if (S.live) renderGraph(S.live);
  if (S.stagesOpen) refreshStages();
});

async function refreshLive() {
  if (S.view !== "line") return;
  const live = await api(withAt(`/api/lines/${S.lineId}/live`));
  if (live.line_id !== S.lineId) return;
  S.live = live;
  $("line-title").textContent = live.title;
  renderMoment();
  renderGraph(live);
}

async function refreshAll() {
  await Promise.all([refreshLive(), refreshOverview(), S.stagesOpen ? refreshStages() : null]);
  if (S.drawer?.refresh) await S.drawer.refresh();
}

// --- граф ---------------------------------------------------------------------------------------------------

const NODE_W = 160, NODE_H = 70, SX = 1.25;
const STATUS = [["in_progress", "var(--accent)", "в работе"], ["conforming", "var(--ok)", "годно"], ["suspect", "var(--bad)", "на рассмотрении"], ["nonconforming", "var(--bad)", "несоответствие"], ["not_assessable", "var(--warn)", "оценка невозможна"]];

function geometry(raw) {
  const nodes = raw.map((n) => ({ ...n, x: n.x * SX, y: n.y }));
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const box = { x: Math.min(...xs) - NODE_W / 2 - 60, y: Math.min(...ys) - NODE_H / 2 - 40 };
  box.w = Math.max(...xs) + NODE_W / 2 + 40 - box.x;
  box.h = Math.max(...ys) + NODE_H / 2 + 60 - box.y;
  return { box, pos: Object.fromEntries(nodes.map((n) => [n.node_id, n])), key: `${S.lineId}:${raw.map((n) => `${n.node_id}@${n.x},${n.y}`).join(",")}` };
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

function setViewBox() { const z = S.zoom; $("graph").setAttribute("viewBox", `${z.x} ${z.y} ${z.w} ${z.h}`); }

function buildGraph(live) {
  const g = geometry(live.nodes);
  S.geometry = g;
  if (!S.zoom || S.zoom.key !== g.key) S.zoom = { ...g.box, key: g.key };
  const svg = $("graph");
  setViewBox();
  const edges = live.edges.map(([a, b]) => `<path class="edge" data-edge="${esc(a)}>${esc(b)}" d="${edgePath(g.pos[a], g.pos[b])}" marker-end="url(#arrow)"/>`).join("");
  const nodes = Object.values(g.pos).map((n) => {
    const icon = n.kind === "operation" ? (n.assembly ? "⧉" : "⚙") : "◉";
    const sub = n.kind === "operation" ? (n.equipment_id || n.station_id) : ({ incoming: "входной контроль", after_operation: "контроль", final: "финальный контроль" }[n.checkpoint_kind] || "контроль");
    const title = n.title.length > 19 ? `${n.title.slice(0, 18)}…` : n.title;
    return `<g class="node ${n.kind}" data-node="${esc(n.node_id)}" transform="translate(${n.x - NODE_W / 2},${n.y - NODE_H / 2})">
      <rect class="box" width="${NODE_W}" height="${NODE_H}" rx="12"/>
      <text class="icon" x="12" y="24">${icon}</text><text x="32" y="24">${esc(title)}</text>
      <text class="sub" x="12" y="44">${esc(sub)}</text><text class="sub" data-f="passed" x="12" y="60"></text>
      <g data-f="badges"></g><g data-f="counts" transform="translate(0,${NODE_H + 16})"></g></g>`;
  }).join("");
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--flow)"/></marker></defs>
    <g id="edges">${edges}</g><g id="nodes">${nodes}</g><g id="moving"></g><g id="fx"></g>`;
  svg.querySelectorAll(".node").forEach((el) => {
    el.addEventListener("click", (e) => {
      const badgeEl = e.target.closest("[data-badge]");
      if (badgeEl) openBadge(el.dataset.node, badgeEl.dataset.badge);
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
  tip.innerHTML = `<b>${esc(n.title)}</b><div>${n.kind === "operation" ? `станок ${esc(n.equipment_id || "—")} · норма ${mins(n.duration_s)}` : "контрольная точка"}</div>
    <div>прошло ${n.passed}${n.machine_state ? ` · станок: ${esc(n.machine_state)}` : ""}</div>
    ${n.open_detections ? `<div class="bad-text">обнаружено, ждёт решения: ${n.open_detections}</div>` : ""}${n.originated ? `<div class="origin-text">возникло здесь: ${n.originated}</div>` : ""}<div class="muted">нажмите — подробности этапа</div>`;
  tip.classList.remove("hidden");
}

function nodeCounts(live) {
  const counts = {};
  for (const item of live.items) {
    if (S.itemType && item.item_type !== S.itemType) continue;
    const node = item.node_id || "__entry";
    counts[node] ??= {};
    counts[node][item.status] = (counts[node][item.status] || 0) + 1;
  }
  return counts;
}

function renderGraph(live) {
  if (!S.geometry || S.geometry.key !== geometry(live.nodes).key) buildGraph(live);
  const svg = $("graph");
  const counts = nodeCounts(live);
  for (const raw of live.nodes) {
    const el = svg.querySelector(`.node[data-node="${CSS.escape(raw.node_id)}"]`);
    if (!el) continue;
    el.classList.remove("state-ok", "state-warning", "state-alarm");
    el.classList.add(`state-${raw.state}`);
    el.classList.toggle("selected", S.drawer?.node === raw.node_id);
    el.querySelector('[data-f="passed"]').textContent = `прошло ${raw.passed}`;
    const badges = [];
    let x = NODE_W - 6;
    const addBadge = (value, tone, kind, title) => {
      const w = 18 + String(value).length * 7;
      x -= w;
      badges.push(`<g class="badge-g" data-badge="${kind}"><title>${title}</title><rect class="count-bg ${tone}" x="${x}" y="-12" width="${w}" height="24" rx="12"/><text class="count light" x="${x + w / 2}" y="4" text-anchor="middle">${value}</text></g>`);
      x -= 4;
    };
    if (raw.open_detections) addBadge(raw.open_detections, "bad", "detected", "обнаружено здесь и ждёт решения — нажмите");
    if (raw.originated) addBadge(raw.originated, "warn", "originated", "возникло здесь по оценке системы — нажмите");
    el.querySelector('[data-f="badges"]').innerHTML = badges.join("");
    const c = counts[raw.node_id] || {};
    let cx = 4;
    el.querySelector('[data-f="counts"]').innerHTML = STATUS.filter(([s]) => c[s]).map(([s, color, label]) => {
      const text = String(c[s]);
      const pill = `<g class="count-pill"><title>${label}: ${text}</title><circle cx="${cx + 8}" cy="0" r="8" fill="${color}"/><text x="${cx + 20}" y="4" class="count">${text}</text></g>`;
      cx += 30 + text.length * 7;
      return pill;
    }).join("");
  }
  animateMoves(live);
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
    const path = direct ? edgePath(a, b) : `M${a.x},${a.y + NODE_H / 2 + 16} L${b.x},${b.y + NODE_H / 2 + 16}`;
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
    if (before && n.detections > before.detections) flash(g.pos[n.node_id]);
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

// --- шкала времени -------------------------------------------------------------------------------------------

async function refreshTimeline() {
  if (S.view !== "line") return;
  S.timeline = await api(`/api/lines/${S.lineId}/timeline`);
  renderTimeline();
}

function renderMoment() {
  $("moment-badge").innerHTML = S.at
    ? `<span class="moment">на ${time(S.at, true)} ${badge("прошлое", "warn")} <button class="btn small" id="to-live">к текущему ▶▶</button></span>`
    : `<span class="moment">${badge("сейчас", "ok")}</span>`;
  $("to-live")?.addEventListener("click", goLive);
}

function renderTimeline() {
  const t = S.timeline;
  if (!t?.start) { $("timeline").innerHTML = `<div class="muted">Событий на линии ещё нет.</div>`; return; }
  const start = new Date(t.start).getTime();
  const end = Math.max(new Date(t.end).getTime(), start + 60000);
  const cur = S.at ? new Date(S.at).getTime() : end;
  const pos = (ms) => `${Math.min(100, Math.max(0, ((ms - start) / (end - start)) * 100))}%`;
  const label = (m) => (m.kind === "detected" ? `обнаружено ${m.defect_type} · ${m.item_id}` : m.kind === "decision" ? `решение: ${L.action[m.action] || m.action}` : `отклонение станка ${m.equipment_id}`);
  $("timeline").innerHTML = `
    <div class="tl-head">
      <div class="row">
        <button class="btn small" data-tl="start" title="В начало">⏮</button>
        <button class="btn small" data-tl="back">−10 мин</button>
        <button class="btn small primary" data-tl="play">${S.playing ? "⏸ Пауза" : "▶ Воспроизвести"}</button>
        <button class="btn small" data-tl="fwd">+10 мин</button>
        <select data-tl="speed" title="Скорость воспроизведения">${[60, 300, 1800].map((v) => `<option value="${v}" ${S.playSpeed === v ? "selected" : ""}>×${v}</option>`).join("")}</select>
        <button class="btn small ${S.at ? "" : "active"}" data-tl="live">Сейчас</button>
      </div>
      <div class="muted small"><b>${time(S.at || t.end, true)}</b> · <span class="tl-key"><i class="m-detected"></i>обнаружение <i class="m-decision"></i>решение <i class="m-deviation"></i>отклонение станка</span></div>
    </div>
    <div class="tl-track">
      <div class="tl-markers">${t.markers.map((m, i) => `<button class="tl-mark m-${m.kind}" style="left:${pos(new Date(m.at).getTime())}" data-mark="${i}" title="${esc(`${time(m.at, true)} · ${label(m)}`)}"></button>`).join("")}</div>
      <input type="range" id="tl-range" min="${start}" max="${end}" step="1000" value="${cur}" aria-label="Момент времени">
    </div>
    <div class="tl-axis"><span>${time(t.start)}</span><span>${time(new Date((start + end) / 2).toISOString())}</span><span>${time(t.end)}</span></div>`;
  const box = $("timeline");
  box.querySelector("#tl-range").addEventListener("input", (e) => scheduleAt(Number(e.target.value) >= end ? null : new Date(Number(e.target.value)).toISOString()));
  box.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", () => {
    const m = t.markers[Number(b.dataset.mark)];
    jumpTo(m.at, m.kind === "detected" ? { nc: m.nc_id } : { node: m.node_id });
  }));
  box.querySelector('[data-tl="start"]').addEventListener("click", () => jumpTo(t.start));
  box.querySelector('[data-tl="back"]').addEventListener("click", () => jumpTo(new Date(cur - 600000).toISOString()));
  box.querySelector('[data-tl="fwd"]').addEventListener("click", () => (cur + 600000 >= end ? goLive() : jumpTo(new Date(cur + 600000).toISOString())));
  box.querySelector('[data-tl="live"]').addEventListener("click", goLive);
  box.querySelector('[data-tl="speed"]').addEventListener("change", (e) => { S.playSpeed = Number(e.target.value); });
  box.querySelector('[data-tl="play"]').addEventListener("click", () => (S.playing ? stopPlaying() : startPlaying()));
}

let atTimer = null;
function scheduleAt(iso) {
  S.at = iso;
  renderMoment();
  clearTimeout(atTimer);
  atTimer = setTimeout(refreshAll, 250);
}

async function jumpTo(iso, focus = {}) {
  S.at = new Date(new Date(iso).getTime() + 1000).toISOString();
  renderTimeline();
  await refreshAll();
  if (focus.nc) await openNc(focus.nc);
  else if (focus.node) await openNode(focus.node);
}

function goLive() {
  stopPlaying();
  S.at = null;
  renderTimeline();
  refreshAll();
}

function startPlaying() {
  const t = S.timeline;
  if (!t) return;
  if (!S.at) S.at = t.start;
  let busy = false;
  S.playing = setInterval(async () => {
    if (busy) return;
    const next = new Date(S.at).getTime() + S.playSpeed * 1000;
    if (next >= new Date(t.end).getTime()) { goLive(); return; }
    S.at = new Date(next).toISOString();
    const range = $("tl-range");
    if (range) range.value = next;
    busy = true;
    try { await refreshLive(); } finally { busy = false; }
  }, 1000);
  renderTimeline();
}
function stopPlaying() {
  if (S.playing) clearInterval(S.playing);
  S.playing = null;
  if (S.view === "line" && S.timeline) renderTimeline();
}

// --- этапы: выпадающая сверху панель, скрыта до нажатия ---------------------------------------------------------

function toggleStages() { S.stagesOpen ? closeStages() : openStages(); }
function openStages() {
  S.stagesOpen = true;
  $("stages-drop").classList.add("open");
  $("stages-drop").setAttribute("aria-hidden", "false");
  renderTop();
  refreshStages();
}
function closeStages() {
  S.stagesOpen = false;
  $("stages-drop").classList.remove("open");
  $("stages-drop").setAttribute("aria-hidden", "true");
  if (S.view === "line") renderTop();
}
$("stages-close").addEventListener("click", closeStages);
["f-type", "f-shift", "f-since", "f-until"].forEach((id) => $(id).addEventListener("change", refreshStages));

function stageQuery() {
  const p = new URLSearchParams();
  const type = $("f-type").value, shift = $("f-shift").value, since = $("f-since").value, until = $("f-until").value;
  if (type) p.set("item_type", type);
  if (shift) p.set("shift", shift);
  if (since) p.set("since", new Date(since).toISOString());
  if (until) p.set("until", new Date(until).toISOString());
  if (S.at) p.set("at", S.at);
  return p.toString();
}

async function refreshStages() {
  const rows = await api(`/api/lines/${S.lineId}/stages?${stageQuery()}`);
  $("stages").innerHTML = `<table><tr><th>этап</th><th class="num">изделий</th><th class="num">выполнений / проверок</th><th class="num">в работе</th><th class="num">доработок</th><th class="num">длительность</th><th class="num">откл. станка</th><th class="num">обнаружено</th><th class="num">возникло</th><th class="num">без признаков</th></tr>
    ${rows.map((r) => { const op = r.kind === "operation"; return `<tr class="click" data-node="${esc(r.node_id)}"><td>${op ? "⚙" : "◉"} ${esc(r.title)}</td><td class="num">${r.items}</td><td class="num">${op ? r.runs : r.checks}</td><td class="num">${op ? r.in_progress : "—"}</td><td class="num">${op ? r.rework_runs : "—"}</td><td class="num">${op ? mins(r.median_reported_s ?? r.median_active_s) : "—"}</td><td class="num">${op ? r.deviations : "—"}</td><td class="num">${op ? "—" : r.first_detections ? badge(r.first_detections, "bad") : 0}</td><td class="num">${r.defects_originated ? badge(r.defects_originated, "warn") : 0}</td><td class="num">${op ? "—" : pct(r.pass_rate)}</td></tr>`; }).join("")}</table>`;
  $("stages").querySelectorAll("tr[data-node]").forEach((tr) => tr.addEventListener("click", () => openNode(tr.dataset.node)));
}

// --- панель роли ----------------------------------------------------------------------------------------------------

async function refreshOverview() {
  if (S.view !== "line") return;
  S.overview = await api(withAt(`/api/lines/${S.lineId}/overview`));
  renderRolePanel();
}

const kpi = (value, label, tone = "") => `<div class="kpi"><b class="${tone}">${esc(value)}</b><span>${esc(label)}</span></div>`;
const nodeTitle = (id) => S.geometry?.pos[id]?.title || id;

function queueList(queue) {
  if (!queue.length) return `<div class="empty">Очередь пуста.</div>`;
  return `<ul class="list">${queue.map((n) => `<li><div class="row spread"><a href="#" data-nc="${esc(n.nc_id)}"><b>${esc(n.defect_type)}</b></a>${badge(n.severity || "—", n.severity === "critical" ? "bad" : n.severity === "major" ? "warn" : "plain")}</div>
    <div class="muted small mono">${esc(n.item_id)} · ${time(n.first_detected_at, true)}</div>
    <button class="btn small" style="margin-top:6px" data-rollback="${esc(n.nc_id)}" data-at="${esc(n.first_detected_at)}" title="Показать линию в момент обнаружения">⟲ к моменту отказа</button></li>`).join("")}</ul>`;
}

function renderRolePanel() {
  const o = S.overview;
  if (!o) return;
  const k = o.kpi;
  const role = S.viewRole;
  let html;
  if (role === "controller") {
    html = `<div class="card-head"><h2>Очередь на решение</h2>${badge(k.open_nonconformances, k.open_nonconformances ? "bad" : "ok")}</div>${queueList(o.queue)}`;
  } else if (role === "master") {
    html = `<div class="card-head"><h2>Сейчас на линии</h2></div><div class="kpis">${kpi(k.in_progress_runs, "операций в работе")}${kpi(k.late_events, "событий опоздало")}</div>
      <h3>В работе</h3>${o.in_progress.length ? `<ul class="list">${o.in_progress.slice(0, 12).map((r) => `<li class="click" data-item="${esc(r.item_id)}"><b class="mono small">${esc(r.item_id)}</b><div class="muted small">${esc(nodeTitle(r.node_id))} · ${esc(r.operator_id || "")}</div></li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <h3>Отклонения оборудования</h3>${o.deviations.length ? `<ul class="list">${o.deviations.map((d) => `<li class="click" data-node="${esc(d.node_id)}"><b>${esc(d.equipment_id)}</b> ${badge(d.state, "warn")}<div class="muted small">${time(d.at, true)}</div></li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <h3>Пропуски сообщений</h3>${Object.keys(o.source_gaps).length ? `<ul class="plain">${Object.entries(o.source_gaps).map(([s, g]) => `<li class="mono small">${esc(s)}: №${g.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}</li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}`;
  } else if (role === "technologist") {
    const entries = Object.entries(o.origins_by_node);
    const max = Math.max(1, ...entries.map(([, v]) => v));
    html = `<div class="card-head"><h2>Где возникают дефекты</h2></div>${entries.length ? `<div class="bars">${entries.map(([node, v]) => `<div class="bar-row" data-origin-node="${esc(node)}"><small>${esc(nodeTitle(node))}</small><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><b>${v}</b></div>`).join("")}</div>` : `<div class="empty">Нет.</div>`}
      <h3>Гипотезы, не подтверждённые людьми</h3>${Object.keys(o.hypotheses).length ? `<ul class="plain">${Object.entries(o.hypotheses).map(([c, v]) => `<li>${esc(L.cause[c] || c)}: ${v}</li>`).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <button class="btn small" data-ocel style="margin-top:10px">Выгрузка OCEL 2.0</button>`;
  } else if (role === "manager") {
    html = `<div class="card-head"><h2>Показатели линии</h2></div><div class="kpis">${kpi(k.items, "изделий")}${kpi(k.conforming, "годно")}${kpi(k.nonconforming, "с несоответствием", k.nonconforming ? "bad-text" : "")}${kpi(k.rework_runs, "доработок")}${kpi(k.open_nonconformances, "ждут решения")}${kpi(k.confirmed, "подтверждено")}</div>
      <div class="stack" style="margin-top:12px"><button class="btn" data-open="economics">Экономика линии</button>${can("line_manage") ? `<button class="btn" data-open="edit-line">Изменить линию</button>` : ""}${can("emulate") ? `<a class="btn" href="/emulator" target="_blank" rel="noopener">Пульт эмулятора ↗</a>` : ""}</div>`;
  } else {
    html = `<div class="card-head"><h2>Администрирование</h2></div><div class="stack">${ADMIN_SECTIONS.map(([s, t]) => `<button class="btn" data-admin="${s}">${t}</button>`).join("")}<button class="btn" data-open="edit-line">Изменить линию</button></div>
      <div class="kpis" style="margin-top:12px">${kpi(k.items, "изделий")}${kpi(k.open_nonconformances, "ждут решения")}</div>`;
  }
  const panel = $("role-panel");
  panel.innerHTML = html;
  bindCommon(panel);
  panel.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => ({ economics: openEconomics, "edit-line": ACTIONS["edit-line"] })[b.dataset.open]()));
  panel.querySelectorAll("[data-admin]").forEach((b) => b.addEventListener("click", () => openAdmin(b.dataset.admin)));
  panel.querySelectorAll("[data-origin-node]").forEach((b) => b.addEventListener("click", () => openBadge(b.dataset.originNode, "originated")));
  panel.querySelector("[data-ocel]")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/export/ocel");
      Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" })), download: "zero-defect.ocel.json" }).click();
    } catch (error) { notify(error.message, true); }
  });
}

function bindCommon(root) {
  root.querySelectorAll("[data-nc]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); openNc(el.dataset.nc); }));
  root.querySelectorAll("[data-item]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); openItem(el.dataset.item); }));
  root.querySelectorAll("[data-node]").forEach((el) => el.addEventListener("click", () => openNode(el.dataset.node)));
  root.querySelectorAll("[data-rollback]").forEach((el) => el.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); jumpTo(el.dataset.at, { nc: el.dataset.rollback }); }));
}

// --- правая панель деталей ---------------------------------------------------------------------------------------------

// Детали выезжают справа поверх графа и прячутся обратно: граф всегда занимает всю ширину,
// а подробности появляются только по нажатию — как и таблица этапов сверху.
function drawer(html, state = {}) {
  S.drawer = state;
  const box = $("drawer");
  box.innerHTML = `<button class="btn small drawer-close" data-drawer-close title="Закрыть (Esc)">✕</button>${html}`;
  box.querySelector("[data-drawer-close]").addEventListener("click", drawerDefault);
  bindCommon(box);
  box.scrollTop = 0;
  box.parentElement.classList.add("open");
  if (S.live) renderGraph(S.live);
}

function drawerDefault() {
  S.drawer = null;
  $("drawer").parentElement.classList.remove("open");
  if (S.live) renderGraph(S.live);
}
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.drawer && !document.querySelector(".modal-backdrop")) drawerDefault(); });

const ncRow = (n) => `<li><div class="row spread"><a href="#" data-nc="${esc(n.nc_id)}"><b>${esc(n.defect_type)}</b></a><span class="row">${badge(...(L.nc[n.status] || [n.status]))}<button class="btn small" data-rollback="${esc(n.nc_id)}" data-at="${esc(n.first_detected_at)}" title="Линия в момент обнаружения">⟲</button></span></div><div class="muted small mono">${esc(n.item_id)} · ${time(n.first_detected_at, true)}</div></li>`;

async function openNode(nodeId) {
  const load = async () => {
    const d = await api(`/api/lines/${S.lineId}/stages/${encodeURIComponent(nodeId)}?${stageQuery()}`);
    const s = d.stats;
    const op = s.kind === "operation";
    const stats = op
      ? `${kpi(s.runs, "выполнений")}${kpi(s.in_progress, "в работе")}${kpi(mins(s.median_reported_s ?? s.median_active_s), "длительность, медиана")}${kpi(s.rework_runs, "доработок")}${kpi(s.deviations, "отклонений станка")}${kpi(s.defects_originated, "дефектов возникло", s.defects_originated ? "origin-text" : "")}`
      : `${kpi(s.checks, "проверок")}${kpi(pct(s.pass_rate), "без признаков")}${kpi(s.found, "с признаками", s.found ? "bad-text" : "")}${kpi(s.not_assessable, "оценка невозможна")}${kpi(s.first_detections, "впервые обнаружено")}${kpi(s.open_detections, "ждут решения")}`;
    const operators = op && Object.keys(s.by_operator).length ? `<h3>Операторы</h3><table><tr><th>оператор</th><th class="num">выполнений</th><th class="num">подтв. ошибок</th></tr>${Object.entries(s.by_operator).map(([w, r]) => `<tr><td class="mono">${esc(w)}</td><td class="num">${r.runs}</td><td class="num">${r.confirmed_errors}</td></tr>`).join("")}</table>` : "";
    drawer(`<div class="card-head"><h2>${op ? "⚙" : "◉"} ${esc(s.title)}</h2>${badge(op ? "станок" : "контроль", "plain")}</div>
      <div class="muted small mono" style="margin-bottom:10px">${esc(s.station_id)}${s.equipment_id ? ` · ${esc(s.equipment_id)}` : ""}${s.checkpoint_id ? ` · ${esc(s.checkpoint_id)}` : ""}</div>
      <div class="kpis">${stats}</div>
      ${d.detected.length ? `<h3 class="bad-text">Обнаружено здесь</h3><ul class="list">${d.detected.slice(0, 8).map(ncRow).join("")}</ul>` : ""}
      ${d.originated.length ? `<h3 class="origin-text">Возникло здесь (оценка системы)</h3><ul class="list">${d.originated.slice(0, 8).map(ncRow).join("")}</ul>` : ""}
      ${operators}
      ${d.items.length ? `<h3>Изделия на этапе</h3><table>${d.items.slice(0, 20).map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono small">${esc(r.item_id)}</td><td>${op ? esc(r.outcome) : badge(...(L.result[r.outcome] || [r.outcome]))}</td><td class="muted small">${time(r.at, true)}</td></tr>`).join("")}</table>` : ""}`,
    { node: nodeId, refresh: load });
  };
  await load();
}

async function openBadge(nodeId, kind) {
  const load = async () => {
    const d = await api(`/api/lines/${S.lineId}/stages/${encodeURIComponent(nodeId)}?${stageQuery()}`);
    const list = kind === "detected" ? d.detected.filter((n) => ["reported", "under_review", "recheck_requested"].includes(n.status)) : d.originated;
    drawer(`<div class="card-head"><h2>${esc(d.stats.title)}</h2>${badge(kind === "detected" ? "обнаружено здесь" : "возникло здесь", kind === "detected" ? "bad" : "warn")}</div>
      <p class="muted small">${kind === "detected" ? "Несоответствия, впервые обнаруженные на этом контроле и ждущие решения контролёра." : "Несоответствия, которые по разбору системы возникли на этом этапе: он единственный или один из возможных между последним чистым контролем и обнаружением."}</p>
      ${list.length ? `<ul class="list">${list.map(ncRow).join("")}</ul>` : `<div class="empty">Нет.</div>`}
      <p class="muted small">⟲ переносит линию в момент обнаружения: видно, что было на станках тогда.</p>`, { node: nodeId, refresh: load });
  };
  await load();
}

async function openItem(itemId) {
  const load = async () => {
    const path = await api(withAt(`/api/items/${encodeURIComponent(itemId)}/path`));
    const route = path.route.map((n, i) => `<div class="hop st-${n.status}"><div class="node-chip" data-node="${esc(n.node_id)}" title="${esc(L.pathStatus[n.status])}"><span class="muted">${String.fromCharCode(65 + i)}</span><b>${esc(n.title)}</b><span class="muted small">${esc(L.pathStatus[n.status])}</span></div><div class="arrow"></div></div>`).join("");
    const steps = path.route.map((n, i) => {
      const runs = n.runs.map((r) => `<div class="small">${r.rework ? badge("доработка", "info") + " " : ""}оператор <span class="mono">${esc(r.operator_id || "—")}</span> · ${time(r.started_at, true)} · ${mins(r.active_s)}${r.deviations ? " " + badge("отклонение станка", "bad") : ""}</div>`).join("");
      const checks = n.checks.map((c) => `<div class="small">${badge(...(L.result[c.result] || [c.result]))} ${time(c.at, true)} · уверенность ${c.confidence ?? "—"}${c.reliable ? "" : " " + badge(c.note || "недостоверно", "warn")}</div>`).join("");
      const ncs = n.nonconformances.map((x) => `<div class="small"><a href="#" data-nc="${esc(x.nc_id)}">${esc(x.defect_type)}</a> ${badge({ detected: "обнаружен здесь", origin: "возник здесь", possible_origin: "мог возникнуть здесь" }[x.role], x.role === "detected" ? "bad" : "warn")}</div>`).join("");
      return `<div class="st-${n.status}"><b>${String.fromCharCode(65 + i)} · ${esc(n.title)}</b> <span class="muted small">${esc(n.equipment_id || "")}</span>${runs}${checks}${ncs}${!runs && !checks ? `<div class="muted small">${esc(L.pathStatus[n.status])}</div>` : ""}</div>`;
    }).join("");
    drawer(`<div class="card-head"><h2 class="mono">${esc(path.item_id)}</h2>${badge(...(L.status[path.status] || [path.status]))}</div>
      <div class="muted small">${esc(path.item_type_id)}${path.parent_id ? ` · в составе <a href="#" data-item="${esc(path.parent_id)}">${esc(path.parent_id)}</a>` : ""}${path.components.length ? ` · компоненты ${path.components.map((c) => `<a href="#" data-item="${esc(c)}">${esc(c)}</a>`).join(", ")}` : ""}</div>
      <div class="route">${route}</div>
      <div class="legend"><span><i style="background:var(--ok)"></i>пройден</span><span><i style="background:var(--origin)"></i>возник</span><span><i style="background:var(--bad)"></i>обнаружен</span><span><i style="background:var(--warn)"></i>возможно / нет оценки</span></div>
      <h3>По этапам</h3><div class="steps">${steps}</div>`, { item: itemId, refresh: load });
  };
  try { await load(); } catch (error) { notify(error.message, true); }
}

async function openNc(ncId) {
  const card = await api(`/api/nonconformances/${encodeURIComponent(ncId)}`);
  const a = card.assessment || {};
  const causes = ["incoming_defect", "equipment_problem", "operator_error", "process_issue", "handling_damage", "other"];
  drawer(`<div class="card-head"><h2>${esc(card.defect_type)}</h2>${badge(...(L.nc[card.status] || [card.status]))}</div>
    <div class="muted small"><a href="#" data-item="${esc(card.item_id)}" class="mono">${esc(card.item_id)}</a> · ${esc(card.nc_id)} · зона ${esc(card.area)} · ${esc(card.severity)}</div>
    <button class="btn small" style="margin-top:8px" data-rollback="${esc(card.nc_id)}" data-at="${esc(card.first_detected_at)}">⟲ линия в момент обнаружения</button>
    <div class="layer source"><div class="layer-title">1 · сообщения анализатора, как пришли</div>
      ${card.signals_detail.map((s) => `<div class="small">${badge(...(L.result[s.inspection_result] || [s.inspection_result]))} ${time(s.occurred_at, true)} · ${esc(s.checkpoint_id)} · уверенность ${s.confidence ?? "—"}${s.reliable ? "" : " " + badge(s.reliability_note || "недостоверно", "warn")}<details><summary class="muted small">исходное сообщение</summary><pre class="raw">${esc(JSON.stringify(s.raw, null, 2))}</pre></details></div>`).join("")}</div>
    <div class="layer system"><div class="layer-title">2 · разбор — оценка системы, не решение</div>
      <div class="row"><b>${esc(L.stage[a.stage] || a.stage)}</b>${badge(...(L.conf[a.stage_confidence] || [a.stage_confidence]))}</div>
      <h3>Основания</h3><ul class="plain small">${(a.evidence || []).map((e) => `<li>${esc(e.text)}</li>`).join("") || "<li>нет</li>"}</ul>
      <h3>Что ещё могло привести к тому же</h3><ul class="plain small">${(a.alternatives || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>нет</li>"}</ul>
      <h3>Каких сведений не хватает</h3><ul class="plain small">${(a.missing || []).map((t) => `<li>${esc(t)}</li>`).join("") || "<li>не отмечено</li>"}</ul></div>
    <div class="layer human"><div class="layer-title">3 · решения людей — отдельные записи журнала</div>
      ${card.decisions.length ? `<ul class="plain small">${card.decisions.map((d) => `<li>${time(d.decided_at, true)} · <span class="mono">${esc(d.author_id)}</span> — ${esc(L.action[d.action] || d.action)}${d.cause_category ? `: ${esc(L.cause[d.cause_category])}` : ""}. <span class="muted">${esc(d.reason)}</span></li>`).join("")}</ul>` : `<div class="empty">Решений нет.</div>`}
      ${S.at ? `<p class="muted small">Линия показана в прошлом. Решения принимаются только в текущем моменте — нажмите «к текущему».</p>` : card.allowed_actions.length ? `<form id="decision" class="form" style="margin-top:8px"><select name="action">${card.allowed_actions.map((x) => `<option value="${x}">${esc(L.action[x])}</option>`).join("")}</select><select name="cause" class="hidden">${causes.map((c) => `<option value="${c}">${esc(L.cause[c])}</option>`).join("")}</select><textarea name="reason" rows="2" placeholder="Обоснование — обязательно" required></textarea><button class="btn primary">Записать решение</button></form>` : `<p class="muted small">У роли «${esc(L.role[session.me.role])}» нет решений по этой карточке.</p>`}</div>`, { nc: ncId });
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
  const load = async () => {
    const f = { ...S.itemFilters, item_type: S.itemFilters.item_type || S.itemType };
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    if (S.at) p.set("at", S.at);
    const data = await api(`/api/lines/${S.lineId}/items?${p}&limit=150`);
    const opt = (values, current, labels = {}) => `<option value="">все</option>` + values.map((v) => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
    drawer(`<div class="card-head"><h2>Изделия линии</h2><span class="muted small">найдено ${data.total}${data.total > data.rows.length ? `, показано ${data.rows.length}` : ""}</span></div>
      <div class="table-wrap"><table><tr><th>изделие</th><th>тип</th><th>статус</th><th>этап</th></tr>
        <tr class="filters"><th><input data-f="q" value="${esc(f.q)}" placeholder="номер"></th><th><select data-f="item_type">${opt(data.facets.item_type, f.item_type)}</select></th>
          <th><select data-f="status">${opt(data.facets.status, f.status, Object.fromEntries(Object.entries(L.status).map(([k, v]) => [k, v[0]])))}</select></th><th><select data-f="stage">${opt(data.facets.stage, f.stage, data.stage_titles)}</select></th></tr>
        ${data.rows.map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono small">${esc(r.item_id)}</td><td class="mono small">${esc(r.item_type_id)}</td><td>${badge(...(L.status[r.status] || [r.status]))}</td><td class="small">${esc(data.stage_titles[r.stage] || "—")}</td></tr>`).join("")}</table></div>`, { items: true, refresh: load });
    $("drawer").querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => { S.itemFilters[el.dataset.f] = el.value; load(); }));
  };
  await load();
}

async function openEconomics() {
  const e = await api(`/api/lines/${S.lineId}/economics`);
  const p = e.parameters, m = e.measured, c = e.computed;
  const labels = { item_value_rub: "стоимость годного, ₽", rework_cost_rub: "стоимость доработки, ₽", scrap_cost_rub: "потери на браке, ₽", hour_cost_rub: "час участка, ₽", shift_hours: "смена, ч" };
  const losses = Object.entries(c.losses_by_node_rub);
  const max = Math.max(1, ...losses.map(([, v]) => v));
  drawer(`<div class="card-head"><h2>Экономика линии</h2></div><p class="muted small">${esc(e.origin)}.</p>
    <div class="kpis">${kpi(rub(c.output_value_rub), "выпуск годных")}${kpi(rub(c.rework_cost_rub + c.scrap_cost_rub), "потери от брака")}${kpi(pct(c.losses_share), "потери к выпуску")}${kpi(rub(c.cost_per_good_item_rub), "затраты на годное")}${kpi(c.capacity_per_shift ?? "—", "изделий за смену")}${kpi(pct(c.final_yield), "годных на ОТК")}</div>
    <h3>Где теряем деньги</h3>${losses.length ? `<div class="bars">${losses.map(([node, v]) => `<div class="bar-row" data-node="${esc(node)}"><small>${esc(nodeTitle(node))}</small><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><b class="small">${Math.round(v / 1000)}к</b></div>`).join("")}</div>` : `<div class="empty">Нет.</div>`}
    <h3>Измерено</h3><dl class="kv"><dt>изделий-продуктов</dt><dd>${m.products}</dd><dt>годных</dt><dd>${m.finished_conforming}</dd><dt>доработок</dt><dd>${m.rework_runs}</dd><dt>часов на участках</dt><dd>${m.station_hours}</dd><dt>узкое место</dt><dd>${esc(nodeTitle(m.bottleneck_node))}</dd></dl>
    <h3>Параметры</h3>${can("line_manage") ? `<form id="econ" class="form">${Object.entries(labels).map(([k, l]) => `<label>${l}<input name="${k}" type="number" step="any" min="0" value="${p[k]}"></label>`).join("")}<button class="btn primary">Сохранить</button></form>` : `<dl class="kv">${Object.entries(labels).map(([k, l]) => `<dt>${l}</dt><dd>${p[k]}</dd>`).join("")}</dl>`}<p class="muted small">${esc(p.note)}</p>`, { economics: true });
  $("econ")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try { await api(`/api/lines/${S.lineId}/economics`, { method: "PUT", body: JSON.stringify(Object.fromEntries(new FormData(ev.target).entries())) }); notify("Параметры сохранены."); openEconomics(); } catch (error) { notify(error.message, true); }
  });
}

// --- инструкция: как в cosmo-net — кнопка рядом с названием, окно поверх экрана, Esc закрывает ---------------------

function openGuide() {
  const role = S.viewRole || session.me?.role || "controller";
  modal(`<div class="modal-head"><h2>Как пользоваться системой</h2><button class="btn small" data-close aria-label="Закрыть инструкцию">✕</button></div>
    <div class="guide-body">
      <section><h3>Для роли «${esc(L.role[role])}»</h3><ol>${GUIDE[role].map((g) => `<li>${esc(g)}</li>`).join("")}</ol></section>
      <section><h3>Производство</h3><p>Первый экран — все линии завода. У каждой — схема, число изделий, операций в работе и несоответствий, ждущих решения; красная рамка — есть что решать. Сверху выбирается изделие: останутся линии, которые его выпускают. Нажмите линию, чтобы перейти к ней.</p></section>
      <section><h3>Линия</h3><p>Всё остальное — на экране линии. В центре граф: этапы процесса и связи между ними. Колёсико мыши приближает, перетаскивание фона двигает, кнопка <b>⤢</b> вписывает граф целиком.</p>
        <ul><li>Под каждым станком — кружки с числами: сколько изделий у него сейчас <span class="dot" style="background:var(--accent)"></span> в работе, <span class="dot" style="background:var(--ok)"></span> годны, <span class="dot" style="background:var(--bad)"></span> с несоответствием, <span class="dot" style="background:var(--warn)"></span> без оценки.</li>
        <li>Когда изделие переходит к следующему этапу, по стрелке пролетает шарик его цвета.</li>
        <li><b class="bad-text">Красная метка</b> на станке — несоответствия, обнаруженные здесь и ждущие решения; <b class="origin-text">оранжевая</b> — возникшие здесь по разбору системы. Нажмите метку — справа появится их список.</li>
        <li>Нажмите сам станок — справа его статистика, операторы и изделия, прошедшие через него.</li></ul></section>
      <section><h3>Шкала времени</h3><p>Внизу — шкала от первого события линии до сейчас. Отметки: <span class="dot" style="background:var(--bad)"></span> обнаружение, <span class="dot" style="background:var(--ok)"></span> решение, <span class="dot" style="background:var(--warn)"></span> отклонение станка. Перетащите ползунок — граф, очередь и детали покажут состояние на этот момент. <b>▶ Воспроизвести</b> прокручивает историю, <b>Сейчас</b> возвращает к текущему.</p>
        <p class="note">В прошлом решения не принимаются: карточку можно прочитать, решение — только в текущем моменте.</p></section>
      <section><h3>Очередь и откат к моменту отказа</h3><p>У контролёра слева — очередь несоответствий. <b>⟲ к моменту отказа</b> переносит всю линию в момент обнаружения и открывает карточку: видно, какие изделия где стояли и что показывали станки тогда.</p></section>
      <section><h3>Карточка несоответствия</h3><p>Три слоя, и они не смешиваются: исходные сообщения анализатора как пришли; разбор системы — этап, основания, альтернативы и недостающие сведения; решения людей отдельными записями журнала с автором и обоснованием.</p></section>
      <section><h3>Этапы процесса и изделия</h3><p><b>Этапы процесса</b> в шапке раскрывает сверху таблицу по всем этапам с фильтрами по изделию, смене и периоду; скрывается той же кнопкой. <b>Изделия</b> — таблица с фильтрами по столбцам; изделие открывается маршрутом A → B → C.</p></section>
      <section><h3>Новая линия и станки</h3><p><b>Новая линия</b> открывает блочный редактор: перетащите блоки «Контроль» и «Операция» на холст, соедините их от правого кружка к левому, задайте параметры справа. Два входа в одну операцию — сборка. Администратор добавляет станок в разделе «Оборудование».</p></section>
      <section><h3>Пульт эмулятора</h3><p>Эмуляция вынесена за пределы системы — в отдельный пульт (кнопка в шапке). Там линию запускают и останавливают, вносят дефект в выбранный этап и проигрывают прогон по данным контракта: сколько изделий, за сколько секунд, какие дефекты куда. Система видит эмулятор как обычные источники событий.</p></section>
      <section><h3>Коротко о частом</h3><dl><dt>Почему изделие «на рассмотрении», а не «брак»?</dt><dd>Сигнал анализатора — ещё не несоответствие: его подтверждает контролёр.</dd><dt>Почему система не называет виновного?</dt><dd>Оператор — участник операции. Ошибка оператора бывает только подтверждённой человеком.</dd></dl></section>
    </div>`);
}

// --- администрирование ---------------------------------------------------------------------------------------------------

const ADMIN_SECTIONS = [["equipment", "Оборудование и станки"], ["defects", "Справочник дефектов"], ["db", "База данных"], ["roles", "Роли и права"], ["integrity", "Целостность журнала"], ["keys", "Ключи"], ["audit", "Журнал действий"], ["integration", "Интеграции"]];

function openAdmin(section) {
  const { dialog, close } = modal(`<div class="modal-head"><h2>Администрирование</h2><button class="btn small" data-close>✕</button></div>
    <div class="admin"><nav class="admin-nav">${ADMIN_SECTIONS.map(([k, t]) => `<button class="btn ${k === section ? "active" : ""}" data-sec="${k}">${t}</button>`).join("")}</nav><div id="admin-body" class="admin-body">загрузка…</div></div>`, { wide: true });
  const show = async (key) => {
    dialog.querySelectorAll("[data-sec]").forEach((b) => b.classList.toggle("active", b.dataset.sec === key));
    const body = dialog.querySelector("#admin-body");
    try { body.innerHTML = await ADMIN[key](); ADMIN_BIND[key]?.(body, close); } catch (error) { body.innerHTML = `<p class="bad-text">${esc(error.message)}</p>`; }
  };
  dialog.querySelectorAll("[data-sec]").forEach((b) => b.addEventListener("click", () => show(b.dataset.sec)));
  show(section);
}

const ADMIN = {
  async equipment() {
    const rows = await api("/api/admin/equipment");
    return `<div class="card-head"><h3>Станки линий</h3><div class="row"><select id="add-line" aria-label="Линия">${S.lines.map((l) => `<option value="${esc(l.line_id)}">${esc(l.title)}</option>`).join("")}</select><button class="btn primary small" id="add-machine">＋ Добавить станок</button></div></div>
      <p class="muted small">Новый станок добавляется блоком «Операция» в редакторе линии: задайте код станка, операторов, длительность и виды дефектов, соедините блок с соседними этапами и сохраните новую версию линии. Изменение линии пишется в журнал критических действий.</p>
      <table><tr><th>линия</th><th>этап</th><th>станок</th><th>операторы</th><th class="num">норма</th><th>состояние</th><th class="num">отклонений</th><th></th></tr>${rows.map((r) => `<tr><td>${esc(r.line_id)}</td><td>${esc(r.stage)}</td><td class="mono">${esc(r.equipment_id)}</td><td class="mono small">${esc(r.operators.join(", "))}</td><td class="num">${r.duration_min} мин</td><td>${r.state ? badge(r.state, ["warning", "deviation", "stopped"].includes(r.state) ? "warn" : "plain") : "—"}</td><td class="num">${r.deviations}</td><td><button class="btn small" data-edit-line="${esc(r.line_id)}">изменить</button></td></tr>`).join("")}</table>`;
  },
  async defects() {
    const rows = await api("/api/admin/defects");
    return `<h3>Справочник дефектов</h3><p class="muted small">Коды приходят от анализаторов и задаются этапам линий. Для каждого — чем он обнаруживается и где граница визуального метода.</p><table><tr><th>код</th><th>название</th><th>чем обнаруживается</th><th>где возникает по настройке линий</th><th class="num">найдено</th><th class="num">подтв.</th></tr>${rows.map((r) => `<tr><td class="mono">${esc(r.code)}</td><td>${esc(r.title)}</td><td class="small muted">${esc(r.method)}</td><td class="small">${esc(r.where.join("; ") || "—")}</td><td class="num">${r.found}</td><td class="num">${r.confirmed}</td></tr>`).join("")}</table>`;
  },
  async db() {
    const d = await api("/api/admin/db");
    return `<h3>База данных · ${esc(d.dialect)}</h3><p class="muted small">Только чтение. Шифротекст журнала, хеши паролей и полезная нагрузка очереди не показываются никогда.</p>
      <table><tr><th>таблица</th><th class="num">строк</th><th>столбцы</th><th>скрыто</th><th></th></tr>${d.tables.map((t) => `<tr><td class="mono">${esc(t.table)}</td><td class="num">${t.rows}</td><td class="small mono">${esc(t.columns.join(", "))}</td><td class="small muted">${esc(t.hidden.join(", ") || "—")}</td><td><button class="btn small" data-table="${esc(t.table)}">строки</button></td></tr>`).join("")}</table><div id="db-rows"></div>`;
  },
  async roles() {
    const d = await api("/api/admin/roles");
    const roles = Object.keys(d.roles);
    return `<h3>Что могут делать пользователи</h3><table><tr><th>право</th>${roles.map((r) => `<th>${esc(L.role[r] || r)}</th>`).join("")}</tr>${d.permissions.map((p) => `<tr><td>${esc(p.title)}</td>${roles.map((r) => `<td class="num">${d.roles[r].includes(p.permission) ? "✓" : ""}</td>`).join("")}</tr>`).join("")}</table>
      <h3>Пользователи</h3><table><tr><th>пользователь</th><th>роль</th><th>пароль</th></tr>${d.users.map((u) => `<tr><td>${esc(u.name)} <span class="mono muted small">${esc(u.user_id)}</span></td><td>${esc(L.role[u.role])}</td><td>${u.has_password ? badge("задан", "ok") : badge("только демо-вход", "plain")}</td></tr>`).join("")}</table>
      <p class="muted small">Пароль задаётся командой <span class="mono">uv run python scripts/keys.py password &lt;пользователь&gt;</span>; в базе хранится только хеш scrypt.</p>`;
  },
  async integrity() {
    return `<h3>Целостность журнала</h3><p class="muted">Проверяются цепочка хешей, подписи пакетов и подписанная голова журнала, хранящаяся отдельно от базы.</p><button class="btn primary" id="verify">Проверить</button><div id="verify-out" style="margin-top:10px"></div>`;
  },
  async keys() {
    const k = await api("/api/keys");
    return `<h3>Ключи и профили</h3><table><tr><th>ключ</th><th>профиль</th><th>статус</th><th>секрет</th></tr>${k.keys.map((x) => `<tr><td class="mono">${esc(x.key_id)}</td><td class="mono">${esc(x.profile_id)}</td><td>${badge(x.status, x.status === "active" ? "ok" : "plain")}</td><td>${x.secret_available ? "есть" : badge("недоступен", "warn")}</td></tr>`).join("")}</table>
      <div class="row" style="margin-top:10px"><select id="profile">${Object.entries(k.profiles).map(([id, m]) => `<option value="${id}">${esc(id)} — ${esc(m)}</option>`).join("")}</select><button class="btn" id="rotate">Выпустить ключ</button></div>`;
  },
  async audit() {
    const rows = await api("/api/audit");
    return `<h3>Журнал критических действий</h3><table><tr><th>№</th><th>когда</th><th>кто</th><th>действие</th></tr>${rows.slice(0, 100).map((r) => `<tr><td>${r.seq}</td><td class="small">${time(r.at, true)}</td><td class="mono small">${esc(r.user_id)}</td><td>${esc(r.action)} <span class="muted mono small">${esc(JSON.stringify(r.details))}</span></td></tr>`).join("")}</table>`;
  },
  async integration() {
    const d = await api("/api/integration");
    return `<div class="card-head"><h3>Интеграции</h3><button class="btn small primary" id="sync">Синхронизировать</button></div><p>Подключено: ${d.adapters.map((a) => badge(a, "info")).join(" ") || "—"}</p>
      ${d.outbox.length ? `<table>${d.outbox.map((o) => `<tr><td class="mono">${esc(o.item_id)}</td><td>${badge(o.status, o.status === "delivered" ? "ok" : "warn")}</td><td>${o.attempts}</td><td class="muted small">${esc(o.last_error || o.external_ref || "")}</td></tr>`).join("")}</table>` : `<div class="empty">Очередь пуста.</div>`}`;
  },
};

const ADMIN_BIND = {
  equipment(body, close) {
    body.querySelector("#add-machine").addEventListener("click", async () => {
      const lineId = body.querySelector("#add-line").value;
      close();
      openEditor({ lines: S.lines, config: await api(`/api/lines/${lineId}`), addMachine: true, onSaved: afterSave });
    });
    body.querySelectorAll("[data-edit-line]").forEach((b) => b.addEventListener("click", async () => {
      close();
      openEditor({ lines: S.lines, config: await api(`/api/lines/${b.dataset.editLine}`), onSaved: afterSave });
    }));
  },
  db(body) {
    body.querySelectorAll("[data-table]").forEach((b) => b.addEventListener("click", async () => {
      const d = await api(`/api/admin/db?table=${encodeURIComponent(b.dataset.table)}`);
      const cols = d.rows.length ? Object.keys(d.rows[0]) : [];
      body.querySelector("#db-rows").innerHTML = `<h3>${esc(d.table)} · последние ${d.rows.length}</h3><div class="table-wrap"><table><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${d.rows.map((r) => `<tr>${cols.map((c) => `<td class="small mono">${esc(r[c])}</td>`).join("")}</tr>`).join("")}</table></div>`;
    }));
  },
  integrity(body) {
    body.querySelector("#verify").addEventListener("click", async () => {
      const r = await api("/api/integrity");
      body.querySelector("#verify-out").innerHTML = r.ok ? `${badge("журнал цел", "ok")} записей ${r.checked}, якорь №${r.anchor_seq}` : `${badge("обнаружено вмешательство", "bad")}<ul class="plain">${r.problems.map((p) => `<li>запись №${p.seq} (${esc(p.kind || "")}): ${esc(p.problem)}</li>`).join("")}</ul>`;
    });
  },
  keys(body) {
    body.querySelector("#rotate").addEventListener("click", async () => { const r = await api("/api/keys/rotate", { method: "POST", body: JSON.stringify({ profile_id: body.querySelector("#profile").value }) }); notify(`Выпущен ${r.key_id}.`); });
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
