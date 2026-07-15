"""
LangGraph 状态图 — 多 Agent 工作流编排

架构：
  用户输入 → Supervisor（路由）→ Detection/Analysis/QA Agent → 汇总 → 回复
"""

from langgraph.graph import END, StateGraph

from app.agent.state import AgentState
from app.core.logger import get_logger

logger = get_logger(__name__)


def build_agent_graph(llm, detection_agent_node, analysis_agent_node, qa_agent_node):
    """
    构建多 Agent 协作状态图

    Args:
        llm: LLM 实例
        detection_agent_node: 检测 Agent 节点函数
        analysis_agent_node: 分析 Agent 节点函数
        qa_agent_node: 问答 Agent 节点函数

    Returns:
        编译后的 LangGraph 图
    """
    from app.agent.supervisor import SupervisorAgent

    supervisor = SupervisorAgent(llm)

    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("supervisor", supervisor.route)
    workflow.add_node("detection", detection_agent_node)
    workflow.add_node("analysis", analysis_agent_node)
    workflow.add_node("qa", qa_agent_node)
    workflow.add_node("summarize", supervisor.summarize)

    # 设置入口
    workflow.set_entry_point("supervisor")

    # Supervisor 条件路由
    workflow.add_conditional_edges(
        "supervisor",
        supervisor.decide_next,
        {
            "detection": "detection",
            "analysis": "analysis",
            "qa": "qa",
        },
    )

    # 各 Agent 执行后进入汇总
    workflow.add_edge("detection", "summarize")
    workflow.add_edge("analysis", "summarize")
    workflow.add_edge("qa", "summarize")
    workflow.add_edge("summarize", END)

    compiled = workflow.compile()
    logger.info("LangGraph 多 Agent 状态图构建完成")
    return compiled