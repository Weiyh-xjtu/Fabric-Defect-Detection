"""并行多 Agent 编排测试。

用假的专家生成器替换真实 specialists，验证事件合并顺序、记忆写入、
失败隔离与客户端断连清理，全程离线、不触发真实 LLM。
"""
import asyncio

import pytest

import app.agent.multi_agent as ma
from app.agent.multi_agent import MultiAgentOrchestrator


class _FakeSpecialist:
    """按脚本 yield 事件的假专家；记录收到的 kwargs 与是否被取消。"""

    def __init__(self, events, delay: float = 0.0):
        self.events = events
        self.delay = delay
        self.received: dict = {}
        self.cancelled = False

    async def chat_stream(self, **kwargs):
        self.received = kwargs
        try:
            for event in self.events:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield dict(event)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.fixture
def orchestrator(monkeypatch):
    orch = MultiAgentOrchestrator(supervisor_llm=None)
    appends: list[tuple] = []
    monkeypatch.setattr(
        ma.conversation_memory,
        "append",
        lambda sid, role, content, uid=None: appends.append((role, content)),
    )
    orch._appends = appends  # 便于用例断言
    return orch


def _two_agent_plan(*_args, **_kwargs):
    async def _plan(message, attachments=None):
        return [
            {"agent": "detection", "task": "检测这张图片"},
            {"agent": "qa", "task": "什么是YOLO"},
        ]

    return _plan


async def _collect(agen):
    return [event async for event in agen]


@pytest.mark.asyncio
async def test_parallel_stream_merges_in_plan_order(orchestrator, monkeypatch):
    detection = _FakeSpecialist(
        [
            {"type": "tool_call", "tool": "detect_single_image", "input": {}},
            {"type": "tool_result", "tool": "detect_single_image", "result": '{"total_objects":3}'},
            {"type": "text_chunk", "content": "检出3个"},
            {"type": "text_chunk", "content": "目标"},
        ],
        delay=0.02,
    )
    qa = _FakeSpecialist(
        [
            {"type": "text_chunk", "content": "YOLO是"},
            {"type": "text_chunk", "content": "单阶段检测算法"},
        ]
    )
    orchestrator.specialists = {"detection": detection, "analysis": None, "qa": qa}
    monkeypatch.setattr(orchestrator, "plan", _two_agent_plan())

    events = await _collect(
        orchestrator.chat_stream(
            message="检测这张图片，并告诉我什么是YOLO",
            attachments=[{"type": "image", "path": "/tmp/x.jpg"}],
            user_id=1,
            session_id="s1",
        )
    )

    # 首事件是扩展 agent_route
    assert events[0]["type"] == "agent_route"
    assert events[0]["agents"] == ["detection", "qa"]
    # 哨兵不外泄
    assert not any(e["type"] == "_specialist_done" for e in events)
    # 无顶层 error
    assert not any(e["type"] == "error" for e in events)
    # 专家事件都带 agent 标签
    assert all(e.get("agent") for e in events if e["type"] != "agent_route")

    merged = "".join(e["content"] for e in events if e["type"] == "text_chunk")
    assert "🔍 检测专家" in merged and "📖 知识问答" in merged
    assert merged.index("检测专家") < merged.index("知识问答")
    # qa 文本必须排在 detection 文本之后（缓冲冲刷）
    assert merged.index("检出3个") < merged.index("YOLO是")

    # 附件只给 detection；两个专家都 record_memory=False
    assert qa.received["record_memory"] is False
    assert qa.received["attachments"] is None
    assert detection.received["record_memory"] is False
    assert detection.received["attachments"] is not None

    # 记忆恰好写 user + assistant 各一次
    assert [role for role, _ in orchestrator._appends] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_parallel_stream_tool_events_stream_live(orchestrator, monkeypatch):
    """非直播专家的工具事件应在直播专家完成前实时出现（文本仍缓冲）。"""
    detection = _FakeSpecialist([{"type": "text_chunk", "content": "检测结论"}], delay=0.05)
    qa = _FakeSpecialist(
        [
            {"type": "tool_call", "tool": "search_knowledge", "input": {}},
            {"type": "tool_result", "tool": "search_knowledge", "result": '{"results":[]}'},
            {"type": "text_chunk", "content": "YOLO说明"},
        ]
    )
    orchestrator.specialists = {"detection": detection, "analysis": None, "qa": qa}
    monkeypatch.setattr(orchestrator, "plan", _two_agent_plan())

    events = await _collect(
        orchestrator.chat_stream(message="x", user_id=1, session_id="s2")
    )
    types_in_order = [(e["type"], e.get("agent")) for e in events]
    # qa 的工具事件先于 detection 的文本 chunk 出现
    qa_tool_idx = types_in_order.index(("tool_result", "qa"))
    detection_text_idx = next(
        i for i, (t, a) in enumerate(types_in_order) if t == "text_chunk" and a == "detection"
    )
    assert qa_tool_idx < detection_text_idx


@pytest.mark.asyncio
async def test_parallel_stream_isolates_specialist_failure(orchestrator, monkeypatch):
    """单专家失败降级为段内 ⚠️ 文本，不发顶层 error，记忆照常写入。"""
    detection = _FakeSpecialist([{"type": "text_chunk", "content": "检测完成"}])
    qa = _FakeSpecialist([{"type": "error", "content": "boom"}])
    orchestrator.specialists = {"detection": detection, "analysis": None, "qa": qa}
    monkeypatch.setattr(orchestrator, "plan", _two_agent_plan())

    events = await _collect(
        orchestrator.chat_stream(message="x", user_id=1, session_id="s3")
    )
    assert not any(e["type"] == "error" for e in events)
    merged = "".join(e["content"] for e in events if e["type"] == "text_chunk")
    assert "检测完成" in merged
    assert "⚠️ 知识问答处理失败：boom" in merged
    assert [role for role, _ in orchestrator._appends] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_parallel_stream_all_failed_emits_error(orchestrator, monkeypatch):
    """全部专家失败时对外发一条真正的 error，且不写记忆。"""
    detection = _FakeSpecialist([{"type": "error", "content": "e1"}])
    qa = _FakeSpecialist([{"type": "error", "content": "e2"}])
    orchestrator.specialists = {"detection": detection, "analysis": None, "qa": qa}
    monkeypatch.setattr(orchestrator, "plan", _two_agent_plan())

    events = await _collect(
        orchestrator.chat_stream(message="x", user_id=1, session_id="s4")
    )
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "所有专家处理失败" in errors[0]["content"]
    assert orchestrator._appends == []


@pytest.mark.asyncio
async def test_parallel_stream_cancels_producers_on_close(orchestrator, monkeypatch):
    """客户端提前断连（aclose）应取消所有 producer 任务且不写记忆。"""
    detection = _FakeSpecialist(
        [{"type": "tool_call", "tool": "t", "input": {}}] + [{"type": "text_chunk", "content": "x"}] * 20,
        delay=0.01,
    )
    qa = _FakeSpecialist([{"type": "text_chunk", "content": "y"}] * 20, delay=0.01)
    orchestrator.specialists = {"detection": detection, "analysis": None, "qa": qa}
    monkeypatch.setattr(orchestrator, "plan", _two_agent_plan())

    agen = orchestrator.chat_stream(message="x", user_id=1, session_id="s5")
    first = await agen.__anext__()
    assert first["type"] == "agent_route"
    await agen.__anext__()  # 至少再取一个事件，确保 producer 已启动
    await agen.aclose()
    await asyncio.sleep(0.05)
    assert detection.cancelled or qa.cancelled
    assert orchestrator._appends == []


@pytest.mark.asyncio
async def test_chat_stream_single_intent_unchanged(orchestrator, monkeypatch):
    """单意图计划应保持原路径：仅前缀单 agent_route，其余事件与专家一致。"""
    specialist = _FakeSpecialist(
        [
            {"type": "text_chunk", "content": "答案"},
        ]
    )
    orchestrator.specialists = {"detection": None, "analysis": None, "qa": specialist}

    async def _single_plan(message, attachments=None):
        return [{"agent": "qa", "task": message}]

    monkeypatch.setattr(orchestrator, "plan", _single_plan)

    events = await _collect(
        orchestrator.chat_stream(message="什么是IoU", user_id=1, session_id="s6")
    )
    assert events[0] == {"type": "agent_route", "agent": "qa"}
    assert events[1]["type"] == "text_chunk"
    assert events[1]["content"] == "答案"
    assert events[1]["agent"] == "qa"
    # 单意图不带 agents 列表；专家自管记忆（record_memory 默认未被编排器改写）
    assert "agents" not in events[0]
    assert "record_memory" not in specialist.received


@pytest.mark.asyncio
async def test_detection_agent_record_memory_flag(monkeypatch):
    """record_memory=False 时专家不写会话记忆；默认写 user+assistant 两次。"""
    from app.agent.detection_agent import DetectionAgent

    agent = DetectionAgent([], name="qa")

    class _FakeExecutor:
        # AgentExecutor 是 Pydantic 模型不允许打补丁，直接整体替换为假执行器
        async def astream_events(self, *_args, **_kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": type("C", (), {"content": "回答"})()},
            }

    agent.executor = _FakeExecutor()

    import app.agent.detection_agent as da

    appends: list[tuple] = []
    monkeypatch.setattr(
        da.conversation_memory,
        "append",
        lambda sid, role, content, uid=None: appends.append((role, content)),
    )
    monkeypatch.setattr(da.conversation_memory, "load", lambda sid, uid=None: [])
    monkeypatch.setattr(da.conversation_memory, "save_attachments", lambda *a, **k: None)

    # record_memory=False → 零写入
    async for _ in agent.chat_stream(message="x", user_id=1, session_id="s", record_memory=False):
        pass
    assert appends == []

    # 默认 → user + assistant 两次
    async for _ in agent.chat_stream(message="x", user_id=1, session_id="s"):
        pass
    assert [role for role, _ in appends] == ["user", "assistant"]


def _dep_plan(plan):
    async def _plan(message, attachments=None):
        return plan

    return _plan


@pytest.mark.asyncio
async def test_parallel_stream_injects_upstream_output_into_dependent(orchestrator, monkeypatch):
    """依赖递进：下游专家应等上游完成，并把上游输出注入自己的任务 prompt。"""
    detection = _FakeSpecialist([{"type": "text_chunk", "content": "检出破洞缺陷"}], delay=0.02)
    analysis = _FakeSpecialist([{"type": "text_chunk", "content": "破洞本周共3次"}])
    orchestrator.specialists = {"detection": detection, "analysis": analysis, "qa": None}
    monkeypatch.setattr(
        orchestrator,
        "plan",
        _dep_plan([
            {"agent": "detection", "task": "检测这张图片", "depends_on": []},
            {"agent": "analysis", "task": "统计这类缺陷本周次数", "depends_on": ["detection"]},
        ]),
    )

    events = await _collect(
        orchestrator.chat_stream(
            message="检测这张图并统计这类缺陷本周次数",
            attachments=[{"type": "image", "path": "/tmp/x.jpg"}],
            user_id=1,
            session_id="dep1",
        )
    )
    assert not any(e["type"] == "error" for e in events)
    # 上游输出被注入下游任务
    assert "检出破洞缺陷" in analysis.received["message"]
    assert "检测这张图片" not in analysis.received["message"]  # 下游 message 用自己的 task
    assert "统计这类缺陷本周次数" in analysis.received["message"]
    merged = "".join(e["content"] for e in events if e["type"] == "text_chunk")
    assert merged.index("检出破洞缺陷") < merged.index("破洞本周共3次")


@pytest.mark.asyncio
async def test_parallel_stream_prefers_structured_detection_dependency(
    orchestrator, monkeypatch
):
    """真实检测工具结果应以最小结构化字段传递，不把检测专家正文注入分析任务。"""
    detection = _FakeSpecialist(
        [
            {
                "type": "tool_result",
                "tool": "detect_single_image",
                "result": '{"total_objects":1,"class_counts":{"hole":1},'
                '"detections":[{"class_name":"hole","confidence":0.8477}]}',
            },
            {"type": "text_chunk", "content": "目标总数 1 个，hole 置信度 0.8477"},
        ]
    )
    analysis = _FakeSpecialist([{"type": "text_chunk", "content": "今日破洞23个"}])
    orchestrator.specialists = {"detection": detection, "analysis": analysis, "qa": None}
    monkeypatch.setattr(
        orchestrator,
        "plan",
        _dep_plan([
            {"agent": "detection", "task": "检测图片", "depends_on": []},
            {"agent": "analysis", "task": "统计今日图中的缺陷", "depends_on": ["detection"]},
        ]),
    )

    await _collect(
        orchestrator.chat_stream(message="x", user_id=1, session_id="dep-structured")
    )

    message = analysis.received["message"]
    assert "[DEPENDENCY_DATA]" in message
    assert '"detected_classes": ["hole"]' in message
    assert '"class_counts": {"hole": 1}' in message
    assert "置信度 0.8477" not in message
    assert "目标总数 1 个" not in message


@pytest.mark.asyncio
async def test_parallel_stream_skips_dependent_when_upstream_fails(orchestrator, monkeypatch):
    """上游失败时下游跳过执行，标记为依赖缺失，且不启动下游专家。"""
    detection = _FakeSpecialist([{"type": "error", "content": "模型加载失败"}])
    analysis = _FakeSpecialist([{"type": "text_chunk", "content": "不应执行"}])
    orchestrator.specialists = {"detection": detection, "analysis": analysis, "qa": None}
    monkeypatch.setattr(
        orchestrator,
        "plan",
        _dep_plan([
            {"agent": "detection", "task": "检测这张图片", "depends_on": []},
            {"agent": "analysis", "task": "统计这类缺陷", "depends_on": ["detection"]},
        ]),
    )

    events = await _collect(
        orchestrator.chat_stream(message="x", user_id=1, session_id="dep2")
    )
    # 下游专家未被真正调用
    assert analysis.received == {}
    merged = "".join(e["content"] for e in events if e["type"] == "text_chunk")
    assert "⚠️ 检测专家处理失败：模型加载失败" in merged
    assert "数据分析" in merged  # 下游节以依赖缺失说明呈现
    assert "不应执行" not in merged


@pytest.mark.asyncio
async def test_chat_honors_dependencies_with_waves(orchestrator, monkeypatch):
    """非流式 chat() 应按波执行：上游先跑、输出注入下游。"""
    class _FakeChatSpecialist:
        def __init__(self, output):
            self.output = output
            self.received = {}

        async def chat(self, **kwargs):
            self.received = kwargs
            return {"output": self.output, "intermediate_steps": []}

    detection = _FakeChatSpecialist("检出污渍缺陷")
    analysis = _FakeChatSpecialist("污渍本周5次")
    orchestrator.specialists = {"detection": detection, "analysis": analysis, "qa": None}
    monkeypatch.setattr(
        orchestrator,
        "plan",
        _dep_plan([
            {"agent": "detection", "task": "检测", "depends_on": []},
            {"agent": "analysis", "task": "统计这类缺陷", "depends_on": ["detection"]},
        ]),
    )

    result = await orchestrator.chat(message="x", user_id=1, session_id="dep3")
    assert result["agent_used"] == "detection,analysis"
    assert "检出污渍缺陷" in analysis.received["message"]
    assert result["output"].index("检出污渍缺陷") < result["output"].index("污渍本周5次")
