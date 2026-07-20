import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from app.agent.graph import build_agent_graph
from app.agent.memory import ConversationMemory
from app.agent.multi_agent import MultiAgentOrchestrator
from app.agent.detection_agent import (
    _current_session_id,
    _current_user_id,
    list_session_attachments,
    search_knowledge,
)
from app.storage.redis_client import redis_client
from app.rag.retriever import KnowledgeRetriever, knowledge_retriever
from app.rag.document_loader import load_documents, split_documents
from app.agent.detection_agent import DETECTION_TOOLS
from app.rag.embedding import embedding_service
from app.vectorstore.pgvector_client import pgvector_client
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


def test_supervisor_plans_multiple_agents_from_json():
    """LLM 输出 JSON 子任务数组时应拆出多专家并行计划。"""
    llm = _FakeRouteLLM(
        '[{"agent":"detection","task":"检测这张图片"},{"agent":"qa","task":"什么是YOLO"}]'
    )
    supervisor = SupervisorAgent(llm)
    result = supervisor.route(
        {"messages": [HumanMessage(content="检测这张图片，并告诉我什么是YOLO")]}
    )
    assert result["next_agent"] == "detection"
    assert result["next_agents"] == ["detection", "qa"]
    assert result["plan"][0]["task"] == "检测这张图片"
    assert result["plan"][1]["task"] == "什么是YOLO"


def test_supervisor_plan_parses_fenced_json():
    """带 ```json 围栏的输出也应正确解析。"""
    llm = _FakeRouteLLM('```json\n[{"agent":"analysis","task":"今日检测次数"}]\n```')
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="今日检测次数")]})
    assert result["next_agents"] == ["analysis"]
    assert result["plan"] == [{"agent": "analysis", "task": "今日检测次数", "depends_on": []}]


def test_supervisor_plan_merges_duplicate_agents():
    """同一专家的多条子任务应合并为一条，保持每个专家最多执行一次。"""
    llm = _FakeRouteLLM(
        '[{"agent":"qa","task":"什么是IoU"},{"agent":"qa","task":"什么是mAP"}]'
    )
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="解释两个概念")]})
    assert result["next_agents"] == ["qa"]
    assert result["plan"] == [{"agent": "qa", "task": "什么是IoU；什么是mAP", "depends_on": []}]


def test_supervisor_plan_rejects_invalid_agent():
    """计划含非法专家名时整体作废，降级到关键词单选。"""
    llm = _FakeRouteLLM('[{"agent":"pilot","task":"驾驶飞机"}]')
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="请检测这张图片")]})
    assert result["next_agents"] == ["detection"]
    assert result["plan"] == [{"agent": "detection", "task": "请检测这张图片"}]


def test_supervisor_plan_parses_dependencies():
    """含 depends_on 的规划应保留依赖，供编排器做递进执行。"""
    llm = _FakeRouteLLM(
        '[{"agent":"detection","task":"检测这张图片"},'
        '{"agent":"analysis","task":"统计这类缺陷的历史次数","depends_on":["detection"]}]'
    )
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="检测并统计")]})
    assert result["next_agents"] == ["detection", "analysis"]
    plan = {entry["agent"]: entry for entry in result["plan"]}
    assert plan["detection"]["depends_on"] == []
    assert plan["analysis"]["depends_on"] == ["detection"]


def test_supervisor_plan_drops_dangling_and_self_dependencies():
    """指向不存在专家或自身的依赖应被剔除。"""
    llm = _FakeRouteLLM(
        '[{"agent":"qa","task":"解释","depends_on":["qa","detection"]}]'
    )
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="解释")]})
    plan = {entry["agent"]: entry for entry in result["plan"]}
    # 自引用与指向计划外的 detection 均被丢弃
    assert plan["qa"]["depends_on"] == []


def test_supervisor_plan_breaks_dependency_cycle():
    """依赖成环时整体降级为并行（清空所有 depends_on），避免死锁。"""
    llm = _FakeRouteLLM(
        '[{"agent":"detection","task":"a","depends_on":["qa"]},'
        '{"agent":"qa","task":"b","depends_on":["detection"]}]'
    )
    supervisor = SupervisorAgent(llm)
    result = supervisor.route({"messages": [HumanMessage(content="环")]})
    assert all(entry["depends_on"] == [] for entry in result["plan"])


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


def test_graph_fans_out_to_parallel_specialists():
    """复合意图规划应让多个专家节点并行执行并在汇总节点合并。"""
    planning_llm = _FakeRouteLLM(
        '[{"agent":"detection","task":"检测图片"},{"agent":"qa","task":"什么是YOLO"}]'
    )
    graph = build_agent_graph(
        planning_llm,
        lambda state: {"detection_result": "detected"},
        lambda state: {"analysis_result": "analysed"},
        lambda state: {"qa_result": "answered"},
    )
    result = graph.invoke({"messages": [HumanMessage(content="检测图片并解释YOLO")]})
    assert result["next_agents"] == ["detection", "qa"]
    assert result["detection_result"] == "detected"
    assert result["qa_result"] == "answered"
    assert not result.get("analysis_result")
    assert "detected" in result["final_response"]
    assert "answered" in result["final_response"]


@pytest.mark.asyncio
async def test_orchestrator_plan_forces_detection_for_attachments(monkeypatch):
    """带附件但规划漏掉 detection 时应在首位强制补上检测子任务。"""
    async def _qa_only(_state):
        return {"next_agent": "qa", "next_agents": ["qa"], "plan": [{"agent": "qa", "task": "什么是YOLO"}]}

    monkeypatch.setattr(multi_agent.graph, "ainvoke", _qa_only)
    plan = await multi_agent.plan("什么是YOLO", attachments=[{"type": "image", "path": "/tmp/x.jpg"}])
    assert plan[0]["agent"] == "detection"
    assert any(entry["agent"] == "qa" for entry in plan)

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


def test_retrieve_reports_pgvector_mode(monkeypatch):
    """向量索引可用时应走 pgvector 检索并如实标注模式。"""
    monkeypatch.setattr(pgvector_client, "count", lambda: 17)
    monkeypatch.setattr(embedding_service, "embed_query", lambda _query: [0.1, 0.2])
    monkeypatch.setattr(
        pgvector_client,
        "search",
        lambda _embedding, top_k: [
            {"content": "破洞处理", "source": "fabric_defects.md", "score": 0.87}
        ],
    )
    retrieval = knowledge_retriever.retrieve("破洞", 3)
    assert retrieval["mode"] == "pgvector"
    assert retrieval["fallback_reason"] is None
    assert retrieval["results"][0]["source"] == "fabric_defects.md"


def test_retrieve_falls_back_with_reason_when_embedding_fails(monkeypatch):
    """embedding 故障时降级为词法检索，且必须携带降级原因。"""
    monkeypatch.setattr(pgvector_client, "count", lambda: 17)

    def _boom(_query):
        raise RuntimeError("embedding offline")

    monkeypatch.setattr(embedding_service, "embed_query", _boom)
    retrieval = knowledge_retriever.retrieve("破洞缺陷", 3)
    assert retrieval["mode"] == "lexical_fallback"
    assert "RuntimeError" in retrieval["fallback_reason"]


def test_retrieve_reports_empty_vector_index(monkeypatch):
    """向量索引未构建时应说明原因，而不是静默词法检索。"""
    monkeypatch.setattr(pgvector_client, "count", lambda: 0)
    retrieval = knowledge_retriever.retrieve("破洞缺陷", 3)
    assert retrieval["mode"] == "lexical_fallback"
    assert "向量索引为空" in retrieval["fallback_reason"]


def test_search_knowledge_tool_reports_mode_and_sources(monkeypatch):
    """检索工具需返回检索模式和去重后的来源文件列表。"""
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
    monkeypatch.setattr(
        knowledge_retriever,
        "retrieve",
        lambda query, top_k=3: {
            "mode": "pgvector",
            "fallback_reason": None,
            "results": [
                {"content": "破洞", "source": "fabric_defects.md", "score": 0.9},
                {"content": "评估", "source": "model_evaluation.md", "score": 0.5},
                {"content": "补充", "source": "fabric_defects.md", "score": 0.4},
            ],
        },
    )
    payload = json.loads(search_knowledge.invoke({"query": "破洞怎么处理"}))
    assert payload["retrieval_mode"] == "pgvector"
    assert payload["sources"] == ["fabric_defects.md", "model_evaluation.md"]
    assert len(payload["results"]) == 3


def test_embed_texts_batches_within_dashscope_limit(monkeypatch):
    """DashScope embedding 单批不能超过 10 条，否则 build 会 400。"""
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EMBEDDING_BASE_URL", "https://embedding.example/v1")
    calls = []

    class _FakeEmbeddings:
        def create(self, model, input):
            calls.append(len(input))
            return SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=[0.0] * settings.EMBEDDING_DIM)
                    for _ in input
                ]
            )

    monkeypatch.setattr(
        embedding_service, "_client", SimpleNamespace(embeddings=_FakeEmbeddings())
    )
    embeddings = embedding_service.embed_texts([f"chunk-{i}" for i in range(23)])
    assert len(embeddings) == 23
    assert calls == [10, 10, 3]

def test_agent_lists_legacy_session_attachments_for_repeat_detection(
    tmp_path, monkeypatch
):
    """无 user_id 的旧版会话也能通过附件查询工具拿到历史附件。"""
    monkeypatch.setattr(
        "app.agent.detection_agent._tool_permission_error", lambda _permission: None
    )
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


def test_analysis_specialist_requires_one_of_its_data_tools():
    """分析专家不能绕过数据工具直接生成统计结论。"""
    agent = multi_agent.specialists["analysis"]
    bound_names = {item.name for item in agent.tools}
    assert agent.required_tool_names == bound_names
    assert agent.max_required_tool_retries == 1
    assert agent._required_tools_for("统计今天的缺陷") == {
        "query_detection_statistics",
        "query_detection_trends",
    }
    assert agent._required_tools_for("系统有哪些角色和权限") == {
        "query_system_users",
        "query_system_roles",
    }

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


def test_agent_prompt_injects_current_date():
    """系统提示词必须包含 {current_date} 占位符，供每次调用注入实时日期，
    否则 LLM 会凭训练数据猜测“今天/昨天”对应的具体日期。"""
    from datetime import datetime

    from app.agent.detection_agent import DetectionAgent, _current_date_text

    agent = DetectionAgent([], name="analysis")
    system_template = agent.executor.agent.runnable.get_prompts()[0].messages[0].prompt.template
    assert "{current_date}" in system_template

    date_text = _current_date_text()
    assert datetime.now().strftime("%Y-%m-%d") in date_text
    assert "星期" in date_text
