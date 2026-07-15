"""LangGraph orchestration: Supervisor -> specialist -> summarize."""
from langgraph.graph import END, StateGraph
from app.agent.state import AgentState
from app.agent.supervisor import SupervisorAgent

def build_agent_graph(llm=None, detection_agent_node=None, analysis_agent_node=None, qa_agent_node=None):
    supervisor = SupervisorAgent(llm)
    def empty(name):
        return lambda state: {f"{name}_result": "该能力暂不可用"}
    workflow = StateGraph(AgentState)
    workflow.add_node("supervisor", supervisor.route)
    workflow.add_node("detection", detection_agent_node or empty("detection"))
    workflow.add_node("analysis", analysis_agent_node or empty("analysis"))
    workflow.add_node("qa", qa_agent_node or empty("qa"))
    workflow.add_node("summarize", supervisor.summarize)
    workflow.set_entry_point("supervisor")
    workflow.add_conditional_edges("supervisor", supervisor.decide_next, {"detection":"detection", "analysis":"analysis", "qa":"qa"})
    for name in ("detection", "analysis", "qa"):
        workflow.add_edge(name, "summarize")
    workflow.add_edge("summarize", END)
    return workflow.compile()
