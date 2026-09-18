import SwiftUI

struct DashboardView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        NavigationSplitView {
            List(DashboardTab.allCases, selection: Binding(get: { Optional(model.tab) }, set: { if let t = $0 { model.tab = t; Task { await model.refresh(all: false) } } })) { tab in
                Label {
                    HStack {
                        Text(tab.title)
                        Spacer()
                        badge(for: tab)
                    }
                } icon: { Image(systemName: tab.symbol) }
                .tag(tab)
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
            .safeAreaInset(edge: .bottom) { footer }
        } detail: {
            Group {
                switch model.tab {
                case .sessions: SessionsView(model: model)
                case .runs: RunsView(model: model)
                case .specs: SpecsView(model: model)
                case .projects: ProjectsView(model: model)
                case .usage: UsageView(model: model)
                case .memory: MemoryView(model: model)
                case .notices: NoticesView(model: model)
                }
            }
            .frame(minWidth: 640)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { Task { await model.refresh(all: true) } } label: { Image(systemName: "arrow.clockwise") }
                    .help("Actualizar")
            }
        }
        .overlay(alignment: .bottom) {
            if let e = model.lastError {
                Text(e).font(.caption).padding(8).background(.red.opacity(0.85)).foregroundStyle(.white).clipShape(Capsule()).padding(8)
            }
        }
    }

    @ViewBuilder func badge(for tab: DashboardTab) -> some View {
        switch tab {
        case .sessions where !model.needsYou.isEmpty: Pill(text: "\(model.needsYou.count)", color: .red)
        case .runs where (model.foreman["active_runs"].int ?? 0) > 0: Pill(text: "\(model.foreman["active_runs"].int ?? 0)", color: .blue)
        default: EmptyView()
        }
    }

    var footer: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Circle().fill(model.foreman["watching"].bool ? Color.green : Color.gray).frame(width: 7, height: 7)
                Text(model.foreman["watching"].bool ? "Vigilando sesiones" : "Vigilancia apagada").font(.caption)
            }
            Text("\(model.foreman["sessions"].int ?? 0) conversaciones · \(model.foreman["active_runs"].int ?? 0) runs activos")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(10)
    }
}

struct Pill: View {
    let text: String
    var color: Color = .secondary
    var body: some View {
        Text(text).font(.caption2.weight(.semibold)).padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.18)).foregroundStyle(color).clipShape(Capsule())
    }
}

func stateColor(_ state: String) -> Color {
    switch state {
    case "needs_you", "failed", "timed_out", "awaiting": return .red
    case "working", "running", "building": return .blue
    case "queued", "planning": return .orange
    case "succeeded", "review", "approved": return .green
    case "idle", "shell", "superseded": return .yellow
    case "gone", "cancelled": return .gray
    default: return .secondary
    }
}

struct EmptyHint: View {
    let text: String
    var body: some View {
        Text(text).foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct SectionHeader: View {
    let text: String
    var body: some View { Text(text.uppercased()).font(.caption.weight(.bold)).foregroundStyle(.secondary).padding(.top, 6) }
}

struct KeyValue: View {
    let k: String
    let v: String
    var body: some View {
        HStack(alignment: .top) {
            Text(k).foregroundStyle(.secondary).frame(width: 110, alignment: .trailing)
            Text(v).textSelection(.enabled)
            Spacer()
        }.font(.callout)
    }
}

// MARK: - Sesiones
struct SessionsView: View {
    @ObservedObject var model: DashboardModel
    @State private var message = ""
    @State private var outcome = ""

    var grouped: [(String, [JSON])] {
        let live = model.sessions.filter { $0["state"].str != "gone" }
        let groups = Dictionary(grouping: live, by: { $0["project"].str })
        return groups.keys.sorted().map { ($0, groups[$0]!) }
    }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                if !model.needsYou.isEmpty {
                    HStack {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                        Text("\(model.needsYou.count) te esperan").font(.headline)
                        Spacer()
                    }.padding(10).background(Color.red.opacity(0.12))
                }
                List(selection: $model.selectedSession) {
                    ForEach(grouped, id: \.0) { project, items in
                        Section(project) {
                            ForEach(items, id: \.["session_id"].str) { s in
                                HStack(spacing: 8) {
                                    Circle().fill(stateColor(s["state"].str)).frame(width: 8, height: 8)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(s["voice_name"].str).font(.body.weight(.medium))
                                        Text(s["summary"].string ?? "sin título").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                    Spacer()
                                    if s["primary"].bool { Pill(text: "principal") }
                                    Pill(text: s["state_label"].str, color: stateColor(s["state"].str))
                                }
                                .tag(s["session_id"].str)
                            }
                        }
                    }
                }
                .onChange(of: model.selectedSession) { _ in outcome = ""; Task { await model.loadSessionDetail() } }
                .overlay { if model.sessions.isEmpty { EmptyHint(text: "No hay conversaciones de Claude Code en marcha.") } }
            }.frame(minWidth: 320)
            detail.frame(minWidth: 380)
        }
        .task { await model.loadSessions() }
    }

    var detail: some View {
        let s = model.sessionDetail["session"]
        return Group {
            if model.selectedSession == nil || s.isNull {
                EmptyHint(text: "Elige una conversación para ver dónde está y hablar con ella.")
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(s["voice_name"].str).font(.title2.weight(.semibold))
                        if s["state"].str == "needs_you" {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Te espera \(relativeAge(s["since"].double).replacingOccurrences(of: "hace ", with: "desde hace ")) — \(s["needs_label"].string ?? "se detuvo esperándote")")
                                    .font(.headline)
                                if s["needs_a_human_hand"].bool {
                                    Text("Solo una tecla tuya la desbloquea (funciona si corre en Terminal.app).").font(.caption)
                                } else if let t = s["last_text"].string, !t.isEmpty {
                                    Text("Preguntó: \(t)").font(.callout).textSelection(.enabled)
                                }
                            }.padding(10).frame(maxWidth: .infinity, alignment: .leading).background(Color.red.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        KeyValue(k: "Estado", v: "\(s["state_label"].str) · \(relativeAge(s["since"].double))")
                        KeyValue(k: "Proyecto", v: s["project"].str)
                        KeyValue(k: "Carpeta", v: s["cwd"].str)
                        KeyValue(k: "Origen", v: "\(s["origin"].str) · \(s["steerable"].bool ? "se le puede escribir" : "sin canal de entrada")")
                        if let t = s["title"].string { KeyValue(k: "Título", v: t) }
                        if let p = s["last_prompt"].string { KeyValue(k: "Última petición", v: p) }
                        if !s["recent_tools"].strings.isEmpty { KeyValue(k: "Herramientas", v: s["recent_tools"].strings.joined(separator: ", ")) }
                        if (s["agents_seen"].int ?? 0) > 0 { KeyValue(k: "Subagentes", v: "\(s["agents_seen"].int ?? 0) (\(s["agents_active"].int ?? 0) activos)") }

                        SectionHeader(text: "Responder")
                        HStack {
                            TextField("Mensaje para la sesión (llega como un turno nuevo)", text: $message).textFieldStyle(.roundedBorder)
                                .onSubmit { send() }
                            Button("Enviar") { send() }.disabled(message.trimmingCharacters(in: .whitespaces).isEmpty || !s["steerable"].bool)
                        }
                        HStack {
                            Text("Tecla en Terminal:").font(.caption).foregroundStyle(.secondary)
                            ForEach(["Intro", "Escape", "1", "2", "3"], id: \.self) { k in
                                Button(k) { Task { outcome = describe(await model.answer(k == "Intro" ? "return" : (k == "Escape" ? "escape" : k))) } }
                            }
                        }
                        if !outcome.isEmpty { Text(outcome).font(.caption).foregroundStyle(.secondary) }

                        SectionHeader(text: "Últimos mensajes")
                        ForEach(Array(model.sessionDetail["transcript"].array.enumerated()), id: \.offset) { _, m in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(m["role"].str == "user" ? "Usuario" : "Sesión").font(.caption.weight(.bold)).foregroundStyle(m["role"].str == "user" ? .blue : .secondary)
                                if !m["text"].str.isEmpty { Text(m["text"].str).font(.callout).textSelection(.enabled) }
                                if !m["tools"].strings.isEmpty { Text(m["tools"].strings.joined(separator: " · ")).font(.caption2).foregroundStyle(.secondary) }
                            }.padding(8).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                    }.padding(14)
                }
            }
        }
    }

    func describe(_ o: String) -> String {
        ["sent": "Hecho.", "not_live": "La sesión ya no está viva.", "not_found": "No está en una pestaña de Terminal.app.",
         "not_permitted": "Falta el permiso de Accesibilidad para Jarvis.app.", "no_tty": "Ese proceso no tiene terminal.",
         "failed": "No se pudo.", "refused": "Mensaje vacío.", "bad_key": "Tecla no permitida."][o] ?? o
    }

    func send() {
        let text = message
        message = ""
        Task { outcome = describe(await model.steer(text)) }
    }
}

// MARK: - Runs
struct RunsView: View {
    @ObservedObject var model: DashboardModel
    @State private var showNew = false
    @State private var newProject = ""
    @State private var newPrompt = ""

    var attention: [JSON] { model.runs.filter { ["failed", "timed_out"].contains($0["status"].str) && ($0["ended_at"].double ?? 0) > Date().timeIntervalSince1970 - 86400 } }
    var active: [JSON] { model.runs.filter { ["queued", "running"].contains($0["status"].str) } }
    var history: [JSON] { model.runs.filter { !["queued", "running"].contains($0["status"].str) } }

    var body: some View {
        HSplitView {
            List(selection: $model.selectedRun) {
                if !attention.isEmpty { Section("Necesitan atención") { ForEach(attention, id: \.["id"].str) { row($0) } } }
                Section("Activos") {
                    if active.isEmpty { Text("Nada en marcha").foregroundStyle(.secondary) }
                    ForEach(active, id: \.["id"].str) { row($0) }
                }
                Section("Historial") { ForEach(history, id: \.["id"].str) { row($0) } }
            }
            .frame(minWidth: 380)
            .onChange(of: model.selectedRun) { _ in Task { await model.loadRunDetail() } }
            .toolbar {
                ToolbarItem { Button { showNew.toggle() } label: { Label("Nuevo run", systemImage: "plus") } }
            }
            .sheet(isPresented: $showNew) { newRunSheet }
            detail.frame(minWidth: 360)
        }
        .task { await model.loadRuns(); await model.loadProjects() }
    }

    func label(_ r: JSON) -> String {
        ["queued": "en cola", "running": "en marcha", "succeeded": "terminado", "failed": "falló", "timed_out": "sin tiempo", "cancelled": "cancelado"][r["status"].str] ?? r["status"].str
    }

    func gist(_ r: JSON) -> String {
        let p = r["prompt"].str
        if p.hasPrefix("[Construcción larga") { return "Construcción: " + (p.components(separatedBy: "\n").first(where: { $0.contains("docs/specs/") })?.trimmingCharacters(in: .whitespaces) ?? "spec") }
        return p.replacingOccurrences(of: "\n", with: " ")
    }

    func row(_ r: JSON) -> some View {
        HStack(spacing: 8) {
            Circle().fill(stateColor(r["status"].str)).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                HStack { Text(r["project_name"].str).font(.body.weight(.medium)); if r["kind"].str == "build" { Pill(text: "build", color: .purple) } }
                Text(gist(r)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if r["status"].str == "running", !r["summary"].str.isEmpty { Text(r["summary"].str).font(.caption2).foregroundStyle(.blue).lineLimit(1) }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Pill(text: label(r), color: stateColor(r["status"].str))
                Text("\(duration(r["started_at"].double, r["ended_at"].double)) · \(tokens((r["input_tokens"].int ?? 0) + (r["output_tokens"].int ?? 0))) tok").font(.caption2).foregroundStyle(.secondary)
            }
        }.tag(r["id"].str)
    }

    var detail: some View {
        let r = model.runDetail
        return Group {
            if model.selectedRun == nil || r.isNull { EmptyHint(text: "Elige un run para ver su prompt y su transcripción en vivo.") } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(r["project_name"].str).font(.title2.weight(.semibold))
                            Pill(text: label(r), color: stateColor(r["status"].str))
                            Spacer()
                            if ["queued", "running"].contains(r["status"].str) { Button("Cancelar") { Task { await model.cancelRun(r["id"].str) } } }
                        }
                        KeyValue(k: "Modelo", v: r["model"].string.flatMap { $0.isEmpty ? nil : $0 } ?? r["requested_model"].str)
                        KeyValue(k: "Duración", v: duration(r["started_at"].double, r["ended_at"].double) + " · empezó " + clockTime(r["started_at"].double))
                        KeyValue(k: "Tokens", v: "entrada \(tokens(r["input_tokens"].int)) · salida \(tokens(r["output_tokens"].int)) · caché \(tokens(r["cache_read_tokens"].int))")
                        KeyValue(k: "Equiv. coste", v: String(format: "$%.4f (informativo; va contra la suscripción)", r["cost_usd"].double ?? 0))
                        KeyValue(k: "Origen", v: "\(r["origin"].str) · \(r["kind"].str) · pid \(r["pid"].int ?? 0)")
                        if !r["error"].str.isEmpty { Text(r["error"].str).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
                        SectionHeader(text: "Prompt")
                        Text(r["prompt"].str).font(.callout).textSelection(.enabled).padding(8).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 6))
                        if !r["result_text"].str.isEmpty {
                            SectionHeader(text: "Resultado")
                            Text(r["result_text"].str).font(.callout).textSelection(.enabled)
                        }
                        SectionHeader(text: "Transcripción (\(model.runEvents.count) eventos)")
                        ForEach(model.runEvents.filter { !$0["text"].str.isEmpty }, id: \.["seq"].int) { e in
                            HStack(alignment: .top, spacing: 8) {
                                Text(e["kind"].str).font(.caption2.monospaced()).foregroundStyle(.secondary).frame(width: 64, alignment: .trailing)
                                Text(e["text"].str).font(.callout).textSelection(.enabled)
                            }
                        }
                    }.padding(14)
                }
            }
        }
    }

    var newRunSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Nuevo run").font(.headline)
            Picker("Proyecto", selection: $newProject) {
                Text("—").tag("")
                ForEach(model.projects, id: \.["path"].str) { Text($0["name"].str).tag($0["path"].str) }
            }
            TextEditor(text: $newPrompt).frame(height: 120).font(.body).border(Color.secondary.opacity(0.3))
            Text("Una petición concreta y corta. Corre sin permisos interactivos en la carpeta del proyecto.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancelar") { showNew = false }
                Button("Lanzar") { Task { if await model.createRun(project: newProject, prompt: newPrompt) { newPrompt = ""; showNew = false } } }
                    .keyboardShortcut(.defaultAction).disabled(newProject.isEmpty || newPrompt.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }.padding(16).frame(width: 480)
    }
}

// MARK: - Specs
struct SpecsView: View {
    @ObservedObject var model: DashboardModel
    @State private var editing: Int?
    @State private var editBody = ""
    @State private var editTitle = ""
    @State private var buildMsg = ""

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 0) {
                Picker("Proyecto", selection: Binding(get: { model.selectedProject ?? "" }, set: { model.selectedProject = $0.isEmpty ? nil : $0; model.selectedDocument = nil; Task { await model.loadDocuments() } })) {
                    ForEach(model.projects, id: \.["path"].str) { Text($0["name"].str).tag($0["path"].str) }
                }.padding(10)
                List(selection: $model.selectedDocument) {
                    ForEach(model.documents, id: \.["path"].str) { d in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(d["title"].str).font(.body.weight(.medium)).lineLimit(1)
                                Spacer()
                                Pill(text: d["kind"].str == "plan" ? "plan" : "spec", color: d["kind"].str == "plan" ? .purple : .blue)
                            }
                            HStack {
                                if d["kind"].str == "spec" { Pill(text: approvalLabel(d["approval"]["state"].str), color: stateColor(d["approval"]["state"].str)) }
                                if !d["progress"].isNull { Pill(text: "\(d["progress"]["done"].int ?? 0)/\(d["progress"]["total"].int ?? 0) tareas", color: .green) }
                                Text(relativeAge(d["modified"].double)).font(.caption2).foregroundStyle(.secondary)
                            }
                        }.tag(d["path"].str)
                    }
                }
                .onChange(of: model.selectedDocument) { _ in editing = nil; Task { await model.loadDocument() } }
                .overlay { if model.documents.isEmpty { EmptyHint(text: "Este proyecto no tiene specs ni planes todavía.\nPídele a Jarvis que escriba el diseño acordado.") } }
            }.frame(minWidth: 300)
            detail.frame(minWidth: 420)
        }
        .task { await model.loadDocuments() }
    }

    func approvalLabel(_ s: String) -> String { ["awaiting": "por aprobar", "approved": "aprobada", "superseded": "cambió tras aprobar"][s] ?? s }

    var detail: some View {
        let d = model.document
        return Group {
            if d.isNull { EmptyHint(text: "Elige un documento. Las secciones van numeradas: dile a Jarvis «cambia la tres» o «aprobada».") } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(d["title"].str).font(.title2.weight(.semibold))
                        HStack(spacing: 10) {
                            if d["kind"].str == "spec" {
                                Pill(text: approvalLabel(d["approval"]["state"].str), color: stateColor(d["approval"]["state"].str))
                                if d["approval"]["state"].str != "approved" { Button("Aprobar tal como está") { Task { await model.approveDocument() } } }
                                if d["approval"]["state"].str == "approved" { Button("Construir con esta spec") { Task { buildMsg = await model.startBuild() ? "Construcción lanzada; síguela en Runs." : (model.lastError ?? "no se pudo") } } }
                            } else if !d["progress"].isNull {
                                let p = d["progress"]
                                Pill(text: "\(p["done"].int ?? 0)/\(p["total"].int ?? 0) tareas · \(p["steps_done"].int ?? 0)/\(p["steps_total"].int ?? 0) pasos", color: .green)
                                if !p["current"].str.isEmpty { Text("Ahora: \(p["current"].str)").font(.caption) }
                            }
                            Text(d["path"].str).font(.caption2).foregroundStyle(.secondary)
                        }
                        if !buildMsg.isEmpty { Text(buildMsg).font(.caption).foregroundStyle(.secondary) }
                        if !d["preamble"].str.isEmpty { Text(d["preamble"].str).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
                        ForEach(d["sections"].array, id: \.["number"].int) { s in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack(alignment: .firstTextBaseline) {
                                    Text("\(s["number"].int ?? 0)").font(.system(size: 26, weight: .bold, design: .rounded)).foregroundStyle(.blue).frame(width: 40, alignment: .trailing)
                                    Text(s["title"].str).font(.headline)
                                    Spacer()
                                    if d["kind"].str == "spec" && editing != s["number"].int {
                                        Button("Editar") { editing = s["number"].int; editBody = s["body"].str; editTitle = s["title"].str }.buttonStyle(.link)
                                    }
                                }
                                if editing == s["number"].int {
                                    TextField("Título", text: $editTitle).textFieldStyle(.roundedBorder)
                                    TextEditor(text: $editBody).frame(minHeight: 120).font(.body).border(Color.secondary.opacity(0.3))
                                    HStack { Spacer(); Button("Cancelar") { editing = nil }; Button("Guardar") { Task { await model.saveSection(s["number"].int ?? 0, body: editBody, title: editTitle); editing = nil } }.keyboardShortcut(.defaultAction) }
                                } else {
                                    Text(s["body"].str.isEmpty ? "(vacía)" : s["body"].str).font(.callout).textSelection(.enabled).padding(.leading, 48)
                                }
                            }.padding(10).background(Color.primary.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }.padding(14)
                }
            }
        }
    }
}

// MARK: - Proyectos
struct ProjectsView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        HSplitView {
            List(selection: $model.selectedProject) {
                ForEach(model.projects, id: \.["path"].str) { p in
                    HStack(spacing: 8) {
                        Image(systemName: "folder").foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(p["name"].str).font(.body.weight(.medium))
                            Text(p["path"].str).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                        if (p["needs_you"].int ?? 0) > 0 { Pill(text: "\(p["needs_you"].int ?? 0) te esperan", color: .red) }
                        if (p["working"].int ?? 0) > 0 { Pill(text: "\(p["working"].int ?? 0) trabajando", color: .blue) }
                        if (p["active_runs"].int ?? 0) > 0 { Pill(text: "\(p["active_runs"].int ?? 0) runs", color: .blue) }
                        if let r = p["review"].string { Pill(text: reviewLabel(r), color: stateColor(r)) }
                    }.tag(p["path"].str)
                }
            }
            .frame(minWidth: 360)
            .onChange(of: model.selectedProject) { _ in Task { await model.loadProjectDetail() } }
            .overlay { if model.projects.isEmpty { EmptyHint(text: "Sin proyectos en \(model.projectsRoot)") } }
            detail.frame(minWidth: 360)
        }
        .task { await model.loadProjects() }
    }

    func reviewLabel(_ r: String) -> String { ["awaiting": "spec por aprobar", "planning": "planificando", "building": "construyendo", "review": "por revisar"][r] ?? r }

    var detail: some View {
        let d = model.projectDetail
        return Group {
            if model.selectedProject == nil || d.isNull { EmptyHint(text: "Elige un proyecto.") } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(d["name"].str).font(.title2.weight(.semibold))
                        Text(d["path"].str).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        if let r = d["review"]["state"].string { HStack { Pill(text: reviewLabel(r), color: stateColor(r)); Button("Ver specs") { model.selectedDocument = nil; model.tab = .specs; Task { await model.loadDocuments() } }.buttonStyle(.link) } }
                        if !d["build"]["progress"].isNull {
                            let p = d["build"]["progress"]
                            SectionHeader(text: "Construcción · \(p["done"].int ?? 0)/\(p["total"].int ?? 0) tareas")
                            ForEach(p["tasks"].array, id: \.["number"].int) { t in
                                HStack(spacing: 8) {
                                    Image(systemName: t["done"].bool ? "checkmark.circle.fill" : "circle").foregroundStyle(t["done"].bool ? .green : .secondary)
                                    Text("\(t["number"].int ?? 0). \(t["title"].str)").font(.callout)
                                    Spacer()
                                    Text("\(t["steps_done"].int ?? 0)/\(t["steps_total"].int ?? 0)").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        SectionHeader(text: "Conversaciones")
                        if d["sessions"].array.isEmpty { Text("Ninguna abierta.").font(.callout).foregroundStyle(.secondary) }
                        ForEach(d["sessions"].array, id: \.["session_id"].str) { s in
                            HStack { Circle().fill(stateColor(s["state"].str)).frame(width: 8, height: 8); Text(s["voice_name"].str); Spacer(); Pill(text: s["state_label"].str, color: stateColor(s["state"].str)) }
                                .contentShape(Rectangle()).onTapGesture { model.selectedSession = s["session_id"].string; model.tab = .sessions }
                        }
                        SectionHeader(text: "Runs recientes")
                        if d["runs"].array.isEmpty { Text("Ninguno.").font(.callout).foregroundStyle(.secondary) }
                        ForEach(d["runs"].array, id: \.["id"].str) { r in
                            HStack { Circle().fill(stateColor(r["status"].str)).frame(width: 8, height: 8); Text(r["prompt"].str.replacingOccurrences(of: "\n", with: " ")).lineLimit(1).font(.callout); Spacer(); Text(relativeAge(r["created_at"].double)).font(.caption).foregroundStyle(.secondary) }
                                .contentShape(Rectangle()).onTapGesture { model.selectedRun = r["id"].string; model.tab = .runs }
                        }
                    }.padding(14)
                }
            }
        }
    }
}

// MARK: - Uso
struct UsageView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Suscripción de Claude Code").font(.title2.weight(.semibold))
                if !model.usage["measured"].bool {
                    Text("Todavía sin lectura: el CLI solo informa de los límites durante un turno. Habla con Jarvis y vuelve.").foregroundStyle(.secondary)
                } else {
                    Text("Medido \(relativeAge(model.usage["observed_at"].double))" + (model.usage["stale"].bool ? " · lectura antigua" : "")).font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 16) {
                    ForEach(model.usage["windows"].array.filter { ["five_hour", "seven_day"].contains($0["key"].str) || $0["utilization"].double != nil }, id: \.["key"].str) { w in
                        Gauge(w: w)
                    }
                }
                Text(model.usage["spoken"].str).font(.callout)
                SectionHeader(text: "Runs de hoy")
                let s = model.usage["runs_today"]
                HStack(spacing: 20) {
                    stat("total", s["total_runs"].int ?? 0)
                    stat("terminados", s["by_status"]["succeeded"].int ?? 0)
                    stat("fallidos", (s["by_status"]["failed"].int ?? 0) + (s["by_status"]["timed_out"].int ?? 0))
                    stat("tokens", (s["total_input_tokens"].int ?? 0) + (s["total_output_tokens"].int ?? 0))
                }
                Text("No hay gasto en dólares: todo va contra las ventanas de la suscripción. El equivalente por run es solo informativo.").font(.caption).foregroundStyle(.secondary)
            }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await model.loadUsage() }
    }

    func stat(_ label: String, _ n: Int) -> some View {
        VStack(alignment: .leading) { Text(n >= 1000 ? tokens(n) : "\(n)").font(.title.weight(.bold).monospacedDigit()); Text(label).font(.caption).foregroundStyle(.secondary) }
    }
}

struct Gauge: View {
    let w: JSON
    var pct: Double { w["utilization"].double ?? 0 }
    var color: Color { pct >= 90 ? .red : (pct >= 70 ? .orange : .green) }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(w["label"].str).font(.headline)
            ZStack {
                Circle().stroke(Color.primary.opacity(0.08), lineWidth: 12)
                Circle().trim(from: 0, to: CGFloat(min(pct, 100) / 100)).stroke(color, style: StrokeStyle(lineWidth: 12, lineCap: .round)).rotationEffect(.degrees(-90))
                Text(w["utilization"].double == nil ? "—" : "\(Int(pct.rounded()))%").font(.title.weight(.bold).monospacedDigit())
            }.frame(width: 120, height: 120)
            Text(w["resets_at"].double == nil ? "sin lectura" : "se reinicia \(clockTime(w["resets_at"].double))").font(.caption).foregroundStyle(.secondary)
        }.padding(14).background(Color.primary.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Memoria
struct MemoryView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                TextField("Buscar en la memoria…", text: $model.memoryQuery).textFieldStyle(.roundedBorder).padding(10)
                    .onSubmit { Task { await model.loadMemory() } }
                List(selection: $model.selectedMemory) {
                    ForEach(model.memoryItems, id: \.["id"].str) { m in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(m["title"].str).font(.body.weight(.medium)).lineLimit(1)
                            HStack { Pill(text: m["type"].str); Pill(text: m["status"].str, color: m["status"].str == "active" ? .green : .yellow); if let p = m["project"].string, !p.isEmpty { Text(p).font(.caption2).foregroundStyle(.secondary) } }
                            if let s = m["snippet"].string { Text(s).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                        }.tag(m["id"].str)
                    }
                }
                .onChange(of: model.selectedMemory) { _ in Task { await model.loadMemoryDetail() } }
                .overlay { if model.memoryItems.isEmpty { EmptyHint(text: model.memoryQuery.isEmpty ? "Sin recuerdos todavía." : "Sin resultados.") } }
            }.frame(minWidth: 320)
            detail.frame(minWidth: 380)
        }
        .task { await model.loadMemory() }
    }

    var detail: some View {
        let d = model.memoryDetail
        let m = d["memory"]
        return Group {
            if model.selectedMemory == nil || d.isNull { EmptyHint(text: "Elige un recuerdo.") } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(m["title"].str).font(.title2.weight(.semibold))
                        HStack { Pill(text: m["type"].str); Pill(text: m["status"].str, color: m["status"].str == "active" ? .green : .yellow); Pill(text: m["provenance"].str); Text(m["id"].str).font(.caption2.monospaced()).foregroundStyle(.secondary) }
                        HStack { Button("Preguntar a Jarvis sobre esto") { Task { await model.askAbout(m["id"].str) } } }
                        Text(d["body"].str).font(.callout).textSelection(.enabled)
                        if !d["sources"].array.isEmpty {
                            SectionHeader(text: "Fuentes")
                            ForEach(Array(d["sources"].array.enumerated()), id: \.offset) { _, s in
                                VStack(alignment: .leading) { Text(s["title"].str).font(.callout.weight(.medium)); if let q = s["quote"].string { Text("«\(q)»").font(.caption).foregroundStyle(.secondary) } }
                            }
                        }
                        if !d["related"].array.isEmpty {
                            SectionHeader(text: "Relaciones")
                            ForEach(Array(d["related"].array.enumerated()), id: \.offset) { _, r in
                                HStack { Text(r["type"].str).font(.caption).foregroundStyle(.secondary); Button(r["title"].str) { model.selectedMemory = r["target"].string }.buttonStyle(.link); if r["status"].str == "suggested" { Pill(text: "sugerida", color: .orange) } }
                            }
                        }
                        if !d["backlinks"].array.isEmpty {
                            SectionHeader(text: "Lo referencian")
                            ForEach(d["backlinks"].array, id: \.["id"].str) { b in Button(b["title"].str) { model.selectedMemory = b["id"].string }.buttonStyle(.link) }
                        }
                    }.padding(14)
                }
            }
        }
    }
}

// MARK: - Avisos
struct NoticesView: View {
    @ObservedObject var model: DashboardModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Lo que Jarvis dijo por su cuenta").font(.title2.weight(.semibold))
                if model.announcements.isEmpty { Text("Todavía nada. Cuando una sesión te espere o un trabajo termine, aparecerá aquí y lo oirás.").foregroundStyle(.secondary) }
                ForEach(model.announcements, id: \.["id"].int) { a in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: a["priority"].str == "urgent" ? "exclamationmark.bubble.fill" : "bubble.left").foregroundStyle(a["priority"].str == "urgent" ? .red : .secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(a["text"].str).font(.callout).textSelection(.enabled)
                            Text("\(relativeAge(a["ts"].double)) · \(a["channel"].str == "voice" ? "por voz" : (a["channel"].str == "notification" ? "notificación" : "texto"))").font(.caption2).foregroundStyle(.secondary)
                        }
                    }.padding(8).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 6))
                }
                if !model.steers.isEmpty {
                    SectionHeader(text: "Mensajes enviados a sesiones")
                    ForEach(model.steers, id: \.["id"].int) { s in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(s["voice_name"].str).font(.callout.weight(.medium)); Pill(text: s["outcome"].str == "sent" ? "enviado" : s["outcome"].str, color: s["outcome"].str == "sent" ? .green : .red); Spacer(); Text(relativeAge(s["created_at"].double)).font(.caption2).foregroundStyle(.secondary) }
                            Text(s["prompt"].str).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                        }.padding(8).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                }
            }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await model.loadAnnouncements() }
    }
}
