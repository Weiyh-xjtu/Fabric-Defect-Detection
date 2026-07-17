"""
LangGraph 多 Agent 共享状态定义

AgentState 是所有 Agent 共享的状态容器，在 LangGraph 状态图中流转。
每个 Agent 读取和修改状态中的特定字段。
"""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """多 Agent 共享状态"""

    # 对话消息列表（使用 add_messages reducer 自动追加）
    messages: Annotated[list, add_messages]

    # 路由决策
    next_agent: str  # "detection" | "analysis" | "qa"（主专家，兼容单选调用方）
    next_agents: list[str]  # 本轮需并行执行的专家（保序去重）；单意图时长度为 1
    plan: list[dict]  # [{"agent": str, "task": str}] 子任务规划

    # 各 Agent 的执行结果
    # 注意：并行 fan-out 时多个专家节点在同一 superstep 执行，langgraph 的
    # LastValue 通道禁止两个节点在同一步写同一个 key（否则抛 InvalidUpdateError）。
    # 这里三个专家分别写各自的 *_result key，互不冲突；messages 用 add_messages
    # reducer 亦可安全并发追加。新增并行节点时务必沿用“各写各的 key”约定。
    detection_result: dict
    analysis_result: dict
    qa_result: str

    # 最终回复
    final_response: str

    # 用户信息
    user_id: int
    session_id: str