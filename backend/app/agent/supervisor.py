"""
Supervisor Agent — 意图识别与任务路由

职责：
  - 分析用户输入，识别意图
  - 路由到对应的子 Agent（detection / analysis / qa）
  - 汇总各 Agent 结果，生成最终回复
"""

import re

from langchain_core.messages import SystemMessage

from app.agent.prompts import SUPERVISOR_ROUTING_PROMPT
from app.core.logger import get_logger

logger = get_logger(__name__)


class SupervisorAgent:
    """主管 Agent"""

    def __init__(self, llm):
        self.llm = llm

    def route(self, state: dict) -> dict:
        """路由：优先使用 LLM，失败时使用确定性规则。"""
        latest_message = state["messages"][-1]
        if self.llm is None:
            next_agent = self._keyword_route(str(latest_message.content))
        else:
            messages = [
                SystemMessage(content=SUPERVISOR_ROUTING_PROMPT),
                latest_message,
            ]
            try:
                response = self.llm.invoke(messages)
                next_agent = self._parse_llm_route(response.content)
                if next_agent is None:
                    logger.warning(
                        "Supervisor LLM 返回非法路由，使用规则降级: %r",
                        response.content,
                    )
                    next_agent = self._keyword_route(str(latest_message.content))
            except Exception as exc:
                logger.warning("Supervisor LLM 路由失败，使用规则降级: %s", exc)
                next_agent = self._keyword_route(str(latest_message.content))

        logger.info("Supervisor 路由决策: %s", next_agent)
        return {"next_agent": next_agent}

    @staticmethod
    def _parse_llm_route(content: object) -> str | None:
        """从模型响应中提取唯一且合法的专家名称。"""
        matches = re.findall(r"\b(detection|analysis|qa)\b", str(content).lower())
        unique_matches = set(matches)
        if len(unique_matches) == 1:
            return unique_matches.pop()
        return None

    @staticmethod
    def _keyword_route(message: str) -> str:
        """无 LLM 时根据常见领域词进行稳定路由。"""
        analysis_terms = ("统计", "多少次", "数量", "趋势", "历史", "今日", "今天", "本周", "类别", "用户", "角色", "权限")
        detection_terms = ("检测", "图片", "图像", "照片", "视频", "zip", "压缩包", "缺陷")
        if any(term in message.lower() for term in analysis_terms):
            return "analysis"
        if any(term in message.lower() for term in detection_terms):
            return "detection"
        return "qa"

    def decide_next(self, state: dict) -> str:
        """条件路由：根据 Supervisor 决策跳转。"""
        next_agent = state.get("next_agent", "qa")
        return next_agent if next_agent in {"detection", "analysis", "qa"} else "qa"

    def summarize(self, state: dict) -> dict:
        """专家节点已负责生成结果，此处只透传选中的结果。"""
        for key in ("detection_result", "analysis_result", "qa_result"):
            if state.get(key):
                return {"final_response": str(state[key])}
        return {"final_response": "抱歉，我没有理解您的请求，请重新描述。"}
