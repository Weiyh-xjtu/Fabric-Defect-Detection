"""
Supervisor Agent — 意图识别与任务规划路由

职责：
  - 分析用户输入，识别一个或多个意图并拆分子任务
  - 路由到对应的子 Agent（detection / analysis / qa），复合意图时并行扇出
  - 汇总各 Agent 结果，生成最终回复
"""

import json
import re

from langchain_core.messages import SystemMessage

from app.agent.prompts import SUPERVISOR_PLANNING_PROMPT
from app.core.logger import get_logger

logger = get_logger(__name__)

_VALID_AGENTS = {"detection", "analysis", "qa"}


class SupervisorAgent:
    """主管 Agent"""

    def __init__(self, llm):
        self.llm = llm

    def route(self, state: dict) -> dict:
        """规划：优先用 LLM 输出 JSON 子任务数组，失败时逐级降级（单标签解析 → 关键词规则）。"""
        latest_message = state["messages"][-1]
        text = str(latest_message.content)
        if self.llm is None:
            plan = [{"agent": self._keyword_route(text), "task": text}]
        else:
            messages = [
                SystemMessage(content=SUPERVISOR_PLANNING_PROMPT),
                latest_message,
            ]
            try:
                response = self.llm.invoke(messages)
                plan = self._parse_plan(response.content)
                if plan is None:
                    single = self._parse_llm_route(response.content)
                    if single is None:
                        logger.warning(
                            "Supervisor LLM 返回非法规划，使用规则降级: %r",
                            response.content,
                        )
                        single = self._keyword_route(text)
                    plan = [{"agent": single, "task": text}]
            except Exception as exc:
                logger.warning("Supervisor LLM 规划失败，使用规则降级: %s", exc)
                plan = [{"agent": self._keyword_route(text), "task": text}]

        agents = list(dict.fromkeys(entry["agent"] for entry in plan))
        logger.info("Supervisor 规划决策: %s", agents)
        return {"next_agent": agents[0], "next_agents": agents, "plan": plan}

    @staticmethod
    def _parse_plan(content: object) -> list[dict] | None:
        """从可能带围栏/前后缀文本的输出中提取首个 JSON 数组；任一条目非法则整体返回 None。"""
        match = re.search(r"\[.*\]", str(content), re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list) or not data:
            return None
        merged: dict[str, dict] = {}
        plan: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                return None
            agent = str(item.get("agent", "")).strip().lower()
            task = str(item.get("task", "")).strip()
            if agent not in _VALID_AGENTS or not task:
                return None
            if agent in merged:
                # 同一专家的多条子任务合并为一条，保持每个专家最多执行一次
                merged[agent]["task"] += "；" + task
            else:
                merged[agent] = {"agent": agent, "task": task}
                plan.append(merged[agent])
        return plan or None

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
        """无 LLM 时根据常见领域词进行稳定路由。

        关键词规则只做单选：多个词组常同时命中同一句单意图消息
        （如“最近检测数量趋势”同时含检测词与统计词），多选会误触发
        额外专家；复合意图拆分只信任 LLM 规划结果。
        """
        analysis_terms = ("统计", "多少次", "数量", "趋势", "历史", "今日", "今天", "本周", "类别", "用户", "角色", "权限")
        detection_terms = ("检测", "图片", "图像", "照片", "视频", "zip", "压缩包", "缺陷")
        if any(term in message.lower() for term in analysis_terms):
            return "analysis"
        if any(term in message.lower() for term in detection_terms):
            return "detection"
        return "qa"

    def decide_next(self, state: dict) -> list[str]:
        """条件路由：返回需并行执行的节点列表（LangGraph 对列表逐项映射实现扇出）。"""
        candidates = state.get("next_agents") or [state.get("next_agent", "qa")]
        valid = list(dict.fromkeys(a for a in candidates if a in _VALID_AGENTS))
        return valid or ["qa"]

    def summarize(self, state: dict) -> dict:
        """合并所有已产出的专家结果；单专家时与原先行为一致（原样透传）。"""
        parts = [
            str(state[key])
            for key in ("detection_result", "analysis_result", "qa_result")
            if state.get(key)
        ]
        if not parts:
            return {"final_response": "抱歉，我没有理解您的请求，请重新描述。"}
        if len(parts) == 1:
            return {"final_response": parts[0]}
        return {"final_response": "\n\n".join(parts)}
