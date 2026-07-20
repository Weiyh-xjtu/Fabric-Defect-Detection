"""
检测智能体 — ReAct Agent + 检测工具绑定

职责：
  -. 创建 LangChain ReAct Agent
  - 绑定检测工具及用户/角色只读查询工具
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
import os
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import AsyncGenerator

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from sqlalchemy import func

from app.config.settings import settings
from app.core.logger import get_logger
from app.core.rbac import (
    DASHBOARD_READ_ANY,
    DETECTION_EXECUTE,
    KNOWLEDGE_READ,
    USER_MANAGE,
    user_has_permission,
)
from app.database.session import SessionLocal
from app.services.dashboard_service import dashboard_service
from app.services.detection_service import detection_service
from app.services.user_service import user_service
from app.agent.memory import conversation_memory
from app.agent.attachment_store import ensure_session_attachment_history
from app.rag.retriever import knowledge_retriever
from app.entity.db_models import (
    DetectionResult,
    DetectionScene,
    DetectionTask,
    ModelVersion,
    User,
)

logger = get_logger(__name__)


_REQUIRED_TOOL_RETRY_TEMPLATE = """强制执行要求（本轮重试）：
你上一次没有调用数据查询工具，因此上一次答案已被系统拒绝。生成任何结论前，
必须至少调用一个与用户请求匹配的工具：{tool_names}。
其它专家的结果只能用于确定查询参数（例如缺陷类别），不能替代平台数据查询。
工具参数必须完整落实当前任务和依赖数据中提供的筛选条件。
只能根据工具实际返回值回答；若工具返回 error，必须如实说明错误，禁止自行补全数据。"""

_REQUIRED_TOOL_FAILURE_MESSAGE = (
    "数据分析未按要求调用查询工具，系统已拒绝未经过数据验证的答案。请稍后重试。"
)


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
_current_session_id: contextvars.ContextVar = contextvars.ContextVar(
    "current_session_id", default=None
)
_current_attachment_names: contextvars.ContextVar = contextvars.ContextVar(
    "current_attachment_names", default=None
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
    # 视频路径：关键帧列表内的 base64，以及仅供前端播放器使用的视频 URL。
    if isinstance(slim.get("key_frames"), list):
        slim["key_frames"] = [
            {k: v for k, v in item.items() if k != "annotated_image_base64"}
            if isinstance(item, dict)
            else item
            for item in slim["key_frames"]
        ]
    slim.pop("annotated_video_url", None)
    return slim


def _finalize_tool_result(result: dict) -> str:
    """
    把检测结果拆成两条通道：
      - 完整版（含 base64）存入 contextvar，供 chat_stream 发给前端渲染标注图；
      - 精简版（剥离 base64）作为工具返回值回喂给 LLM，避免浪费数万 token。

    Returns:
        精简版结果的 JSON 字符串（给 LLM）。
    """
    # LangChain 会在线程中执行同步工具。ContextVar 的值会复制到子线程，
    # 但子线程 set() 的新值不会回传，因此使用父子上下文共享的可变 holder。
    full_result = json.dumps(result, ensure_ascii=False)
    result_holder = _last_full_tool_result.get()
    if isinstance(result_holder, dict):
        result_holder["result"] = full_result
    else:
        _last_full_tool_result.set(full_result)
    # 精简版返回给 LLM
    return json.dumps(_strip_base64_for_llm(result), ensure_ascii=False)


def _current_date_text() -> str:
    """构造注入提示词的当前系统日期文本，如 2026-07-19（星期日）。

    Agent 是长驻单例，日期必须在每次请求时计算并通过提示词变量注入，
    否则 LLM 只能凭训练数据猜测“今天/昨天”对应的具体日期。
    """
    now = datetime.now()
    weekday_cn = "一二三四五六日"[now.weekday()]
    return f"{now.strftime('%Y-%m-%d')}（星期{weekday_cn}）"


def _tool_permission_error(permission: str) -> str | None:
    """Return a JSON error when the request user cannot invoke a tool."""
    user_id = _current_user_id.get()
    if user_id is None:
        return json.dumps({"error": "需要登录后才能使用该工具"}, ensure_ascii=False)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None or not user.is_active:
            return json.dumps({"error": "用户不存在或已被禁用"}, ensure_ascii=False)
        if not user_has_permission(db, user, permission):
            return json.dumps(
                {"error": "无权使用该工具", "required_permission": permission},
                ensure_ascii=False,
            )
        return None
    finally:
        db.close()


def _append_attachment_context(
    message: str,
    attachments: list[dict] = None,
    image_path: str = None,
) -> str:
    """把结构化附件转换成 Agent 可稳定识别的路径提示。"""
    normalized = list(attachments or [])
    if image_path and not normalized:
        normalized.append({"type": "image", "path": image_path})

    image_paths = [
        item["path"]
        for item in normalized
        if item.get("type") == "image" and item.get("path")
    ]
    zip_paths = [
        item["path"]
        for item in normalized
        if item.get("type") == "zip" and item.get("path")
    ]
    video_paths = [
        item["path"]
        for item in normalized
        if item.get("type") == "video" and item.get("path")
    ]

    context_lines = []
    if len(image_paths) == 1:
        context_lines.append(f"[附件图片路径: {image_paths[0]}]")
    elif image_paths:
        context_lines.append(
            f"[附件图片路径列表: {json.dumps(image_paths, ensure_ascii=False)}]"
        )
    if zip_paths:
        context_lines.append(f"[附件ZIP路径: {zip_paths[0]}]")
    if video_paths:
        context_lines.append(f"[附件视频路径: {video_paths[0]}]")

    if not context_lines:
        return message
    return f"{message}\n" + "\n".join(context_lines)


def _build_attachment_name_map(
    attachments: list[dict] = None,
    image_path: str = None,
) -> dict[str, str]:
    """构建服务器附件路径到浏览器原始文件名的请求级映射。"""
    name_map = {
        item["path"]: item.get("filename") or os.path.basename(item["path"])
        for item in (attachments or [])
        if item.get("path")
    }
    if image_path and image_path not in name_map:
        name_map[image_path] = os.path.basename(image_path)
    return name_map


def _register_current_attachments(
    attachments: list[dict] | None,
    image_path: str | None,
    session_id: str | int | None,
    user_id: int | str | None = None,
) -> list[dict]:
    """规范化本轮附件并写入会话附件记忆，供 Agent 复检时查询。

    历史附件不再由后端关键词规则恢复：Agent 会在需要时主动调用
    list_session_attachments 工具，自行决定复用哪些路径。
    """
    current = list(attachments or [])
    if image_path and not current:
        current = [
            {
                "type": "image",
                "path": image_path,
                "filename": os.path.basename(image_path),
            }
        ]
    if current:
        conversation_memory.save_attachments(session_id, current, user_id)
    return current


@tool
def list_session_attachments(attachment_type: str = "") -> str:
    """
    查询当前会话中用户发送过的全部检测附件（图片/ZIP/视频），按轮次从旧到新返回。

    当用户要求重新检测、复检，或提到“上面/之前发的图片”“第N张图”“那个视频”等
    历史附件、而本轮消息没有附件路径提示时，先调用本工具获取真实路径，
    再把选中的 path 传给对应检测工具。

    Args:
        attachment_type: 可选类型过滤：image、zip 或 video，留空返回全部类型

    Returns:
        JSON 字符串。rounds 中 round 数字越大表示发送时间越新；
        file_exists 为 false 的文件已失效，不能再用于检测。
    """
    if error := _tool_permission_error(DETECTION_EXECUTE):
        return error
    session_id = _current_session_id.get()
    if session_id is None:
        return json.dumps(
            {"error": "当前对话没有会话上下文，无法查询历史附件"},
            ensure_ascii=False,
        )
    normalized_type = (attachment_type or "").strip().lower()
    if normalized_type not in {"", "image", "zip", "video"}:
        return json.dumps(
            {
                "error": f"暂不支持附件类型 {attachment_type!r}",
                "supported_types": ["image", "zip", "video"],
            },
            ensure_ascii=False,
        )

    user_id = _current_user_id.get()
    name_map = _current_attachment_names.get()
    rounds = []
    existing_paths = set()
    missing_paths = set()
    attachment_history = ensure_session_attachment_history(session_id, user_id)
    for index, attachments in enumerate(attachment_history, start=1):
        items = []
        for item in attachments:
            path = item.get("path") if isinstance(item, dict) else None
            item_type = item.get("type", "image") if isinstance(item, dict) else None
            if not path or (normalized_type and item_type != normalized_type):
                continue
            filename = item.get("filename") or os.path.basename(path)
            file_exists = os.path.isfile(path)
            (existing_paths if file_exists else missing_paths).add(path)
            # 合入请求级文件名映射（就地修改，线程内可见），
            # 让复检落库时仍能显示浏览器原始文件名。
            if isinstance(name_map, dict):
                name_map.setdefault(path, filename)
            items.append(
                {
                    "type": item_type,
                    "path": path,
                    "filename": filename,
                    "file_exists": file_exists,
                }
            )
        if items:
            rounds.append({"round": index, "attachments": items})
    return json.dumps(
        {
            "session_id": str(session_id),
            "total_rounds": len(rounds),
            "available_files": len(existing_paths),
            "missing_files": len(missing_paths),
            "rounds": rounds,
            "note": "round 越大表示发送时间越新；file_exists=false 的文件已失效，不能再检测",
        },
        ensure_ascii=False,
    )


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
    if error := _tool_permission_error(DETECTION_EXECUTE):
        return error
    result = detection_service.detect_single(
        image_path,
        conf=conf,
        iou=iou,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
        original_filename=(_current_attachment_names.get() or {}).get(image_path),
    )
    return _finalize_tool_result(result)


@tool
def detect_batch_images(
    image_paths: list[str], conf: float = 0.25, iou: float = 0.45
) -> str:
    """
    批量检测多张图片中的目标物体。

    Args:
        image_paths: 图片文件路径列表
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45

    Returns:
        JSON 字符串，包含每张图片的检测结果汇总
    """
    if error := _tool_permission_error(DETECTION_EXECUTE):
        return error
    result = detection_service.detect_batch(
        image_paths,
        conf=conf,
        iou=iou,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
        original_filenames=[
            (_current_attachment_names.get() or {}).get(path)
            or os.path.basename(path)
            for path in image_paths
        ],
    )
    return _finalize_tool_result(result)


@tool
def detect_zip_images_file(
    zip_path: str, conf: float = 0.25, iou: float = 0.45
) -> str:
    """
    解压 ZIP 文件并批量检测其中所有图片的目标物体。

    Args:
        zip_path: ZIP 文件路径
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45

    Returns:
        JSON 字符串，包含 ZIP 内所有图片的检测结果汇总
    """
    if error := _tool_permission_error(DETECTION_EXECUTE):
        return error
    result = detection_service.detect_zip(
        zip_path,
        conf=conf,
        iou=iou,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
        original_filename=(_current_attachment_names.get() or {}).get(zip_path),
    )
    return _finalize_tool_result(result)

@tool
def detect_video_file(
    video_path: str,
    conf: float = 0.25,
    iou: float = 0.45,
    frame_sample_rate: int = 5,
) -> str:
    """
    检测视频文件中的目标物体。对视频进行帧采样后逐帧检测。

    Args:
        video_path: 视频文件路径（mp4/avi/mov 等）
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45
        frame_sample_rate: 帧采样间隔，每 N 帧取 1 帧，默认 5

    Returns:
        JSON 字符串，包含视频检测结果（关键帧、目标统计、时长信息）
    """
    if error := _tool_permission_error(DETECTION_EXECUTE):
        return error
    result = detection_service.detect_video(
        video_path,
        conf=conf,
        iou=iou,
        frame_sample_rate=frame_sample_rate,
        scene_id=_current_scene_id.get(),
        user_id=_current_user_id.get(),
    )
    return _finalize_tool_result(result)


@tool
def query_system_users(
    keyword: str = "",
    role: str = "",
    page: int = 1,
    page_size: int = 20,
) -> str:
    """
    查询系统用户列表，可按用户名/邮箱关键词和角色标识筛选。

    Args:
        keyword: 可选的用户名或邮箱关键词
        role: 可选的角色标识，例如 system_admin、quality_inspector、production_manager
        page: 页码，默认 1
        page_size: 每页数量，默认 20，最大 100

    Returns:
        JSON 字符串，只包含非敏感用户资料和角色
    """
    if error := _tool_permission_error(USER_MANAGE):
        return error

    db = SessionLocal()
    try:
        result = user_service.list_users(
            db,
            page=max(1, page),
            page_size=min(max(1, page_size), 100),
            keyword=keyword or None,
        )
        if role:
            role_name = role.strip().lower()
            result["items"] = [
                item
                for item in result["items"]
                if role_name in {name.lower() for name in item["roles"]}
            ]
            result["filtered_count"] = len(result["items"])
            result["role_filter"] = role_name
        return json.dumps(result, ensure_ascii=False)
    finally:
        db.close()


@tool
def query_system_roles() -> str:
    """
    查询系统角色及其权限编码，用于回答角色、管理员和权限相关问题。

    Returns:
        JSON 字符串，包含角色名称、显示名、描述和权限编码列表
    """
    if error := _tool_permission_error(USER_MANAGE):
        return error

    db = SessionLocal()
    try:
        return json.dumps(
            {"roles": user_service.list_roles(db)},
            ensure_ascii=False,
        )
    finally:
        db.close()

def _parse_tool_date(value: str) -> date | None:
    """把 LLM 传入的日期字符串解析为 date，无法解析时返回 None。

    同时接受带年份和「只有月日」两类写法，避免 LLM 因不知当前年份而猜错：
      - 带年份：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
      - 仅月日：MM-DD / MM/DD / MM.DD —— 由服务端按当前年份补齐；
        若补齐后是未来日期（如 1 月询问 12-15），回退到上一年。
    """
    if not value or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # 仅月日：用当前年份补齐，服务端时钟为准，杜绝模型猜年份。
    today = datetime.now().date()
    for fmt in ("%m-%d", "%m/%d", "%m.%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        candidate = parsed.replace(year=today.year)
        # 检测记录不可能在未来；未来日期说明应归属上一年。
        if candidate > today:
            candidate = candidate.replace(year=today.year - 1)
        return candidate
    return None


def _resolve_defect_filter(defect: str) -> list[str] | None:
    """把单个缺陷类别参数规整成 dashboard_service 需要的过滤列表。"""
    if not defect or not defect.strip():
        return None
    return [defect.strip()]


def _default_model_scene_id(db) -> int | None:
    """取全局默认检测模型的归属场景 id，用于统计工具的场景隔离。

    与检测执行的写入归属保持同一口径（模型 is_global_default → scene_id）。
    只做轻量查询，不触发 ensure_model_available 的 MinIO 恢复副作用；
    未配置全局模型时返回 None（统计回退为全场景）。
    """
    row = (
        db.query(ModelVersion.scene_id)
        .filter(ModelVersion.is_global_default.is_(True))
        .first()
    )
    return row[0] if row else None


def _current_scene_context(db) -> tuple[int | None, str | None]:
    """统计工具的场景上下文：(scene_id, 场景显示名)。

    优先取请求注入的 _current_scene_id（预留），否则回退到全局默认模型场景。
    """
    scene_id = _current_scene_id.get() or _default_model_scene_id(db)
    if not scene_id:
        return None, None
    row = (
        db.query(DetectionScene.display_name)
        .filter(DetectionScene.id == scene_id)
        .first()
    )
    return scene_id, (row[0] if row else None)


@tool
def query_detection_statistics(
    days: int = 7,
    task_type: str = "all",
    today: bool = False,
    start_date: str = "",
    end_date: str = "",
    defect: str = "",
) -> str:
    """查询检测任务统计，支持自定义时间段与按缺陷类别过滤。

    统计范围自动限定在当前检测场景（全局默认模型的归属场景）内，
    与检测执行的写入口径一致；返回的 scene 字段为该场景名，
    回答时应向用户说明统计所属场景。

    Args:
        days: 最近天数，1-365；当 today=true 或提供 start_date/end_date 时忽略。
        task_type: all、single、batch 或 video。batch 同时包含历史 zip/folder 类型。
            指定 defect 时该参数不生效（缺陷统计基于目标级数据）。
        today: 是否严格按今天 00:00 至当前时间统计。
        start_date: 自定义起始日期（含），格式 YYYY-MM-DD；优先于 days/today。
        end_date: 自定义结束日期（含），格式 YYYY-MM-DD；默认今天。
        defect: 缺陷类别，可用英文 code（如 "hole"）或中文名（如 "破洞"），
            大小写不敏感，系统会自动做中英互认。给定后只统计该缺陷，返回
            命中该缺陷的任务数、图片数与目标（缺陷）总数。

    Returns:
        JSON 字符串。未过滤缺陷时包含任务数、状态分布、图片数、目标数、
        平均推理耗时和各任务类型数量；过滤缺陷时包含该缺陷的任务/图片/目标数。
        若该缺陷在时间段内 0 命中，会附带 available_defects（实际存在的缺陷
        类别列表），此时应结合它判断是否用词有误，必要时改用列表中的名称重查，
        不要直接断言“没有该缺陷”。摄像头等无法统计的类型会返回 error。
    """
    if error := _tool_permission_error(DASHBOARD_READ_ANY):
        return error

    parsed_start = _parse_tool_date(start_date)
    parsed_end = _parse_tool_date(end_date)
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return json.dumps(
            {"error": "开始日期不能晚于结束日期"}, ensure_ascii=False
        )
    days = max(1, min(days, 365))
    defect_filter = _resolve_defect_filter(defect)

    # 缺陷维度统计：委托 dashboard_service 做目标级聚合，保持与看板一致。
    if defect_filter:
        db = SessionLocal()
        try:
            if parsed_start or parsed_end:
                win_start, win_end = parsed_start, parsed_end
            elif today:
                today_date = datetime.now().date()
                win_start, win_end = today_date, today_date
            else:
                win_start, win_end = None, None
            # 统计口径与检测执行一致：限定在全局默认模型的归属场景内。
            scene_id, scene_name = _current_scene_context(db)
            result = dashboard_service.get_statistics(
                db, None, days, win_start, win_end, defect_filter, scene_id
            )
            defect_count = result.get("total_objects", 0)
            payload = {
                "defect": defect.strip(),
                "scene": scene_name or "全部场景",
                "from": result.get("start_at"),
                "to": result.get("end_at"),
                "matched_tasks": result.get("total_tasks", 0),
                "matched_images": result.get("matched_images", result.get("total_images", 0)),
                "defect_count": defect_count,
                "growth": result.get("growth", {}),
                "note": "以上为该缺陷类别在当前检测场景内的目标级统计",
            }
            # 0 命中时附上该时间段实际存在的缺陷类别，便于纠正用词歧义。
            if defect_count == 0:
                options = dashboard_service.get_defect_options(
                    db, None, days, win_start, win_end, scene_id
                )
                payload["available_defects"] = [
                    {"name": item["name"], "name_cn": item["name_cn"]}
                    for item in options.get("options", [])
                ]
                payload["note"] = (
                    "该时间段内未匹配到此缺陷。available_defects 为实际存在的缺陷类别，"
                    "若用户用词与之相近，请据此确认或改用其中的名称重新查询。"
                )
            return json.dumps(payload, ensure_ascii=False)
        finally:
            db.close()

    normalized_type = (task_type or "all").strip().lower()
    task_type_filters = {
        "all": None,
        "single": ("single",),
        "batch": ("batch", "zip", "folder"),
        "video": ("video",),
    }
    if normalized_type in {"camera", "realtime", "实时", "摄像头"}:
        return json.dumps(
            {
                "error": "当前实时摄像头检测不会创建检测任务记录，无法统计执行次数",
                "supported_task_types": ["single", "batch", "video"],
            },
            ensure_ascii=False,
        )
    if normalized_type not in task_type_filters:
        return json.dumps(
            {
                "error": f"暂不支持按任务类型 {task_type!r} 查询",
                "supported_task_types": ["all", "single", "batch", "video"],
            },
            ensure_ascii=False,
        )

    now = datetime.now()
    if parsed_start or parsed_end:
        end_day = parsed_end or now.date()
        start_day = parsed_start or (end_day - timedelta(days=days - 1))
        since = datetime.combine(start_day, datetime.min.time())
        until = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
        period_label = "custom_range"
    elif today:
        since = datetime.combine(now.date(), datetime.min.time())
        until = now
        period_label = "today"
    else:
        since = now - timedelta(days=days)
        until = now
        period_label = "recent_days"
    db = SessionLocal()
    try:
        # 统计口径与检测执行一致：限定在全局默认模型的归属场景内。
        scene_id, scene_name = _current_scene_context(db)
        time_base = db.query(DetectionTask).filter(
            DetectionTask.created_at >= since,
            DetectionTask.created_at < until,
        )
        if scene_id:
            time_base = time_base.filter(DetectionTask.scene_id == scene_id)
        type_rows = time_base.with_entities(
            DetectionTask.task_type,
            func.count(DetectionTask.id),
        ).group_by(DetectionTask.task_type).all()

        base = time_base
        selected_types = task_type_filters[normalized_type]
        if selected_types:
            base = base.filter(DetectionTask.task_type.in_(selected_types))

        total_tasks = base.count()
        status_rows = base.with_entities(
            DetectionTask.status,
            func.count(DetectionTask.id),
        ).group_by(DetectionTask.status).all()
        status_counts = {str(status): int(count) for status, count in status_rows}
        completed = status_counts.get("completed", 0)
        totals = base.with_entities(
            func.coalesce(func.sum(DetectionTask.total_images), 0),
            func.coalesce(func.sum(DetectionTask.total_objects), 0),
            func.coalesce(func.avg(DetectionTask.total_inference_time), 0),
        ).one()

        type_counts = {"single": 0, "batch": 0, "video": 0, "other": 0}
        for stored_type, count in type_rows:
            if stored_type == "single":
                key = "single"
            elif stored_type in {"batch", "zip", "folder"}:
                key = "batch"
            elif stored_type == "video":
                key = "video"
            else:
                key = "other"
            type_counts[key] += int(count)

        return json.dumps(
            {
                "period": period_label,
                "days": 1 if period_label == "today" else days,
                "from": since.isoformat(timespec="seconds"),
                "to": until.isoformat(timespec="seconds"),
                "scene": scene_name or "全部场景",
                "task_type": normalized_type,
                "total_tasks": total_tasks,
                "completed_tasks": completed,
                "status_counts": status_counts,
                "success_rate": round(completed / total_tasks * 100, 2) if total_tasks else 0,
                "total_images": int(totals[0] or 0),
                "total_objects": int(totals[1] or 0),
                "average_inference_time_ms": round(float(totals[2] or 0), 2),
                "task_type_counts": type_counts,
            },
            ensure_ascii=False,
        )
    finally:
        db.close()


@tool
def query_detection_trends(
    days: int = 7,
    start_date: str = "",
    end_date: str = "",
    defect: str = "",
) -> str:
    """查询检测趋势与缺陷类别分布，支持自定义时间段与单缺陷趋势。

    统计范围自动限定在当前检测场景（全局默认模型的归属场景）内，
    返回的 scene 字段为该场景名，回答时应向用户说明统计所属场景。

    Args:
        days: 最近天数，1-365；提供 start_date/end_date 时忽略。
        start_date: 自定义起始日期（含），格式 YYYY-MM-DD；优先于 days。
        end_date: 自定义结束日期（含），格式 YYYY-MM-DD；默认今天。
        defect: 缺陷类别，可用英文 code（如 "hole"）或中文名（如 "破洞"），
            大小写不敏感，系统会自动做中英互认。给定后 daily 返回该缺陷
            每天的检测数量趋势，用于回答“某缺陷在某时间段的变化趋势”。

    Returns:
        JSON 字符串，包含 daily 每日趋势与 class_distribution 缺陷类别分布。
        若指定缺陷在时间段内 0 命中，会附带 available_defects（实际存在的缺陷
        类别列表），应据此判断是否用词有误，必要时改用列表中的名称重查。
    """
    if error := _tool_permission_error(DASHBOARD_READ_ANY):
        return error

    parsed_start = _parse_tool_date(start_date)
    parsed_end = _parse_tool_date(end_date)
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return json.dumps(
            {"error": "开始日期不能晚于结束日期"}, ensure_ascii=False
        )
    days = max(1, min(days, 365))
    defect_filter = _resolve_defect_filter(defect)

    db = SessionLocal()
    try:
        # 统计口径与检测执行一致：限定在全局默认模型的归属场景内。
        scene_id, scene_name = _current_scene_context(db)
        trend = dashboard_service.get_trend(
            db, None, days, parsed_start, parsed_end, defect_filter, scene_id
        )
        class_dist = dashboard_service.get_class_distribution(
            db, None, days, parsed_start, parsed_end, defect_filter, scene_id
        )
        daily = [
            {
                "date": item["date"],
                "tasks": item["task_count"],
                "objects": item["object_count"],
            }
            for item in trend["trend"]
        ]
        payload = {
            "days": days,
            "from": trend.get("start_at"),
            "to": trend.get("end_at"),
            "scene": scene_name or "全部场景",
            "daily": daily,
            "class_distribution": [
                {"class_name": item["name"], "count": item["value"]}
                for item in class_dist["distribution"]
            ],
        }
        if defect_filter:
            payload["defect"] = defect.strip()
            payload["note"] = "daily.objects 为该缺陷每日检测数量"
            # 0 命中时附上可选缺陷类别，帮助纠正用词歧义。
            if sum(item["objects"] for item in daily) == 0:
                options = dashboard_service.get_defect_options(
                    db, None, days, parsed_start, parsed_end, scene_id
                )
                payload["available_defects"] = [
                    {"name": item["name"], "name_cn": item["name_cn"]}
                    for item in options.get("options", [])
                ]
                payload["note"] = (
                    "该时间段内未匹配到此缺陷。available_defects 为实际存在的缺陷类别，"
                    "若用户用词与之相近，请据此确认或改用其中的名称重新查询。"
                )
        return json.dumps(payload, ensure_ascii=False)
    finally:
        db.close()

@tool
def search_knowledge(query: str, top_k: int = 3) -> str:
    """从 backend/knowledge_base 的 Markdown/TXT 文档中检索相关片段。

    Returns:
        JSON 字符串。retrieval_mode 表示实际检索方式（pgvector=向量检索，
        lexical_fallback=词法降级）；sources 为命中的来源文件列表。
        回答时必须注明引用了哪些来源文件。
    """
    if error := _tool_permission_error(KNOWLEDGE_READ):
        return error
    retrieval = knowledge_retriever.retrieve(query, top_k)
    return json.dumps(
        {
            "query": query,
            "retrieval_mode": retrieval["mode"],
            "fallback_reason": retrieval["fallback_reason"],
            "sources": sorted(
                {item["source"] for item in retrieval["results"] if item.get("source")}
            ),
            "results": retrieval["results"],
        },
        ensure_ascii=False,
    )


# 工具列表（绑定到 Agent）
DETECTION_TOOLS = [
    list_session_attachments,
    detect_single_image,
    detect_batch_images,
    detect_zip_images_file,
    detect_video_file,
    query_system_users,
    query_system_roles,
    query_detection_statistics,
    query_detection_trends,
    search_knowledge,
]

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

    def __init__(
        self,
        tools: list | None = None,
        system_prompt: str | None = None,
        name: str = "detection",
        required_tool_names: set[str] | None = None,
        required_tool_resolver: Callable[[str], set[str]] | None = None,
        required_tool_call_validator: (
            Callable[[str, list[tuple[str, object]]], bool] | None
        ) = None,
        max_required_tool_retries: int = 1,
    ):
        """初始化 Agent，创建 LLM 和 AgentExecutor"""
        self.llm = create_llm()
        self.tools = tools or DETECTION_TOOLS
        self.name = name
        self.required_tool_names = set(required_tool_names or ())
        self.required_tool_resolver = required_tool_resolver
        self.required_tool_call_validator = required_tool_call_validator
        self.max_required_tool_retries = max(0, max_required_tool_retries)

        # OpenAI Tools Agent 系统提示词
        default_system_prompt = """你是一个专业的目标检测平台助手。你可以执行图片、ZIP 压缩包和视频检测，也可以查询系统用户、角色和权限。

重要规则：
- 当用户消息中包含 [附件图片路径: xxx] 时，xxx 就是图片的服务器路径，你应直接使用它调用检测工具
- 当用户消息中包含 [附件图片路径列表: [...]] 时，必须把列表原样作为 image_paths 调用批量检测工具
- 当用户消息中包含 [附件ZIP路径: xxx] 时，必须使用 xxx 调用 ZIP 检测工具
- 当用户消息中包含 [附件视频路径: xxx] 时，xxx 就是视频的服务器路径，你应直接使用它调用视频检测工具
- 不要要求用户再次提供路径，直接使用附件中给出的路径
- 当用户要求重新检测、复检，或提到“上面/之前发的图片”“所有图片”“第N张图”“那个视频”等历史附件，而本轮消息没有附件路径提示时，必须先调用 list_session_attachments 查询会话附件记录，再根据用户描述挑选对应 path 调用检测工具
- 挑选历史附件时按 round 从新到旧、路径去重。例如“重新检测上面5张图片”就是收集最近发送的 5 张不同图片；“重新检测所有图片”就是收集全部图片
- 只能使用附件提示或 list_session_attachments 返回的 path，禁止编造或修改路径；file_exists 为 false 的文件已失效，不要用它检测，并在回复中说明
- 会话附件记录为空或全部失效时，明确请用户重新上传，不要执行检测
- 对于单张图片，调用 detect_single_image 工具
- 对于多张图片或 ZIP 文件，调用 detect_batch_images 或 detect_zip_images_file 工具
- 对于视频文件，调用 detect_video_file 工具
- 用户询问“有哪些用户”、用户数量或某个用户时，调用 query_system_users 工具
- 用户询问系统管理员时，调用 query_system_users，并将 role 设置为 system_admin
- 用户询问系统角色或权限时，调用 query_system_roles 工具
- 用户与角色工具只返回非敏感资料；绝不能索取、推测或输出密码、Token 等凭据

工作流程：
1. 理解用户意图
2. 如果本轮有附件路径，直接调用对应检测工具；如果用户指的是历史附件，先调用 list_session_attachments 再检测
3. 如果是用户、角色或权限问题，调用对应查询工具
4. 调用工具获取结果
5. 用自然语言总结结果

回复格式要求：
- 先报告检测到的目标总数
- 列出各类别的数量统计
- 对于视频检测，还要报告视频时长和处理的帧数
- 如果有标注图，告知用户可以在结果卡片中查看
- 简洁专业，不要过度解释"""
        # 追加当前日期占位符：Agent 为长驻单例，日期在每次调用时通过
        # {current_date} 变量注入，确保“今天/昨天/本周”等相对日期换算正确。
        system_prompt = (system_prompt or default_system_prompt) + (
            "\n\n当前系统日期：{current_date}。"
            "用户提到“今天/昨天/前天/本周/上周/最近N天”等相对日期时，"
            "必须以该系统日期为基准换算成具体日期（例如“昨天”＝系统日期减一天），"
            "并在回答中使用换算后的日期，绝不能凭记忆或猜测使用其他日期。"
            "\n\n{runtime_instruction}"
        )

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
            tools=self.tools,
            prompt=prompt,
        )

        self.executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True,  # 开发阶段开启，可查看 Agent 思考过程
            max_iterations=8,  # 限制循环次数，防止无限循环；需容纳“查会话附件 → 检测 → 总结”多步流程
            return_intermediate_steps=True,  # 返回中间步骤（Tool 调用记录）
        )

        logger.info("%s Agent 初始化完成，绑定 %d 个工具", self.name, len(self.tools))

    def _required_tools_for(self, message: str) -> set[str]:
        """返回当前消息必须调用的候选工具集合。"""
        if self.required_tool_resolver is not None:
            return set(self.required_tool_resolver(message))
        return set(self.required_tool_names)

    @staticmethod
    def _tool_calls_from_steps(
        intermediate_steps: list | None,
    ) -> list[tuple[str, object]]:
        """从 AgentExecutor 中间步骤提取工具名和参数。"""
        calls: list[tuple[str, object]] = []
        for step in intermediate_steps or []:
            action = step[0] if isinstance(step, (tuple, list)) and step else step
            if isinstance(action, dict):
                tool_name = action.get("tool")
                tool_input = action.get("tool_input", {})
            else:
                tool_name = getattr(action, "tool", None)
                tool_input = getattr(action, "tool_input", {})
            if tool_name:
                calls.append((str(tool_name), tool_input))
        return calls

    def _tool_requirement_met(
        self,
        message: str,
        tool_calls: list[tuple[str, object]],
        required_tool_names: set[str],
    ) -> bool:
        """校验必需工具是否被调用，以及调用参数是否满足任务约束。"""
        if not required_tool_names:
            return True
        if not any(tool_name in required_tool_names for tool_name, _ in tool_calls):
            return False
        if self.required_tool_call_validator is not None:
            return self.required_tool_call_validator(message, tool_calls)
        return True

    @staticmethod
    def _required_tool_retry_instruction(required_tool_names: set[str]) -> str:
        """生成仅在无工具调用重试时注入的系统级纠正指令。"""
        return _REQUIRED_TOOL_RETRY_TEMPLATE.format(
            tool_names="、".join(sorted(required_tool_names))
        )

    async def chat(
        self,
        message: str,
        image_path: str = None,
        attachments: list[dict] = None,
        user_id: int = None,
        scene_id: int = None,
        session_id: str | int = None,
        record_memory: bool = True,
    ) -> dict:
        """
        处理用户对话消息

        Args:
            message: 用户文本消息
            image_path: 附带的图片路径（可选）
            attachments: 结构化附件列表（单图/多图/ZIP/视频，可选）
            user_id: 当前登录用户 ID，用于检测记录归属
            scene_id: 检测场景 ID（可选）
            session_id: 会话 ID，用于对话与附件记忆（可选）
            record_memory: 是否写入会话记忆。并行编排下由编排器统一写一次，
                各专家应传 False，避免重复追加同一会话历史。

        Returns:
            Agent 响应字典
        """
        attachments = _register_current_attachments(
            attachments, image_path, session_id, user_id
        )
        message = _append_attachment_context(message, attachments)

        # 注入请求级上下文，供工具读取
        token_user = _current_user_id.set(user_id)
        token_scene = _current_scene_id.set(scene_id)
        token_session = _current_session_id.set(session_id)
        token_names = _current_attachment_names.set(
            _build_attachment_name_map(attachments)
        )
        try:
            history = conversation_memory.load(session_id, user_id)
            required_tool_names = self._required_tools_for(message)
            result = None
            for attempt in range(self.max_required_tool_retries + 1):
                result = await self.executor.ainvoke(
                    {
                        "input": message,
                        "chat_history": history,
                        "current_date": _current_date_text(),
                        "runtime_instruction": (
                            self._required_tool_retry_instruction(required_tool_names)
                            if attempt
                            else ""
                        ),
                    }
                )
                if self._tool_requirement_met(
                    message,
                    self._tool_calls_from_steps(result.get("intermediate_steps")),
                    required_tool_names,
                ):
                    break
                logger.warning(
                    "%s Agent 第 %d 次执行未满足必需工具调用约束，拒绝答案%s",
                    self.name,
                    attempt + 1,
                    "并重试" if attempt < self.max_required_tool_retries else "",
                )
            else:  # pragma: no cover - range 永远至少执行一次
                result = None

            if result is None or not self._tool_requirement_met(
                message,
                self._tool_calls_from_steps(result.get("intermediate_steps")),
                required_tool_names,
            ):
                return {"output": _REQUIRED_TOOL_FAILURE_MESSAGE, "intermediate_steps": []}
            if record_memory:
                conversation_memory.append(session_id, "user", message, user_id)
                conversation_memory.append(session_id, "assistant", result["output"], user_id)

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
            _current_session_id.reset(token_session)
            _current_attachment_names.reset(token_names)

    async def chat_stream(
        self,
        message: str,
        image_path: str = None,
        attachments: list[dict] = None,
        user_id: int = None,
        scene_id: int = None,
        session_id: str | int = None,
        record_memory: bool = True,
    ) -> AsyncGenerator:
        """
        流式处理对话消息（用于 SSE）

        逐个 yield Agent 的思考步骤和最终结果

        Args:
            message: 用户文本消息
            image_path: 附带的图片路径（可选）
            attachments: 结构化附件列表（单图/多图/ZIP/视频，可选）
            user_id: 当前登录用户 ID，用于检测记录归属
            scene_id: 检测场景 ID（可选）
            session_id: 会话 ID，用于对话与附件记忆（可选）
            record_memory: 是否写入会话记忆。并行编排下由编排器统一写一次，
                各专家应传 False，避免重复追加同一会话历史。

        Yields:
            SSE 事件数据字典
        """
        attachments = _register_current_attachments(
            attachments, image_path, session_id, user_id
        )
        message = _append_attachment_context(message, attachments)

        # 注入请求级上下文，供工具读取
        token_user = _current_user_id.set(user_id)
        token_scene = _current_scene_id.set(scene_id)
        token_session = _current_session_id.set(session_id)
        token_names = _current_attachment_names.set(
            _build_attachment_name_map(attachments)
        )
        result_holder = {"result": None}
        token_full = _last_full_tool_result.set(result_holder)
        try:
            history = conversation_memory.load(session_id, user_id)
            required_tool_names = self._required_tools_for(message)
            accepted_chunks: list[str] = []
            accepted = False
            for attempt in range(self.max_required_tool_retries + 1):
                response_chunks: list[str] = []
                buffered_events: list[dict] = []
                attempt_tool_calls: list[tuple[str, object]] = []

                async for event in self.executor.astream_events(
                    {
                        "input": message,
                        "chat_history": history,
                        "current_date": _current_date_text(),
                        "runtime_instruction": (
                            self._required_tool_retry_instruction(required_tool_names)
                            if attempt
                            else ""
                        ),
                    },
                    version="v2",
                ):
                    event_kind = event["event"]
                    frontend_event = None

                    if event_kind == "on_chat_model_stream":
                        # 对强制工具 Agent 先缓冲文本；整轮无工具时丢弃，避免错误答案闪现。
                        chunk = event["data"]["chunk"]
                        if hasattr(chunk, "content") and chunk.content:
                            response_chunks.append(chunk.content)
                            frontend_event = {
                                "type": "text_chunk",
                                "content": chunk.content,
                            }

                    elif event_kind == "on_tool_start":
                        tool_name = event["name"]
                        tool_input = event["data"].get("input", {})
                        logger.info("工具调用: %s, 输入: %s", tool_name, str(tool_input)[:200])
                        attempt_tool_calls.append((tool_name, tool_input))
                        frontend_event = {
                            "type": "tool_call",
                            "tool": tool_name,
                            "input": tool_input,
                        }

                    elif event_kind == "on_tool_end":
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
                        logger.debug("on_tool_end data keys: %s", list(tool_data.keys()))
                        # 完整检测结果给前端，精简结果仍只回喂 LLM。
                        current_holder = _last_full_tool_result.get()
                        full_result = (
                            current_holder.get("result")
                            if isinstance(current_holder, dict)
                            else current_holder
                        )
                        frontend_result = (
                            full_result
                            if full_result is not None
                            else (str(tool_output) if tool_output else "")
                        )
                        if isinstance(current_holder, dict):
                            current_holder["result"] = None
                        else:
                            _last_full_tool_result.set(None)
                        frontend_event = {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": frontend_result,
                        }

                    if frontend_event is not None:
                        if required_tool_names:
                            buffered_events.append(frontend_event)
                        else:
                            yield frontend_event

                if self._tool_requirement_met(
                    message, attempt_tool_calls, required_tool_names
                ):
                    for buffered in buffered_events:
                        yield buffered
                    accepted_chunks = response_chunks
                    accepted = True
                    break

                logger.warning(
                    "%s Agent 第 %d 次流式执行未满足必需工具调用约束，丢弃答案%s",
                    self.name,
                    attempt + 1,
                    "并重试" if attempt < self.max_required_tool_retries else "",
                )

            if not accepted:
                yield {"type": "error", "content": _REQUIRED_TOOL_FAILURE_MESSAGE}
                return

            if record_memory:
                conversation_memory.append(session_id, "user", message, user_id)
                if accepted_chunks:
                    conversation_memory.append(session_id, "assistant", "".join(accepted_chunks), user_id)

        except Exception as e:
            logger.error("Agent 流式执行异常: %s", str(e), exc_info=True)
            yield {
                "type": "error",
                "content": f"处理出错：{str(e)}",
            }
        finally:
            _current_user_id.reset(token_user)
            _current_scene_id.reset(token_scene)
            _current_session_id.reset(token_session)
            _current_attachment_names.reset(token_names)
            _last_full_tool_result.reset(token_full)


# 创建全局单例
detection_agent = DetectionAgent()
