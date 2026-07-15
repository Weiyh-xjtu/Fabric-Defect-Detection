"""Load and split user-managed Markdown/TXT knowledge documents."""
import re
from pathlib import Path
from app.config.settings import BACKEND_DIR, settings

KNOWLEDGE_DIR = BACKEND_DIR / "knowledge_base"

def load_documents(directory: Path = KNOWLEDGE_DIR) -> list[dict]:
    if not directory.exists():
        return []
    documents = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            documents.append({"content": path.read_text(encoding="utf-8", errors="ignore"), "source": path.name})
    return documents

def split_documents(
    documents: list[dict],
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[dict]:
    size = chunk_size or settings.RAG_CHUNK_SIZE
    overlap = settings.RAG_CHUNK_OVERLAP if overlap is None else overlap
    step = max(1, size - overlap)
    chunks = []
    for document in documents:
        sections = re.split(r"(?=^#{1,6}\s)", document["content"], flags=re.MULTILINE)
        index = 0
        for section in sections:
            section = section.strip()
            for start in range(0, len(section), step):
                content = section[start:start + size].strip()
                if content:
                    chunks.append({"content": content, "metadata": {"source": document["source"], "chunk": index}})
                    index += 1
                if start + size >= len(section):
                    break
    return chunks
