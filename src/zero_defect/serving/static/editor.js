// Блочный редактор линии. Блок вытягивается из палитры на холст, соединяется с другим
// блоком протягиванием от выхода (правый кружок) ко входу (левый кружок), параметры
// задаются в правой панели. Сохранение отдаёт граф серверу, а тот проверяет его целиком:
// цикл, связь в никуда или операция без длительности не сохранятся.
import { api, esc, modal, notify } from "./common.js?v=0.3.4";

const BLOCK_W = 170, BLOCK_H = 74;
const KINDS = {
  inspection: { title: "Контроль", icon: "◉", defaults: { duration_min: 2, defect_rate_pct: 0 } },
  operation: { title: "Операция", icon: "⚙", defaults: { duration_min: 8, defect_rate_pct: 3 } },
};

function blankLine(lines) {
  const n = lines.length + 1;
  return {
    line_id: `L${n}`, title: `Линия ${n}`, product_type_id: `PART-${n}`, description: "", takt_min: 8,
    economics: { item_value_rub: 0, rework_cost_rub: 0, scrap_cost_rub: 0, hour_cost_rub: 0, shift_hours: 12 },
    nodes: [], edges: [],
  };
}

function fromConfig(config) {
  return {
    line_id: config.line_id, title: config.title, product_type_id: config.product_type_id, description: config.description || "",
    takt_min: Math.round((config.takt_s / 60) * 10) / 10,
    economics: { ...config.economics },
    nodes: config.nodes.map((n) => ({
      node_id: n.node_id, title: n.title, kind: n.kind, x: n.x * 1.25, y: n.y,
      duration_min: Math.round((n.duration_s / 60) * 10) / 10, defect_rate_pct: Math.round(n.defect_rate * 1000) / 10,
      defect_types: n.defect_types, rework_types: n.rework_types, equipment_id: n.equipment_id, operators: n.operators,
      checkpoint_kind: n.checkpoint_kind, item_type_id: n.item_type_id, origin: n.origin, output_type: n.output_type,
    })),
    edges: config.edges.map((e) => [...e]),
  };
}

export function openEditor({ lines, config = null, addMachine = false, onSaved }) {
  const line = config ? fromConfig(config) : blankLine(lines);
  const editing = !!config;
  let selected = null;
  let counter = line.nodes.length;
  const { dialog, close } = modal(`
    <div class="modal-head"><h2>${editing ? `Линия ${esc(line.line_id)} — изменение` : "Новая производственная линия"}</h2><button class="btn small" data-close>✕</button></div>
    <div class="editor">
      <aside class="palette">
        <h3>Блоки</h3>
        <div class="palette-item" draggable="true" data-kind="inspection">◉ Контроль<span>входной, после операции, финальный</span></div>
        <div class="palette-item" draggable="true" data-kind="operation">⚙ Операция<span>станок, операторы, длительность</span></div>
        <p class="muted">Перетащите блок на холст. Соедините блоки, протянув линию от правого кружка к левому кружку следующего. Два входа в одну операцию — это сборка.</p>
        <h3>Линия</h3>
        <form id="ed-meta" class="form">
          <label>Код линии<input name="line_id" value="${esc(line.line_id)}" ${editing ? "readonly" : ""} required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,31}"></label>
          <label>Название<input name="title" value="${esc(line.title)}" required></label>
          <label>Выпускаемое изделие<input name="product_type_id" value="${esc(line.product_type_id)}" required></label>
          <label>Такт, мин<input name="takt_min" type="number" min="0.5" step="0.5" value="${line.takt_min}"></label>
          <details><summary class="muted">Экономика линии</summary>
            <label>Стоимость годного, ₽<input name="item_value_rub" type="number" min="0" value="${line.economics.item_value_rub ?? 0}"></label>
            <label>Стоимость доработки, ₽<input name="rework_cost_rub" type="number" min="0" value="${line.economics.rework_cost_rub ?? 0}"></label>
            <label>Потери на браке, ₽<input name="scrap_cost_rub" type="number" min="0" value="${line.economics.scrap_cost_rub ?? 0}"></label>
            <label>Стоимость часа участка, ₽<input name="hour_cost_rub" type="number" min="0" value="${line.economics.hour_cost_rub ?? 0}"></label>
          </details>
        </form>
      </aside>
      <section class="canvas-wrap"><div id="ed-canvas" class="canvas"><svg id="ed-edges" class="canvas-edges"></svg></div></section>
      <aside id="ed-inspector" class="inspector"></aside>
    </div>
    <div class="modal-foot"><span id="ed-status" class="muted"></span><div class="row"><button class="btn" data-close>Отмена</button><button id="ed-save" class="btn primary">${editing ? "Сохранить новую версию" : "Создать линию"}</button></div></div>`, { wide: true });

  const canvas = dialog.querySelector("#ed-canvas");
  const svg = dialog.querySelector("#ed-edges");
  const inspector = dialog.querySelector("#ed-inspector");

  const node = (id) => line.nodes.find((n) => n.node_id === id);
  const port = (n, side) => ({ x: n.x + (side === "out" ? BLOCK_W : 0), y: n.y + BLOCK_H / 2 });
  const curve = (a, b) => { const dx = Math.max(40, Math.abs(b.x - a.x) / 2); return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`; };

  function status() {
    const sources = line.nodes.filter((n) => !line.edges.some(([, t]) => t === n.node_id));
    const sinks = line.nodes.filter((n) => !line.edges.some(([s]) => s === n.node_id));
    dialog.querySelector("#ed-status").textContent = `${line.nodes.length} этапов, ${line.edges.length} связей · входов ${sources.length}, выходов ${sinks.length}`;
  }

  function drawEdges(temp = null) {
    const h = Math.max(canvas.scrollHeight, 600), w = Math.max(canvas.scrollWidth, 900);
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.innerHTML = `<defs><marker id="ed-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>` +
      line.edges.map(([a, b], i) => node(a) && node(b) ? `<path class="ed-edge ${selected?.edge === i ? "selected" : ""}" data-edge="${i}" d="${curve(port(node(a), "out"), port(node(b), "in"))}" marker-end="url(#ed-arrow)"/>` : "").join("") +
      (temp ? `<path class="ed-edge temp" d="${curve(temp.from, temp.to)}"/>` : "");
    svg.querySelectorAll("[data-edge]").forEach((p) => p.addEventListener("click", (e) => { e.stopPropagation(); selected = { edge: Number(p.dataset.edge) }; renderInspector(); drawEdges(); }));
  }

  function drawBlocks() {
    canvas.querySelectorAll(".block").forEach((b) => b.remove());
    for (const n of line.nodes) {
      const el = document.createElement("div");
      el.className = `block ${n.kind} ${selected?.node === n.node_id ? "selected" : ""}`;
      el.style.left = `${n.x}px`;
      el.style.top = `${n.y}px`;
      el.dataset.node = n.node_id;
      el.innerHTML = `<span class="port in" data-port="in"></span><b>${KINDS[n.kind].icon} ${esc(n.title)}</b>
        <small>${n.duration_min} мин${n.kind === "operation" ? ` · ${esc(n.equipment_id || "станок не задан")}` : ""}${n.defect_rate_pct ? ` · дефект ${n.defect_rate_pct} %` : ""}</small><span class="port out" data-port="out"></span>`;
      canvas.appendChild(el);
      bindBlock(el, n);
    }
    drawEdges();
    status();
  }

  function bindBlock(el, n) {
    el.addEventListener("pointerdown", (e) => {
      if (e.target.dataset.port) return;
      e.preventDefault();
      selected = { node: n.node_id };
      renderInspector();
      canvas.querySelectorAll(".block").forEach((b) => b.classList.toggle("selected", b.dataset.node === n.node_id));
      const start = { x: e.clientX, y: e.clientY, nx: n.x, ny: n.y };
      const move = (ev) => {
        n.x = Math.max(0, Math.round((start.nx + ev.clientX - start.x) / 10) * 10);
        n.y = Math.max(0, Math.round((start.ny + ev.clientY - start.y) / 10) * 10);
        el.style.left = `${n.x}px`;
        el.style.top = `${n.y}px`;
        drawEdges();
      };
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
    el.querySelector('[data-port="out"]').addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const rect = canvas.getBoundingClientRect();
      const from = port(n, "out");
      const move = (ev) => drawEdges({ from, to: { x: ev.clientX - rect.left + canvas.scrollLeft, y: ev.clientY - rect.top + canvas.scrollTop } });
      const up = (ev) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        const target = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".block");
        const to = target?.dataset.node;
        if (to && to !== n.node_id && !line.edges.some(([a, b]) => a === n.node_id && b === to)) line.edges.push([n.node_id, to]);
        drawEdges();
        status();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  function addBlock(kind, x, y) {
    counter += 1;
    let id = `${line.line_id}-B${String(counter).padStart(2, "0")}`;
    while (node(id)) { counter += 1; id = `${line.line_id}-B${String(counter).padStart(2, "0")}`; }
    const n = { node_id: id, title: KINDS[kind].title, kind, x, y, defect_types: [], rework_types: [], operators: [], equipment_id: kind === "operation" ? `EQ-${id}` : null, checkpoint_kind: null, origin: "manufactured", ...KINDS[kind].defaults };
    line.nodes.push(n);
    selected = { node: id };
    drawBlocks();
    renderInspector();
    return n;
  }

  function renderInspector() {
    if (selected?.edge != null) {
      const [a, b] = line.edges[selected.edge] || [];
      inspector.innerHTML = `<h3>Связь</h3><p>${esc(node(a)?.title)} → ${esc(node(b)?.title)}</p><button class="btn danger" id="del-edge">Удалить связь</button>`;
      inspector.querySelector("#del-edge").addEventListener("click", () => { line.edges.splice(selected.edge, 1); selected = null; renderInspector(); drawEdges(); status(); });
      return;
    }
    const n = selected?.node && node(selected.node);
    if (!n) {
      inspector.innerHTML = `<h3>Параметры блока</h3><p class="muted">Выберите блок на холсте — здесь появятся его параметры: название, длительность, вероятность дефекта, виды дефектов, станок и операторы.</p>`;
      return;
    }
    const isSource = !line.edges.some(([, t]) => t === n.node_id);
    inspector.innerHTML = `<h3>${KINDS[n.kind].icon} ${KINDS[n.kind].title}</h3>
      <form class="form" id="ed-node">
        <label>Название<input name="title" value="${esc(n.title)}"></label>
        <label>Длительность, мин<input name="duration_min" type="number" min="0.1" step="0.1" value="${n.duration_min}"></label>
        <label>${n.kind === "operation" ? "Вероятность дефекта на операции, %" : "Доля брака на входе, %"}<input name="defect_rate_pct" type="number" min="0" max="100" step="0.5" value="${n.defect_rate_pct}"></label>
        <label>Виды дефектов (коды через запятую)<input name="defect_types" value="${esc(n.defect_types.join(", "))}" placeholder="BURR, CRACK"></label>
        ${n.kind === "operation" ? `<label>Станок<input name="equipment_id" value="${esc(n.equipment_id || "")}"></label>
          <label>Операторы (через запятую)<input name="operators" value="${esc(n.operators.join(", "))}"></label>
          <label>Дефекты, которые дорабатываются<input name="rework_types" value="${esc(n.rework_types.join(", "))}"></label>`
        : `<label>Вид контроля<select name="checkpoint_kind"><option value="">по месту в линии</option>${["incoming", "after_operation", "final"].map((k) => `<option value="${k}" ${n.checkpoint_kind === k ? "selected" : ""}>${{ incoming: "входной", after_operation: "после операции", final: "финальный" }[k]}</option>`).join("")}</select></label>`}
        ${isSource ? `<label>Тип изделия на входе<input name="item_type_id" value="${esc(n.item_type_id || "")}" placeholder="по умолчанию — изделие линии"></label>
          <label>Происхождение<select name="origin"><option value="manufactured" ${n.origin !== "purchased" ? "selected" : ""}>изготавливается</option><option value="purchased" ${n.origin === "purchased" ? "selected" : ""}>покупное</option></select></label>` : ""}
      </form>
      <button class="btn danger" id="del-node" style="margin-top:10px">Удалить блок</button>`;
    inspector.querySelectorAll("#ed-node [name]").forEach((input) => input.addEventListener("input", () => {
      const v = input.value;
      if (["defect_types", "operators", "rework_types"].includes(input.name)) n[input.name] = v.split(",").map((x) => x.trim()).filter(Boolean);
      else if (["duration_min", "defect_rate_pct"].includes(input.name)) n[input.name] = Number(v);
      else n[input.name] = v || null;
      drawBlocks();
      canvas.querySelector(`[data-node="${CSS.escape(n.node_id)}"]`)?.classList.add("selected");
    }));
    inspector.querySelector("#del-node").addEventListener("click", () => {
      line.nodes = line.nodes.filter((x) => x.node_id !== n.node_id);
      line.edges = line.edges.filter(([a, b]) => a !== n.node_id && b !== n.node_id);
      selected = null;
      drawBlocks();
      renderInspector();
    });
  }

  dialog.querySelectorAll(".palette-item").forEach((item) => item.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", item.dataset.kind)));
  canvas.addEventListener("dragover", (e) => e.preventDefault());
  canvas.addEventListener("drop", (e) => {
    e.preventDefault();
    const kind = e.dataTransfer.getData("text/plain");
    if (!KINDS[kind]) return;
    const rect = canvas.getBoundingClientRect();
    addBlock(kind, Math.max(0, e.clientX - rect.left + canvas.scrollLeft - BLOCK_W / 2), Math.max(0, e.clientY - rect.top + canvas.scrollTop - BLOCK_H / 2));
  });
  canvas.addEventListener("pointerdown", (e) => { if (e.target === canvas || e.target === svg) { selected = null; renderInspector(); drawBlocks(); } });

  dialog.querySelector("#ed-save").addEventListener("click", async () => {
    const meta = Object.fromEntries(new FormData(dialog.querySelector("#ed-meta")).entries());
    const spec = {
      line_id: meta.line_id, title: meta.title, product_type_id: meta.product_type_id, takt_min: Number(meta.takt_min), description: line.description,
      economics: { item_value_rub: Number(meta.item_value_rub), rework_cost_rub: Number(meta.rework_cost_rub), scrap_cost_rub: Number(meta.scrap_cost_rub), hour_cost_rub: Number(meta.hour_cost_rub), shift_hours: line.economics.shift_hours ?? 12 },
      nodes: line.nodes.map((n) => ({ ...n, x: Math.round(n.x / 1.25), y: Math.round(n.y) })),
      edges: line.edges,
    };
    try {
      await api(editing ? `/api/lines/${encodeURIComponent(line.line_id)}/graph` : "/api/lines/graph", { method: editing ? "PUT" : "POST", body: JSON.stringify(spec) });
      notify(editing ? `Сохранена новая версия линии ${spec.line_id}.` : `Линия ${spec.line_id} создана.`);
      close();
      onSaved?.(spec.line_id);
    } catch (error) { notify(error.message, true); }
  });

  if (!line.nodes.length) {
    const a = addBlock("inspection", 40, 160);
    a.title = "Входной контроль";
    const b = addBlock("operation", 280, 160);
    b.title = "Обработка";
    const c = addBlock("inspection", 520, 160);
    c.title = "Финальный контроль";
    line.edges.push([a.node_id, b.node_id], [b.node_id, c.node_id]);
    selected = null;
  }
  if (addMachine) {
    const maxX = Math.max(0, ...line.nodes.map((n) => n.x));
    const n = addBlock("operation", maxX + 60, 420);
    n.title = "Новый станок";
  }
  drawBlocks();
  renderInspector();
}
