"""Runtime LangGraph orchestrator used by the chat API."""
from typing import AsyncGenerator
from langchain_core.messages import HumanMessage
from app.agent.detection_agent import (
    DetectionAgent,
    detect_batch_images,
    detect_single_image,
    detect_video_file,
    detect_zip_images_file,
    query_detection_statistics,
    query_detection_trends,
    query_system_roles,
    query_system_users,
    search_knowledge,
)
from app.agent.prompts import ANALYSIS_PROMPT, QA_PROMPT
from app.agent.graph import build_agent_graph

class MultiAgentOrchestrator:
    def __init__(self):
        def mark(name):
            return lambda _state: {f"{name}_result": name}
        # Routing uses deterministic Supervisor rules so it remains available when
        # the external LLM is temporarily unreachable. Specialists still use the LLM.
        self.graph = build_agent_graph(None, mark("detection"), mark("analysis"), mark("qa"))
        self.specialists = {
            "detection": DetectionAgent(
                [detect_single_image, detect_batch_images, detect_zip_images_file, detect_video_file],
                name="detection",
            ),
            "analysis": DetectionAgent(
                [query_detection_statistics, query_detection_trends, query_system_users, query_system_roles],
                system_prompt=(
                    ANALYSIS_PROMPT
                    + " 必须调用工具获取真实数据，禁止编造统计数字。"
                    "询问今日/今天时使用 days=1；询问类别、类型、分布或哪类最多时，"
                    "调用 query_detection_trends，并基于 class_distribution 回答。"
                    "没有数据时明确回答对应时间范围内暂无检测记录，不要要求上传附件。"
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
