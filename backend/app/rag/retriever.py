"""Local Markdown knowledge-base loader and retriever.

This implementation deliberately reads the repository's existing knowledge_base
directory and does not create or overwrite user documents.  It provides an
offline lexical fallback; a pgvector backend can replace ``search`` later without
changing the Agent tool contract.
"""
import re
from pathlib import Path
from threading import Lock

from app.config.settings import BACKEND_DIR


class KnowledgeRetriever:
    def __init__(self, knowledge_dir: Path | None = None, chunk_size: int = 700):
        self.knowledge_dir = knowledge_dir or BACKEND_DIR / "knowledge_base"
        self.chunk_size = chunk_size
        self._chunks: list[dict[str, str]] = []
        self._signature: tuple = ()
        self._lock = Lock()

    def _files(self) -> list[Path]:
        if not self.knowledge_dir.exists():
            return []
        return sorted(p for p in self.knowledge_dir.rglob("*") if p.suffix.lower() in {".md", ".txt"})

    def _load_if_changed(self) -> None:
        files = self._files()
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        if signature == self._signature:
            return
        with self._lock:
            chunks = []
            for path in files:
                text = path.read_text(encoding="utf-8", errors="ignore")
                sections = re.split(r"(?=^#{1,6}\s)", text, flags=re.MULTILINE)
                for section in sections:
                    section = section.strip()
                    for start in range(0, len(section), self.chunk_size):
                        content = section[start:start + self.chunk_size].strip()
                        if content:
                            chunks.append({"content": content, "source": path.name})
            self._chunks, self._signature = chunks, signature

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = text.lower()
        latin = re.findall(r"[a-z0-9_]+", lowered)
        chinese = [lowered[i:i + 2] for i in range(len(lowered) - 1) if "\u4e00" <= lowered[i] <= "\u9fff"]
        return set(latin + chinese)

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        self._load_if_changed()
        query_tokens = self._tokens(query)
        ranked = []
        for chunk in self._chunks:
            content_tokens = self._tokens(chunk["content"])
            overlap = len(query_tokens & content_tokens)
            exact_bonus = 5 if query.lower() in chunk["content"].lower() else 0
            if overlap or exact_bonus:
                ranked.append((overlap + exact_bonus, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [{**chunk, "score": score} for score, chunk in ranked[:max(1, min(top_k, 10))]]

    def stats(self) -> dict:
        self._load_if_changed()
        return {"documents": len(self._files()), "chunks": len(self._chunks), "directory": str(self.knowledge_dir)}


knowledge_retriever = KnowledgeRetriever()
