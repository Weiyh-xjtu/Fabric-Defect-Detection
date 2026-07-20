"""分析 Agent 强制数据查询工具调用的回归测试。"""

from types import SimpleNamespace

import pytest

import app.agent.detection_agent as da
from app.agent.detection_agent import DetectionAgent


@pytest.fixture
def required_agent(monkeypatch):
    agent = DetectionAgent(
        [],
        name="analysis",
        required_tool_names={"query_detection_statistics"},
        max_required_tool_retries=1,
    )
    appends: list[tuple[str, str]] = []
    monkeypatch.setattr(da.conversation_memory, "load", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        da.conversation_memory,
        "append",
        lambda _sid, role, content, _uid=None: appends.append((role, content)),
    )
    monkeypatch.setattr(
        da.conversation_memory, "save_attachments", lambda *_args, **_kwargs: None
    )
    agent._test_appends = appends
    return agent


@pytest.mark.asyncio
async def test_chat_retries_and_rejects_first_answer_without_required_tool(
    required_agent,
):
    """非流式首次直答应被丢弃，重试调用工具后才接受答案。"""

    class _FakeExecutor:
        def __init__(self):
            self.inputs = []

        async def ainvoke(self, payload):
            self.inputs.append(payload)
            if len(self.inputs) == 1:
                return {"output": "未经查询：今天只有 1 个缺陷", "intermediate_steps": []}
            return {
                "output": "查询结果：今天共有 4 个缺陷",
                "intermediate_steps": [
                    (
                        SimpleNamespace(tool="query_detection_statistics"),
                        '{"total_objects": 4}',
                    )
                ],
            }

    executor = _FakeExecutor()
    required_agent.executor = executor

    result = await required_agent.chat(
        message="统计今天的缺陷", user_id=1, session_id="required-chat"
    )

    assert result["output"] == "查询结果：今天共有 4 个缺陷"
    assert len(executor.inputs) == 2
    assert executor.inputs[0]["runtime_instruction"] == ""
    assert "上一次答案已被系统拒绝" in executor.inputs[1]["runtime_instruction"]
    assert [content for _role, content in required_agent._test_appends] == [
        "统计今天的缺陷",
        "查询结果：今天共有 4 个缺陷",
    ]


@pytest.mark.asyncio
async def test_chat_stream_discards_unverified_text_then_retries(required_agent):
    """流式首次直答不能泄漏到前端，第二次的工具事件和验证后文本正常输出。"""

    class _FakeExecutor:
        def __init__(self):
            self.inputs = []

        async def astream_events(self, payload, **_kwargs):
            self.inputs.append(payload)
            if len(self.inputs) == 1:
                yield {
                    "event": "on_chat_model_stream",
                    "data": {
                        "chunk": SimpleNamespace(content="未经查询的错误统计")
                    },
                }
                return
            yield {
                "event": "on_tool_start",
                "name": "query_detection_statistics",
                "data": {"input": {"today": True}},
            }
            yield {
                "event": "on_tool_end",
                "name": "query_detection_statistics",
                "data": {"output": '{"total_objects": 4}'},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="今天共检出 4 个缺陷")},
            }

    executor = _FakeExecutor()
    required_agent.executor = executor

    events = [
        event
        async for event in required_agent.chat_stream(
            message="统计今天的缺陷", user_id=1, session_id="required-stream"
        )
    ]

    assert len(executor.inputs) == 2
    assert "上一次答案已被系统拒绝" in executor.inputs[1]["runtime_instruction"]
    assert not any("未经查询" in event.get("content", "") for event in events)
    assert [event["type"] for event in events] == [
        "tool_call",
        "tool_result",
        "text_chunk",
    ]
    assert events[0]["tool"] == "query_detection_statistics"
    assert events[-1]["content"] == "今天共检出 4 个缺陷"
    assert required_agent._test_appends[-1] == (
        "assistant",
        "今天共检出 4 个缺陷",
    )


@pytest.mark.asyncio
async def test_chat_stream_errors_after_retry_still_skips_required_tool(required_agent):
    """连续两次拒绝调用工具时，不返回任何模型编造文本，只返回验证错误。"""

    class _FakeExecutor:
        def __init__(self):
            self.calls = 0

        async def astream_events(self, *_args, **_kwargs):
            self.calls += 1
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content=f"编造答案{self.calls}")},
            }

    executor = _FakeExecutor()
    required_agent.executor = executor

    events = [
        event
        async for event in required_agent.chat_stream(
            message="统计今天的缺陷", user_id=1, session_id="required-failure"
        )
    ]

    assert executor.calls == 2
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "系统已拒绝" in events[0]["content"]
    assert required_agent._test_appends == []

