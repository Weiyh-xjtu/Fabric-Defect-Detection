"""Runtime LangGraph orchestrator used by the chat API."""
from typing import AsyncGenerator
from langchain_core.messages import HumanMessage
from app.agent.detection_agent import detection_agent
from app.agent.graph import build_agent_graph

class MultiAgentOrchestrator:
    def __init__(self):
        def mark(name):
            return lambda _state: {f"{name}_result": name}
        # Routing uses deterministic Supervisor rules so it remains available when
        # the external LLM is temporarily unreachable. Specialists still use the LLM.
        self.graph = build_agent_graph(None, mark("detection"), mark("analysis"), mark("qa"))
        self.specialist = detection_agent

    async def route(self, message: str, attachments: list[dict] | None = None) -> str:
        routed_message = message
        if attachments:
            routed_message += " 检测附件"
        state = await self.graph.ainvoke({"messages": [HumanMessage(content=routed_message)]})
        return state.get("next_agent", "qa")

    async def chat_stream(self, **kwargs) -> AsyncGenerator[dict, None]:
        agent_name = await self.route(kwargs.get("message", ""), kwargs.get("attachments"))
        yield {"type": "agent_route", "agent": agent_name}
        async for event in self.specialist.chat_stream(**kwargs):
            event.setdefault("agent", agent_name)
            yield event

    async def chat(self, **kwargs) -> dict:
        agent_name = await self.route(kwargs.get("message", ""), kwargs.get("attachments"))
        result = await self.specialist.chat(**kwargs)
        result["agent_used"] = agent_name
        return result

multi_agent = MultiAgentOrchestrator()
