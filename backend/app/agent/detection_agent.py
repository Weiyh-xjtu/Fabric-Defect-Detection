"""
检测智能体 — ReAct Agent + 检测工具绑定

职责：
  -. 创建 LangChain ReAct Agent
  - 绑定检测相关工具（单图/批量/ZIP）
  - 处理 SSE 流式输出 Agent 的思考过程和结果

架构：
  用户消息 → Agent（LLM 决策）→ 调用 DetectionTool → 返回 结果

使用方式：
  from app.agent.detection_agent import DetectionAgent

  agent = DetectionAgent()
  response = await agent.chat("检测这张图片", image_path="xxx.jpg")
"""

import contextvars
import copy
import json
from typing import AsyncGenerator

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.config.settings import settings
from app.core.logger import get_logger
from app.services.detection_service import detection_service

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════
# 零、请求级上下文（用于把当前用户/场景透传给工具）
# ══════════════════════════════════════════════════════════════
# 工具是模块级 @tool 函数，无法通过 LLM 参数传入 user_id（也不应让 LLM
# 决定归属用户）。这里用 contextvars 在每次请求开始时注入，工具执行时读取。
# contextvars 对 asyncio 任务安全，天然隔离并发请求。
_current_user_id: contextvars.ContextVar = contextvars.ContextVar(
    "current_user_id", default=None
)
_current_scene_id: contextvars.ContextVar = contextvars.ContextVar(
    "current_scene_id", default=None
)

# 暂存最近一次工具调用的「完整」检测结果 JSON（含 base64 标注图），
# 供 chat_stream 在 on_tool_end 时取出、原样发给前端渲染标注图。
# 工具返回给 LLM 的是剥离 base64 的精简版（省 token），二者数据通道分离。
_last_full_tool_result: contextvars.ContextVar = contextvars.ContextVar(
    "last_full_tool_result", default=None
)


# ══════════════════════════════════════════════════════════════
# 一、定义检测工具（Agent 可调用的 Tools）
# ══════════════════════════════════════════════════════════════


def _strip_base64_for_llm(result: dict) -> dict:
    """
    剥离检测结果中的 base64 标注图字段，返回精简后的副本。

    原因：base64 标注图（单张可达数万 token）会作为 ToolMessage 回喂给 LLM，
    但纯文本 LLM 无法理解图像像素，这些 token 对生成回复毫无帮助，纯属浪费，
    还可能撑爆上下文、拖慢响应。标注图通过前端检测结果卡片直接展示，无需过 LLM。

    注意：不修改原始 result（前端仍需 base64 渲染），只返回给 LLM 的精简副本。
    """
    if not isinstance(result, dict):
        return result
    slim = copy.copy(result)
    # 单图路径：顶层 base64 字段
    slim.pop("annotated_image_base64", None)
    # 批量/ZIP 路径：annotated_images 列表内每张图的 base64
    if isinstance(slim.get("annotated_images"), list):
        slim["annotated_images"] = [
            {k: v for k, v in item.items() if k != "annotated_image_base64"}
            if isinstance(item, dict)
            else item
            for item in slim["annotated_images"]
        ]
    # 视频路径：关键帧中的 base64 缩略图同样不能回传给 LLM。
    if isinstance(slim.get("key_frames"), list):
        slim["key_frames"] = [
            {k: v for k, v in frame.items() if k != "annotated_image_base64"}
            if isinstance(frame, dict)
            else frame
            for frame in slim["key_frames"]
        ]
    return slim


def _finalize_tool_result(result: dict) -> str:
    """
    把检测结果拆成两条通道：
      - 完整版（含 base64）存入 contextvar，供 chat_stream 发给前端渲染标注图；
      - 精简版（剥离 base64）作为工具返回值回喂给 LLM，避免浪费数万 token。

    Returns:
        精简版结果的 JSON 字符串（给 LLM）。
    """
    # 完整版原样存起来，前端 SSE 用它渲染标注图
    _last_full_tool_result.set(json.dumps(result, ensure_ascii=False))
    # 精简版返回给 LLM
    return json.dumps(_strip_base64_for_llm(result), ensure_ascii=False)


@tool
def detect_single_image(image_path: str, conf: float = 0.25, iou: float = 0.45) -> str:
    """
    检测单张图片中的目标物体。

    Args:
        image_path: 图片文件路径或 URL
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45

    Returns:
        JSON 字符串，包含检测结果（目标数量、类别统计、标注图路径）
    """
    result = detection_service.detect_single(
        image_path,
        conf=conf,
        iou=iou,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
    )
    return _finalize_tool_result(result)


@tool
def detect_batch_images(image_paths: list[str], conf: float = 0.25) -> str:
    """
    批量检测多张图片中的目标物体。

    Args:
        image_paths: 图片文件路径列表
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含每张图片的检测结果汇总
    """
    result = detection_service.detect_batch(
        image_paths,
        conf=conf,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
    )
    return _finalize_tool_result(result)


@tool
def detect_zip_images_file(zip_path: str, conf: float = 0.25) -> str:
    """
    解压 ZIP 文件并批量检测其中所有图片的目标物体。

    Args:
        zip_path: ZIP 文件路径
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含 ZIP 内所有图片的检测结果汇总
    """
    result = detection_service.detect_zip(
        zip_path,
        conf=conf,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
    )
    return _finalize_tool_result(result)

@tool
def detect_video_file(
    video_path: str, conf: float = 0.25, frame_sample_rate: int = 5
) -> str:
    """
    检测视频文件中的目标物体。对视频进行帧采样后逐帧检测。

    Args:
        video_path: 视频文件路径（mp4/avi/mov 等）
        conf: 置信度阈值，默认 0.25
        frame_sample_rate: 帧采样间隔，每 N 帧取 1 帧，默认 5

    Returns:
        JSON 字符串，包含视频检测结果（关键帧、目标统计、时长信息）
    """
    result = detection_service.detect_video(
        video_path,
        conf=conf,
        frame_sample_rate=frame_sample_rate,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
    )
    result["type"] = "video"
    return _finalize_tool_result(result)


# 工具列表（绑定到 Agent）
DETECTION_TOOLS = [detect_single_image, detect_batch_images, detect_zip_images_file, detect_video_file]


# ══════════════════════════════════════════════════════════════
# 二、创建 LLM 实例
# ══════════════════════════════════════════════════════════════


def create_llm():
    """
    根据配置创建 LLM 实例

    支持三种 LLM 后端：
      1. 通义千问（Qwen，通过 OpenAI 兼容接口）
      2. OpenAI（GPT-4o-mini）
      3. Ollama 本地部署
    """
    # 优先使用通义千问（国内访问快，有免费额度）
    qwen_api_key = getattr(settings, "QWEN_API_KEY", "")
    if qwen_api_key and qwen_api_key != "sk-your-qwen-api-key":
        api_key = qwen_api_key
        base_url = getattr(
            settings, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model_name = getattr(settings, "QWEN_MODEL", "qwen-plus")
    else:
        # 回退到 OpenAI
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    return ChatOpenAI(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=0.1,  # 低温度，减少随机性，检测结果需要确定性
    )


# ══════════════════════════════════════════════════════════════
# 三、创建 ReAct Agent
# ══════════════════════════════════════════════════════════════


class DetectionAgent:
    """检测智能体 — 封装 ReAct Agent 创建和对话逻辑"""

    def __init__(self):
        """初始化 Agent，创建 LLM 和 AgentExecutor"""
        self.llm = create_llm()

        # OpenAI Tools Agent 系统提示词
        system_prompt = """你是一个专业的目标检测助手。你可以帮用户检测图片中的目标物体。

重要规则：
- 当用户消息中包含 [附件图片路径: xxx] 时，xxx 就是图片的服务器路径，你应直接使用它调用检测工具
- 当用户消息中包含 [附件视频路径: xxx] 时，xxx 就是视频的服务器路径，你应直接使用它调用视频检测工具
- 不要要求用户再次提供路径，直接使用附件中给出的路径
- 对于单张图片，调用 detect_single_image 工具
- 对于多张图片或 ZIP 文件，调用 detect_batch_images 或 detect_zip_images_file 工具
- 对于视频文件，调用 detect_video_file 工具

工作流程：
1. 理解用户意图
2. 如果有附件路径，直接调用对应检测工具
3. 调用工具获取检测结果
4. 用自然语言总结检测结果

回复格式要求：
- 先报告检测到的目标总数
- 列出各类别的数量统计
- 对于视频检测，还要报告视频时长和处理的帧数
- 如果有标注图，告知用户可以在结果卡片中查看
- 简洁专业，不要过度解释"""

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        # 创建 OpenAI Tools Agent（与 ChatPromptTemplate + MessagesPlaceholder 完全兼容）
        agent = create_openai_tools_agent(
            llm=self.llm,
            tools=DETECTION_TOOLS,
            prompt=prompt,
        )

        self.executor = AgentExecutor(
            agent=agent,
            tools=DETECTION_TOOLS,
            verbose=True,  # 开发阶段开启，可查看 Agent 思考过程
            max_iterations=5,  # 限制循环次数，防止无限循环
            return_intermediate_steps=True,  # 返回中间步骤（Tool 调用记录）
        )

        logger.info("DetectionAgent 初始化完成，绑定 %d 个工具", len(DETECTION_TOOLS))

    async def chat(
        self,
        message: str,
        image_path: str = None,
        user_id: int = None,
        scene_id: int = None,
    ) -> dict:
        """
        处理用户对话消息

        Args:
            message: 用户文本消息
            image_path: 附带的图片路径（可选）
            user_id: 当前登录用户 ID，用于检测记录归属
            scene_id: 检测场景 ID（可选）

        Returns:
            Agent 响应字典
        """
        # 如果有图片附件，将路径信息追加到消息中
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"

        # 注入请求级上下文，供工具读取
        token_user = _current_user_id.set(user_id)
        token_scene = _current_scene_id.set(scene_id)
        try:
            result = await self.executor.ainvoke({"input": message})

            return {
                "output": result["output"],
                "intermediate_steps": result.get("intermediate_steps", []),
            }
        except Exception as e:
            logger.error("Agent 执行异常: %s", str(e), exc_info=True)
            return {
                "output": f"抱歉，处理过程中出现错误：{str(e)}",
                "intermediate_steps": [],
            }
        finally:
            _current_user_id.reset(token_user)
            _current_scene_id.reset(token_scene)

    async def chat_stream(
        self,
        message: str,
        image_path: str = None,
        user_id: int = None,
        scene_id: int = None,
    ) -> AsyncGenerator:
        """
        流式处理对话消息（用于 SSE）

        逐个 yield Agent 的思考步骤和最终结果

        Args:
            message: 用户文本消息
            image_path: 附带的图片路径（可选）
            user_id: 当前登录用户 ID，用于检测记录归属
            scene_id: 检测场景 ID（可选）

        Yields:
            SSE 事件数据字典
        """
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"

        # 注入请求级上下文，供工具读取
        token_user = _current_user_id.set(user_id)
        token_scene = _current_scene_id.set(scene_id)
        token_full = _last_full_tool_result.set(None)
        try:
            async for event in self.executor.astream_events(
                {"input": message},
                version="v2",
            ):
                event_kind = event["event"]

                if event_kind == "on_chat_model_stream":
                    # LLM 正在生成回复的文本片段
                    chunk = event["data"]["chunk"]
                    if hasattr(chunk, "content") and chunk.content:
                        yield {
                            "type": "text_chunk",
                            "content": chunk.content,
                        }

                elif event_kind == "on_tool_start":
                    # Agent 开始调用工具
                    tool_name = event["name"]
                    tool_input = event["data"].get("input", {})
                    logger.info("工具调用: %s, 输入: %s", tool_name, str(tool_input)[:200])
                    yield {
                        "type": "tool_call",
                        "tool": tool_name,
                        "input": tool_input,
                    }

                elif event_kind == "on_tool_end":
                    # 工具调用完成
                    # 兼容不同 LangChain 版本的 output 路径
                    tool_data = event.get("data", {})
                    tool_output = tool_data.get("output", "")
                    tool_name = event.get("name", "")
                    logger.info(
                        "工具完成: %s, output类型=%s, output长度=%d",
                        tool_name,
                        type(tool_output).__name__,
                        len(str(tool_output)) if tool_output else 0,
                    )
                    # 记录 event data 的所有键，便于调试
                    logger.debug("on_tool_end data keys: %s", list(tool_data.keys()))
                    # 发给前端的用「完整版」结果（含 base64 标注图），供结果卡片渲染；
                    # LLM 收到的仍是工具返回的精简版（不含 base64）。两条通道分离。
                    full_result = _last_full_tool_result.get()
                    frontend_result = (
                        full_result
                        if full_result is not None
                        else (str(tool_output) if tool_output else "")
                    )
                    _last_full_tool_result.set(None)  # 用完即清，防止串到下一次工具调用
                    yield {
                        "type": "tool_result",
                        "tool": tool_name,
                        "result": frontend_result,
                    }

        except Exception as e:
            logger.error("Agent 流式执行异常: %s", str(e), exc_info=True)
            yield {
                "type": "error",
                "content": f"处理出错：{str(e)}",
            }
        finally:
            _current_user_id.reset(token_user)
            _current_scene_id.reset(token_scene)
            _last_full_tool_result.reset(token_full)


# 创建全局单例
detection_agent = DetectionAgent()
