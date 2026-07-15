"""Runtime LangGraph orchestrator used by the chat API."""
from typing import AsyncGenerator
from langchain_core.messages import HumanMessage
from app.agent.detection_agent import (
    DetectionAgent,
    create_llm,
    detect_batch_images,
    detect_single_image,
    detect_video_file,
    detect_zip_images_file,
    list_session_attachments,
    query_detection_statistics,
    query_detection_trends,
    query_system_roles,
    query_system_users,
    search_knowledge,
)
from app.agent.prompts import ANALYSIS_PROMPT, QA_PROMPT
from app.agent.graph import build_agent_graph

_USE_CONFIGURED_LLM = object()


class MultiAgentOrchestrator:
    def __init__(self, supervisor_llm=_USE_CONFIGURED_LLM):
        def mark(name):
            return lambda _state: {f"{name}_result": name}

        # 生产运行时优先使用 LLM 路由；显式传入 None 可用于离线测试，
        # Supervisor 本身仍会在调用失败或输出非法时降级到关键词规则。
        if supervisor_llm is _USE_CONFIGURED_LLM:
            supervisor_llm = create_llm()
        self.graph = build_agent_graph(
            supervisor_llm,
            mark("detection"),
            mark("analysis"),
            mark("qa"),
        )
        self.specialists = {
            "detection": DetectionAgent(
                [
                    list_session_attachments,
                    detect_single_image,
                    detect_batch_images,
                    detect_zip_images_file,
                    detect_video_file,
                ],
                name="detection",
            ),
            "analysis": DetectionAgent(
                [query_detection_statistics, query_detection_trends, query_system_users, query_system_roles],
                system_prompt=(
                    ANALYSIS_PROMPT
                    + " 必须调用工具获取真实数据，禁止编造统计数字。"
                    "询问今日/今天时调用 query_detection_statistics 并设置 today=true。"
                    "询问批量、单图或视频检测次数时，分别设置 task_type=batch、single 或 video；"
                    "询问每日趋势、缺陷类别分布或哪类缺陷最多时调用 query_detection_trends。"
                    "只能根据工具实际返回的字段回答；如果工具返回 error，或用户要求的统计维度"
                    "不在工具结果中，必须明确说明当前无法查询该数据，禁止用总任务数等无关统计代替。"
                    "没有数据时明确回答对应筛选条件下暂无检测记录，不要要求上传附件。"
                ),
                name="analysis",
            ),
            "qa": DetectionAgent(
                [search_knowledge],
                system_prompt=QA_PROMPT + " 领域问题必须先调用 search_knowledge，并根据来源片段回答；检索不到时明确说明。",
                name="qa",
            ),
        }

    async def route(self, message: str, attachments: list[dict] | None = None) -> str:
        routed_message = message
        if attachments:
            routed_message += " 检测附件"
        state = await self.graph.ainvoke({"messages": [HumanMessage(content=routed_message)]})
        return state.get("next_agent", "qa")

    async def chat_stream(self, **kwargs) -> AsyncGenerator[dict, None]:
        agent_name = await self.route(kwargs.get("message", ""), kwargs.get("attachments"))
        yield {"type": "agent_route", "agent": agent_name}
        async for event in self.specialists[agent_name].chat_stream(**kwargs):
            event.setdefault("agent", agent_name)
            yield event

    async def chat(self, **kwargs) -> dict:
        agent_name = await self.route(kwargs.get("message", ""), kwargs.get("attachments"))
        result = await self.specialists[agent_name].chat(**kwargs)
        result["agent_used"] = agent_name
        return result

multi_agent = MultiAgentOrchestrator()
