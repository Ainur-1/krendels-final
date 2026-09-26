// Пульт эмулятора: отдельная страница, вынесенная за пределы системы. Отсюда линиями
// управляют так, как управлял бы стенд: пуск, остановка, дефект в выбранный этап и
// прогон по данным контракта. Система видит всё это обычными событиями источников.
import { $, L, api, badge, can, esc, login, notify, poller, session } from "./common.js?v=0.5.0";

let lines = [];
let configs = {};
let chosen = null;
const forced = [];

async function start() {
  if (!session.token) {
    document.querySelector("main").innerHTML = `<section class="card"><p>Для работы с пультом войдите в систему под ролью с правом управления эмуляцией: руководитель производства, мастер участка или администратор.</p><a class="btn primary" href="/">Войти</a></section>`;
    return;
  }
  try { await login(session.token); } catch { location.href = "/"; return; }
  $("user-chip").innerHTML = `<b>${esc(session.me.role_title)}</b>`;
  $("user-chip").title = `${session.me.name} (${session.me.user_id})`;
  if (!can("emulate")) {
    document.querySelector("main").innerHTML = `<section class="card"><p>У роли «${esc(session.me.role_title)}» нет права управления эмуляцией.</p></section>`;
    return;
  }
  await loadLines();
  poller(refreshLines, 1500);
}

async function loadLines() {
  lines = await api("/api/lines");
  for (const line of lines) configs[line.line_id] = await api(`/api/lines/${line.line_id}`);
  chosen = chosen && configs[chosen] ? chosen : lines[0]?.line_id;
  renderLines();
  renderRun();
}

async function refreshLines() {
  const fresh = await api("/api/lines");
  if (fresh.length !== lines.length) { await loadLines(); return; }
  lines = fresh;
  renderLines();
}

function renderLines() {
  $("emu-lines").innerHTML = lines.map((l) => {
    const em = l.emulation;
    const run = em.run && !em.run.finished ? em.run : null;
    const nodes = configs[l.line_id]?.order.map((id) => configs[l.line_id].nodes.find((n) => n.node_id === id)) || [];
    return `<article class="emu-line" data-line="${esc(l.line_id)}">
      <div class="row spread"><b>${esc(l.title)}</b>${run ? badge(`Прогон: ${Math.round(run.progress * 100)} %`, "info") : em.running ? badge(`Работает, ×${Math.round(em.speed)}`, "ok") : badge("Остановлена", "plain")}</div>
      ${run ? `<div class="progress" style="margin:6px 0"><i style="width:${Math.round(run.progress * 100)}%"></i></div>` : ""}
      <div class="row" style="margin-top:6px">
        ${em.running ? `<button class="btn small" data-act="stop">■ Стоп</button>` : `<button class="btn small primary" data-act="start">▶ Пуск</button>`}
        <select data-act="speed" aria-label="Ускорение">${[30, 60, 120, 240].map((v) => `<option value="${v}" ${Math.round(em.speed) === v ? "selected" : ""}>×${v}</option>`).join("")}</select>
      </div>
      <div class="row" style="margin-top:6px">
        <select data-act="node" aria-label="Этап">${nodes.map((n) => `<option value="${esc(n.node_id)}">${n.kind === "operation" ? "⚙" : "◉"} ${esc(n.title)}</option>`).join("")}</select>
        <button class="btn small" data-act="defect">Внести дефект</button><button class="btn small" data-act="deviation">Отклонение станка</button>
      </div>
      ${Object.keys(em.forced).length ? `<div class="muted small">Ожидают следующего изделия: ${Object.entries(em.forced).map(([n, k]) => `${esc(n)} (${k === "deviation" ? "отклонение станка" : "дефект"})`).join(", ")}</div>` : ""}
    </article>`;
  }).join("");
  $("emu-lines").querySelectorAll("[data-line]").forEach((card) => {
    const id = card.dataset.line;
    const post = (path, body) => api(`/api/lines/${id}/${path}`, { method: "POST", body: JSON.stringify(body) }).then(refreshLines).catch((e) => notify(e.message, true));
    card.querySelector('[data-act="start"]')?.addEventListener("click", () => post("emulation", { action: "start", speed: Number(card.querySelector('[data-act="speed"]').value) }));
    card.querySelector('[data-act="stop"]')?.addEventListener("click", () => post("emulation", { action: "stop" }));
    card.querySelector('[data-act="speed"]').addEventListener("change", (e) => post("emulation", { action: "start", speed: Number(e.target.value) }));
    for (const kind of ["defect", "deviation"]) {
      card.querySelector(`[data-act="${kind}"]`).addEventListener("click", () => {
        const node = card.querySelector('[data-act="node"]').value;
        post("emulation/inject", { node_id: node, kind }).then(() => notify(`${kind === "deviation" ? "Отклонение станка" : "Дефект"} будет внесён в следующее изделие на этапе ${node}.`));
      });
    }
  });
}

function renderRun() {
  const config = configs[chosen];
  if (!config) { $("emu-run").innerHTML = `<div class="empty">Линий нет.</div>`; return; }
  const nodes = config.order.map((id) => config.nodes.find((n) => n.node_id === id));
  $("emu-run").innerHTML = `<form id="run-form" class="form">
      <label>Линия<select name="line">${lines.map((l) => `<option value="${esc(l.line_id)}" ${l.line_id === chosen ? "selected" : ""}>${esc(l.title)}</option>`).join("")}</select></label>
      <div class="form-row"><label>Изделий<input name="items" type="number" min="1" max="200" value="24"></label><label>Показать за, с<input name="duration_s" type="number" min="10" max="900" value="60"></label><label>Зерно<input name="seed" type="number" value="7"></label></div>
      <h3>Вероятность дефекта по этапам, %</h3>
      <table><tr><th>этап</th><th>вид</th><th class="num">норма, мин</th><th class="num">дефект, %</th></tr>${nodes.map((n) => `<tr><td>${esc(n.title)}</td><td class="small muted">${n.kind === "operation" ? "операция" : "контроль"}</td><td class="num">${Math.round((n.duration_s / 60) * 10) / 10}</td><td class="num"><input data-rate="${esc(n.node_id)}" type="number" min="0" max="100" step="0.5" value="${Math.round(n.defect_rate * 1000) / 10}" style="width:80px"></td></tr>`).join("")}</table>
      <h3>Принудительные дефекты</h3>
      <table id="forced"><tr><th>изделие №</th><th>этап</th><th>вид</th><th></th></tr>${forced.map((f, i) => `<tr><td><input data-f="item" data-i="${i}" type="number" min="1" value="${f.item}" style="width:70px"></td><td><select data-f="node_id" data-i="${i}">${nodes.map((n) => `<option value="${esc(n.node_id)}" ${n.node_id === f.node_id ? "selected" : ""}>${esc(n.title)}</option>`).join("")}</select></td><td><select data-f="kind" data-i="${i}"><option value="defect" ${f.kind === "defect" ? "selected" : ""}>дефект</option><option value="deviation" ${f.kind === "deviation" ? "selected" : ""}>отклонение станка</option></select></td><td><button type="button" class="btn small" data-del="${i}">×</button></td></tr>`).join("")}</table>
      <button type="button" class="btn small" id="add-forced">＋ дефект</button>
      <button class="btn primary">Проиграть прогон</button>
    </form>`;
  const form = $("run-form");
  form.line.addEventListener("change", () => { chosen = form.line.value; forced.length = 0; renderRun(); });
  $("add-forced").addEventListener("click", () => { forced.push({ item: 1, node_id: nodes[0].node_id, kind: "defect" }); renderRun(); });
  form.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => { const f = forced[Number(el.dataset.i)]; f[el.dataset.f] = el.dataset.f === "item" ? Number(el.value) : el.value; }));
  form.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { forced.splice(Number(b.dataset.del), 1); renderRun(); }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rates = Object.fromEntries([...form.querySelectorAll("[data-rate]")].map((el) => [el.dataset.rate, Number(el.value)]));
    const body = { items: Number(form.items.value), duration_s: Number(form.duration_s.value), seed: Number(form.seed.value), defect_rates_pct: rates, defects: forced };
    try {
      const r = await api(`/api/lines/${chosen}/runs`, { method: "POST", body: JSON.stringify(body) });
      notify(`Прогон запущен на линии ${chosen}: ускорение ×${r.speed}. Ход прогона отображается на графе линии в системе.`);
      refreshLines();
    } catch (error) { notify(error.message, true); }
  });
}

$("emu-example").addEventListener("click", async () => { $("emu-json").value = JSON.stringify(await api("/api/flows/example"), null, 2); });
$("emu-import").addEventListener("click", async () => {
  try {
    const r = await api("/api/flows", { method: "POST", body: $("emu-json").value });
    notify(`Прогон из JSON запущен на линии ${r.line_id}: ускорение ×${r.speed}.`);
    await loadLines();
  } catch (error) { notify(error.message, true); }
});

start();
