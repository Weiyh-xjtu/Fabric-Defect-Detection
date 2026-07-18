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


def _format_upstream_context(upstream_outputs: dict | None) -> str:
    """把上游专家的输出整理成注入下游任务 prompt 的依赖上下文。

    仅保留有实际文本的上游结果；全部为空时返回空串（视为无上下文）。
    """
    if not upstream_outputs:
        return ""
    parts: list[str] = []
    for agent, output in upstream_outputs.items():
        text = (output or "").strip()
        if text:
            parts.append(f"【{AGENT_LABELS.get(agent, agent)}的结果】\n{text}")
    if not parts:
        return ""
    body = "\n\n".join(parts)
    return (
        "[以下是你所依赖的其它专家已完成的结果，请据此完成上面的任务，不要重复它们已给出的内容]\n"
        + body
    )


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
                    "询问昨天/前天等某一天时，按系统提示中的当前日期换算出那一天，"
                    "把 start_date 和 end_date 都设为该日期（如昨天＝当前日期减一天）。"
                    "涉及具体日期区间时，把 start_date/end_date 传给工具即可。"
                    "关键：当用户只说了月日（如“7.15到7.17”“6月1号”）而没有明确年份时，"
                    "必须原样按“月-日”格式传参（如 start_date=\"07-15\"、end_date=\"07-17\"），"
                    "绝对不要自己猜测或补全年份——服务端会按当前系统时间填充正确年份。"
                    "只有用户明确写出年份时才传完整的 YYYY-MM-DD。"
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

    def _specialist_kwargs(
        self, entry: dict, kwargs: dict, upstream_outputs: dict | None = None
    ) -> dict:
        """构造并行模式下单个专家的调用参数。

        upstream_outputs：{上游专家名: 其输出文本}。非空时把上游结论拼进 message，
        使下游任务能基于上游结果继续（依赖递进）。
        """
        sk = dict(kwargs)
        task = entry["task"]
        context = _format_upstream_context(upstream_outputs)
        if context:
            task = f"{task}\n\n{context}"
        sk["message"] = task
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
        """按依赖关系执行多个专家并把事件合并为单一有序流。

        执行模型（依赖递进）：
          - 无 depends_on 的专家立即启动，天然并行；
          - 有 depends_on 的专家先等待其全部上游完成，再把上游的输出文本注入
            自己的任务 prompt（见 _specialist_kwargs 的 upstream_outputs），
            从而“用上游结果继续下游”，实现含依赖的递进执行；
          - 上游失败时下游不再执行，直接标记为失败（依赖缺失）。

        合并策略（与纯并行版一致）：
          - tool_call / tool_result 实时透传（带 agent 标签），工具过程可见；
          - 文本按计划顺序“有序渐进冲刷”：首位专家实时直出，其余按专家缓冲，
            前序专家完成后依序发分节标题、冲刷缓冲并转为实时；
          - 单专家失败转为该节 ⚠️ 文本；全部失败才对外发真正的 error 事件。
        """
        agents = [entry["agent"] for entry in plan]
        yield {"type": "agent_route", "agent": agents[0], "agents": agents, "plan": plan}

        queue: asyncio.Queue = asyncio.Queue()
        # 每个专家一个完成事件 + 其输出文本，供下游 gating 与结果注入使用。
        done_events: dict[str, asyncio.Event] = {a: asyncio.Event() for a in agents}
        outputs: dict[str, str] = {}        # agent -> 已收集的输出文本
        failed_agents: set[str] = set()      # 自身失败或上游失败而未执行的专家

        async def produce(entry: dict) -> None:
            agent_name = entry["agent"]
            collected: list[str] = []
            try:
                # 依赖递进：等待全部上游完成，再注入其输出继续本任务。
                for dep in entry.get("depends_on") or []:
                    await done_events[dep].wait()
                missing = [d for d in (entry.get("depends_on") or []) if d in failed_agents]
                if missing:
                    failed_agents.add(agent_name)
                    labels = "、".join(AGENT_LABELS.get(d, d) for d in missing)
                    await queue.put({
                        "type": "error",
                        "agent": agent_name,
                        "content": f"依赖的{labels}未成功完成，已跳过",
                    })
                    return
                upstream = {dep: outputs.get(dep, "") for dep in (entry.get("depends_on") or [])}
                async for event in self.specialists[agent_name].chat_stream(
                    **self._specialist_kwargs(entry, kwargs, upstream)
                ):
                    event["agent"] = agent_name
                    etype = event.get("type")
                    if etype == "text_chunk":
                        collected.append(str(event.get("content", "")))
                    elif etype == "error":
                        # 专家以 error 事件（而非抛异常）报告失败时也要标记，
                        # 否则依赖它的下游会误判上游成功而继续执行。
                        failed_agents.add(agent_name)
                    await queue.put(event)
            except Exception as exc:  # chat_stream 自身兜错，这里是双保险
                failed_agents.add(agent_name)
                await queue.put({"type": "error", "agent": agent_name, "content": str(exc)})
            finally:
                outputs[agent_name] = "".join(collected)
                done_events[agent_name].set()  # 无论成功失败都释放下游，避免死等
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

        # 依赖递进：按拓扑分波执行——同一波内相互独立可并行，波间串行；
        # 下游专家把已完成上游的输出注入自己的任务 prompt。
        by_agent = {entry["agent"]: entry for entry in plan}
        outputs: dict[str, str] = {}
        wave_steps: dict[str, list] = {}
        failed: set[str] = set()
        resolved: set[str] = set()
        remaining = [entry["agent"] for entry in plan]
        while remaining:
            ready = [
                a for a in remaining
                if set(by_agent[a].get("depends_on") or []) <= resolved
            ]
            if not ready:  # sanitize 后不应发生；兜底防止死循环
                ready = list(remaining)
            wave_agents = [a for a in ready if not (set(by_agent[a].get("depends_on") or []) & failed)]
            # 上游失败的下游直接标记失败，不再执行
            for a in ready:
                if a not in wave_agents:
                    failed.add(a)
                    outputs[a] = ""
            wave_results = await asyncio.gather(
                *(
                    self.specialists[a].chat(
                        **self._specialist_kwargs(
                            by_agent[a],
                            kwargs,
                            {dep: outputs.get(dep, "") for dep in (by_agent[a].get("depends_on") or [])},
                        )
                    )
                    for a in wave_agents
                )
            )
            for a, result in zip(wave_agents, wave_results):
                outputs[a] = result.get("output", "")
                wave_steps[a] = result.get("intermediate_steps", [])
            resolved |= set(ready)
            remaining = [a for a in remaining if a not in ready]

        sections: list[str] = []
        steps: list = []
        for entry in plan:
            agent_name = entry["agent"]
            if agent_name in failed:
                body = f"⚠️ {AGENT_LABELS.get(agent_name, agent_name)}处理失败：依赖未成功完成，已跳过"
            else:
                body = outputs.get(agent_name, "")
                steps.extend(wave_steps.get(agent_name, []))
            sections.append(_section_header(agent_name, first=not sections) + body)
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
