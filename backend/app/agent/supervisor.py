"""Supervisor node: deterministic intent routing with optional LLM refinement."""
from langchain_core.messages import HumanMessage, SystemMessage
from app.agent.prompts import SUPERVISOR_ROUTING_PROMPT
from app.core.logger import get_logger

logger = get_logger(__name__)

class SupervisorAgent:
    def __init__(self, llm=None):
        self.llm = llm

    def route(self, state: dict) -> dict:
        text = str((state.get("messages") or [{"content": ""}])[-1].content if hasattr((state.get("messages") or [None])[-1], "content") else (state.get("messages") or [{"content": ""}])[-1].get("content", "")).lower()
        if any(k in text for k in ("统计", "多少次", "趋势", "分析", "数量", "用户", "角色", "权限", "管理员")):
            choice = "analysis"
        elif any(k in text for k in ("检测", "图片", "视频", "zip", "批量")):
            choice = "detection"
        else:
            choice = "qa"
        if self.llm:
            try:
                value = self.llm.invoke([SystemMessage(content=SUPERVISOR_ROUTING_PROMPT), HumanMessage(content=text)]).content.strip().lower()
                if value in {"detection", "analysis", "qa"}:
                    choice = value
            except Exception as exc:
                logger.warning("Supervisor LLM 路由失败，使用规则路由: %s", exc)
        return {"next_agent": choice}

    def decide_next(self, state: dict) -> str:
        return state.get("next_agent", "qa") if state.get("next_agent") in {"detection", "analysis", "qa"} else "qa"

    def summarize(self, state: dict) -> dict:
        result = state.get("detection_result") or state.get("analysis_result") or state.get("qa_result")
        return {"final_response": result if isinstance(result, str) else str(result or "无法处理该请求")}
