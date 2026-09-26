// Общее для интерфейса системы и пульта эмулятора: запросы, вход, подписи, форматирование.

export const $ = (id) => document.getElementById(id);
export const esc = (value) =>
  String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const time = (iso, seconds = false) =>
  iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }) : "—";
export const mins = (s) => (s == null ? "—" : s >= 60 ? `${(s / 60).toFixed(1)} мин` : `${Math.round(s)} с`);
export const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)} %`);
export const rub = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString("ru-RU")} ₽`);
export const badge = (text, tone = "plain") => `<span class="badge ${tone}">${esc(text)}</span>`;

export const L = {
  role: { controller: "Контролёр ОТК", master: "Мастер участка", technologist: "Технолог", manager: "Руководитель производства", admin: "Администратор", edge: "Источник событий" },
  status: { conforming: ["Годно", "ok"], nonconforming: ["Несоответствие", "bad"], suspect: ["На рассмотрении", "review"], not_assessable: ["Оценка невозможна", "warn"], in_progress: ["В работе", "blue"], unknown: ["Нет данных", "plain"] },
  nc: { reported: ["Сигнал анализатора", "warn"], under_review: ["На рассмотрении", "info"], recheck_requested: ["Назначена повторная проверка", "info"], confirmed: ["Подтверждено", "bad"], rejected: ["Сигнал отклонён", "plain"], closed: ["Устранено", "ok"] },
  stage: { incoming: "Входной брак", operation: "Возникло на операции", between_checks: "Между проверками", unknown: "Этап не установлен" },
  conf: { strong: ["Основания достаточные", "ok"], moderate: ["Основания частичные", "warn"], insufficient: ["Сведений недостаточно", "bad"] },
  cause: { incoming_defect: "Входной брак", equipment_problem: "Проблема оборудования", operator_error: "Ошибка оператора", process_issue: "Техпроцесс", handling_damage: "Повреждение при перемещении", not_established: "Не установлена", other: "Иная" },
  action: { start_review: "Начать рассмотрение", request_recheck: "Назначить повторную проверку", confirm: "Подтвердить несоответствие", reject: "Отклонить сигнал", close: "Закрыть как устранённое", reopen: "Открыть повторно", confirm_cause: "Установить причину" },
  pathStatus: { pending: "Не пройден", passed: "Пройден", processing: "В работе", reworked: "Доработка", not_assessable: "Оценка невозможна", possible_origin: "Возможный этап возникновения", defect_detected: "Дефект обнаружен", defect_origin: "Этап возникновения" },
  // Шаги маршрута изделия в хронологии: проблемный проход и повторный проход различаются.
  visit: { passed: "Пройден", possible_origin: "Возможный этап возникновения", origin: "Этап возникновения", defect: "Дефект обнаружен", not_assessable: "Оценка невозможна", rework: "Доработка", ok: "Успешно", processing: "В работе", pending: "Не пройден" },
  runOutcome: { completed: "Завершено", in_progress: "В работе", reworked: "Доработка", paused: "Пауза" },
  result: { no_defect_signs: ["Признаков нет", "ok"], defect_signs_found: ["Признаки дефекта", "bad"], not_assessable: ["Оценка невозможна", "warn"] },
  event: { item_registered: "Поступление", component_linked: "Установка компонента", operation_started: "Начало операции", operation_paused: "Пауза", operation_resumed: "Возобновление", operation_finished: "Завершение операции", inspection_reported: "Контроль", operator_action: "Действие оператора", machine_state: "Состояние станка" },
};

// Токен в localStorage: пульт эмулятора открывается в отдельной вкладке и должен знать,
// кто вошёл. Если хранилище недоступно, вход просто не переживает перезагрузку.
export const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* без хранилища */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* без хранилища */ } },
};

export const session = { token: store.get("zd-token"), me: null, onExpired: null };

export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}) };
  const response = await fetch(path, { ...options, headers });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : await response.text();
  if (response.status === 401 && session.token) {
    session.token = null;
    store.del("zd-token");
    session.onExpired?.();
    throw new Error("сессия истекла, войдите снова");
  }
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ") : JSON.stringify(body));
  }
  return body;
}

export async function login(token) {
  session.token = token;
  store.set("zd-token", token);
  session.me = await api("/api/me");
  return session.me;
}

export function logout() {
  session.token = null;
  session.me = null;
  store.del("zd-token");
}

export const can = (permission) => !!session.me?.permissions.includes(permission);

export function notify(text, bad = false) {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = Object.assign(document.createElement("div"), { id: "toast" });
    document.body.appendChild(toast);
  }
  toast.textContent = text;
  toast.className = bad ? "toast bad" : "toast";
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.add("hidden"), 4500);
}

// Опрос без наложения: пока предыдущий запрос не вернулся, следующий не уходит. Иначе на
// медленном ответе запросы копятся и интерфейс начинает «лагать» от собственных опросов.
export function poller(fn, ms) {
  let busy = false;
  let stopped = false;
  const run = async () => {
    if (busy || stopped) return;
    busy = true;
    try { await fn(); } catch (error) { console.warn(error); } finally { busy = false; }
  };
  // Фоновые такты пропускаются, пока вкладка скрыта, а при возвращении на неё данные
  // обновляются сразу. Первая загрузка (now) идёт всегда.
  const id = setInterval(() => { if (!document.hidden) run(); }, ms);
  const onVisible = () => { if (!document.hidden) run(); };
  document.addEventListener("visibilitychange", onVisible);
  return { stop() { stopped = true; clearInterval(id); document.removeEventListener("visibilitychange", onVisible); }, now: run };
}

// Окно поверх экрана. Закрывается крестиком, Esc или нажатием вне окна — плавно: сначала
// гаснет, потом удаляется, чтобы закрытие не выглядело обрывом.
export function modal(html, { wide = false, onClose } = {}) {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" tabindex="-1">${html}</div>`;
  document.body.appendChild(backdrop);
  const dialog = backdrop.firstElementChild;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("keydown", onKey);
    backdrop.classList.add("closing");
    setTimeout(() => { backdrop.remove(); onClose?.(); }, 180);
  };
  // Esc закрывает только верхнее окно: над редактором может быть открыто ещё одно.
  const onKey = (e) => { if (e.key === "Escape" && backdrop === [...document.querySelectorAll(".modal-backdrop")].pop()) close(); };
  window.addEventListener("keydown", onKey);
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(); });
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  dialog.focus();
  return { dialog, close };
}

// Выбор нескольких значений из справочника с поиском: выбранное — метками, остальное —
// выпадающим списком под полем поиска. Enter берёт первое найденное, Backspace в пустом
// поле снимает последнее выбранное.
export function picker(root, { options, selected = [], placeholder = "найти и добавить…", empty = "в справочнике больше ничего нет", onChange }) {
  let chosen = selected.filter((v) => options.some((o) => o.value === v));
  root.classList.add("picker");
  root.innerHTML = `<div class="picker-box"><span class="picker-chips"></span><input class="picker-search" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></div><div class="picker-list hidden" role="listbox"></div>`;
  const chips = root.querySelector(".picker-chips"), input = root.querySelector(".picker-search"), list = root.querySelector(".picker-list");
  const label = (v) => options.find((o) => o.value === v);
  const renderChips = () => {
    chips.innerHTML = chosen.map((v) => `<span class="picker-chip" title="${esc(label(v)?.hint || "")}">${esc(label(v)?.label || v)}<button type="button" data-drop="${esc(v)}" aria-label="Убрать">×</button></span>`).join("");
    chips.querySelectorAll("[data-drop]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); set(chosen.filter((v) => v !== b.dataset.drop)); }));
  };
  const matches = () => {
    const q = input.value.trim().toLowerCase();
    return options.filter((o) => !chosen.includes(o.value) && (!q || `${o.value} ${o.label} ${o.hint || ""}`.toLowerCase().includes(q)));
  };
  const renderList = () => {
    const found = matches();
    list.innerHTML = found.length
      ? found.map((o) => `<div class="picker-option" data-pick="${esc(o.value)}" role="option"><b>${esc(o.label)}</b>${o.hint ? `<span>${esc(o.hint)}</span>` : ""}</div>`).join("")
      : `<div class="picker-empty">${esc(input.value ? "ничего не найдено" : empty)}</div>`;
    list.querySelectorAll("[data-pick]").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); set([...chosen, el.dataset.pick]); input.value = ""; renderList(); }));
  };
  const set = (values) => { chosen = values; renderChips(); if (!list.classList.contains("hidden")) renderList(); onChange?.(chosen.slice()); };
  input.addEventListener("focus", () => { list.classList.remove("hidden"); renderList(); });
  input.addEventListener("blur", () => list.classList.add("hidden"));
  input.addEventListener("input", renderList);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); const first = matches()[0]; if (first) { set([...chosen, first.value]); input.value = ""; renderList(); } }
    if (e.key === "Backspace" && !input.value && chosen.length) set(chosen.slice(0, -1));
    if (e.key === "Escape") { e.stopPropagation(); input.blur(); }
  });
  root.querySelector(".picker-box").addEventListener("click", () => input.focus());
  renderChips();
  return { get: () => chosen.slice() };
}

// Бегущая строка для текста, который не помещается в блок: две копии едут влево без
// остановки, как на табло. Текст, который помещается, остаётся как есть.
const MARQUEE_GAP = 40;
const MARQUEE_SPEED = 35; // пикселей в секунду: читается и не мельтешит

export function marquee(el) {
  if (!el || el.dataset.mq || el.scrollWidth <= el.clientWidth + 1) return;
  const width = el.scrollWidth;
  const html = el.innerHTML;
  el.dataset.mq = "1";
  el.classList.add("mq-box");
  el.innerHTML = `<span class="mq-track" style="--d:-${width + MARQUEE_GAP}px;animation-duration:${((width + MARQUEE_GAP) / MARQUEE_SPEED).toFixed(1)}s"><span>${html}</span><span aria-hidden="true">${html}</span></span>`;
}

export function marqueeSvg(svg) {
  const ns = "http://www.w3.org/2000/svg";
  const defs = svg.querySelector("defs");
  svg.querySelectorAll("text[data-fit]").forEach((text, i) => {
    const max = Number(text.dataset.fit);
    const length = text.getComputedTextLength();
    if (!length || length <= max) return;
    const x = Number(text.getAttribute("x")), y = Number(text.getAttribute("y"));
    const clip = document.createElementNS(ns, "clipPath");
    clip.id = `mq-${Date.now().toString(36)}-${i}`;
    const rect = document.createElementNS(ns, "rect");
    Object.entries({ x, y: y - 16, width: max, height: 22 }).forEach(([k, v]) => rect.setAttribute(k, v));
    clip.appendChild(rect);
    defs.appendChild(clip);
    const frame = document.createElementNS(ns, "g");
    frame.setAttribute("clip-path", `url(#${clip.id})`);
    const track = document.createElementNS(ns, "g");
    track.setAttribute("class", "mq-track");
    track.setAttribute("style", `--d:-${length + MARQUEE_GAP}px;animation-duration:${((length + MARQUEE_GAP) / MARQUEE_SPEED).toFixed(1)}s`);
    text.replaceWith(frame);
    frame.appendChild(track);
    text.removeAttribute("data-fit");
    track.appendChild(text);
    const copy = text.cloneNode(true);
    copy.setAttribute("x", x + length + MARQUEE_GAP);
    copy.setAttribute("aria-hidden", "true");
    track.appendChild(copy);
  });
}
