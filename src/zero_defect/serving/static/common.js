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
  role: { controller: "контролёр ОТК", master: "мастер участка", technologist: "технолог", manager: "руководитель производства", admin: "администратор", edge: "источник событий" },
  status: { conforming: ["годно", "ok"], nonconforming: ["несоответствие", "bad"], suspect: ["на рассмотрении", "bad"], not_assessable: ["оценка невозможна", "warn"], in_progress: ["в работе", "info"], unknown: ["нет данных", "plain"] },
  nc: { reported: ["сообщение о признаке", "warn"], under_review: ["рассматривается", "info"], recheck_requested: ["доп. проверка", "info"], confirmed: ["подтверждено", "bad"], rejected: ["отклонено", "plain"], closed: ["закрыто", "ok"] },
  stage: { incoming: "входной брак", operation: "возникло на операции", between_checks: "между проверками", unknown: "этап не установлен" },
  conf: { strong: ["основания сильные", "ok"], moderate: ["основания умеренные", "warn"], insufficient: ["сведений недостаточно", "bad"] },
  cause: { incoming_defect: "входной брак", equipment_problem: "проблема оборудования", operator_error: "ошибка оператора", process_issue: "техпроцесс", handling_damage: "повреждение при перемещении", not_established: "не установлена", other: "иное" },
  action: { start_review: "Начать рассмотрение", request_recheck: "Назначить доп. проверку", confirm: "Подтвердить несоответствие", reject: "Отклонить сигнал", close: "Закрыть (устранено)", reopen: "Открыть заново", confirm_cause: "Установить причину" },
  pathStatus: { pending: "ещё не прошёл", passed: "пройден", processing: "в работе", reworked: "доработка", not_assessable: "оценка невозможна", possible_origin: "мог возникнуть здесь", defect_detected: "обнаружен здесь", defect_origin: "возник здесь" },
  result: { no_defect_signs: ["признаков нет", "ok"], defect_signs_found: ["признаки дефекта", "bad"], not_assessable: ["оценка невозможна", "warn"] },
  event: { item_registered: "поступление", component_linked: "установка компонента", operation_started: "начало операции", operation_paused: "пауза", operation_resumed: "возобновление", operation_finished: "завершение операции", inspection_reported: "контроль", operator_action: "действие оператора", machine_state: "состояние станка" },
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

export function modal(html, { wide = false, onClose } = {}) {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" tabindex="-1">${html}</div>`;
  document.body.appendChild(backdrop);
  const dialog = backdrop.firstElementChild;
  const close = () => { backdrop.remove(); window.removeEventListener("keydown", onKey); onClose?.(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  window.addEventListener("keydown", onKey);
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(); });
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  dialog.focus();
  return { dialog, close };
}
