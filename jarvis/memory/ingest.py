"""Ingesta de fuentes: texto, Markdown y PDF. Conserva el original, calcula hash y crea manifiesto."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from .schema import Source, new_id, slugify
from .store import MemoryError, MemoryService

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


@dataclass
class IngestResult:
    source: Source
    duplicate: bool
    text: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pdf(path: Path) -> tuple[str, bool]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts, total = [], 0
    for i, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        total += len(txt)
        parts.append(f"[p.{i}]\n{txt}")
    needs_ocr = len(reader.pages) > 0 and total < 25 * len(reader.pages)
    return "\n\n".join(parts), needs_ocr


def ingest_file(service: MemoryService, path: Path, title: str | None = None, max_bytes: int = 5_000_000,
                origin: str | None = None) -> IngestResult:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MemoryError(f"no existe el archivo: {path}")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise MemoryError(f"archivo demasiado grande ({len(data)} bytes > {max_bytes})")
    sha = _sha256(data)
    existing = service.source_by_hash(sha)
    if existing:
        return IngestResult(existing, True, service.source_text(existing.id) or "")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, needs_ocr = extract_pdf(path)
        media = "application/pdf"
    elif suffix in TEXT_SUFFIXES:
        text, needs_ocr = data.decode("utf-8", errors="replace"), False
        media = mimetypes.guess_type(str(path))[0] or "text/plain"
    else:
        raise MemoryError(f"formato no soportado: {suffix} (admitidos: txt, md, pdf)")
    now = service.now()
    sid = new_id("src", now)
    stored = service.raw / "documents" / f"{sid}__{slugify(path.stem)}{suffix}"
    with service.lock():
        shutil.copy2(path, stored)
        if suffix == ".pdf":
            service._atomic_write_text(Path(str(stored) + ".txt"), text)
        src = Source(id=sid, title=title or path.stem, kind="document", original_name=path.name,
                     stored_path=str(stored.relative_to(service.root)), sha256=sha, size_bytes=len(data),
                     media_type=media, imported_at=now, origin=origin or str(path), text_chars=len(text),
                     needs_ocr=needs_ocr)
        service.source_save(src)
        service._journal("ingest", None, src.title, None, "user", source_id=sid, sha256=sha[:12],
                         chars=len(text), needs_ocr=needs_ocr)
    return IngestResult(src, False, text)


def ingest_text(service: MemoryService, text: str, title: str, kind: str = "note",
                origin: str | None = None) -> IngestResult:
    data = text.encode("utf-8")
    sha = _sha256(data)
    existing = service.source_by_hash(sha)
    if existing:
        return IngestResult(existing, True, text)
    now = service.now()
    sid = new_id("src", now)
    sub = "conversations" if kind == "conversation" else "notes"
    stored = service.raw / sub / f"{sid}__{slugify(title)}.md"
    with service.lock():
        service._atomic_write_text(stored, text)
        src = Source(id=sid, title=title, kind=kind, original_name=f"{slugify(title)}.md",  # type: ignore[arg-type]
                     stored_path=str(stored.relative_to(service.root)), sha256=sha, size_bytes=len(data),
                     media_type="text/markdown", imported_at=now, origin=origin, text_chars=len(text))
        service.source_save(src)
        service._journal("ingest", None, title, None, "user", source_id=sid, sha256=sha[:12], chars=len(text))
    return IngestResult(src, False, text)


def locate_quote(text: str, quote: str) -> str | None:
    """Devuelve un localizador (página o líneas) para un fragmento dentro del texto extraído."""
    if not quote:
        return None
    pos = text.find(quote.strip()[:80])
    if pos < 0:
        return None
    before = text[:pos]
    page = before.rfind("[p.")
    if page >= 0:
        end = text.find("]", page)
        return text[page + 1:end]
    line = before.count("\n") + 1
    return f"líneas {line}-{line + quote.count(chr(10))}"
