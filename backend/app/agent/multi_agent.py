"""Runtime LangGraph orchestrator used by the chat API.

复合意图（如“检测这张图片，并告诉我什么是YOLO”）由 Supervisor 规划为多个
子任务后并行执行：每个专家一个 asyncio 任务，工具事件实时透传，文本按计划
顺序分节合并为单一有序流。单意图路径与并行能力引入前的行为完全一致。
"""
import asyncio
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage

from app.agent.detection_agent import (
    DetectionAgent,
    _append_attachment_context,
    create_llm,
    detect_batch_images,
    detect_single_image,
    detect_video_file,
    detect_zip_images_file,
    list_session_attachments,
    query_detection_statistics,
    query_detection_trends,
    query_system_roles,
    query_system_users,
    search_knowledge,
)
from app.agent.memory import conversation_memory
from app.agent.prompts import ANALYSIS_PROMPT, QA_PROMPT
from app.agent.graph import build_agent_graph
from app.core.logger import get_logger

logger = get_logger(__name__)

_USE_CONFIGURED_LLM = object()

# 分节标题所用中文名与图标；须与前端 frontend/src/utils/toolChain.js 的
# AGENT_NAME_MAP 保持一致（双侧各 3 项，修改时同步）。
AGENT_LABELS = {"detection": "检测专家", "analysis": "数据分析", "qa": "知识问答"}
AGENT_EMOJIS = {"detection": "🔍", "analysis": "📊", "qa": "📖"}

# 队列内部哨兵事件类型：标记某个专家流结束，仅编排器内部消费，绝不对外 yield。
_SPECIALIST_DONE = "_specialist_done"


def _section_header(agent: str, first: bool) -> str:
    """并行模式下各专家回答的分节标题；首节不带分割线。"""
    label = AGENT_LABELS.get(agent, agent)
    emoji = AGENT_EMOJIS.get(agent, "🤖")
    return ("" if first else "\n\n---\n\n") + f"#### {emoji} {label}\n\n"


class MultiAgentOrchestrator:
    def __init__(self, supervisor_llm=_USE_CONFIGURED_LLM):
        def mark(name):
            return lambda _state: {f"{name}_result": name}

        # 生产运行时优先使用 LLM 路由；显式传入 None 可用于离线测试，
        # Supervisor 本身仍会在调用失败或输出非法时降级到关键词规则。
        if supervisor_llm is _USE_CONFIGURED_LLM:
            supervisor_llm = create_llm()
        self.graph = build_agent_graph(
            supervisor_llm,
            mark("detection"),
            mark("analysis"),
            mark("qa"),
        )
        self.specialists = {
            "detection": DetectionAgent(
                [
                    list_session_attachments,
                    detect_single_image,
                    detect_batch_images,
                    detect_zip_images_file,
                    detect_video_file,
                ],
                name="detection",
            ),
            "analysis": DetectionAgent(
                [query_detection_statistics, query_detection_trends, query_system_users, query_system_roles],
                system_prompt=(
                    ANALYSIS_PROMPT
                    + " 必须调用工具获取真实数据，禁止编造统计数字。"
                    "询问今日/今天时调用 query_detection_statistics 并设置 today=true。"
                    "询问批量、单图或视频检测次数时，分别设置 task_type=batch、single 或 video；"
                    "询问每日趋势、缺陷类别分布或哪类缺陷最多时调用 query_detection_trends。"
                    "只能根据工具实际返回的字段回答；如果工具返回 error，或用户要求的统计维度"
                    "不在工具结果中，必须明确说明当前无法查询该数据，禁止用总任务数等无关统计代替。"
                    "没有数据时明确回答对应筛选条件下暂无检测记录，不要要求上传附件。"
                ),
                name="analysis",
            ),
            "qa": DetectionAgent(
                [search_knowledge],
                system_prompt=(
                    QA_PROMPT
                    + " 领域问题必须先调用 search_knowledge，并根据来源片段回答；检索不到时明确说明。"
                    "回答末尾用「来源：」列出引用的来源文件名；"
                    "如果工具返回 retrieval_mode=lexical_fallback，须说明本次为本地词法检索（向量检索暂不可用）。"
                ),
                name="qa",
            ),
        }

    async def route(self, message: str, attachments: list[dict] | None = None) -> str:
        routed_message = message
        if attachments:
            routed_message += " 检测附件"
        state = await self.graph.ainvoke({"messages": [HumanMessage(content=routed_message)]})
        return state.get("next_agent", "qa")

    async def plan(self, message: str, attachments: list[dict] | None = None) -> list[dict]:
        """规划子任务。永远返回非空列表；单意图时长度为 1。"""
        routed_message = message
        if attachments:
            routed_message += " 检测附件"
        state = await self.graph.ainvoke({"messages": [HumanMessage(content=routed_message)]})
        plan = [
            entry
            for entry in (state.get("plan") or [])
            if isinstance(entry, dict) and entry.get("agent") in self.specialists and entry.get("task")
        ]
        if not plan:
            plan = [{"agent": state.get("next_agent", "qa"), "task": message}]
        # 有附件但规划漏掉 detection 时强制补上，保持“带附件必检测”的既有偏置
        if attachments and all(entry["agent"] != "detection" for entry in plan):
            plan.insert(0, {"agent": "detection", "task": "检测用户上传的附件"})
        return plan

    def _specialist_kwargs(self, entry: dict, kwargs: dict) -> dict:
        """构造并行模式下单个专家的调用参数。"""
        sk = dict(kwargs)
        sk["message"] = entry["task"]
        sk["record_memory"] = False  # 会话记忆由编排器统一写一次
        if entry["agent"] != "detection":
            # 附件只交给检测专家：其余专家不注册附件轮次，避免重复写入
            sk["attachments"] = None
            sk["image_path"] = None
        return sk

    async def chat_stream(self, **kwargs) -> AsyncGenerator[dict, None]:
        plan = await self.plan(kwargs.get("message", ""), kwargs.get("attachments"))
        if len(plan) == 1:
            # 单意图：与并行能力引入前完全一致的路径（专家自管记忆与附件注册）
            agent_name = plan[0]["agent"]
            yield {"type": "agent_route", "agent": agent_name}
            async for event in self.specialists[agent_name].chat_stream(**kwargs):
                event.setdefault("agent", agent_name)
                yield event
            return
        async for event in self._parallel_chat_stream(plan, **kwargs):
            yield event

    async def _parallel_chat_stream(self, plan: list[dict], **kwargs) -> AsyncGenerator[dict, None]:
        """并行执行多个专家并把事件合并为单一有序流。

        合并策略：
          - tool_call / tool_result 实时透传（带 agent 标签），工具并行过程可见；
          - 文本按计划顺序“有序渐进冲刷”：首位专家实时直出，其余按专家缓冲，
            前序专家完成后依序发分节标题、冲刷缓冲并转为实时；
          - 单专家失败转为该节 ⚠️ 文本；全部失败才对外发真正的 error 事件。
        """
        agents = [entry["agent"] for entry in plan]
        yield {"type": "agent_route", "agent": agents[0], "agents": agents, "plan": plan}

        queue: asyncio.Queue = asyncio.Queue()

        async def produce(entry: dict) -> None:
            agent_name = entry["agent"]
            try:
                async for event in self.specialists[agent_name].chat_stream(
                    **self._specialist_kwargs(entry, kwargs)
                ):
                    event["agent"] = agent_name
                    await queue.put(event)
            except Exception as exc:  # chat_stream 自身兜错，这里是双保险
                await queue.put({"type": "error", "agent": agent_name, "content": str(exc)})
            finally:
                await queue.put({"type": _SPECIALIST_DONE, "agent": agent_name})

        tasks = [asyncio.create_task(produce(entry)) for entry in plan]
        buffers: dict[str, list[str]] = {}  # 未“开播”专家的文本缓冲
        errors: dict[str, str] = {}
        finished: set[str] = set()
        section_started: set[str] = set()
        merged_chunks: list[str] = []  # 全量合并文本，结束后一次性写入会话记忆
        live = 0  # 当前实时直出的专家在 agents 中的下标

        def open_section(agent_name: str) -> dict:
            header = _section_header(agent_name, first=not section_started)
            section_started.add(agent_name)
            merged_chunks.append(header)
            return {"type": "text_chunk", "content": header, "agent": agent_name}

        try:
            while len(finished) < len(agents):
                event = await queue.get()
                etype = event.get("type")
                agent_name = event.get("agent")

                if etype == _SPECIALIST_DONE:
                    finished.add(agent_name)
                    # 依序推进：当前直播专家完成 → 输出失败占位（如有）→ 冲刷下一位缓冲
                    while live < len(agents) and agents[live] in finished:
                        current = agents[live]
                        if current in errors:
                            if current not in section_started:
                                yield open_section(current)
                            notice = (
                                "\n\n" if merged_chunks and not merged_chunks[-1].endswith("\n\n") else ""
                            ) + f"⚠️ {AGENT_LABELS.get(current, current)}处理失败：{errors[current]}"
                            merged_chunks.append(notice)
                            yield {"type": "text_chunk", "content": notice, "agent": current}
                        live += 1
                        if live < len(agents):
                            pending = buffers.pop(agents[live], [])
                            if pending:
                                yield open_section(agents[live])
                                for chunk in pending:
                                    merged_chunks.append(chunk)
                                    yield {"type": "text_chunk", "content": chunk, "agent": agents[live]}

                elif etype == "text_chunk":
                    if live < len(agents) and agent_name == agents[live]:
                        if agent_name not in section_started:
                            yield open_section(agent_name)
                        merged_chunks.append(str(event.get("content", "")))
                        yield event
                    else:
                        buffers.setdefault(agent_name, []).append(str(event.get("content", "")))

                elif etype == "error":
                    # 原始 error 事件会让前端整条消息进入错误态并覆盖已渲染内容，
                    # 单专家失败降级为段内 ⚠️ 文本，只有全部失败才对外发 error。
                    errors[agent_name] = str(event.get("content", "处理失败"))

                else:
                    # tool_call / tool_result 等事件实时放行（带 agent 标签）
                    yield event

            if len(errors) == len(agents):
                detail = "；".join(f"{AGENT_LABELS.get(a, a)}：{errors[a]}" for a in agents)
                yield {"type": "error", "content": f"所有专家处理失败：{detail}"}
                return

            # 正常/部分失败结束：编排器统一写一次会话记忆。
            # 用户消息带附件路径提示，与单专家路径的记忆内容保持一致。
            session_id = kwargs.get("session_id")
            user_id = kwargs.get("user_id")
            decorated = _append_attachment_context(
                kwargs.get("message", ""), kwargs.get("attachments"), kwargs.get("image_path")
            )
            conversation_memory.append(session_id, "user", decorated, user_id)
            merged_text = "".join(merged_chunks)
            if merged_text.strip():
                conversation_memory.append(session_id, "assistant", merged_text, user_id)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def chat(self, **kwargs) -> dict:
        plan = await self.plan(kwargs.get("message", ""), kwargs.get("attachments"))
        if len(plan) == 1:
            agent_name = plan[0]["agent"]
            result = await self.specialists[agent_name].chat(**kwargs)
            result["agent_used"] = agent_name
            return result

        # 并行执行；DetectionAgent.chat 自行捕获异常并返回错误文案，gather 不会抛出
        results = await asyncio.gather(
            *(
                self.specialists[entry["agent"]].chat(**self._specialist_kwargs(entry, kwargs))
                for entry in plan
            )
        )
        sections: list[str] = []
        steps: list = []
        for entry, result in zip(plan, results):
            sections.append(_section_header(entry["agent"], first=not sections) + result.get("output", ""))
            steps.extend(result.get("intermediate_steps", []))
        merged = "".join(sections)

        session_id = kwargs.get("session_id")
        user_id = kwargs.get("user_id")
        decorated = _append_attachment_context(
            kwargs.get("message", ""), kwargs.get("attachments"), kwargs.get("image_path")
        )
        conversation_memory.append(session_id, "user", decorated, user_id)
        conversation_memory.append(session_id, "assistant", merged, user_id)
        return {
            "output": merged,
            "intermediate_steps": steps,
            "agent_used": ",".join(entry["agent"] for entry in plan),
        }

multi_agent = MultiAgentOrchestrator()
