"""Shared state for the LangGraph multi-agent workflow."""
from typing import Annotated, Any, TypedDict

try:
    from langgraph.graph.message import add_messages
except Exception:  # pragma: no cover
    def add_messages(left, right):
        return list(left or []) + list(right or [])


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    next_agent: str
    detection_result: dict[str, Any]
    analysis_result: dict[str, Any]
    qa_result: str
    final_response: str
    user_id: int | None
    session_id: str | None
    attachments: list[dict[str, Any]]
