"""Índice de búsqueda textual (SQLite FTS5). Reconstruible desde los Markdown."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .schema import Memory

_STOP = set("""a al algo ante bajo cabe con contra de del desde durante en entre hacia hasta mediante para por segun
sin so sobre tras y o u e ni que el la los las un una unos unas lo le les se su sus mi mis tu tus es son fue era
esta este esto esa ese eso aqui ahi alli como cuando donde cual quien cuanto muy mas menos pero si no ya me te nos
sabes que sobre acerca dime cuentame recuerdas recuerda""".split())


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def query_tokens(query: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]{3,}", _fold(query))
    return [t for t in toks if t not in _STOP][:12]


@dataclass
class SearchHit:
    id: str
    title: str
    type: str
    status: str
    score: float
    snippet: str
    project: str | None = None


class MemoryIndex:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS pages(
            id TEXT PRIMARY KEY, path TEXT, type TEXT, title TEXT, status TEXT,
            project TEXT, updated TEXT, mtime REAL)""")
        c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            id UNINDEXED, title, aliases, tags, body,
            tokenize='unicode61 remove_diacritics 2')""")
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert(self, mem: Memory) -> None:
        mtime = mem.path.stat().st_mtime if mem.path and mem.path.exists() else 0.0
        c = self.conn
        c.execute("DELETE FROM pages WHERE id=?", (mem.id,))
        c.execute("DELETE FROM pages_fts WHERE id=?", (mem.id,))
        c.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?,?)",
                  (mem.id, str(mem.path) if mem.path else "", mem.type, mem.title, mem.status,
                   mem.project, mem.updated.isoformat(), mtime))
        c.execute("INSERT INTO pages_fts VALUES(?,?,?,?,?)",
                  (mem.id, mem.title, " ".join(mem.aliases), " ".join(mem.tags), mem.body))
        c.commit()

    def remove(self, mem_id: str) -> None:
        self.conn.execute("DELETE FROM pages WHERE id=?", (mem_id,))
        self.conn.execute("DELETE FROM pages_fts WHERE id=?", (mem_id,))
        self.conn.commit()

    def rebuild(self, memories: list[Memory]) -> int:
        c = self.conn
        c.execute("DELETE FROM pages")
        c.execute("DELETE FROM pages_fts")
        c.commit()
        for m in memories:
            self.upsert(m)
        return len(memories)

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def indexed_ids(self) -> dict[str, float]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT id, mtime FROM pages")}

    def stale_entries(self, memories: list[Memory]) -> list[str]:
        """Ids cuyo archivo cambió, falta en el índice o sobra en él."""
        current = {m.id: (m.path.stat().st_mtime if m.path else 0.0) for m in memories}
        indexed = self.indexed_ids()
        stale = [i for i, mt in current.items() if i not in indexed or abs(indexed[i] - mt) > 1e-6]
        stale += [i for i in indexed if i not in current]
        return stale

    def search(self, query: str, limit: int = 10, types: list[str] | None = None,
               include_hidden: bool = False) -> list[SearchHit]:
        toks = query_tokens(query)
        if not toks:
            return []
        hits = self._fts(" AND ".join(f'"{t}"*' for t in toks), limit, types, include_hidden)
        if len(hits) < max(3, limit // 2) and len(toks) > 1:
            more = self._fts(" OR ".join(f'"{t}"*' for t in toks), limit, types, include_hidden)
            seen = {h.id for h in hits}
            hits += [h for h in more if h.id not in seen]
        return hits[:limit]

    def _fts(self, match: str, limit: int, types: list[str] | None, include_hidden: bool) -> list[SearchHit]:
        sql = """SELECT p.id, p.title, p.type, p.status, p.project, bm25(pages_fts, 5.0, 3.0, 2.0, 1.0) AS rank,
                 snippet(pages_fts, 4, '', '', '…', 18)
                 FROM pages_fts JOIN pages p ON p.id = pages_fts.id WHERE pages_fts MATCH ?"""
        params: list = [match]
        if not include_hidden:
            sql += " AND p.status IN ('active','disputed')"
        if types:
            sql += f" AND p.type IN ({','.join('?' * len(types))})"
            params += types
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [SearchHit(id=r[0], title=r[1], type=r[2], status=r[3], project=r[4], score=-float(r[5]), snippet=r[6] or "")
                for r in rows]
