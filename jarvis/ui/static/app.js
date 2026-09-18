/* Panel local de JARVIS. Sin dependencias remotas. Toda escritura pasa por la API del servicio de memoria. */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;
  try { history.replaceState(null, "", location.pathname); } catch (e) {}
  const $ = (s) => document.querySelector(s);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const TYPES = ["preference", "fact", "project", "person", "concept", "decision", "procedure", "question", "note", "source", "source_doc"];
  const GLYPH = { preference: "★", fact: "•", project: "P", person: "@", concept: "○", decision: "✓", procedure: "≡", question: "?", note: "n", source: "§", source_doc: "▣" };
  const TYPE_ES = { preference: "preferencia", fact: "hecho", project: "proyecto", person: "persona", concept: "concepto", decision: "decisión", procedure: "procedimiento", question: "pregunta", note: "nota", source: "resumen de fuente", source_doc: "documento fuente" };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  // ------------------------------------------------------------------ API
  async function api(path, opts = {}) {
    const r = await fetch("/api" + path, { ...opts, headers: { "X-Jarvis-Token": TOKEN, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (!r.ok) { let d = {}; try { d = await r.json(); } catch (e) {} throw new Error(d.detail || d.error || r.statusText); }
    return r.json();
  }
  const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

  // ------------------------------------------------------------------ estado global
  const S = { state: "IDLE", name: $("#assistantName").textContent, graph: { nodes: [], edges: [] }, view: "global",
              selected: null, hover: null, neighbors: new Set(), positions: {}, paused: false, memVersion: 0,
              streams: {}, listItems: [], listIdx: -1, wake: {} };

  // ------------------------------------------------------------------ chat
  const messages = $("#messages");
  function addMsg(role, text, meta) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    if (meta) el.appendChild(meta);
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }
  function metaRow({ cited = [], tools = [], cost, model, cancelled, error, turn_id }) {
    const m = document.createElement("div"); m.className = "meta";
    cited.forEach(id => { const c = document.createElement("span"); c.className = "chip cited"; c.textContent = "fuente " + id.slice(-6); c.title = id; c.onclick = () => openMemory(id); m.appendChild(c); });
    tools.forEach(t => { const c = document.createElement("span"); c.className = "chip tool"; c.textContent = "⚙ " + t; m.appendChild(c); });
    if (turn_id) { const c = document.createElement("span"); c.className = "chip"; c.textContent = "ver recuerdos usados"; c.onclick = () => showTrace(turn_id); m.appendChild(c); }
    if (cost != null) m.appendChild(Object.assign(document.createElement("span"), { textContent: `~$${cost.toFixed(4)}` }));
    if (model) m.appendChild(Object.assign(document.createElement("span"), { textContent: model }));
    if (cancelled) m.appendChild(Object.assign(document.createElement("span"), { textContent: "CANCELADO", className: "chip" }));
    if (error) m.appendChild(Object.assign(document.createElement("span"), { textContent: "error: " + error, className: "chip" }));
    return m;
  }
  function onDelta(ev) {
    let st = S.streams[ev.turn_id];
    if (!st) { st = S.streams[ev.turn_id] = { el: addMsg("assistant", ""), text: "", tools: [] }; }
    st.text += ev.text;
    st.el.textContent = st.text.replace(/\s*\[mem:mem_[0-9]{8}_[a-f0-9]{6}\]/g, "");
    messages.scrollTop = messages.scrollHeight;
  }
  function onMessage(ev) {
    const st = S.streams[ev.turn_id];
    const tools = (ev.trace && ev.trace.tool_calls || []).map(t => t.tool);
    const meta = metaRow({ cited: ev.cited, tools, cost: ev.cost_usd, model: ev.model, cancelled: ev.cancelled, error: ev.error, turn_id: ev.turn_id });
    if (st) { st.el.textContent = ev.text; st.el.appendChild(meta); delete S.streams[ev.turn_id]; }
    else addMsg("assistant", ev.text, meta);
    if (ev.pending_approvals && ev.pending_approvals.length) renderApprovals(ev.pending_approvals);
    loadTraces();
  }
  $("#chatForm").onsubmit = async (e) => {
    e.preventDefault();
    const inp = $("#chatInput"); const text = inp.value.trim(); if (!text) return;
    inp.value = "";
    try { await post("/chat", { text }); } catch (err) { addMsg("system", "No se pudo enviar: " + err.message); }
  };
  $("#btnStop").onclick = () => post("/cancel").catch(e => addMsg("system", e.message));
  $("#btnPtt").onclick = () => post("/ptt").catch(e => addMsg("system", e.message));
  $("#btnListen").onclick = async () => {
    const on = $("#btnListen").getAttribute("aria-pressed") !== "true";
    try { const r = await post("/listening", { enabled: on }); setListening(r.listening); } catch (e) { addMsg("system", e.message); }
  };
  function setListening(on) { $("#btnListen").setAttribute("aria-pressed", on ? "true" : "false"); $("#btnListen").textContent = on ? "Escucha: activa" : "Escucha: apagada"; }

  // ------------------------------------------------------------------ aprobaciones
  function renderApprovals(list) {
    const box = $("#approvals"); box.innerHTML = "";
    list.forEach(a => {
      const d = document.createElement("div"); d.className = "approval";
      if (a.kind === "question") {
        d.innerHTML = `<div><strong>Pregunta</strong>: ${esc(a.args.question)}</div><div class="row"></div>`;
        const row = d.querySelector(".row");
        (a.args.options || []).forEach(label => { const b = document.createElement("button"); b.className = "btn small"; b.textContent = label; b.onclick = () => post(`/approvals/${a.id}/answer`, { answer: label }).then(r => { addMsg("system", r.message); refreshApprovals(); }).catch(e => addMsg("system", e.message)); row.appendChild(b); });
        const other = document.createElement("input"); other.placeholder = "otra respuesta…"; other.className = "small";
        other.onkeydown = (e) => { if (e.key === "Enter" && other.value.trim()) post(`/approvals/${a.id}/answer`, { answer: other.value.trim() }).then(r => { addMsg("system", r.message); refreshApprovals(); }); };
        row.appendChild(other);
        box.appendChild(d); return;
      }
      d.innerHTML = `<div><strong>Permiso</strong>: ${esc(a.description)}</div><details class="small muted"><summary>detalle</summary>${esc(a.tool)} · ${esc(JSON.stringify(a.args))} · vence ${new Date(a.expires * 1000).toLocaleTimeString()}</details><div class="row"></div>`;
      const row = d.querySelector(".row");
      const ok = document.createElement("button"); ok.className = "btn small accent"; ok.textContent = "Aprobar";
      ok.onclick = () => post(`/approvals/${a.id}/approve`, { args_hash: a.args_hash }).then(r => { addMsg("system", r.message); refreshApprovals(); }).catch(e => addMsg("system", e.message));
      const no = document.createElement("button"); no.className = "btn small danger"; no.textContent = "Rechazar";
      no.onclick = () => post(`/approvals/${a.id}/reject`).then(r => { addMsg("system", r.message); refreshApprovals(); }).catch(e => addMsg("system", e.message));
      row.append(ok, no); box.appendChild(d);
    });
  }
  const refreshApprovals = () => api("/approvals").then(r => renderApprovals(r.pending)).catch(() => {});
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ estado
  function applyState(snap) {
    S.state = snap.state;
    const p = $("#statePill"); p.textContent = snap.state; p.className = "pill st-" + snap.state;
    const c = snap.claude || {}; const cp = $("#claudePill");
    cp.textContent = "Claude: " + (c.connected ? "conectado" : "desconectado") + (c.model ? " · " + c.model : "") + (snap.demo ? " · DEMO" : "");
    cp.className = "pill " + (c.connected ? "good" : "bad"); cp.title = c.last_error || "";
    const v = snap.voice || {}; const vp = $("#voicePill");
    if (v.running) { const w = v.wake || {}; vp.textContent = `voz: ${w.provider || "ptt"} «${w.keyword || ""}»` + (v.mic_error ? " · sin micro" : ""); vp.className = "pill " + (v.mic_error || v.wake_error ? "bad" : "good"); vp.title = v.wake_error || v.mic_error || (w.note || ""); }
    else { vp.textContent = "voz: inactiva (inicia con --listen)"; vp.className = "pill"; }
    setListening(!!snap.listening);
    if (snap.memory) $("#memPill").textContent = `memoria: ${snap.memory.current} recuerdos · v${snap.memory.version}`;
    if (snap.assistant && snap.assistant !== S.name) { S.name = snap.assistant; $("#assistantName").textContent = S.name; document.title = S.name + " — panel"; }
    S.wake = snap.wake_word || S.wake;
    $("#techState").textContent = JSON.stringify(snap, null, 1);
  }
  async function refreshState() { try { applyState(await api("/state")); } catch (e) { $("#claudePill").textContent = "sin conexión con el panel"; } }

  // ------------------------------------------------------------------ SSE
  let es;
  function connectEvents() {
    es = new EventSource("/api/events?token=" + encodeURIComponent(TOKEN));
    es.onmessage = (m) => { let ev; try { ev = JSON.parse(m.data); } catch (e) { return; } handleEvent(ev); };
    es.onerror = () => { es.close(); setTimeout(connectEvents, 2000); };
  }
  function handleEvent(ev) {
    switch (ev.kind) {
      case "hello": applyState(ev.state); if (ev.memory_version !== S.memVersion) resync(); refreshApprovals(); break;
      case "state": S.state = ev.state; $("#statePill").textContent = ev.state; $("#statePill").className = "pill st-" + ev.state; $("#statePill").title = ev.reason || ""; break;
      case "chat.user": addMsg("user", ev.text); break;
      case "chat.text_delta": onDelta(ev); break;
      case "chat.tool_use": { const st = S.streams[ev.turn_id]; if (st) st.tools.push(ev.name); break; }
      case "chat.message": onMessage(ev); break;
      case "chat.system": addMsg("system", ev.text); break;
      case "approval.requested": refreshApprovals(); break;
      case "approvals": renderApprovals(ev.pending); break;
      case "cancelled": addMsg("system", "Cancelado" + (ev.already_executed && ev.already_executed.length ? " · ya se había ejecutado: " + ev.already_executed.join(", ") : "")); break;
      case "voice.transcript": addMsg("system", "(voz) " + ev.text); break;
      case "voice.discarded": addMsg("system", "Activación descartada: sin voz útil"); break;
      case "voice.status": refreshState(); break;
      case "config.changed": refreshState(); loadConfig(); break;
      case "memory.created": upsertNode(ev.node); S.memVersion = ev.version; refreshState(); break;
      case "memory.updated": upsertNode(ev.node); S.memVersion = ev.version; if (S.selected === ev.id) openMemory(ev.id, true); break;
      case "memory.deleted": removeNode(ev.id); S.memVersion = ev.version; refreshState(); break;
      case "relation.created": case "relation.deleted": case "index.rebuilt": resync(); break;
      case "ui.show": handleUiShow(ev); break;
      case "tool.call": break;
    }
  }
  function handleUiShow(ev) {
    switchTab("memory");
    if (ev.view === "graph") setView("global");
    else if (ev.view === "local" && ev.target) { $("#fDepth").value = ev.depth || 1; S.selected = ev.target; setView("local"); }
    else if (ev.view === "trace") setView("trace");
    else if (ev.view === "memory" && ev.target) openMemory(ev.target);
    else if (ev.view === "list") { if (ev.filter_type) $("#fType").value = ev.filter_type; if (ev.target) $("#fProject").value = ev.target; setView("list"); }
  }

  // ------------------------------------------------------------------ grafo
  let Graph;
  function initGraph() {
    const el = $("#graph");
    Graph = ForceGraph()(el)
      .nodeId("id").linkSource("source").linkTarget("target")
      .backgroundColor("rgba(0,0,0,0)")
      .nodeRelSize(4)
      .nodeVal(n => 1 + Math.min(n.degree || 0, 12) * 0.6)
      .nodeLabel(n => `${esc(n.title)} · ${TYPE_ES[n.type] || n.type} · ${n.status}${n.degree != null ? " · " + n.degree + " conexiones" : ""}`)
      .nodeCanvasObject(paintNode).nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, nodeR(n) + 2, 0, 2 * Math.PI); ctx.fill(); })
      .linkColor(l => linkColor(l)).linkWidth(l => (l.kind === "source" ? 0.6 : 1) * (isHi(l) ? 2 : 1))
      .linkLineDash(l => l.status === "suggested" ? [3, 3] : (l.kind === "source" ? [1, 3] : (l.type === "reemplaza" ? [6, 3] : null)))
      .linkDirectionalArrowLength(l => l.type === "reemplaza" || l.type === "decidido_para" || l.type === "pertenece_a" ? 4 : 0).linkDirectionalArrowRelPos(1)
      .linkLabel(l => `${l.type} (${l.status === "suggested" ? "sugerida" : "explícita"}${l.rationale ? ": " + esc(l.rationale) : ""})`)
      .onNodeClick(n => { openMemory(n.id); selectNode(n.id); })
      .onNodeHover(n => { S.hover = n ? n.id : null; el.style.cursor = n ? "pointer" : null; })
      .onNodeDragEnd(n => { S.positions[n.id] = [n.x, n.y]; saveLayout(); })
      .onEngineStop(() => { Graph.graphData().nodes.forEach(n => { if (n.x != null) S.positions[n.id] = [n.x, n.y]; }); saveLayout(); })
      .onBackgroundClick(() => selectNode(null))
      .cooldownTicks(reduced ? 0 : 200).d3AlphaDecay(0.04).d3VelocityDecay(0.35)
      .width(el.clientWidth).height(el.clientHeight);
    new ResizeObserver(() => Graph.width(el.clientWidth).height(el.clientHeight)).observe(el);
    renderLegend();
  }
  const nodeR = (n) => 4 * Math.sqrt(1 + Math.min(n.degree || 0, 12) * 0.6);
  const isHi = (l) => S.selected && (l.source.id === S.selected || l.target.id === S.selected);
  function linkColor(l) {
    const dim = S.selected && !isHi(l);
    if (l.type === "contradice") return dim ? "rgba(247,118,142,.25)" : css("--danger");
    if (l.kind === "source") return dim ? "rgba(115,218,202,.2)" : css("--source");
    return dim ? "rgba(140,150,165,.15)" : (l.status === "suggested" ? css("--warn") : "rgba(140,150,165,.6)");
  }
  function paintNode(n, ctx, scale) {
    const r = nodeR(n);
    const color = css("--" + (n.type in GLYPH ? n.type : "note")) || "#888";
    const dim = S.selected && S.selected !== n.id && !S.neighbors.has(n.id);
    ctx.globalAlpha = dim ? 0.25 : (n.status === "active" ? 1 : 0.55);
    ctx.beginPath();
    if (n.type === "source_doc") ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r); else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = color; ctx.fill();
    if (n.status !== "active") { ctx.setLineDash([2, 2]); ctx.strokeStyle = css("--muted"); ctx.lineWidth = 1 / scale; ctx.stroke(); ctx.setLineDash([]); }
    if (n.cited) { ctx.strokeStyle = css("--ok"); ctx.lineWidth = 2 / scale; ctx.beginPath(); ctx.arc(n.x, n.y, r + 3 / scale, 0, 2 * Math.PI); ctx.stroke(); }
    if (S.selected === n.id) { ctx.strokeStyle = css("--fg"); ctx.lineWidth = 2 / scale; ctx.beginPath(); ctx.arc(n.x, n.y, r + 2 / scale, 0, 2 * Math.PI); ctx.stroke(); }
    if (scale > 1.4) { ctx.fillStyle = "#0b1020"; ctx.font = `${Math.max(6, r * 1.1)}px ${css("--font")}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(GLYPH[n.type] || "•", n.x, n.y); }
    const showLabel = scale > 2.2 || S.selected === n.id || S.hover === n.id || S.neighbors.has(n.id) || (n.degree || 0) >= 6 && scale > 1.2;
    if (showLabel) {
      const fs = Math.max(10 / scale, 2.5); ctx.font = `${fs}px ${css("--font")}`; ctx.textAlign = "center"; ctx.textBaseline = "top";
      const label = n.title.length > 36 ? n.title.slice(0, 34) + "…" : n.title;
      const w = ctx.measureText(label).width;
      ctx.fillStyle = css("--bg2"); ctx.globalAlpha = dim ? 0.2 : 0.85; ctx.fillRect(n.x - w / 2 - 2, n.y + r + 1, w + 4, fs + 2);
      ctx.globalAlpha = dim ? 0.3 : 1; ctx.fillStyle = css("--fg"); ctx.fillText(label, n.x, n.y + r + 2);
    }
    ctx.globalAlpha = 1;
  }
  function renderLegend() {
    const lg = $("#legend"); lg.innerHTML = "";
    TYPES.forEach(t => { const d = document.createElement("div"); d.className = "lg"; d.innerHTML = `<i class="${t === "source_doc" ? "sq" : ""}" style="background:${css("--" + t)}"></i><span>${GLYPH[t]} ${TYPE_ES[t]}</span>`; lg.appendChild(d); });
  }
  function selectNode(id) {
    S.selected = id; S.neighbors = new Set();
    if (id) Graph.graphData().links.forEach(l => { if (l.source.id === id) S.neighbors.add(l.target.id); if (l.target.id === id) S.neighbors.add(l.source.id); });
  }
  function centerOn(id, zoom) {
    const n = Graph.graphData().nodes.find(x => x.id === id);
    if (n && n.x != null) { Graph.centerAt(n.x, n.y, reduced ? 0 : 500); if (zoom) Graph.zoom(3, reduced ? 0 : 500); }
  }
  let saveTimer;
  function saveLayout() { clearTimeout(saveTimer); saveTimer = setTimeout(() => api("/layout", { method: "PUT", body: JSON.stringify({ positions: S.positions }) }).catch(() => {}), 1200); }

  function filters() {
    return { type: $("#fType").value, project: $("#fProject").value, relation: $("#fRelation").value, tag: $("#fTag").value.trim().toLowerCase(),
             date: $("#fDate").value, hidden: $("#fHidden").checked, sources: $("#fSources").checked, orphans: $("#fOrphans").checked,
             showSources: $("#fShowSources").checked, depth: +$("#fDepth").value || 1, max: +$("#fMax").value || 60 };
  }
  function applyFilters(g) {
    const f = filters();
    let nodes = g.nodes.filter(n => (f.showSources || n.type !== "source_doc") && (f.hidden || n.status === "active" || n.status === "disputed" || n.type === "source_doc"));
    if (f.type) nodes = nodes.filter(n => n.type === f.type || n.type === "source_doc");
    if (f.project) nodes = nodes.filter(n => n.project === f.project);
    if (f.tag) nodes = nodes.filter(n => (n.tags || []).some(t => t.toLowerCase().includes(f.tag)));
    if (f.date) nodes = nodes.filter(n => n.updated >= f.date);
    if (f.sources) nodes = nodes.filter(n => n.has_sources);
    const ids = new Set(nodes.map(n => n.id));
    let edges = g.edges.filter(e => ids.has(e.source) && ids.has(e.target) && (!f.relation || e.type === f.relation));
    if (f.orphans) { const con = new Set(); edges.forEach(e => { con.add(e.source); con.add(e.target); }); nodes = nodes.filter(n => !con.has(n.id)); edges = []; }
    return { nodes, edges, note: nodes.length !== g.nodes.length ? `mostrando ${nodes.length} de ${g.nodes.length} nodos` : "" };
  }
  function draw(g, note) {
    const f = applyFilters(g);
    const prev = new Map(Graph.graphData().nodes.map(n => [n.id, n]));
    const nodes = f.nodes.map(n => { const p = prev.get(n.id); const pos = S.positions[n.id]; const o = { ...n }; if (p) { o.x = p.x; o.y = p.y; o.vx = p.vx; o.vy = p.vy; } else if (pos) { o.x = pos[0]; o.y = pos[1]; } return o; });
    const links = f.edges.map(e => ({ ...e }));
    Graph.graphData({ nodes, links });
    selectNode(S.selected);
    $("#graphNote").textContent = [note, f.note].filter(Boolean).join(" · ");
    renderList(nodes);
  }
  async function resync() {
    try {
      if (S.view === "local" && S.selected) { const f = filters(); const g = await api(`/memory/graph/local/${S.selected}?depth=${f.depth}&max_nodes=${f.max}&include_hidden=${f.hidden}`); S.graph = g; draw(g, `vista local de ${g.center.slice(-6)} · profundidad ${g.depth}${g.truncated ? " · TRUNCADA (límite de nodos)" : ""}`); }
      else if (S.view === "trace") { const g = S.traceTurn ? await api(`/memory/trace/${S.traceTurn}`) : await api("/memory/trace/last"); S.graph = g; draw(g, g.turn_id ? `recuerdos usados en ${g.turn_id}: ${g.nodes.length} recuperados, ${g.nodes.filter(n => n.cited).length} citados` : "aún no hay respuestas con traza"); }
      else { const g = await api(`/memory/graph?include_hidden=${filters().hidden}&include_sources=${filters().showSources}`); S.graph = g; S.memVersion = g.version; draw(g, g.nodes.length > 1500 ? `grafo grande (${g.nodes.length} nodos): usa filtros por tipo o proyecto` : ""); }
    } catch (e) { $("#graphNote").textContent = "error al cargar el grafo: " + e.message; }
    loadProjects();
  }
  function upsertNode(node) {
    if (!node) return;
    const idx = S.graph.nodes.findIndex(n => n.id === node.id);
    if (idx >= 0) S.graph.nodes[idx] = { ...S.graph.nodes[idx], ...node }; else S.graph.nodes.push(node);
    draw(S.graph, $("#graphNote").textContent);
  }
  function removeNode(id) {
    S.graph.nodes = S.graph.nodes.filter(n => n.id !== id);
    S.graph.edges = S.graph.edges.filter(e => e.source !== id && e.target !== id);
    delete S.positions[id];
    if (S.selected === id) { selectNode(null); closeReader(); }
    $("#searchResults").querySelectorAll(`[data-id="${id}"]`).forEach(el => el.remove());
    draw(S.graph, "");
  }
  function setView(v) {
    S.view = v; document.querySelectorAll(".vm").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    $("#listView").hidden = v !== "list"; $(".graphwrap").hidden = v === "list";
    if (v !== "trace") S.traceTurn = null;
    if (v === "list") { renderList(applyFilters(S.graph).nodes); } else resync();
  }
  document.querySelectorAll(".vm").forEach(b => b.onclick = () => setView(b.dataset.view));
  ["fType", "fProject", "fRelation", "fTag", "fDate", "fHidden", "fSources", "fOrphans", "fShowSources", "fDepth", "fMax"].forEach(id => $("#" + id).addEventListener("change", () => S.view === "list" ? renderList(applyFilters(S.graph).nodes) : resync()));
  $("#gFit").onclick = () => Graph.zoomToFit(reduced ? 0 : 400, 40);
  $("#gCenter").onclick = () => S.selected && centerOn(S.selected, true);
  $("#gPause").onclick = () => { S.paused = !S.paused; S.paused ? Graph.pauseAnimation() : Graph.resumeAnimation(); $("#gPause").setAttribute("aria-pressed", S.paused); };
  $("#gReset").onclick = () => { Graph.zoom(1, 300); Graph.centerAt(0, 0, 300); selectNode(null); };
  $("#gLayoutReset").onclick = () => { S.positions = {}; saveLayout(); Graph.d3ReheatSimulation(); };
  TYPES.filter(t => t !== "source_doc").forEach(t => $("#fType").appendChild(Object.assign(document.createElement("option"), { value: t, textContent: TYPE_ES[t] })));
  async function loadProjects() {
    try { const r = await api("/memory/list"); const sel = $("#fProject"); const cur = sel.value; sel.innerHTML = '<option value="">todos</option>'; r.projects.forEach(p => sel.appendChild(Object.assign(document.createElement("option"), { value: p.slug, textContent: `${p.title} (${p.count})` }))); sel.value = cur; } catch (e) {}
  }

  // ------------------------------------------------------------------ lista accesible
  function renderList(nodes) {
    const ul = $("#memList"); ul.innerHTML = ""; S.listItems = nodes.slice().sort((a, b) => a.title.localeCompare(b.title));
    S.listItems.forEach((n, i) => {
      const li = document.createElement("li"); li.setAttribute("role", "option"); li.dataset.id = n.id; li.tabIndex = -1;
      li.innerHTML = `<span class="pill" style="border-color:${css("--" + n.type)}">${GLYPH[n.type]} ${TYPE_ES[n.type] || n.type}</span><span>${esc(n.title)}<div class="small muted">${esc(n.summary || "")}</div></span><span class="small muted">${n.degree || 0} conex. · ${n.status}</span>`;
      li.onclick = () => { S.listIdx = i; openMemory(n.id); };
      ul.appendChild(li);
    });
  }
  $("#memList").addEventListener("keydown", (e) => {
    const items = [...$("#memList").children]; if (!items.length) return;
    if (e.key === "ArrowDown") { S.listIdx = Math.min(items.length - 1, S.listIdx + 1); } else if (e.key === "ArrowUp") { S.listIdx = Math.max(0, S.listIdx - 1); }
    else if (e.key === "Enter" && S.listIdx >= 0) { openMemory(items[S.listIdx].dataset.id); return; } else return;
    e.preventDefault(); items.forEach((li, i) => li.setAttribute("aria-selected", i === S.listIdx)); items[S.listIdx].focus();
  });

  // ------------------------------------------------------------------ búsqueda
  let searchTimer;
  $("#searchInput").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 200); });
  $("#searchInput").addEventListener("keydown", (e) => {
    const items = [...$("#searchResults").children]; if (!items.length) return;
    let idx = items.findIndex(x => x.getAttribute("aria-selected") === "true");
    if (e.key === "ArrowDown") idx = Math.min(items.length - 1, idx + 1); else if (e.key === "ArrowUp") idx = Math.max(0, idx - 1);
    else if (e.key === "Enter") { if (idx >= 0) items[idx].click(); return; } else if (e.key === "Escape") { $("#searchResults").innerHTML = ""; return; } else return;
    e.preventDefault(); items.forEach((el, i) => el.setAttribute("aria-selected", i === idx));
  });
  async function runSearch() {
    const q = $("#searchInput").value.trim(); const box = $("#searchResults"); box.innerHTML = "";
    if (!q) return;
    try {
      const f = filters(); const r = await api(`/memory/search?q=${encodeURIComponent(q)}&include_hidden=${f.hidden}&types=${encodeURIComponent(f.type)}`);
      r.hits.forEach(h => { const d = document.createElement("div"); d.className = "result"; d.setAttribute("role", "option"); d.dataset.id = h.id; d.innerHTML = `<span class="pill">${GLYPH[h.type] || ""} ${TYPE_ES[h.type] || h.type}</span><span>${esc(h.title)} <span class="muted small">${esc(h.snippet)}</span></span>`; d.onclick = () => { openMemory(h.id); selectNode(h.id); if (S.view !== "list") { if (S.view !== "global" && !Graph.graphData().nodes.some(n => n.id === h.id)) setView("global"); centerOn(h.id, true); } }; box.appendChild(d); });
      if (!r.hits.length) box.innerHTML = '<div class="result muted">sin resultados</div>';
    } catch (e) { box.innerHTML = `<div class="result muted">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ lector
  const reader = $("#reader");
  function renderMarkdown(md) {
    md = md.replace(/\[\[(mem_[0-9]{8}_[a-f0-9]{6})(?:\|([^\]]*))?\]\]/g, (m, id, label) => `[${label || id}](#mem:${id})`)
           .replace(/\[mem:(mem_[0-9]{8}_[a-f0-9]{6})\]/g, (m, id) => `[${id.slice(-6)}](#mem:${id})`);
    const html = marked.parse(md, { async: false, gfm: true, breaks: true });
    DOMPurify.removeAllHooks();
    DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") { const h = node.getAttribute("href") || ""; if (!h.startsWith("#mem:")) { node.removeAttribute("href"); node.setAttribute("title", h); } node.removeAttribute("target"); }
    });
    return DOMPurify.sanitize(html, { ALLOWED_TAGS: ["p", "br", "strong", "em", "b", "i", "u", "s", "code", "pre", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "a", "table", "thead", "tbody", "tr", "th", "td", "hr", "span", "del", "sup", "sub"],
                                    ALLOWED_ATTR: ["href", "title"], ALLOW_DATA_ATTR: false, FORBID_TAGS: ["img", "svg", "iframe", "script", "style", "video", "audio", "object", "embed", "link", "meta", "form", "input", "math"] });
  }
  $("#rBody").addEventListener("click", (e) => { const a = e.target.closest("a[href^='#mem:']"); if (a) { e.preventDefault(); openMemory(a.getAttribute("href").slice(5)); } });
  async function openMemory(id, silent) {
    if (id.startsWith("src_")) { return showSource(id, ""); }
    let d; try { d = await api(`/memory/${id}`); } catch (e) { addMsg("system", "No se pudo abrir el recuerdo: " + e.message); return; }
    const m = d.memory; S.selected = id; S.current = d;
    $("#rType").textContent = `${GLYPH[m.type]} ${TYPE_ES[m.type] || m.type}`; $("#rType").style.borderColor = css("--" + m.type);
    $("#rStatus").textContent = m.status; $("#rStatus").className = "pill " + (m.status === "active" ? "good" : "bad");
    $("#rProv").textContent = { user_statement: "dicho por ti", source_extraction: "extraído de fuente", assistant_inference: "inferencia del asistente", proposal: "propuesta sin confirmar" }[m.provenance] || m.provenance;
    $("#rTitle").textContent = m.title;
    $("#rDates").textContent = `${m.id} · creado ${m.created.slice(0, 10)} · actualizado ${m.updated.slice(0, 10)}` + (m.valid_from ? ` · válido desde ${m.valid_from}` : "") + (m.valid_until ? ` · hasta ${m.valid_until}` : "") + (m.superseded_by ? ` · reemplazado por ${m.superseded_by}` : "") + (m.project ? ` · proyecto ${m.project}` : "") + (m.tags && m.tags.length ? ` · etiquetas: ${m.tags.join(", ")}` : "") + ` · sensibilidad ${m.sensitivity}`;
    $("#rBody").innerHTML = renderMarkdown(d.body || "");
    const src = $("#rSources"); src.innerHTML = "";
    d.sources.forEach(s => { const li = document.createElement("li"); li.innerHTML = `<a data-src="${esc(s.source_id)}">${esc(s.title)}</a> <span class="muted small">${esc(s.locator || "")}${s.quote ? " · «" + esc(s.quote) + "»" : ""}</span>`; li.querySelector("a").onclick = () => showSource(s.source_id, s.quote || ""); src.appendChild(li); });
    if (!d.sources.length) src.innerHTML = '<li class="muted small">sin fuentes (procedencia: ' + esc($("#rProv").textContent) + ")</li>";
    const rel = $("#rRelated"); rel.innerHTML = "";
    d.related.forEach(r => { const li = document.createElement("li"); li.innerHTML = `<span class="small muted">${esc(r.type)}${r.status === "suggested" ? " (sugerida" + (r.rationale ? ": " + esc(r.rationale) : "") + ")" : ""} → </span><a>${esc(r.title)}</a> `; li.querySelector("a").onclick = () => openMemory(r.target);
      if (r.status === "suggested") { const ok = document.createElement("button"); ok.className = "btn small"; ok.textContent = "confirmar"; ok.onclick = () => post("/memory/relations/confirm", { source: id, target: r.target, type: r.type }).then(() => openMemory(id)); const no = document.createElement("button"); no.className = "btn small"; no.textContent = "descartar"; no.onclick = () => post("/memory/relations/delete", { source: id, target: r.target, type: r.type }).then(() => openMemory(id)); li.append(ok, " ", no); }
      rel.appendChild(li); });
    d.backlinks.forEach(b => { const li = document.createElement("li"); li.innerHTML = `<span class="small muted">← enlazado desde </span><a>${esc(b.title)}</a>`; li.querySelector("a").onclick = () => openMemory(b.id); rel.appendChild(li); });
    if (!d.related.length && !d.backlinks.length) rel.innerHTML = '<li class="muted small">sin conexiones</li>';
    const j = $("#rJournal"); j.innerHTML = "";
    d.journal.slice().reverse().forEach(e => { const li = document.createElement("li"); li.textContent = `${e.ts.slice(0, 16).replace("T", " ")} · ${e.op} · ${e.actor}${e.reason ? " · " + e.reason : ""}${e.details && e.details.fields ? " · campos: " + e.details.fields.join(", ") : ""}`; j.appendChild(li); });
    if (d.versions.length) { const li = document.createElement("li"); li.className = "muted"; li.textContent = `${d.versions.length} versión(es) anterior(es) archivadas`; j.appendChild(li); }
    $("#rDialog").hidden = true;
    reader.hidden = false; if (!silent) $("#rClose").focus();
  }
  function closeReader() { reader.hidden = true; }
  $("#rClose").onclick = closeReader;
  $("#rAsk").onclick = () => S.selected && post(`/memory/ask/${S.selected}`).catch(e => addMsg("system", e.message));
  $("#rLocal").onclick = () => { switchTab("memory"); setView("local"); };
  $("#rSource").onclick = () => { const s = S.current && S.current.sources[0]; if (s) showSource(s.source_id, s.quote || ""); else dialog("<p>Este recuerdo no tiene fuentes documentales; su procedencia es: " + esc($("#rProv").textContent) + ".</p>"); };
  $("#rCorrect").onclick = () => {
    const d = S.current; if (!d) return;
    dialog(`<h4>Corregir «${esc(d.memory.title)}»</h4>
      <label>Modo <select id="cMode"><option value="supersede">reemplazar (cambio temporal: el anterior queda como reemplazado)</option><option value="edit">editar (error de redacción, conserva id)</option><option value="dispute">marcar como disputado</option><option value="archive">archivar</option></select></label>
      <label>Título nuevo <input id="cTitle" value="${esc(d.memory.title)}"></label>
      <label>Contenido nuevo <textarea id="cBody" rows="5">${esc(d.body)}</textarea></label>
      <label>Motivo <input id="cReason" placeholder="p. ej. el usuario corrigió la fecha"></label>
      <div class="row"><button class="btn small accent" id="cOk">Aplicar</button> <button class="btn small" id="cNo">Cancelar</button></div>`);
    $("#cNo").onclick = () => $("#rDialog").hidden = true;
    $("#cOk").onclick = async () => { try { const r = await post(`/memory/${d.memory.id}/correct`, { mode: $("#cMode").value, new_content: $("#cBody").value, new_title: $("#cTitle").value, reason: $("#cReason").value || "corrección desde el panel" }); addMsg("system", "Corrección aplicada: " + r.node.id); openMemory(r.node.id); } catch (e) { addMsg("system", "No se aplicó: " + e.message); } };
  };
  $("#rForget").onclick = async () => {
    const d = S.current; if (!d) return;
    let plan; try { plan = await post(`/memory/${d.memory.id}/forget/plan`); } catch (e) { return addMsg("system", e.message); }
    dialog(`<h4>Olvidar «${esc(plan.title)}»</h4><p>Se eliminará:</p><ul>
      <li>la página ${esc(plan.page)}</li><li>${plan.versions.length} versión(es) archivada(s)</li><li>su entrada en el índice, la selección y la caché visual</li>
      ${plan.referencing.map(r => `<li>referencias en «${esc(r.title)}» (${esc(r.how)})</li>`).join("")}
      ${plan.sources.map(s => `<li>fuente exclusiva «${esc(s.title)}» — <label><input type="checkbox" class="delsrc"> borrar también el archivo original</label></li>`).join("")}</ul>
      <p class="small muted">${plan.notes.map(esc).join("<br>")}</p>
      <div class="row"><button class="btn small danger" id="fOk">Olvidar definitivamente</button> <button class="btn small" id="fNo">Cancelar</button></div>`);
    $("#fNo").onclick = () => $("#rDialog").hidden = true;
    $("#fOk").onclick = async () => { try { const r = await post(`/memory/${d.memory.id}/forget`, { confirm: true, delete_sources: [...document.querySelectorAll(".delsrc")].some(c => c.checked) }); addMsg("system", `Olvidado ${r.memory_id}: ${r.removed_files.length} archivos, ${r.updated_pages.length} páginas actualizadas. Se conserva: ${r.kept.join("; ")}`); closeReader(); } catch (e) { addMsg("system", "No se olvidó: " + e.message); } };
  };
  function dialog(html) { const d = $("#rDialog"); d.innerHTML = html; d.hidden = false; }
  async function showSource(sid, quote) {
    try { const r = await api(`/memory/source/${sid}?quote=${encodeURIComponent(quote || "")}`); const s = r.source;
      let ex = esc(r.excerpt); if (quote && r.found) ex = ex.replace(esc(quote), `<mark>${esc(quote)}</mark>`);
      if (reader.hidden) { reader.hidden = false; $("#rTitle").textContent = s.title; $("#rBody").innerHTML = ""; $("#rSources").innerHTML = ""; $("#rRelated").innerHTML = ""; $("#rJournal").innerHTML = ""; $("#rDates").textContent = s.id; $("#rType").textContent = "▣ documento fuente"; $("#rStatus").textContent = ""; $("#rProv").textContent = ""; }
      dialog(`<h4>Fuente «${esc(s.title)}»</h4><div class="small muted">${esc(s.original_name)} · ${esc(s.media_type)} · ${s.size_bytes} bytes · importada ${esc(s.imported_at.slice(0, 10))} · sha256 ${esc(s.sha256.slice(0, 12))}… · ${s.derived_memories.length} recuerdos derivados${s.needs_ocr ? " · requiere OCR" : ""}</div><pre style="white-space:pre-wrap;max-height:300px;overflow:auto">${ex}</pre>${quote && !r.found ? '<p class="small">La cita no se encontró literalmente en el texto extraído.</p>' : ""}<button class="btn small" id="sNo">Cerrar</button>`);
      $("#sNo").onclick = () => $("#rDialog").hidden = true;
    } catch (e) { addMsg("system", e.message); }
  }

  // ------------------------------------------------------------------ trazas, historial, config
  async function loadTraces() {
    try { const h = await api("/history"); const ul = $("#traceList"); ul.innerHTML = "";
      h.items.filter(i => i.role === "assistant").reverse().forEach(i => { const li = document.createElement("li"); li.innerHTML = `<a>${esc(i.turn_id)}</a> · ${esc((i.text || "").slice(0, 90))} <span class="small muted">citados: ${(i.cited || []).length}</span>`; li.querySelector("a").onclick = () => showTrace(i.turn_id); ul.appendChild(li); });
    } catch (e) {}
  }
  function showTrace(turnId) { switchTab("memory"); S.traceTurn = turnId; S.view = "trace"; document.querySelectorAll(".vm").forEach(b => b.classList.toggle("active", b.dataset.view === "trace")); $("#listView").hidden = true; $(".graphwrap").hidden = false; resync(); }
  async function loadJournal() {
    try { const r = await api("/memory/journal?limit=100"); const ul = $("#journalList"); ul.innerHTML = "";
      r.entries.reverse().forEach(e => { const li = document.createElement("li"); li.innerHTML = `<span class="small muted">${esc(e.ts.slice(0, 16).replace("T", " "))}</span> <strong>${esc(e.op)}</strong> ${e.id ? `<a>${esc(e.title || e.id)}</a>` : esc(e.title || "")} <span class="small muted">${esc(e.reason || "")}</span>`; const a = li.querySelector("a"); if (a) a.onclick = () => openMemory(e.id); ul.appendChild(li); });
    } catch (e) {}
  }
  $("#btnLint").onclick = async () => { const r = await api("/memory/lint"); const o = $("#lintOut"); o.innerHTML = r.issues.length ? "" : "<div>Sin incidencias.</div>"; r.issues.forEach(i => { const d = document.createElement("div"); d.innerHTML = `[${esc(i.severity)}] ${esc(i.kind)}: ${esc(i.message)}` + (i.id ? ` <a class="chip" data-id="${esc(i.id)}">abrir</a>` : ""); const a = d.querySelector("a"); if (a) a.onclick = () => openMemory(i.id); o.appendChild(d); }); };
  $("#btnRebuild").onclick = async () => { const r = await post("/memory/rebuild"); $("#lintOut").textContent = `índice reconstruido: ${r.pages} páginas`; };
  async function loadConfig() {
    try { const c = await api("/config"); const f = $("#configForm");
      const dev = await api("/audio/devices").catch(() => ({ devices: [], voices: [] }));
      const vs = f.elements["tts.voice"]; vs.innerHTML = ""; dev.voices.forEach(v => vs.appendChild(Object.assign(document.createElement("option"), { value: v.name, textContent: `${v.name} (${v.locale})` })));
      const ds = f.elements["audio.input_device"]; ds.innerHTML = '<option value="">por defecto</option>'; dev.devices.filter(d => d.inputs > 0).forEach(d => ds.appendChild(Object.assign(document.createElement("option"), { value: d.name, textContent: d.name })));
      f.elements["assistant.display_name"].value = c.assistant.display_name; f.elements["wake_word.sensitivity"].value = c.wake_word.sensitivity;
      vs.value = c.tts.voice; f.elements["tts.rate"].value = c.tts.rate; f.elements["tts.enabled"].checked = c.tts.enabled; ds.value = c.audio.input_device || "";
      f.elements["audio.end_silence_ms"].value = c.audio.end_silence_ms; f.elements["memory.auto_save_preferences"].checked = c.memory.auto_save_preferences; f.elements["memory.save_conversation_summaries"].checked = c.memory.save_conversation_summaries;
      $("#wakeInfo").textContent = `proveedor ${c.wake_word.provider}, palabra «${c.wake_word.keyword_label}», modelo ${c.wake_word.model_path}, idioma ${c.wake_word.language}. Para otra palabra hace falta otro modelo acústico (ver README).`;
      S.configSnapshot = c;
    } catch (e) {}
  }
  $("#configForm").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target; const out = []; const c = S.configSnapshot || {};
    const get = (k) => k.split(".").reduce((o, p) => (o || {})[p], c);
    for (const el of f.elements) { if (!el.name) continue; let v = el.type === "checkbox" ? el.checked : el.value; if (el.type === "number") v = Number(v); if (el.name === "audio.input_device" && v === "") v = null;
      if (JSON.stringify(v) === JSON.stringify(get(el.name) ?? null)) continue;
      try { const r = await post("/config", { key: el.name, value: v }); out.push(`${el.name} guardado` + (r.note ? ". " + r.note : "")); } catch (err) { out.push(`${el.name}: ${err.message}`); } }
    $("#configOut").textContent = out.join(" · ") || "sin cambios"; loadConfig(); refreshState();
  };

  // ------------------------------------------------------------------ pestañas, tema, teclado
  function switchTab(name) { document.querySelectorAll(".tab").forEach(t => { const on = t.dataset.tab === name; t.classList.toggle("active", on); t.setAttribute("aria-selected", on); }); document.querySelectorAll(".tabpanel").forEach(p => p.hidden = p.id !== "tab-" + name); if (name === "journal") loadJournal(); if (name === "config") loadConfig(); if (name === "traces") loadTraces(); if (name === "memory" && Graph) Graph.width($("#graph").clientWidth).height($("#graph").clientHeight); }
  document.querySelectorAll(".tab").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
  $("#btnTheme").onclick = () => { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = t; try { localStorage.setItem("jarvis-theme", t); } catch (e) {} renderLegend(); };
  try { const t = localStorage.getItem("jarvis-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (!reader.hidden) closeReader(); else post("/cancel").catch(() => {}); }
    if (e.key === "/" && document.activeElement !== $("#chatInput") && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") { e.preventDefault(); switchTab("memory"); $("#searchInput").focus(); }
  });

  // ------------------------------------------------------------------ arranque
  (async () => {
    initGraph();
    try { S.positions = (await api("/layout")).positions || {}; } catch (e) {}
    await refreshState();
    await resync();
    if (!reduced) setTimeout(() => Graph.zoomToFit(400, 40), 800);
    api("/history").then(h => h.items.forEach(i => { if (i.role === "user") addMsg("user", i.text); else if (i.role === "assistant") addMsg("assistant", i.text, metaRow({ cited: i.cited || [], turn_id: i.turn_id, error: i.error, cancelled: i.cancelled })); else addMsg("system", i.text); })).catch(() => {});
    refreshApprovals(); loadTraces(); connectEvents();
  })();
})();
