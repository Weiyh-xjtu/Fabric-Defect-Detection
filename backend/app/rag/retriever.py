"""Local Markdown knowledge-base loader and retriever.

This implementation deliberately reads the repository's existing knowledge_base
directory and does not create or overwrite user documents.  It provides an
offline lexical fallback; a pgvector backend can replace ``search`` later without
changing the Agent tool contract.
"""
import re
from pathlib import Path
from threading import Lock, Thread

from app.config.settings import BACKEND_DIR, settings
from app.core.logger import get_logger
from app.rag.document_loader import (
    ALLOWED_KB_EXTENSIONS,
    extract_text,
    load_documents,
    split_documents,
)
from app.rag.embedding import embedding_service
from app.vectorstore.pgvector_client import pgvector_client

logger = get_logger(__name__)


class KnowledgeRetriever:
    def __init__(self, knowledge_dir: Path | None = None, chunk_size: int | None = None):
        self.knowledge_dir = knowledge_dir or BACKEND_DIR / "knowledge_base"
        self.chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
        self._chunks: list[dict[str, str]] = []
        self._signature: tuple = ()
        self._lock = Lock()
        # 后台重建编排：_rebuild_lock 保护状态与线程/挂起标记，与 _lock（保护
        # 词法检索缓存）分离，避免检索与重建互相阻塞。
        self._rebuild_lock = Lock()
        self._rebuild_thread: Thread | None = None
        self._rebuild_pending = False
        self._rebuild_state: dict = {
            "status": "idle",  # idle | running | success | failed
            "detail": None,
            "documents": 0,
            "total_chunks": 0,
            "updated_at": None,
        }

    def _files(self) -> list[Path]:
        if not self.knowledge_dir.exists():
            return []
        return sorted(p for p in self.knowledge_dir.rglob("*") if p.suffix.lower() in ALLOWED_KB_EXTENSIONS)

    def _load_if_changed(self) -> None:
        files = self._files()
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        if signature == self._signature:
            return
        with self._lock:
            chunks = []
            for path in files:
                text = extract_text(path)
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

    def retrieve(self, query: str, top_k: int | None = None) -> dict:
        """检索并返回结果与实际使用的检索模式，供上层如实反馈。

        Returns:
            {"mode": "pgvector" | "lexical_fallback", "fallback_reason": str | None,
             "results": [{"content", "source", "score"}, ...]}
        """
        top_k = top_k or settings.RAG_TOP_K
        fallback_reason = None
        if self.knowledge_dir == BACKEND_DIR / "knowledge_base":
            try:
                if pgvector_client.count() > 0:
                    results = pgvector_client.search(
                        embedding_service.embed_query(query), top_k
                    )
                    return {"mode": "pgvector", "fallback_reason": None, "results": results}
                fallback_reason = "向量索引为空，尚未执行知识库索引构建"
            except Exception as exc:
                # Embedding/pgvector 暂时不可用时自动退回本地检索，但必须留痕。
                fallback_reason = f"{type(exc).__name__}: {exc}"
                logger.warning("向量检索失败，降级为本地词法检索: %s", exc)
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
        return {
            "mode": "lexical_fallback",
            "fallback_reason": fallback_reason,
            "results": [
                {**chunk, "score": score}
                for score, chunk in ranked[:max(1, min(top_k, 10))]
            ],
        }

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """兼容旧调用方：只返回结果列表。"""
        return self.retrieve(query, top_k)["results"]

    def stats(self) -> dict:
        self._load_if_changed()
        vector_count = pgvector_client.count() if self.knowledge_dir == BACKEND_DIR / "knowledge_base" else 0
        return {
            "documents": len(self._files()),
            "chunks": len(self._chunks),
            "total_chunks": vector_count or len(self._chunks),
            "vector_chunks": vector_count,
            "mode": "pgvector" if vector_count else "lexical_fallback",
            "directory": str(self.knowledge_dir),
        }

    def build(self) -> dict:
        if self.knowledge_dir != BACKEND_DIR / "knowledge_base":
            raise RuntimeError("仅默认知识库支持向量索引构建")
        documents = load_documents(self.knowledge_dir)
        chunks = split_documents(documents)
        if not chunks:
            raise RuntimeError("知识库中没有可索引的 Markdown/TXT/PDF 内容")
        embeddings = embedding_service.embed_texts([item["content"] for item in chunks])
        pgvector_client.init_table()
        inserted = pgvector_client.replace(chunks, embeddings)
        return {"documents": len(documents), "total_chunks": inserted, "embedding_model": settings.EMBEDDING_MODEL, "embedding_dim": settings.EMBEDDING_DIM}

    # ── 后台自动重建 ────────────────────────────────────
    def rebuild_status(self) -> dict:
        """返回当前重建状态的快照，供前端轮询展示。"""
        with self._rebuild_lock:
            return dict(self._rebuild_state)

    def schedule_rebuild(self) -> dict:
        """请求一次后台向量索引重建。

        文件增删后调用。若已有重建在跑，仅置挂起标记（去抖），使连续增删只在
        末尾补跑一轮，保证最终文件状态一定被索引，同时避免重复 embedding 开销。
        embedding/pgvector 不可用时重建线程会把状态记为 failed 并留原因，不抛出，
        此时词法降级检索仍随文件签名自动更新（优雅降级）。
        """
        with self._rebuild_lock:
            if self._rebuild_thread and self._rebuild_thread.is_alive():
                self._rebuild_pending = True
                return dict(self._rebuild_state)
            self._rebuild_state = {**self._rebuild_state, "status": "running", "detail": None}
            thread = Thread(target=self._run_rebuild_loop, name="kb-rebuild", daemon=True)
            self._rebuild_thread = thread
            thread.start()
            return dict(self._rebuild_state)

    def _run_rebuild_loop(self) -> None:
        """在后台线程内重建，直到无挂起请求为止。"""
        from datetime import datetime

        while True:
            try:
                result = self.build()
                state = {
                    "status": "success",
                    "detail": None,
                    "documents": result["documents"],
                    "total_chunks": result["total_chunks"],
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            except Exception as exc:
                logger.warning("知识库向量索引重建失败：%s", exc)
                state = {
                    "status": "failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "documents": self._rebuild_state.get("documents", 0),
                    "total_chunks": self._rebuild_state.get("total_chunks", 0),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            with self._rebuild_lock:
                self._rebuild_state = state
                if self._rebuild_pending:
                    # 有增删在重建期间发生，补跑一轮并保持 running 展示。
                    self._rebuild_pending = False
                    self._rebuild_state = {**state, "status": "running", "detail": None}
                    continue
                self._rebuild_thread = None
                return


knowledge_retriever = KnowledgeRetriever()
