import sqlite3
import threading
from pathlib import Path
from typing import Callable

import sqlite_vec
from fastembed import TextEmbedding

from file_parser import chunk_text, extract_text_from_file, is_supported_rag_file

# Lightweight CPU-friendly local embedding model (~67 MB quantized ONNX, 384 dimensions)
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384


class LocalRagEngine:
    """SQLite + sqlite-vec local RAG index with FastEmbed ONNX embeddings."""

    def __init__(self, db_path: Path, cache_dir: Path | None = None) -> None:
        self.db_path = db_path
        self.cache_dir = cache_dir
        self._lock = threading.RLock()
        self._embedder: TextEmbedding | None = None
        self._db_initialized = False

    def _get_embedder(self) -> TextEmbedding:
        if self._embedder is None:
            kwargs: dict = {"threads": 2}
            if self.cache_dir is not None:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                kwargs["cache_dir"] = str(self.cache_dir)
                # If model weights are already cached locally, skip HuggingFace network checks for instant offline load
                has_cached_onnx = any(self.cache_dir.rglob("*.onnx"))
                if has_cached_onnx:
                    kwargs["local_files_only"] = True
            self._embedder = TextEmbedding(model_name=EMBEDDING_MODEL_NAME, **kwargs)
        return self._embedder

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL;")
        if not self._db_initialized:
            self._init_schema(conn)
            self._db_initialized = True
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS indexed_files (
                file_path TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_file_path ON chunks(file_path);
            CREATE INDEX IF NOT EXISTS idx_chunks_file_name ON chunks(file_name);

            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                file_name TEXT,
                embedding float[{EMBEDDING_DIM}]
            );
            """
        )
        conn.commit()

    def _delete_file_records(self, conn: sqlite3.Connection, rel_path_str: str) -> None:
        rows = conn.execute("SELECT id FROM chunks WHERE file_path = ?", (rel_path_str,)).fetchall()
        if rows:
            ids = [r[0] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", ids)
            conn.execute("DELETE FROM chunks WHERE file_path = ?", (rel_path_str,))
        conn.execute("DELETE FROM indexed_files WHERE file_path = ?", (rel_path_str,))

    def index_file(self, file_path: Path, base_dir: Path) -> int:
        """Parse, chunk, embed, and store a single file in SQLite + sqlite-vec. Returns chunk count."""
        resolved = file_path.resolve()
        base_resolved = base_dir.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(base_resolved):
            return 0
        if not is_supported_rag_file(resolved):
            return 0

        rel_str = resolved.relative_to(base_resolved).as_posix()
        file_name = resolved.name
        stat = resolved.stat()

        text = extract_text_from_file(resolved)
        chunks = chunk_text(text)

        embeddings: list[bytes] = []
        if chunks:
            embedder = self._get_embedder()
            vecs = list(embedder.embed(chunks))
            embeddings = [sqlite_vec.serialize_float32(v.tolist()) for v in vecs]

        with self._lock:
            conn = self._connect()
            try:
                self._delete_file_records(conn, rel_str)
                for idx, (chunk_str, vec_bytes) in enumerate(zip(chunks, embeddings)):
                    cur = conn.execute(
                        "INSERT INTO chunks (file_name, file_path, chunk_index, content) VALUES (?, ?, ?, ?)",
                        (file_name, rel_str, idx, chunk_str),
                    )
                    chunk_id = cur.lastrowid
                    conn.execute(
                        "INSERT INTO vec_chunks (chunk_id, file_name, embedding) VALUES (?, ?, ?)",
                        (chunk_id, file_name, vec_bytes),
                    )
                conn.execute(
                    "INSERT INTO indexed_files (file_path, file_name, mtime, size) VALUES (?, ?, ?, ?)",
                    (rel_str, file_name, stat.st_mtime, stat.st_size),
                )
                conn.commit()
            finally:
                conn.close()

        return len(chunks)

    def remove_path(self, target_path: Path, base_dir: Path, is_dir: bool = False) -> None:
        """Remove a deleted/moved file or directory from the RAG database."""
        resolved = target_path.resolve()
        base_resolved = base_dir.resolve()
        if not resolved.is_relative_to(base_resolved):
            return
        rel_str = resolved.relative_to(base_resolved).as_posix()

        with self._lock:
            conn = self._connect()
            try:
                if is_dir:
                    prefix = f"{rel_str.rstrip('/')}/%"
                    rows = conn.execute(
                        "SELECT DISTINCT file_path FROM indexed_files WHERE file_path = ? OR file_path LIKE ?",
                        (rel_str, prefix),
                    ).fetchall()
                    for (fp,) in rows:
                        self._delete_file_records(conn, fp)
                else:
                    self._delete_file_records(conn, rel_str)
                conn.commit()
            finally:
                conn.close()

    def index_directory(
        self,
        base_dir: Path,
        agent_dir: Path,
        is_blocked_fn: Callable[[str], bool],
    ) -> dict[str, int]:
        """Embed all supported files in base_dir into SQLite + sqlite-vec (incremental on unchanged files)."""
        import os

        base_resolved = base_dir.resolve()
        agent_resolved = agent_dir.resolve()

        # Collect current eligible files on disk
        disk_files: dict[str, Path] = {}
        for root, dirs, files in os.walk(base_resolved):
            root_path = Path(root)
            dirs[:] = sorted(
                d
                for d in dirs
                if not is_blocked_fn(d) and (root_path / d).resolve() != agent_resolved
            )
            for fname in sorted(files):
                if is_blocked_fn(fname):
                    continue
                fpath = (root_path / fname).resolve()
                if not fpath.is_relative_to(base_resolved) or fpath.is_relative_to(agent_resolved):
                    continue
                if is_supported_rag_file(fpath):
                    rel_str = fpath.relative_to(base_resolved).as_posix()
                    disk_files[rel_str] = fpath

        with self._lock:
            conn = self._connect()
            try:
                prev_ws_row = conn.execute("SELECT value FROM meta WHERE key = 'workspace_root'").fetchone()
                prev_ws = prev_ws_row[0] if prev_ws_row else None
                if prev_ws != str(base_resolved):
                    # Workspace switched: clear previous workspace records cleanly
                    conn.execute("DELETE FROM vec_chunks")
                    conn.execute("DELETE FROM chunks")
                    conn.execute("DELETE FROM indexed_files")
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('workspace_root', ?)",
                        (str(base_resolved),),
                    )
                    conn.commit()

                existing = {
                    row[0]: (row[1], row[2])
                    for row in conn.execute("SELECT file_path, mtime, size FROM indexed_files").fetchall()
                }

                # Remove stale files no longer on disk
                stale = set(existing.keys()) - set(disk_files.keys())
                for rel_str in stale:
                    self._delete_file_records(conn, rel_str)
                if stale:
                    conn.commit()
            finally:
                conn.close()

        indexed_count = 0
        skipped_count = 0
        chunk_total = 0

        for rel_str, fpath in disk_files.items():
            try:
                st = fpath.stat()
            except OSError:
                continue
            prev = existing.get(rel_str)
            if prev is not None and abs(prev[0] - st.st_mtime) < 1e-3 and prev[1] == st.st_size:
                skipped_count += 1
                continue
            chunk_total += self.index_file(fpath, base_resolved)
            indexed_count += 1

        return {
            "indexed_files": indexed_count,
            "unchanged_files": skipped_count,
            "new_chunks": chunk_total,
        }

    def search(self, query: str, top_k: int = 5) -> str:
        """Run semantic vector search over indexed workspace chunks."""
        q = (query or "").strip()
        if not q:
            return "Error: Query cannot be empty."

        embedder = self._get_embedder()
        q_vec = next(iter(embedder.embed([q])))
        q_bytes = sqlite_vec.serialize_float32(q_vec.tolist())

        with self._lock:
            conn = self._connect()
            try:
                total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                if total_chunks == 0:
                    return "No indexed content found in workspace."

                k = min(top_k, total_chunks)
                rows = conn.execute(
                    """
                    SELECT
                        v.chunk_id,
                        v.distance,
                        c.file_name,
                        c.file_path,
                        c.chunk_index,
                        c.content
                    FROM vec_chunks AS v
                    JOIN chunks AS c ON c.id = v.chunk_id
                    WHERE v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance ASC
                    """,
                    (q_bytes, k),
                ).fetchall()
            finally:
                conn.close()

        if not rows:
            return "No relevant chunks found."

        out: list[str] = []
        for rank, (_cid, dist, file_name, file_path, chunk_idx, content) in enumerate(rows, start=1):
            out.append(
                f"[{rank}] File: {file_name} (path: {file_path}, chunk #{chunk_idx}, distance: {dist:.4f})\n"
                f"{content}"
            )
        return "\n\n---\n\n".join(out)
