"""Servicio de memoria: lectura, búsqueda, escritura validada, corrección y olvido.

El modelo y la interfaz *proponen*; este servicio valida y persiste con escrituras
atómicas (tmp + os.replace), bloqueo entre escritores y registro de eventos.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .events import EventBus
from .index import MemoryIndex, SearchHit
from .schema import (
    SCHEMA_MD, TYPE_DIRS, WIKILINK_RE, Memory, MemoryProposal, Relation, Source,
    new_id, normalize_title, slugify,
)


class MemoryError(Exception):
    pass


class NotFound(MemoryError):
    pass


class DuplicateMemory(MemoryError):
    def __init__(self, existing_id: str, title: str) -> None:
        super().__init__(f"ya existe un recuerdo activo con ese título: {title} ({existing_id})")
        self.existing_id = existing_id


class ConflictError(MemoryError):
    pass


@dataclass
class ForgetPlan:
    memory_id: str
    title: str
    page: str
    versions: list[str]
    referencing: list[dict[str, str]]  # {id, title, how}
    sources: list[dict[str, Any]]  # fuentes derivadas exclusivamente de este recuerdo
    notes: list[str] = field(default_factory=list)


@dataclass
class ForgetReport:
    memory_id: str
    removed_files: list[str]
    updated_pages: list[str]
    removed_sources: list[str]
    kept: list[str]


class MemoryService:
    def __init__(self, root: Path, runtime_dir: Path, timezone: str = "UTC",
                 bus: EventBus | None = None, logger: Any = None) -> None:
        self.root = Path(root)
        self.runtime_dir = Path(runtime_dir)
        self.tz = ZoneInfo(timezone)
        self.bus = bus or EventBus()
        self.log = logger
        self.wiki = self.root / "wiki"
        self.raw = self.root / "raw"
        self.manifests = self.root / "manifests"
        self.archive = self.root / "archive" / "versions"
        self.staging = self.root / "staging"
        self.journal_path = self.root / "journal" / "log.jsonl"
        self._cache: dict[str, Memory] = {}
        self._cache_stamp: tuple | None = None
        self.ensure_layout()
        self._cleanup_tmp()
        self.index = MemoryIndex(self.runtime_dir / "indexes" / "memory.sqlite")
        if self.index.count() == 0 and self._scan_paths():
            self.rebuild_index()

    # ------------------------------------------------------------- layout
    def ensure_layout(self) -> None:
        for d in [self.wiki, self.raw / "documents", self.raw / "notes", self.raw / "conversations",
                  self.manifests, self.archive, self.staging, self.journal_path.parent, self.runtime_dir]:
            d.mkdir(parents=True, exist_ok=True)
        schema = self.root / "SCHEMA.md"
        if not schema.exists():
            schema.write_text(SCHEMA_MD, encoding="utf-8")
        if not (self.wiki / "index.md").exists():
            self._regen_index_md()

    def _cleanup_tmp(self) -> None:
        """Escrituras interrumpidas dejan *.tmp; el archivo original nunca se toca hasta el replace."""
        for tmp in self.root.rglob("*.tmp"):
            try:
                tmp.unlink()
                self._log("info", f"limpiado archivo temporal huérfano {tmp.name}")
            except OSError:
                pass

    def _log(self, level: str, msg: str) -> None:
        if self.log:
            getattr(self.log, level, self.log.info)(msg)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ------------------------------------------------------------- versión / bloqueo
    def version(self) -> int:
        p = self.root / ".version"
        try:
            return int(p.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _bump_version(self) -> int:
        v = self.version() + 1
        self._atomic_write_text(self.root / ".version", str(v))
        return v

    @contextmanager
    def lock(self, timeout: float = 10.0) -> Iterator[None]:
        lock_path = self.root / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise ConflictError("la memoria está bloqueada por otra operación")
                    time.sleep(0.02)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # ------------------------------------------------------------- E/S atómica
    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _path_for(self, mem: Memory) -> Path:
        return self.wiki / TYPE_DIRS[mem.type] / f"{slugify(mem.title)}-{mem.id[-6:]}.md"

    def _write(self, mem: Memory) -> Memory:
        old_path = mem.path
        new_path = self._path_for(mem)
        if old_path and old_path.exists() and old_path != new_path:
            self._archive_version(mem.id, old_path)
            old_path.unlink()
        self._atomic_write_text(new_path, mem.to_markdown())
        mem.path = new_path
        self._cache[mem.id] = mem
        self._cache_stamp = None
        self.index.upsert(mem)
        return mem

    def _archive_version(self, mem_id: str, path: Path) -> Path:
        dest = self.archive / mem_id / f"{datetime.now():%Y%m%dT%H%M%S%f}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return dest

    def _journal(self, op: str, mem_id: str | None, title: str | None, reason: str | None = None,
                 actor: str = "service", **details: Any) -> dict[str, Any]:
        entry = {"ts": self.now().isoformat(), "op": op, "id": mem_id, "title": title,
                 "reason": reason, "actor": actor, "details": details}
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        with open(self.wiki / "log.md", "a", encoding="utf-8") as f:
            f.write(f"- [{entry['ts']}] {op} | {title or ''} ({mem_id or '-'}) — {reason or ''}\n")
        return entry

    def _regen_index_md(self) -> None:
        mems = sorted(self._scan().values(), key=lambda m: (m.type, m.title.lower()))
        lines = ["# Índice de la memoria", "", "Regenerado por el servicio de memoria. No editar a mano.", ""]
        by_type: dict[str, list[Memory]] = {}
        for m in mems:
            by_type.setdefault(m.type, []).append(m)
        for t in TYPE_DIRS:
            items = by_type.get(t)
            if not items:
                continue
            lines.append(f"## {t}")
            for m in items:
                rel = m.path.relative_to(self.wiki) if m.path else ""
                flag = "" if m.status == "active" else f" _({m.status})_"
                summ = f" — {m.summary}" if m.summary else ""
                lines.append(f"- [{m.title}]({rel}) `{m.id}`{flag}{summ}")
            lines.append("")
        self._atomic_write_text(self.wiki / "index.md", "\n".join(lines))

    # ------------------------------------------------------------- lectura
    def _scan_paths(self) -> list[Path]:
        return [p for p in self.wiki.rglob("*.md") if p.name not in ("index.md", "log.md")]

    def _scan(self, force: bool = False) -> dict[str, Memory]:
        """Carga incremental: solo se vuelven a leer las páginas cuyo mtime cambió."""
        paths = self._scan_paths()
        stamp = tuple(sorted((str(p), p.stat().st_mtime_ns) for p in paths))
        if not force and stamp == self._cache_stamp:
            return self._cache
        known = {str(m.path): m for m in self._cache.values() if m.path}
        old_stamps = dict(self._cache_stamp or ())
        cache: dict[str, Memory] = {}
        for p in paths:
            key = str(p)
            mt = p.stat().st_mtime_ns
            if not force and key in known and old_stamps.get(key) == mt:
                cache[known[key].id] = known[key]
                continue
            try:
                m = Memory.from_markdown(p.read_text(encoding="utf-8"), p)
            except Exception as e:  # página corrupta: se informa, no se propaga
                self._log("warning", f"página inválida {p}: {e}")
                continue
            cache[m.id] = m
        self._cache, self._cache_stamp = cache, stamp
        return cache

    def list(self, include_hidden: bool = False, types: list[str] | None = None,
             project: str | None = None) -> list[Memory]:
        out = []
        for m in self._scan().values():
            if not include_hidden and not m.is_current():
                continue
            if types and m.type not in types:
                continue
            if project and (m.project or "") != project:
                continue
            out.append(m)
        return sorted(out, key=lambda m: m.updated, reverse=True)

    def get(self, mem_id: str) -> Memory | None:
        return self._scan().get(mem_id)

    def require(self, mem_id: str) -> Memory:
        m = self.get(mem_id)
        if not m:
            raise NotFound(f"no existe el recuerdo {mem_id}")
        return m

    def find_by_title(self, title: str, type_: str | None = None, include_hidden: bool = False) -> Memory | None:
        key = normalize_title(title)
        for m in self.list(include_hidden=include_hidden):
            if type_ and m.type != type_:
                continue
            if normalize_title(m.title) == key or any(normalize_title(a) == key for a in m.aliases):
                return m
        return None

    def search(self, query: str, limit: int = 10, types: list[str] | None = None,
               include_hidden: bool = False) -> list[SearchHit]:
        return self.index.search(query, limit=limit, types=types, include_hidden=include_hidden)

    def journal(self, mem_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        out = []
        with open(self.journal_path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if mem_id is None or e.get("id") == mem_id or mem_id in json.dumps(e.get("details", {})):
                    out.append(e)
        return out[-limit:]

    def versions(self, mem_id: str) -> list[Path]:
        d = self.archive / mem_id
        return sorted(d.glob("*.md")) if d.exists() else []

    def projects(self) -> list[dict[str, Any]]:
        names: dict[str, dict[str, Any]] = {}
        for m in self.list(types=["project"]):
            names[m.project or slugify(m.title)] = {"slug": m.project or slugify(m.title), "title": m.title, "id": m.id, "count": 0}
        for m in self.list():
            if m.project:
                names.setdefault(m.project, {"slug": m.project, "title": m.project, "id": None, "count": 0})
                names[m.project]["count"] += 1
        return sorted(names.values(), key=lambda x: x["slug"])

    def stats(self) -> dict[str, Any]:
        all_ = list(self._scan().values())
        return {"total": len(all_), "current": sum(1 for m in all_ if m.is_current()),
                "by_type": {t: sum(1 for m in all_ if m.type == t) for t in TYPE_DIRS if any(m.type == t for m in all_)},
                "sources": len(list(self.manifests.glob("*.json"))), "version": self.version(),
                "indexed": self.index.count()}

    # ------------------------------------------------------------- escritura
    def _validate_links(self, mem: Memory) -> None:
        known = self._scan()
        for rel in mem.related:
            if rel.target not in known:
                raise MemoryError(f"relación hacia un recuerdo inexistente: {rel.target}")
        for sid in {s.source_id for s in mem.sources}:
            if not (self.manifests / f"{sid}.json").exists():
                raise MemoryError(f"fuente inexistente: {sid}")

    def create(self, proposal: MemoryProposal, actor: str = "user", allow_duplicate: bool = False) -> Memory:
        with self.lock():
            existing = self.find_by_title(proposal.title, proposal.type)
            if existing and not allow_duplicate:
                raise DuplicateMemory(existing.id, existing.title)
            now = self.now()
            mem = Memory(id=new_id("mem", now), created=now, updated=now,
                         **proposal.model_dump(exclude={"body"}), body=proposal.body)
            self._validate_links(mem)
            self._write(mem)
            self._bump_version()
            self._regen_index_md()
            self._journal("create", mem.id, mem.title, proposal.change_reason, actor, type=mem.type,
                          provenance=mem.provenance)
            for rel in mem.related:
                self._journal("relation.create", mem.id, mem.title, None, actor, target=rel.target, type=rel.type,
                              status=rel.status)
        self._link_sources(mem)
        self.bus.publish("memory.created", id=mem.id, node=self.node_summary(mem), version=self.version())
        for rel in mem.related:
            self.bus.publish("relation.created", source=mem.id, target=rel.target, type=rel.type, status=rel.status)
        return mem

    def update(self, mem_id: str, *, body: str | None = None, title: str | None = None,
               summary: str | None = None, tags: list[str] | None = None, aliases: list[str] | None = None,
               project: str | None = None, status: str | None = None, sensitivity: str | None = None,
               valid_from=None, valid_until=None, reason: str | None = None, actor: str = "user",
               expected_updated: datetime | None = None) -> Memory:
        with self.lock():
            mem = self.require(mem_id)
            if expected_updated and mem.updated != expected_updated:
                raise ConflictError("el recuerdo cambió desde que se leyó; vuelve a leerlo")
            assert mem.path
            self._archive_version(mem.id, mem.path)
            data = mem.model_dump()
            data["body"] = mem.body
            for k, v in dict(body=body, title=title, summary=summary, tags=tags, aliases=aliases, project=project,
                             status=status, sensitivity=sensitivity, valid_from=valid_from,
                             valid_until=valid_until).items():
                if v is not None:
                    data[k] = v
            data["updated"] = self.now()
            data["change_reason"] = reason
            new = Memory.model_validate(data)
            new.path = mem.path
            self._validate_links(new)
            self._write(new)
            self._bump_version()
            self._regen_index_md()
            self._journal("update", new.id, new.title, reason, actor,
                          fields=[k for k, v in dict(body=body, title=title, summary=summary, tags=tags, status=status,
                                                     project=project, aliases=aliases).items() if v is not None])
        self.bus.publish("memory.updated", id=new.id, node=self.node_summary(new), version=self.version())
        return new

    def supersede(self, old_id: str, proposal: MemoryProposal, reason: str, actor: str = "user") -> Memory:
        """Corrección temporal: el recuerdo anterior queda `superseded` y enlazado al nuevo."""
        with self.lock():
            old = self.require(old_id)
            now = self.now()
            rels = list(proposal.related) + [Relation(target=old.id, type="reemplaza", status="explicit",
                                                       provenance=actor, rationale=reason)]
            data = proposal.model_dump(exclude={"body", "related", "change_reason"})
            data.setdefault("project", old.project)
            new = Memory(id=new_id("mem", now), created=now, updated=now, related=rels, supersedes=old.id,
                         body=proposal.body, change_reason=reason, **data)
            self._validate_links(new)
            self._write(new)
            assert old.path
            self._archive_version(old.id, old.path)
            old_data = old.model_dump()
            old_data.update(body=old.body, status="superseded", superseded_by=new.id, updated=now, change_reason=reason)
            old_new = Memory.model_validate(old_data)
            old_new.path = old.path
            self._write(old_new)
            self._bump_version()
            self._regen_index_md()
            self._journal("supersede", new.id, new.title, reason, actor, supersedes=old.id)
            self._journal("update", old.id, old.title, reason, actor, fields=["status"], superseded_by=new.id)
        self._link_sources(new)
        self.bus.publish("memory.created", id=new.id, node=self.node_summary(new), version=self.version())
        self.bus.publish("memory.updated", id=old.id, node=self.node_summary(old_new), version=self.version())
        self.bus.publish("relation.created", source=new.id, target=old.id, type="reemplaza", status="explicit")
        return new

    def dispute(self, mem_id: str, reason: str, other_id: str | None = None, actor: str = "user") -> Memory:
        with self.lock():
            mem = self.require(mem_id)
            assert mem.path
            self._archive_version(mem.id, mem.path)
            data = mem.model_dump()
            data.update(body=mem.body, status="disputed", updated=self.now(), change_reason=reason)
            if other_id:
                self.require(other_id)
                data["related"] = data["related"] + [{"target": other_id, "type": "contradice", "status": "explicit",
                                                      "provenance": actor, "rationale": reason}]
            new = Memory.model_validate(data)
            new.path = mem.path
            self._write(new)
            self._bump_version()
            self._regen_index_md()
            self._journal("dispute", mem.id, mem.title, reason, actor, other=other_id)
        self.bus.publish("memory.updated", id=new.id, node=self.node_summary(new), version=self.version())
        if other_id:
            self.bus.publish("relation.created", source=mem.id, target=other_id, type="contradice", status="explicit")
        return new

    def link(self, src_id: str, dst_id: str, type_: str = "relacionado_con", status: str = "explicit",
             rationale: str | None = None, actor: str = "user") -> Memory:
        with self.lock():
            src = self.require(src_id)
            self.require(dst_id)
            if any(r.target == dst_id and r.type == type_ for r in src.related):
                return src
            data = src.model_dump()
            data.update(body=src.body, updated=self.now())
            data["related"] = data["related"] + [{"target": dst_id, "type": type_, "status": status,
                                                  "provenance": actor, "rationale": rationale}]
            new = Memory.model_validate(data)
            new.path = src.path
            self._write(new)
            self._bump_version()
            self._journal("relation.create", src.id, src.title, rationale, actor, target=dst_id, type=type_, status=status)
        self.bus.publish("relation.created", source=src_id, target=dst_id, type=type_, status=status)
        return new

    def confirm_relation(self, src_id: str, dst_id: str, type_: str, actor: str = "user") -> Memory:
        with self.lock():
            src = self.require(src_id)
            data = src.model_dump()
            data.update(body=src.body, updated=self.now())
            changed = False
            for r in data["related"]:
                if r["target"] == dst_id and r["type"] == type_ and r["status"] != "explicit":
                    r["status"] = "explicit"
                    changed = True
            if not changed:
                return src
            new = Memory.model_validate(data)
            new.path = src.path
            self._write(new)
            self._bump_version()
            self._journal("relation.confirm", src.id, src.title, None, actor, target=dst_id, type=type_)
        self.bus.publish("relation.created", source=src_id, target=dst_id, type=type_, status="explicit")
        return new

    def unlink(self, src_id: str, dst_id: str, type_: str | None = None, actor: str = "user") -> Memory:
        with self.lock():
            src = self.require(src_id)
            data = src.model_dump()
            kept = [r for r in data["related"] if not (r["target"] == dst_id and (type_ is None or r["type"] == type_))]
            if len(kept) == len(data["related"]):
                return src
            data.update(body=src.body, updated=self.now(), related=kept)
            new = Memory.model_validate(data)
            new.path = src.path
            self._write(new)
            self._bump_version()
            self._journal("relation.delete", src.id, src.title, None, actor, target=dst_id, type=type_)
        self.bus.publish("relation.deleted", source=src_id, target=dst_id, type=type_)
        return new

    # ------------------------------------------------------------- olvido
    def plan_forget(self, mem_id: str) -> ForgetPlan:
        mem = self.require(mem_id)
        referencing = []
        for other in self._scan().values():
            if other.id == mem_id:
                continue
            hows = []
            if any(r.target == mem_id for r in other.related):
                hows.append("relación")
            if mem_id in other.links_in_body():
                hows.append("enlace en el texto")
            if other.supersedes == mem_id or other.superseded_by == mem_id:
                hows.append("cadena de reemplazo")
            if hows:
                referencing.append({"id": other.id, "title": other.title, "how": ", ".join(hows)})
        exclusive_sources = []
        for sid in {s.source_id for s in mem.sources}:
            src = self.source_get(sid)
            if src and set(src.derived_memories) <= {mem_id}:
                exclusive_sources.append({"id": src.id, "title": src.title, "path": src.stored_path})
        notes = ["Las entradas del registro (journal) conservan id y título del recuerdo como metadato.",
                 "Si tienes copias de seguridad externas (Time Machine, iCloud), este borrado no las alcanza."]
        return ForgetPlan(memory_id=mem.id, title=mem.title, page=str(mem.path.relative_to(self.root)) if mem.path else "",
                          versions=[str(v.relative_to(self.root)) for v in self.versions(mem.id)],
                          referencing=referencing, sources=exclusive_sources, notes=notes)

    def forget(self, mem_id: str, reason: str = "solicitud del usuario", delete_sources: bool = False,
               actor: str = "user") -> ForgetReport:
        removed: list[str] = []
        updated: list[str] = []
        removed_sources: list[str] = []
        with self.lock():
            mem = self.require(mem_id)
            plan = self.plan_forget(mem_id)
            # 1. referencias en otras páginas
            for ref in plan.referencing:
                other = self.require(ref["id"])
                data = other.model_dump()
                data["related"] = [r for r in data["related"] if r["target"] != mem_id]
                body = WIKILINK_RE.sub(lambda m: mem.title if m.group(1) == mem_id else m.group(0), other.body)
                data.update(body=body, updated=self.now(), change_reason=f"referencia eliminada: {reason}")
                if data.get("supersedes") == mem_id:
                    data["supersedes"] = None
                if data.get("superseded_by") == mem_id:
                    data["superseded_by"] = None
                    if data["status"] == "superseded":
                        data["status"] = "archived"
                new = Memory.model_validate(data)
                new.path = other.path
                self._write(new)
                updated.append(new.id)
            # 2. página, versiones, índice, staging
            if mem.path and mem.path.exists():
                mem.path.unlink()
                removed.append(plan.page)
            vdir = self.archive / mem_id
            if vdir.exists():
                shutil.rmtree(vdir)
                removed += plan.versions
            for st in self.staging.glob(f"*{mem_id}*"):
                st.unlink()
                removed.append(str(st.relative_to(self.root)))
            self.index.remove(mem_id)
            self._cache.pop(mem_id, None)
            self._cache_stamp = None
            # 3. manifiestos de fuentes
            for sid in {s.source_id for s in mem.sources}:
                src = self.source_get(sid)
                if not src:
                    continue
                src.derived_memories = [d for d in src.derived_memories if d != mem_id]
                if delete_sources and not src.derived_memories:
                    p = self.root / src.stored_path
                    if p.exists():
                        p.unlink()
                    (self.manifests / f"{sid}.json").unlink(missing_ok=True)
                    removed_sources.append(sid)
                else:
                    self.source_save(src)
            self._bump_version()
            self._regen_index_md()
            self._journal("forget", mem_id, mem.title, reason, actor, removed=removed, updated=updated,
                          removed_sources=removed_sources)
        self.bus.publish("memory.deleted", id=mem_id, version=self.version())
        for uid in updated:
            u = self.get(uid)
            if u:
                self.bus.publish("memory.updated", id=uid, node=self.node_summary(u), version=self.version())
        kept = ["journal/log.jsonl (metadatos: id, título, motivo)"]
        if not delete_sources and mem.sources:
            kept.append("fuentes en raw/ (no se pidió borrarlas)")
        return ForgetReport(memory_id=mem_id, removed_files=removed, updated_pages=updated,
                            removed_sources=removed_sources, kept=kept)

    # ------------------------------------------------------------- fuentes
    def source_get(self, source_id: str) -> Source | None:
        p = self.manifests / f"{source_id}.json"
        if not p.exists():
            return None
        return Source.model_validate_json(p.read_text(encoding="utf-8"))

    def sources(self) -> list[Source]:
        return [Source.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(self.manifests.glob("*.json"))]

    def source_save(self, src: Source) -> None:
        self._atomic_write_text(self.manifests / f"{src.id}.json", src.model_dump_json(indent=2))

    def source_by_hash(self, sha256: str) -> Source | None:
        for s in self.sources():
            if s.sha256 == sha256:
                return s
        return None

    def source_text(self, source_id: str) -> str | None:
        src = self.source_get(source_id)
        if not src:
            return None
        txt = self.root / (src.stored_path + ".txt")
        if txt.exists():
            return txt.read_text(encoding="utf-8")
        p = self.root / src.stored_path
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _link_sources(self, mem: Memory) -> None:
        for sid in {s.source_id for s in mem.sources}:
            src = self.source_get(sid)
            if src and mem.id not in src.derived_memories:
                src.derived_memories.append(mem.id)
                self.source_save(src)

    # ------------------------------------------------------------- índice
    def rebuild_index(self) -> int:
        with self.lock():
            n = self.index.rebuild(list(self._scan(force=True).values()))
            self._regen_index_md()
            self._journal("index.rebuild", None, None, None, "service", pages=n)
        self.bus.publish("index.rebuilt", pages=n, version=self.version())
        return n

    # ------------------------------------------------------------- proyección
    def node_summary(self, m: Memory) -> dict[str, Any]:
        summary = m.summary or re.sub(r"\s+", " ", m.body)[:160]
        return {"id": m.id, "title": m.title, "type": m.type, "status": m.status, "summary": summary,
                "created": m.created.isoformat(), "updated": m.updated.isoformat(),
                "path": str(m.path.relative_to(self.root)) if m.path else None,
                "provenance": m.provenance, "project": m.project, "tags": m.tags,
                "has_sources": bool(m.sources), "sensitivity": m.sensitivity,
                "degree": 0}
