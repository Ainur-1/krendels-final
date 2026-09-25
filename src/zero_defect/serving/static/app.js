// Интерфейс Zero Defect. Без сборки и без внешних библиотек: в закрытом контуре нет
// ни CDN, ни npm, и страница должна открываться с того же сервера, что отдаёт API.
import { CONTRACT } from "./contract.js";

const view = document.getElementById("view");
const userSelect = document.getElementById("user");
const toast = document.getElementById("toast");

const LABELS = {
  status: {
    conforming: ["годно", "ok"],
    nonconforming: ["несоответствие", "bad"],
    suspect: ["на рассмотрении", "warn"],
    not_assessable: ["оценка невозможна", "warn"],
    in_progress: ["в работе", "plain"],
    unknown: ["нет данных", "plain"],
  },
  nc: {
    reported: ["сообщение о признаке", "warn"],
    under_review: ["рассматривается", "info"],
    recheck_requested: ["назначена доп. проверка", "info"],
    confirmed: ["подтверждено", "bad"],
    rejected: ["отклонено", "plain"],
    closed: ["закрыто", "ok"],
  },
  stage: {
    incoming: "входной брак",
    operation: "возникло на операции",
    between_checks: "между проверками без операций",
    unknown: "этап не установлен",
  },
  confidence: { strong: ["основания сильные", "ok"], moderate: ["основания умеренные", "warn"], insufficient: ["сведений недостаточно", "bad"] },
  cause: {
    incoming_defect: "входной брак",
    equipment_problem: "проблема оборудования",
    operator_error: "ошибка оператора",
    process_issue: "техпроцесс",
    handling_damage: "повреждение при перемещении",
    not_established: "причина не установлена",
    other: "иное",
  },
  action: {
    start_review: "Начать рассмотрение",
    request_recheck: "Назначить доп. проверку",
    confirm: "Подтвердить несоответствие",
    reject: "Отклонить сигнал",
    close: "Закрыть (устранено)",
    reopen: "Открыть заново",
    confirm_cause: "Установить причину",
  },
  event: {
    item_registered: "поступление на учёт",
    component_linked: "установка компонента",
    operation_started: "начало операции",
    operation_paused: "пауза",
    operation_resumed: "возобновление",
    operation_finished: "завершение операции",
    inspection_reported: "результат контроля",
    operator_action: "действие оператора",
    machine_state: "состояние оборудования",
  },
  inspection: {
    defect_signs_found: ["признаки дефекта", "bad"],
    no_defect_signs: ["признаков нет", "ok"],
    not_assessable: ["оценка невозможна", "warn"],
  },
  flag: { late: "опоздало", out_of_order: "не по порядку", clock_skew: "расхождение часов" },
  role: { controller: "контролёр", master: "мастер", technologist: "технолог", manager: "руководитель", admin: "администратор" },
};

let token = null;
let me = null;

const esc = (value) =>
  String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const badge = (pair, fallback) => {
  const [text, tone] = pair || [fallback ?? "—", "plain"];
  return `<span class="badge ${tone}">${esc(text)}</span>`;
};
const time = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const minutes = (seconds) => (seconds == null ? "—" : `${(seconds / 60).toFixed(1)} мин`);

function notify(text, bad = false) {
  toast.textContent = text;
  toast.className = bad ? "toast bad" : "toast";
  toast.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => (toast.hidden = true), 4000);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const response = await fetch(path, { ...options, headers });
  const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || response.statusText);
  return body;
}

function remember(key, value) {
  try { sessionStorage.setItem(key, value); } catch { /* хранилище недоступно — работаем без него */ }
}
function recall(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

async function login(userId) {
  const result = await api("/api/auth/demo-login", { method: "POST", body: JSON.stringify({ user_id: userId }) });
  token = result.token;
  me = await api("/api/me");
  remember("zd-user", userId);
}

// --- Линия ------------------------------------------------------------------------

async function renderLine() {
  const data = await api("/api/line");
  const c = data.counters;
  const stat = (value, label) => `<div class="panel"><div class="stat">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`;
  view.innerHTML = `
    <h1>Состояние линии</h1>
    <p class="lead">Что сейчас происходит на участках, какие несоответствия ждут решения и что пришло с отклонениями от правил поставки.</p>
    <div class="grid">
      ${stat(c.items, "изделий в истории")}
      ${stat(c.in_progress_runs, "операций в работе")}
      ${stat(c.open_nonconformances, "несоответствий ждут решения")}
      ${stat(c.quarantine, "сообщений в карантине")}
      ${stat(c.late_events, "событий пришло с опозданием")}
      ${stat(c.sources_with_gaps, "источников с пропусками")}
    </div>
    <div class="cols" style="margin-top:14px">
      <div class="panel">
        <h2>Участки</h2>
        <div class="table-wrap"><table>
          <tr><th>участок</th><th>в работе</th><th>выполнено</th><th>открытых NC</th></tr>
          ${data.stations.map((s) => `<tr><td>${esc(s.title)} <span class="muted mono">${esc(s.station_id)}</span></td>
            <td>${s.in_progress.map((r) => `<span class="badge info">${esc(r.item_id)}</span>`).join(" ") || "—"}</td>
            <td>${s.completed_runs}</td><td>${s.open_nonconformances ? badge([s.open_nonconformances, "warn"]) : "0"}</td></tr>`).join("")}
        </table></div>
        <h2 style="margin-top:14px">Оборудование</h2>
        <div class="table-wrap"><table>
          <tr><th>оборудование</th><th>последнее состояние</th><th>когда</th><th>отклонений</th></tr>
          ${data.equipment.map((e) => `<tr><td class="mono">${esc(e.equipment_id)}</td>
            <td>${badge([e.state, ["warning", "deviation", "stopped"].includes(e.state) ? "warn" : "plain"])} ${esc(e.message ?? "")}</td>
            <td>${time(e.at)}</td><td>${e.deviations}</td></tr>`).join("")}
        </table></div>
        <h2 style="margin-top:14px">Пропуски в нумерации источников</h2>
        ${Object.keys(data.source_gaps).length ? `<ul class="list">${Object.entries(data.source_gaps).map(([s, g]) => `<li><span class="mono">${esc(s)}</span>: не дошли №${g.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}</li>`).join("")}</ul>` : `<div class="empty">Пропусков нет.</div>`}
      </div>
      <div class="panel">
        <h2>Последние поступившие события</h2>
        <div class="table-wrap"><table>
          <tr><th>поступило</th><th>событие</th><th>изделие</th><th>источник</th><th></th></tr>
          ${data.recent_events.map((e) => `<tr class="${e.item_id ? "click" : ""}" data-item="${esc(e.item_id ?? "")}">
            <td class="mono">${time(e.received_at)}</td><td>${esc(LABELS.event[e.event_type] || e.event_type)}</td>
            <td class="mono">${esc(e.item_id ?? e.equipment_id ?? "")}</td><td class="mono muted">${esc(e.source_id)}</td>
            <td>${e.flags.map((f) => badge([LABELS.flag[f] || f, "warn"])).join(" ")}</td></tr>`).join("")}
        </table></div>
      </div>
    </div>`;
  view.querySelectorAll("tr[data-item]").forEach((row) => row.dataset.item && row.addEventListener("click", () => openItem(row.dataset.item)));
}

// --- Изделия ----------------------------------------------------------------------

async function renderItems(filter = "") {
  const rows = await api(`/api/items${filter ? `?status=${encodeURIComponent(filter)}` : ""}`);
  view.innerHTML = `
    <div class="row spread"><div><h1>Изделия</h1><p class="lead">Каждое изделие — с компонентами, операциями, проверками и несоответствиями в одной истории.</p></div>
      <label class="row muted">статус <select id="status-filter"><option value="">все</option>
        ${Object.entries(LABELS.status).map(([key, [text]]) => `<option value="${key}" ${key === filter ? "selected" : ""}>${text}</option>`).join("")}
      </select></label></div>
    <div class="panel table-wrap"><table>
      <tr><th>изделие</th><th>тип</th><th>статус</th><th>в составе</th><th>компоненты</th><th>задание</th><th>операций</th><th>проверок</th></tr>
      ${rows.map((r) => `<tr class="click" data-item="${esc(r.item_id)}"><td class="mono">${esc(r.item_id)}</td><td class="mono">${esc(r.item_type_id)}</td>
        <td>${badge(LABELS.status[r.status], r.status)}</td><td class="mono">${esc(r.parent_id ?? "")}</td>
        <td class="mono">${esc(r.components.join(", "))}</td><td class="mono">${esc(r.work_order_id)}</td><td>${r.runs}</td><td>${r.observations}</td></tr>`).join("")}
    </table></div>`;
  document.getElementById("status-filter").addEventListener("change", (e) => renderItems(e.target.value));
  view.querySelectorAll("tr[data-item]").forEach((row) => row.addEventListener("click", () => openItem(row.dataset.item)));
}

function durationCell(run) {
  const d = run.durations;
  const reported = d.reported_s != null ? `<div class="muted">источник: ${minutes(d.reported_s)} (${esc(d.reported_meaning)})</div>` : "";
  return `<div>на участке ${minutes(d.station_time_s)}, активно ${minutes(d.active_time_s)} <span class="badge plain">система</span></div>${reported}`;
}

async function openItem(itemId) {
  setTab("items");
  const item = await api(`/api/items/${encodeURIComponent(itemId)}`);
  view.innerHTML = `
    <p><a href="#" id="back">← все изделия</a></p>
    <div class="row spread"><h1 class="mono">${esc(item.item_id)}</h1>${badge(LABELS.status[item.status], item.status)}</div>
    <div class="cols">
      <div class="panel"><h2>Сведения</h2><dl class="kv">
        <dt>тип</dt><dd class="mono">${esc(item.item_type_id)}</dd>
        <dt>происхождение</dt><dd>${item.origin === "purchased" ? "покупной" : item.origin === "manufactured" ? "изготовлен на предприятии" : "—"}</dd>
        <dt>задание</dt><dd class="mono">${esc(item.work_order_id)}</dd>
        <dt>поступил на учёт</dt><dd>${item.registered ? time(item.registered_at) : badge(["не зарегистрирован", "warn"])}</dd>
        <dt>в составе</dt><dd>${item.parent_id ? `<a href="#" data-item="${esc(item.parent_id)}" class="mono">${esc(item.parent_id)}</a>` : "—"}</dd>
        <dt>компоненты</dt><dd>${item.components.map((c) => `<a href="#" data-item="${esc(c.item_id)}" class="mono">${esc(c.item_id)}</a> ${badge(LABELS.status[c.status], c.status)}`).join("<br>") || "—"}</dd>
      </dl></div>
      <div class="panel"><h2>Несоответствия</h2>
        ${item.nonconformances.length ? `<table>${item.nonconformances.map((n) => `<tr class="click" data-nc="${esc(n.nc_id)}"><td class="mono">${esc(n.nc_id)}</td><td>${esc(n.defect_type)}</td><td>${badge(LABELS.nc[n.status], n.status)}</td></tr>`).join("")}</table>` : `<div class="empty">Несоответствий нет.</div>`}
      </div>
    </div>
    <div class="panel"><h2>Операции</h2><div class="table-wrap"><table>
      <tr><th>операция</th><th>участок</th><th>оператор</th><th>оборудование</th><th>начало → конец</th><th>длительность</th><th>смена</th><th>статус</th></tr>
      ${item.runs.map((r) => `<tr><td class="mono">${esc(r.operation_id)} ${r.is_rework ? badge(["повторная", "warn"]) : ""}<div class="muted">${esc(r.run_id)}</div>${r.rework_reason ? `<div class="muted">${esc(r.rework_reason)}</div>` : ""}</td>
        <td class="mono">${esc(r.station_id)}</td><td class="mono">${esc(r.operator_id)}</td><td class="mono">${esc(r.equipment_id)}</td>
        <td>${time(r.started_at)} → ${time(r.finished_at)}</td><td>${durationCell(r)}</td>
        <td>${esc(r.shift.id)} <span class="muted">(${r.shift.origin === "reported" ? "источник" : "система"})</span></td>
        <td>${badge([r.status, r.status === "completed" ? "ok" : "warn"])}${r.machine_events.some((m) => ["warning", "deviation", "stopped"].includes(m.machine_state)) ? " " + badge(["отклонение станка", "bad"]) : ""}</td></tr>`).join("")}
    </table></div></div>
    <div class="panel"><h2>История</h2><ul class="timeline">
      ${item.timeline.map((e) => `<li><time>${time(e.occurred_at)}</time><div><b>${esc(LABELS.event[e.event_type] || e.event_type)}</b>
        ${e.inspection_result ? badge(LABELS.inspection[e.inspection_result]) : ""} ${e.machine_state ? badge([e.machine_state, "plain"]) : ""}
        ${e.flags.map((f) => badge([LABELS.flag[f] || f, "warn"])).join(" ")}
        <div class="muted">${[e.checkpoint_id, e.station_id, e.operator_id, e.equipment_id, e.message].filter(Boolean).map(esc).join(" · ")}
        ${e.defects.length ? " · " + e.defects.map((d) => esc(`${d.defect_type} (${d.area ?? "зона не указана"})`)).join(", ") : ""}</div>
        <div class="muted mono">${esc(e.event_id)} · источник ${esc(e.source_id)} · поступило ${time(e.received_at)}</div></div></li>`).join("")}
    </ul></div>`;
  document.getElementById("back").addEventListener("click", (e) => { e.preventDefault(); renderItems(); });
  view.querySelectorAll("a[data-item]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); openItem(a.dataset.item); }));
  view.querySelectorAll("tr[data-nc]").forEach((row) => row.addEventListener("click", () => openNc(row.dataset.nc)));
}

// --- Несоответствия ---------------------------------------------------------------

async function renderNcs() {
  const rows = await api("/api/nonconformances");
  view.innerHTML = `
    <h1>Несоответствия</h1>
    <p class="lead">Сообщение анализатора, оценка системы и решение людей хранятся раздельно. Статус карточки — это решения контролёра, а не вывод модели.</p>
    <div class="panel table-wrap"><table>
      <tr><th>карточка</th><th>изделие</th><th>дефект</th><th>тяжесть</th><th>обнаружено</th><th>сигналов</th><th>этап (оценка системы)</th><th>статус</th><th>причина</th></tr>
      ${rows.map((n) => `<tr class="click" data-nc="${esc(n.nc_id)}"><td class="mono">${esc(n.nc_id)}</td><td class="mono">${esc(n.item_id)}</td>
        <td>${esc(n.defect_type)}<div class="muted">${esc(n.area ?? "")}</div></td><td>${esc(n.severity)}</td>
        <td>${time(n.first_detected_at)}<div class="muted mono">${esc(n.checkpoint_id)}</div></td><td>${n.signals}</td>
        <td>${esc(LABELS.stage[n.stage] || n.stage)} ${badge(LABELS.confidence[n.stage_confidence])}</td>
        <td>${badge(LABELS.nc[n.status], n.status)}</td>
        <td>${n.confirmed_cause ? badge([LABELS.cause[n.confirmed_cause], "ok"]) : `<span class="muted">гипотеза: ${esc(LABELS.cause[n.presumed_cause] || n.presumed_cause)}</span>`}</td></tr>`).join("")}
    </table></div>`;
  view.querySelectorAll("tr[data-nc]").forEach((row) => row.addEventListener("click", () => openNc(row.dataset.nc)));
}

async function openNc(ncId) {
  setTab("ncs");
  const card = await api(`/api/nonconformances/${encodeURIComponent(ncId)}`);
  const a = card.assessment || {};
  const causeOptions = ["incoming_defect", "equipment_problem", "operator_error", "process_issue", "handling_damage", "other"];
  view.innerHTML = `
    <p><a href="#" id="back">← все несоответствия</a></p>
    <div class="row spread"><div><h1>${esc(card.defect_type)} · <span class="mono">${esc(card.item_id)}</span></h1>
      <div class="muted mono">${esc(card.nc_id)} · зона: ${esc(card.area)} · тяжесть: ${esc(card.severity)}</div></div>
      <div class="row">${badge(LABELS.nc[card.status], card.status)}${card.confirmed_cause ? badge([`причина: ${LABELS.cause[card.confirmed_cause]}`, "ok"]) : ""}</div></div>
    ${card.signals_after_last_decision ? `<div class="panel" style="border-color:var(--warn)">После последнего решения поступило новых сигналов: ${card.signals_after_last_decision}. Решение стоит пересмотреть.</div>` : ""}
    <div class="panel layer source"><div class="layer-title">1 · исходные сообщения анализатора — как пришли</div>
      ${card.signals_detail.map((s) => `<div class="row"><b>${time(s.occurred_at)}</b> ${badge(LABELS.inspection[s.inspection_result])}
        <span class="mono">${esc(s.checkpoint_id)} (${esc(s.checkpoint_kind)})</span>
        <span class="muted">уверенность ${s.confidence ?? "—"} · качество ${esc(s.observation_quality)} · ${esc(s.analyzer_version)}</span>
        ${s.reliable ? badge(["наблюдение достоверно", "ok"]) : badge([`недостоверно: ${s.reliability_note}`, "warn"])}</div>
        <details><summary class="muted">исходное сообщение ${esc(s.event_id)}</summary><pre class="raw">${esc(JSON.stringify(s.raw, null, 2))}</pre></details>`).join("")}
      <h2 style="margin-top:10px">Материалы</h2>
      ${card.evidence.length ? card.evidence.map((m) => `<div class="row">${badge([m.kind === "photo" ? "фото" : "видео", "plain"])} <span class="mono">${esc(m.uri)}</span>
        <span class="muted">${time(m.captured_at)} · ${esc(m.checkpoint_id)} · ${esc(m.item_id)}</span> ${m.available ? badge(["доступно", "ok"]) : badge([m.note, "warn"])}</div>`).join("") : `<div class="empty">Фото и видео к сигналу не приложены.</div>`}
    </div>
    <div class="panel layer system"><div class="layer-title">2 · разбор обстоятельств — оценка системы, не решение</div>
      <div class="row"><b>${esc(LABELS.stage[a.stage] || a.stage)}</b> ${badge(LABELS.confidence[a.stage_confidence])}
        <span class="muted">предполагаемая причина: ${esc(LABELS.cause[a.presumed_cause] || a.presumed_cause)}</span></div>
      <div class="cols" style="margin-top:10px">
        <div><h2>Основания</h2>${(a.evidence || []).length ? `<ul class="list">${a.evidence.map((e) => `<li>${esc(e.text)}</li>`).join("")}</ul>` : `<div class="empty">Оснований нет.</div>`}</div>
        <div><h2>Что ещё могло привести к тому же</h2>${(a.alternatives || []).length ? `<ul class="list">${a.alternatives.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<div class="empty">Альтернатив не видно.</div>`}
          <h2 style="margin-top:10px">Каких сведений не хватает</h2>${(a.missing || []).length ? `<ul class="list">${a.missing.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<div class="empty">Недостающих сведений не отмечено.</div>`}</div>
      </div>
      ${(a.participants || []).length ? `<h2 style="margin-top:10px">Участники операций</h2><ul class="list">${a.participants.map((p) => `<li><span class="mono">${esc(p.operator_id)}</span> — ${esc(p.operation_id)} (${esc(p.run_id)}): ${esc(p.note)}</li>`).join("")}</ul>` : ""}
      ${card.recheck_outcome ? `<p><b>Дополнительная проверка:</b> ${esc(card.recheck_outcome)}</p>` : ""}
    </div>
    <div class="panel layer human"><div class="layer-title">3 · решения людей — отдельные записи журнала</div>
      ${card.decisions.length ? `<table><tr><th>когда</th><th>кто</th><th>решение</th><th>обоснование</th></tr>${card.decisions.map((d) => `<tr><td>${time(d.decided_at)}</td>
        <td class="mono">${esc(d.author_id)} <span class="muted">${esc(LABELS.role[d.author_role] || d.author_role)}</span></td>
        <td>${esc(LABELS.action[d.action] || d.action)}${d.cause_category ? `: ${esc(LABELS.cause[d.cause_category])}` : ""}</td><td>${esc(d.reason)}</td></tr>`).join("")}</table>` : `<div class="empty">Решений пока нет.</div>`}
      ${card.allowed_actions.length ? `<form class="form" id="decision" style="margin-top:12px">
        <select name="action">${card.allowed_actions.map((x) => `<option value="${x}">${esc(LABELS.action[x] || x)}</option>`).join("")}</select>
        <select name="cause" hidden>${causeOptions.map((x) => `<option value="${x}">${esc(LABELS.cause[x])}</option>`).join("")}</select>
        <textarea name="reason" rows="2" placeholder="Обоснование — обязательно" required></textarea>
        <button class="primary" type="submit">Записать решение</button></form>` : `<p class="muted">У роли «${esc(LABELS.role[me?.role] || me?.role)}» нет решений по этой карточке.</p>`}
    </div>`;
  document.getElementById("back").addEventListener("click", (e) => { e.preventDefault(); renderNcs(); });
  const form = document.getElementById("decision");
  if (form) {
    const syncCause = () => (form.cause.hidden = form.action.value !== "confirm_cause");
    form.action.addEventListener("change", syncCause);
    syncCause();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = { action: form.action.value, reason: form.reason.value, cause_category: form.action.value === "confirm_cause" ? form.cause.value : null };
        await api(`/api/nonconformances/${encodeURIComponent(ncId)}/decisions`, { method: "POST", body: JSON.stringify(body) });
        notify("Решение записано в журнал отдельной записью.");
        openNc(ncId);
      } catch (error) { notify(error.message, true); }
    });
  }
}

// --- Показатели -------------------------------------------------------------------

function table(object, head) {
  const entries = Object.entries(object || {});
  if (!entries.length) return `<div class="empty">Нет данных.</div>`;
  return `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${entries.map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
}

function durations(group) {
  const entries = Object.entries(group || {});
  return `<table><tr><th></th><th>выполнений</th><th>среднее активное</th><th>медиана</th><th>по данным источника</th></tr>
    ${entries.map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td>${v.system_active.count}</td><td>${minutes(v.system_active.mean_s)}</td>
      <td>${minutes(v.system_active.median_s)}</td><td>${v.reported.count ? minutes(v.reported.mean_s) : "—"}</td></tr>`).join("")}</table>`;
}

async function renderMetrics() {
  const m = await api("/api/metrics");
  const stat = (value, label) => `<div class="panel"><div class="stat">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`;
  view.innerHTML = `
    <h1>Показатели</h1>
    <p class="lead">Дефекты и изделия с дефектами считаются раздельно; входной брак, проблемы оборудования, ошибки операторов и гипотезы — отдельно. Время помечено происхождением.</p>
    <div class="grid">
      ${stat(m.items.inspected, "изделий проверено")}
      ${stat(m.items.with_confirmed_nonconformance, "изделий с подтверждёнными несоответствиями")}
      ${stat(m.defects.nonconformances_total, "несоответствий (дефектов)")}
      ${stat(m.defects.signals_total, "сигналов анализатора")}
      ${stat(m.defects.confirmed, "подтверждено")}
      ${stat(m.defects.rejected, "отклонено как ложные")}
      ${stat(m.operations.rework_runs, "повторных обработок")}
      ${stat(m.operations.unfinished, "незавершённых операций")}
      ${stat(m.extra.first_pass_yield == null ? "—" : `${Math.round(m.extra.first_pass_yield * 100)} %`, "с первого раза (система)")}
    </div>
    <div class="cols" style="margin-top:14px">
      <div class="panel"><h2>Дефекты по типам</h2>${table(m.defects.by_type, ["тип", "карточек"])}
        <h2 style="margin-top:12px">по участкам обнаружения</h2>${table(m.defects.by_station, ["участок", "карточек"])}
        <h2 style="margin-top:12px">по линиям</h2>${table(m.defects.by_line, ["линия", "карточек"])}</div>
      <div class="panel"><h2>Причины</h2><dl class="kv">
        <dt>установлена</dt><dd>${m.causes.established}</dd><dt>не установлена</dt><dd>${m.causes.not_established}</dd>
        <dt>входной брак</dt><dd>${m.causes.incoming_defects_confirmed}</dd><dt>проблемы оборудования</dt><dd>${m.causes.equipment_problems_confirmed}</dd>
        <dt>ошибки операторов</dt><dd>${m.causes.operator_errors_confirmed}</dd></dl>
        <h2 style="margin-top:12px">Гипотезы системы (не подтверждены)</h2>${table(Object.fromEntries(Object.entries(m.causes.hypotheses).map(([k, v]) => [LABELS.cause[k] || k, v])), ["гипотеза", "карточек"])}
        <h2 style="margin-top:12px">Подтверждённые ошибки по сопоставимым работам</h2>
        <table><tr><th>операция</th><th>оператор</th><th>выполнений</th><th>подтв. ошибок</th><th>доля</th></tr>
        ${Object.entries(m.comparable_work).flatMap(([op, rows]) => Object.entries(rows).map(([who, r]) => `<tr><td class="mono">${esc(op)}</td><td class="mono">${esc(who)}</td><td>${r.runs}</td><td>${r.confirmed_errors}</td><td>${(r.error_rate * 100).toFixed(0)} %</td></tr>`)).join("")}</table>
      </div>
    </div>
    <div class="cols" style="margin-top:14px">
      <div class="panel"><h2>Длительность по участкам</h2>${durations(m.operations.duration_by_station)}<h2 style="margin-top:12px">по сменам</h2>${durations(m.operations.duration_by_shift)}</div>
      <div class="panel"><h2>Длительность по операторам</h2>${durations(m.operations.duration_by_operator)}
        <p class="muted">${esc(m.operations.duration_origin)}</p>
        <dl class="kv"><dt>задержка обнаружения, медиана</dt><dd>${minutes(m.extra.detection_delay_s.median_s)}</dd>
        <dt>ожидание между операциями, медиана</dt><dd>${minutes(m.extra.waiting_between_operations_s.median_s)}</dd>
        <dt>поступление</dt><dd>принято ${m.ingest.accepted}, повторов ${m.ingest.duplicates}, в карантине ${m.ingest.rejected}, опоздало ${m.ingest.late}</dd></dl></div>
    </div>`;
}

// --- Журнал и защита ----------------------------------------------------------------

async function renderSecurity() {
  const quarantine = await api("/api/quarantine");
  let admin = "";
  if (me?.permissions.includes("admin")) {
    const [keys, audit] = await Promise.all([api("/api/keys"), api("/api/audit")]);
    admin = `
      <div class="cols">
        <div class="panel"><h2>Целостность журнала</h2>
          <p class="muted">Проверяются цепочка хешей, подписи всех записей и подписанная голова журнала, хранящаяся отдельно от базы.</p>
          <button class="primary" id="verify">Проверить целостность</button><div id="verify-result" style="margin-top:10px"></div></div>
        <div class="panel"><h2>Ключи и профили</h2>
          <table><tr><th>ключ</th><th>версия</th><th>профиль</th><th>статус</th><th>секрет</th></tr>
          ${keys.keys.map((k) => `<tr><td class="mono">${esc(k.key_id)}</td><td>${k.key_version}</td><td class="mono">${esc(k.profile_id)}</td><td>${badge([k.status, k.status === "active" ? "ok" : "plain"])}</td><td>${k.secret_available ? "есть" : badge(["недоступен", "warn"])}</td></tr>`).join("")}</table>
          <div class="row" style="margin-top:10px"><select id="profile">${Object.entries(keys.profiles).map(([id, mech]) => `<option value="${id}">${esc(id)} — ${esc(mech)}</option>`).join("")}</select>
          <button class="ghost" id="rotate">Выпустить новый ключ</button></div>
          <p class="muted">Старые записи не перешифровываются: они остаются проверяемыми своим ключом и профилем.</p></div>
      </div>
      <div class="panel"><h2>Журнал критических действий</h2><div class="table-wrap"><table>
        <tr><th>№</th><th>когда</th><th>кто</th><th>действие</th><th>подробности</th></tr>
        ${audit.slice(0, 60).map((r) => `<tr><td>${r.seq}</td><td>${time(r.at)}</td><td class="mono">${esc(r.user_id)} <span class="muted">${esc(r.role)}</span></td><td>${esc(r.action)}</td><td class="mono">${esc(JSON.stringify(r.details))}</td></tr>`).join("")}
      </table></div></div>`;
  }
  view.innerHTML = `
    <h1>Журнал и защита</h1>
    <p class="lead">Исходные события хранятся отдельно от изменяемых представлений и только дописываются. Отклонённые сообщения не теряются — они в карантине с причинами.</p>
    ${admin || `<div class="panel muted">Проверка целостности, ключи и журнал критических действий доступны администратору.</div>`}
    <div class="panel"><h2>Карантин: сообщения, не прошедшие проверку</h2>
      ${quarantine.length ? `<div class="table-wrap"><table><tr><th>поступило</th><th>ошибки</th><th>сообщение</th></tr>
      ${quarantine.map((q) => `<tr><td>${time(q.received_at)}</td><td>${q.errors.map((e) => `<div>${badge([e.code, "bad"])} <span class="mono">${esc(e.field)}</span> ${esc(e.message)}</div>`).join("")}</td>
        <td><details><summary class="mono">${esc(q.raw?.event_id ?? "без идентификатора")}</summary><pre class="raw">${esc(JSON.stringify(q.raw, null, 2))}</pre></details></td></tr>`).join("")}</table></div>` : `<div class="empty">Карантин пуст.</div>`}
    </div>`;
  document.getElementById("verify")?.addEventListener("click", async () => {
    const r = await api("/api/integrity");
    document.getElementById("verify-result").innerHTML = r.ok
      ? `${badge(["журнал цел", "ok"])} проверено записей: ${r.checked}, голова №${r.head_seq}, якорь №${r.anchor_seq}`
      : `${badge(["обнаружено вмешательство", "bad"])}<ul class="list">${r.problems.map((p) => `<li>запись №${p.seq} (${esc(p.kind ?? "")}): ${esc(p.problem)}</li>`).join("")}</ul>`;
  });
  document.getElementById("rotate")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/keys/rotate", { method: "POST", body: JSON.stringify({ profile_id: document.getElementById("profile").value }) });
      notify(`Выпущен ${r.key_id} (${r.profile_id}).`);
      renderSecurity();
    } catch (error) { notify(error.message, true); }
  });
}

// --- Интеграции -------------------------------------------------------------------

async function renderIntegration() {
  const data = await api("/api/integration");
  view.innerHTML = `
    <h1>Интеграции</h1>
    <p class="lead">Задания приходят из внешней системы и становятся событиями поступления; итоги по изделиям уходят через исходящую очередь, которая переживает недоступность получателя.</p>
    <div class="panel row spread"><div>Подключено: ${data.adapters.map((a) => badge([a, "info"])).join(" ") || "—"}
      ${Object.entries(data.last_errors).filter(([, e]) => e).map(([a, e]) => `<div>${badge([a, "bad"])} ${esc(e)}</div>`).join("")}</div>
      ${me?.permissions.includes("integrate") ? `<button class="primary" id="sync">Синхронизировать</button>` : ""}</div>
    <div class="cols" style="margin-top:14px">
      <div class="panel"><h2>Исходящая очередь</h2>${data.outbox.length ? `<div class="table-wrap"><table><tr><th>сообщение</th><th>изделие</th><th>статус</th><th>попыток</th><th>ошибка</th><th>ответ</th></tr>
        ${data.outbox.map((o) => `<tr><td class="mono">${esc(o.message_id)}</td><td class="mono">${esc(o.item_id)}</td><td>${badge([o.status, o.status === "delivered" ? "ok" : o.status === "pending" ? "warn" : "bad"])}</td><td>${o.attempts}</td><td>${esc(o.last_error ?? "")}</td><td class="mono">${esc(o.external_ref ?? "")}</td></tr>`).join("")}</table></div>` : `<div class="empty">Очередь пуста.</div>`}</div>
      <div class="panel"><h2>Сопоставление идентификаторов</h2>${data.id_map.length ? `<table><tr><th>система</th><th>сущность</th><th>внешний</th><th>внутренний</th></tr>${data.id_map.map((r) => `<tr><td>${esc(r.system)}</td><td>${esc(r.entity)}</td><td class="mono">${esc(r.external_id)}</td><td class="mono">${esc(r.internal_id)}</td></tr>`).join("")}</table>` : `<div class="empty">Пока пусто.</div>`}</div>
    </div>
    <div class="panel"><h2>Журнал обмена</h2>${data.exchanges.length ? `<table><tr><th>№</th><th>когда</th><th>система</th><th>сообщение</th><th>попытка</th><th>итог</th></tr>${data.exchanges.map((x) => `<tr><td>${x.seq}</td><td>${time(x.at)}</td><td>${esc(x.system)}</td><td class="mono">${esc(x.message_id)}</td><td>${x.attempt}</td><td>${badge([x.status, x.status === "delivered" ? "ok" : "warn"])} ${esc(x.error ?? "")}</td></tr>`).join("")}</table>` : `<div class="empty">Обменов ещё не было.</div>`}</div>`;
  document.getElementById("sync")?.addEventListener("click", async () => {
    try {
      const report = await api("/api/integration/sync", { method: "POST" });
      notify(Object.entries(report).map(([name, r]) => `${name}: заданий ${r.pull.orders ?? "—"}, в очередь ${r.queued}, доставлено ${r.push.delivered}`).join("; "));
      renderIntegration();
    } catch (error) { notify(error.message, true); }
  });
}

// --- навигация --------------------------------------------------------------------

const VIEWS = { line: renderLine, items: () => renderItems(), ncs: renderNcs, metrics: renderMetrics, security: renderSecurity, integration: renderIntegration };
let current = "line";

function setTab(name) {
  current = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  remember("zd-view", name);
}

async function show(name) {
  setTab(name);
  view.innerHTML = `<div class="empty">Загрузка…</div>`;
  try { await VIEWS[name](); } catch (error) { view.innerHTML = `<div class="panel">Не удалось загрузить: ${esc(error.message)}</div>`; }
}

document.getElementById("tabs").addEventListener("click", (e) => { if (e.target.dataset.view) show(e.target.dataset.view); });
userSelect.addEventListener("change", async () => { await login(userSelect.value); show(current); });

(async function start() {
  console.info("Контракт событий, версии:", CONTRACT.versions.join(", "));
  const users = await api("/api/users");
  userSelect.innerHTML = users.map((u) => `<option value="${esc(u.user_id)}">${esc(u.name)} · ${esc(LABELS.role[u.role] || u.role)}</option>`).join("");
  const saved = recall("zd-user");
  userSelect.value = users.some((u) => u.user_id === saved) ? saved : "ctrl-01";
  await login(userSelect.value);
  show(recall("zd-view") in VIEWS ? recall("zd-view") : "line");
})();
