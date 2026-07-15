import pytest
from langchain_core.messages import HumanMessage
from app.agent.graph import build_agent_graph
from app.agent.memory import ConversationMemory
from app.agent.multi_agent import multi_agent
from app.agent.detection_agent import _resolve_conversation_attachments
from app.storage.redis_client import redis_client
from app.rag.retriever import KnowledgeRetriever

@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "expected"), [
    ("请检测这张图片", "detection"),
    ("最近检测数量趋势", "analysis"),
    ("什么是 IoU", "qa"),
])
async def test_supervisor_routes_to_specialists(message, expected):
    assert await multi_agent.route(message) == expected

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

def test_repeat_detection_restores_structured_attachments(tmp_path):
    image = tmp_path / "fabric.jpg"
    image.write_bytes(b"image")
    session = "pytest-repeat-detection"
    memory = ConversationMemory()
    memory.clear(session)
    original = [{"type": "image", "path": str(image), "filename": "fabric.jpg"}]
    memory.save_attachments(session, original)
    restored, legacy_path = _resolve_conversation_attachments(
        "再检测一次", [], None, session
    )
    assert restored == original
    assert legacy_path is None
    memory.clear(session)
