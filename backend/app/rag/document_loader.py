"""Load and split user-managed Markdown/TXT/PDF knowledge documents."""
import re
from pathlib import Path
from app.config.settings import BACKEND_DIR, settings
from app.core.logger import get_logger

logger = get_logger(__name__)

KNOWLEDGE_DIR = BACKEND_DIR / "knowledge_base"

# 知识库支持的文档类型；上传接口与检索器共用，避免各处硬编码后缀集合。
ALLOWED_KB_EXTENSIONS = {".md", ".txt", ".pdf"}


def _extract_pdf_text(path: Path) -> str:
    """提取 PDF 纯文本；解析失败时返回空串并留痕，不中断整库加载。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("未安装 pypdf，无法解析 PDF：%s", path.name)
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.warning("PDF 解析失败 %s：%s", path.name, exc)
        return ""


def extract_text(path: Path) -> str:
    """按文件类型抽取纯文本。未知/失败一律返回空串，交由调用方跳过空内容。"""
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def load_documents(directory: Path = KNOWLEDGE_DIR) -> list[dict]:
    if not directory.exists():
        return []
    documents = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in ALLOWED_KB_EXTENSIONS:
            content = extract_text(path)
            if content.strip():
                documents.append({"content": content, "source": path.name})
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
