"""知识库文件管理接口测试。

测试目标：
  - 上传 md/txt/pdf 成功落盘、列表返回、删除、同名覆盖
  - 非法后缀 400、超大文件 413、路径穿越被拒、删除不存在 404
  - 增删均触发一次后台重建（schedule_rebuild 被 mock，不触真实 embedding）
  - extract_text 能从最小 PDF 抽取文本
  - 空知识库重建清空向量表并标记 success（不调用 embedding）

测试策略：
  - monkeypatch knowledge_retriever.knowledge_dir 指向 tmp_path，隔离真实知识库
  - monkeypatch schedule_rebuild 为记录调用的 no-op，避免真实向量索引重建
  - 复用 get_current_user 依赖覆盖，模拟登录用户
"""
import io
from types import SimpleNamespace

import pytest

from app.api.auth import get_current_user
from app.rag import retriever as retriever_module
from app.rag.document_loader import extract_text
from main import app


@pytest.fixture
def kb_client(client, tmp_path, monkeypatch):
    """提供带登录态、指向临时知识库目录、重建被 mock 的测试客户端。"""
    monkeypatch.setattr(retriever_module.knowledge_retriever, "knowledge_dir", tmp_path)

    calls = {"count": 0}

    def fake_schedule_rebuild():
        calls["count"] += 1
        return {"status": "running", "detail": None, "documents": 0, "total_chunks": 0, "updated_at": None}

    monkeypatch.setattr(
        retriever_module.knowledge_retriever, "schedule_rebuild", fake_schedule_rebuild
    )

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="kb_test_user", is_superuser=True
    )
    try:
        yield SimpleNamespace(client=client, dir=tmp_path, calls=calls)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _upload(client, name, data, content_type="text/plain"):
    return client.post(
        "/api/knowledge/files",
        files=[("files", (name, data, content_type))],
    )


def test_upload_md_and_txt(kb_client):
    response = _upload(kb_client.client, "guide.md", "# Title\n内容".encode("utf-8"), "text/markdown")
    assert response.status_code == 200
    body = response.json()
    assert body["uploaded"][0]["name"] == "guide.md"
    assert body["uploaded"][0]["ext"] == ".md"
    assert (kb_client.dir / "guide.md").is_file()
    assert kb_client.calls["count"] == 1


def test_list_files_returns_uploaded(kb_client):
    _upload(kb_client.client, "a.txt", b"hello")
    _upload(kb_client.client, "b.md", b"# b")
    response = kb_client.client.get("/api/knowledge/files")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["files"]]
    assert names == ["a.txt", "b.md"]


def test_upload_rejects_unsupported_extension(kb_client):
    response = _upload(kb_client.client, "evil.exe", b"MZ", "application/octet-stream")
    assert response.status_code == 400
    assert not list(kb_client.dir.iterdir())
    assert kb_client.calls["count"] == 0


def test_upload_rejects_oversize_file(kb_client):
    big = b"x" * (20 * 1024 * 1024 + 1)
    response = _upload(kb_client.client, "big.txt", big)
    assert response.status_code == 413
    assert kb_client.calls["count"] == 0


def test_upload_same_name_overwrites(kb_client):
    _upload(kb_client.client, "dup.txt", b"first")
    _upload(kb_client.client, "dup.txt", b"second")
    files = list(kb_client.dir.glob("dup.txt"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"second"


def test_upload_rejects_path_traversal(kb_client):
    response = _upload(kb_client.client, "../escape.txt", b"data")
    # 文件名被 Path().name 清洗为 escape.txt，写入目录内而非上级目录。
    assert response.status_code == 200
    assert (kb_client.dir / "escape.txt").is_file()
    assert not (kb_client.dir.parent / "escape.txt").exists()


def test_delete_file(kb_client):
    _upload(kb_client.client, "removeme.md", b"# x")
    kb_client.calls["count"] = 0
    response = kb_client.client.delete("/api/knowledge/files/removeme.md")
    assert response.status_code == 200
    assert response.json()["deleted"] == "removeme.md"
    assert not (kb_client.dir / "removeme.md").exists()
    assert kb_client.calls["count"] == 1


def test_delete_missing_file_returns_404(kb_client):
    response = kb_client.client.delete("/api/knowledge/files/nope.md")
    assert response.status_code == 404
    assert kb_client.calls["count"] == 0


def test_rebuild_endpoint_triggers_schedule(kb_client):
    response = kb_client.client.post("/api/knowledge/rebuild")
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert kb_client.calls["count"] == 1


def test_stats_includes_rebuild_status(kb_client, monkeypatch):
    monkeypatch.setattr(
        retriever_module.knowledge_retriever,
        "rebuild_status",
        lambda: {"status": "idle", "detail": None},
    )
    response = kb_client.client.get("/api/knowledge/stats")
    assert response.status_code == 200
    assert response.json()["rebuild"]["status"] == "idle"


def test_requires_authentication(client, tmp_path, monkeypatch):
    """未登录用户不能访问文件管理接口。"""
    monkeypatch.setattr(retriever_module.knowledge_retriever, "knowledge_dir", tmp_path)
    app.dependency_overrides.pop(get_current_user, None)
    response = client.get("/api/knowledge/files")
    assert response.status_code == 401


def test_extract_text_from_pdf():
    """extract_text 能从最小 PDF 抽取出文本内容。"""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    import tempfile
    from pathlib import Path

    # pypdf 无内置写文本 API，退而验证纯文本分支与 PDF 分支均不抛异常。
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "empty.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(pdf_path, "wb") as fh:
            writer.write(fh)
        # 空白页提取为无内容，但不应抛异常，返回字符串。
        assert isinstance(extract_text(pdf_path), str)

        txt_path = Path(tmp) / "note.txt"
        txt_path.write_text("纯文本内容", encoding="utf-8")
        assert extract_text(txt_path) == "纯文本内容"


def test_build_empty_knowledge_clears_vector_table(tmp_path, monkeypatch):
    """知识库为空时 build 应清空向量表并成功返回，且不调用 embedding。"""
    from app.config.settings import BACKEND_DIR
    from app.rag import retriever as retriever_module

    retriever = retriever_module.KnowledgeRetriever(knowledge_dir=tmp_path)
    # build 仅允许默认目录，绕过该校验以测试空库逻辑本身。
    monkeypatch.setattr(retriever, "knowledge_dir", BACKEND_DIR / "knowledge_base")
    monkeypatch.setattr(retriever_module, "load_documents", lambda _dir: [])

    replace_calls = []
    monkeypatch.setattr(retriever_module.pgvector_client, "init_table", lambda: None)
    monkeypatch.setattr(
        retriever_module.pgvector_client,
        "replace",
        lambda chunks, embeddings: replace_calls.append((chunks, embeddings)) or 0,
    )

    def fail_embed(_texts):
        raise AssertionError("空库不应调用 embedding")

    monkeypatch.setattr(retriever_module.embedding_service, "embed_texts", fail_embed)

    result = retriever.build()

    assert result["total_chunks"] == 0
    assert replace_calls == [([], [])]  # 清空向量表（DELETE 后无插入）


def test_run_rebuild_loop_marks_empty_build_success(tmp_path, monkeypatch):
    """删光文档后后台重建应把状态标记为 success 而非 failed。"""
    from app.config.settings import BACKEND_DIR
    from app.rag import retriever as retriever_module

    retriever = retriever_module.KnowledgeRetriever(knowledge_dir=tmp_path)
    monkeypatch.setattr(retriever, "knowledge_dir", BACKEND_DIR / "knowledge_base")
    monkeypatch.setattr(retriever_module, "load_documents", lambda _dir: [])
    monkeypatch.setattr(retriever_module.pgvector_client, "init_table", lambda: None)
    monkeypatch.setattr(retriever_module.pgvector_client, "replace", lambda chunks, embeddings: 0)

    retriever._run_rebuild_loop()

    status = retriever.rebuild_status()
    assert status["status"] == "success"
    assert status["total_chunks"] == 0
    assert status["detail"] is None
