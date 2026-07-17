"""
LangGraph 状态图 — 多 Agent 工作流编排

架构：
  用户输入 → Supervisor（规划）→ Detection/Analysis/QA Agent（可并行）→ 汇总 → 回复

Supervisor 的 decide_next 可返回节点名列表：复合意图时多个专家节点在同一
superstep 并行执行，各自写入独立的 *_result 状态键，随后 summarize 汇聚一次。
单意图时列表长度为 1，行为与单选路由一致。
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

    # Supervisor 条件路由：decide_next 返回节点名列表，langgraph 对每一项映射到
    # 对应节点并在同一步并行触发，实现复合意图的并行扇出。
    workflow.add_conditional_edges(
        "supervisor",
        supervisor.decide_next,
        {
            "detection": "detection",
            "analysis": "analysis",
            "qa": "qa",
        },
    )

    # 各 Agent 执行后进入汇总；即使多个专家并行触发，summarize 也只在其后执行一次。
    workflow.add_edge("detection", "summarize")
    workflow.add_edge("analysis", "summarize")
    workflow.add_edge("qa", "summarize")
    workflow.add_edge("summarize", END)

    compiled = workflow.compile()
    logger.info("LangGraph 多 Agent 状态图构建完成")
    return compiled