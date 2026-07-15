import json

import pytest
from langchain_core.messages import HumanMessage
from app.agent.graph import build_agent_graph
from app.agent.memory import ConversationMemory
from app.agent.multi_agent import MultiAgentOrchestrator
from app.agent.detection_agent import (
    _current_session_id,
    _current_user_id,
    list_session_attachments,
)
from app.storage.redis_client import redis_client
from app.rag.retriever import KnowledgeRetriever
from app.rag.document_loader import load_documents, split_documents
from app.agent.detection_agent import DETECTION_TOOLS
from app.rag.embedding import embedding_service
from app.config.settings import settings
from app.agent.supervisor import SupervisorAgent

multi_agent = MultiAgentOrchestrator(supervisor_llm=None)

@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "expected"), [
    ("请检测这张图片", "detection"),
    ("最近检测数量趋势", "analysis"),
    ("什么是 IoU", "qa"),
    ("今日进行了哪些类别的检测任务", "analysis"),
    ("本周哪类缺陷最多", "analysis"),
])
async def test_supervisor_routes_to_specialists(message, expected):
    assert await multi_agent.route(message) == expected

class _FakeRouteLLM:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.error:
            raise self.error
        return type("Response", (), {"content": self.content})()


def test_supervisor_prefers_llm_over_keyword_rule():
    llm = _FakeRouteLLM("qa")
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="检测这张图片")]})
    assert result["next_agent"] == "qa"
    assert llm.calls == 1


def test_supervisor_falls_back_when_llm_fails():
    llm = _FakeRouteLLM(error=RuntimeError("offline"))
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="最近检测数量趋势")]})
    assert result["next_agent"] == "analysis"
    assert llm.calls == 1


def test_supervisor_falls_back_on_invalid_llm_output():
    llm = _FakeRouteLLM("我认为可以选择 detection 或 qa")
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="请检测附件")]})
    assert result["next_agent"] == "detection"


def test_graph_executes_selected_node():
    graph = build_agent_graph(
        None,
        lambda state: {"detection_result": "detected"},
        lambda state: {"analysis_result": "analysed"},
        lambda state: {"qa_result": "answered"},
    )
    result = graph.invoke({"messages": [HumanMessage(content="检测图片")]})
    assert result["next_agent"] == "detection"
    assert result["final_response"] == "detected"

def test_memory_appends_in_fallback_or_redis():
    memory = ConversationMemory(max_messages=3)
    session = "pytest-langgraph-memory"
    memory.clear(session)
    memory.append(session, "user", "第一条")
    memory.append(session, "assistant", "第二条")
    assert [item["content"] for item in memory.load(session)] == ["第一条", "第二条"]
    memory.clear(session)

def test_retriever_reads_existing_knowledge_base(tmp_path):
    (tmp_path / "custom.md").write_text("# 布匹缺陷\n破洞缺陷需要重点复检。", encoding="utf-8")
    retriever = KnowledgeRetriever(tmp_path)
    results = retriever.search("破洞缺陷")
    assert results
    assert results[0]["source"] == "custom.md"
    assert "重点复检" in results[0]["content"]

def test_agent_lists_legacy_session_attachments_for_repeat_detection(tmp_path):
    """无 user_id 的旧版会话也能通过附件查询工具拿到历史附件。"""
    image = tmp_path / "fabric.jpg"
    image.write_bytes(b"image")
    session = "pytest-repeat-detection"
    memory = ConversationMemory()
    memory.clear(session)
    original = [{"type": "image", "path": str(image), "filename": "fabric.jpg"}]
    memory.save_attachments(session, original)
    token_session = _current_session_id.set(session)
    token_user = _current_user_id.set(None)
    try:
        listing = json.loads(list_session_attachments.invoke({}))
    finally:
        _current_session_id.reset(token_session)
        _current_user_id.reset(token_user)
    assert listing["total_rounds"] == 1
    listed = listing["rounds"][0]["attachments"][0]
    assert listed["path"] == str(image)
    assert listed["filename"] == "fabric.jpg"
    assert listed["file_exists"] is True
    memory.clear(session)


def test_detection_specialist_binds_session_attachment_tool():
    """detection 专家必须绑定会话附件查询工具，才能复检历史图片。"""
    names = {item.name for item in multi_agent.specialists["detection"].tools}
    assert "list_session_attachments" in names

def test_day11_tool_count_and_groups():
    names = {item.name for item in DETECTION_TOOLS}
    assert len(names) >= 9
    assert {"query_detection_statistics", "query_detection_trends", "search_knowledge"} <= names

def test_document_loader_splits_markdown(tmp_path):
    (tmp_path / "knowledge.md").write_text("# A\n" + "内容" * 30, encoding="utf-8")
    chunks = split_documents(load_documents(tmp_path), chunk_size=20, overlap=5)
    assert len(chunks) > 1
    assert chunks[0]["metadata"]["source"] == "knowledge.md"


def test_embedding_uses_dedicated_credentials(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setattr(settings, "EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.setattr(settings, "QWEN_API_KEY", "chat-qwen-key")
    key, url, _model = embedding_service._configuration()
    assert (key, url) == ("embedding-key", "https://embedding.example/v1")


def test_memory_is_isolated_by_user():
    memory = ConversationMemory(max_messages=3)
    session = "same-session-id"
    memory.clear(session, 1)
    memory.clear(session, 2)
    memory.append(session, "user", "用户一的消息", 1)
    memory.append(session, "user", "用户二的消息", 2)
    assert memory.load(session, 1)[0]["content"] == "用户一的消息"
    assert memory.load(session, 2)[0]["content"] == "用户二的消息"
    memory.clear(session, 1)
    memory.clear(session, 2)
