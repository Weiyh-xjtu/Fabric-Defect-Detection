"""
Pydantic 请求/响应模型
用于 API 接口的数据验证和序列化
分层原则：
  - Create 模型：创建资源时的请求体
  - Update 模型：更新资源时的请求体（所有字段可选）
  - Response 模型：API 返回的响应体（过滤敏感字段）
  - List 模型：分页列表查询的参数和响应
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SchemaModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


# ══════════════════════════════════════════════════════════════
# 一、用户与权限
# ══════════════════════════════════════════════════════════════

# --- 认证相关 ---
class UserRegister(SchemaModel):
    """用户注册请求"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: str = Field(..., description="邮箱")
    password: str = Field(..., min_length=6, max_length=100, description="密码")


class UserLogin(SchemaModel):
    """用户登录请求"""
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class UserBrief(SchemaModel):
    """用户简要信息（嵌入在 Token 响应中）"""
    id: int
    username: str
    email: str
    avatar: Optional[str] = None
    roles: list[str] = []
    permissions: list[str] = []
    is_superuser: bool = False
    model_config = {
        "from_attributes": True,
    }


class TokenResponse(SchemaModel):
    """登录成功响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserBrief


# --- 用户管理 ---
class UserResponse(SchemaModel):
    """用户详情响应"""
    id: int
    username: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    is_active: bool
    is_superuser: bool
    roles: list[str] = []
    permissions: list[str] = []
    last_login_at: Optional[datetime] = None
    created_at: datetime
    model_config = {
        "from_attributes": True,
    }


class UserUpdate(SchemaModel):
    """用户信息更新"""
    phone: Optional[str] = None
    avatar: Optional[str] = None
    email: Optional[str] = None


class ChangePassword(SchemaModel):
    """修改密码"""
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=6, max_length=100, description="新密码")


class UserRolesUpdate(SchemaModel):
    """管理员整体替换用户角色。"""
    role_names: list[str] = Field(..., min_length=1, description="角色标识列表")


class UserStatusUpdate(SchemaModel):
    """管理员启用或禁用用户。"""
    is_active: bool


# --- 角色权限 ---
class RoleResponse(SchemaModel):
    """角色响应"""
    id: int
    name: str
    display_name: str
    description: Optional[str] = None
    is_system: bool
    permissions: list[str] = []  # 权限编码列表
    created_at: datetime

    class Config:
        from_attributes = True


class RoleCreate(SchemaModel):
    """创建角色"""
    name: str = Field(..., min_length=2, max_length=50, description="角色标识")
    display_name: str = Field(..., description="角色显示名")
    description: Optional[str] = None
    permission_codes: list[str] = Field(default=[], description="权限编码列表")


class PermissionResponse(SchemaModel):
    """权限响应"""
    id: int
    code: str
    name: str
    module: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════
# 二、检测业务
# ══════════════════════════════════════════════════════════════

# --- 检测场景 ---
class SceneCreate(SchemaModel):
    """创建检测场景"""
    name: str = Field(..., description="场景标识，如 remote_sensing")
    display_name: str = Field(..., description="场景显示名，如 遥感目标检测")
    description: Optional[str] = None
    category: str = Field(..., description="分类：agriculture/industry/remote_sensing/medical/traffic")
    class_names: list[str] = Field(..., description="类别列表")
    class_names_cn: Optional[dict[str, str]] = Field(None, description="中文名映射")


class SceneResponse(SchemaModel):
    """检测场景响应"""
    id: int
    name: str
    display_name: str
    description: Optional[str] = None
    category: str
    class_names: list
    class_names_cn: Optional[dict] = None
    is_active: bool
    default_model: Optional["ModelVersionBrief"] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- 检测任务 ---
class DetectionTaskResponse(SchemaModel):
    """检测任务响应"""
    id: int
    user_id: int
    scene_id: int
    scene_name: Optional[str] = None
    model_version_id: Optional[int] = None
    task_type: str
    status: str
    total_images: int
    total_objects: int
    total_inference_time: float
    conf_threshold: float
    iou_threshold: float
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DetectionResultResponse(SchemaModel):
    """单条检测结果响应"""
    id: int
    task_id: int
    image_path: str
    annotated_image_url: Optional[str] = None
    class_name: str
    class_name_cn: Optional[str] = None
    class_id: int
    confidence: float
    bbox: list  # [x1, y1, x2, y2]
    inference_time: Optional[float] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DetectionTaskDetail(SchemaModel):
    """检测任务详情（含结果列表）"""
    task: DetectionTaskResponse
    results: list[DetectionResultResponse] = []


# --- 检测统计 ---
class DetectionStatistics(SchemaModel):
    """检测统计数据"""
    total_tasks: int
    total_images: int
    total_objects: int
    avg_inference_time: float
    class_distribution: dict[str, int]  # 各类别检测次数
    daily_trend: list[dict]             # 每日检测趋势
    scene_distribution: dict[str, int]  # 各场景检测次数


# ══════════════════════════════════════════════════════════════
# 三、模型管理
# ══════════════════════════════════════════════════════════════

# --- 训练任务 ---
class TrainingTaskCreate(SchemaModel):
    """创建训练任务"""
    scene_id: int = Field(..., description="关联场景 ID")
    model_name: str = Field(default="yolo11n", description="基础模型")
    epochs: int = Field(default=100, ge=5, le=500, description="训练轮数")
    img_size: int = Field(default=640, description="图像尺寸")
    batch_size: int = Field(default=16, ge=1, le=64, description="批次大小")
    device: str = Field(default="0", description="训练设备")
    optimizer: str = Field(default="SGD", description="优化器")
    lr0: float = Field(default=0.01, description="初始学习率")
    augment_config: Optional[dict] = Field(None, description="数据增强配置")


class TrainingTaskResponse(SchemaModel):
    """训练任务响应"""
    id: int
    user_id: int
    scene_id: int
    scene_name: Optional[str] = None
    task_uuid: str
    status: str
    model_name: str
    epochs: int
    current_epoch: int
    progress: int
    img_size: int
    batch_size: int
    device: str
    dataset_size: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TrainingMetricResponse(SchemaModel):
    """训练指标响应（单 epoch）"""
    epoch: int
    box_loss: Optional[float] = None
    cls_loss: Optional[float] = None
    dfl_loss: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    map50: Optional[float] = None
    map50_95: Optional[float] = None
    lr: Optional[float] = None

    class Config:
        from_attributes = True


# --- 模型评估与导出 ---
class ModelValidateRequest(SchemaModel):
    """模型评估请求"""
    split: Literal["train", "val", "test"] = Field(default="val", description="评估数据集划分")
    conf: float = Field(default=0.001, ge=0, le=1, description="置信度阈值")
    iou: float = Field(default=0.6, ge=0, le=1, description="NMS IoU 阈值")


class ModelExportRequest(SchemaModel):
    """模型导出请求"""
    version: Optional[str] = Field(
        None,
        pattern=r"^v\d+\.\d+\.\d+$",
        description="版本号，如 v1.0.0",
    )
    description: Optional[str] = Field(None, max_length=1000, description="版本描述")
    set_default: bool = Field(default=False, description="是否设为场景默认模型")
    upload_minio: bool = Field(default=True, description="是否上传到 MinIO")


class ModelValidateResponse(SchemaModel):
    """模型评估报告"""
    task_id: int
    task_uuid: str
    split: str
    overall: dict
    per_class: dict
    model_version_id: Optional[int] = None
    model_version: Optional[str] = None
    evaluation_id: Optional[int] = None
    evaluated_at: Optional[str] = None
    cached: bool = False
    artifacts: dict = Field(default_factory=dict)


class ModelValidateStartResponse(SchemaModel):
    """模型评估启动响应（评估在后台异步执行）"""
    task_id: int
    status: Literal["running", "completed"]
    split: str
    message: str
    cached: bool = False
    report: Optional[ModelValidateResponse] = None


class ModelValidateStatusResponse(SchemaModel):
    """模型评估状态轮询响应；completed 时携带评估报告"""
    task_id: int
    status: Literal["idle", "running", "completed", "failed"]
    split: Optional[str] = None
    error: Optional[str] = None
    report: Optional[ModelValidateResponse] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    cached: bool = False


class ModelExportResponse(SchemaModel):
    """模型导出响应"""
    model_version_id: int
    version: str
    model_name: str
    model_path: str
    export_dir: str
    minio_url: Optional[str] = None
    file_size: Optional[int] = None
    evaluation: dict
    is_default: bool
    is_global_default: bool = False
    message: str


# --- 模型版本 ---
class ModelVersionBrief(SchemaModel):
    """模型版本简要信息"""
    id: int
    version: str
    model_name: str
    model_type: str
    map50: Optional[float] = None
    is_default: bool
    is_global_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class ModelVersionResponse(SchemaModel):
    """模型版本详情"""
    id: int
    scene_id: int
    scene_name: Optional[str] = None
    training_task_id: Optional[int] = None
    version: str
    model_name: str
    model_type: str
    status: str
    model_path: str
    minio_url: Optional[str] = None
    minio_object_name: Optional[str] = None
    file_sha256: Optional[str] = None
    backed_up_at: Optional[datetime] = None
    map50: Optional[float] = None
    map50_95: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    per_class_ap: Optional[dict] = None
    description: Optional[str] = None
    file_size: Optional[int] = None
    is_default: bool
    is_global_default: bool = False
    file_exists: bool = False
    detection_task_count: int = 0
    backup_available: bool = False
    archived_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ModelVersionCreate(SchemaModel):
    """手动上传模型版本"""
    scene_id: int
    version: str = Field(..., description="版本号")
    model_name: str = Field(..., description="模型名称")
    model_type: str = Field(default="yolo11n", description="模型类型")
    description: Optional[str] = None


class ModelEvaluationRequest(SchemaModel):
    """从模型管理页启动版本评估。"""
    split: Literal["train", "val", "test"] = "val"
    conf: float = Field(default=0.001, ge=0, le=1)
    iou: float = Field(default=0.6, ge=0, le=1)


# ══════════════════════════════════════════════════════════════
# 四、智能体对话
# ══════════════════════════════════════════════════════════════
class ChatSessionCreate(SchemaModel):
    """创建对话会话"""
    title: Optional[str] = None


class ChatSessionResponse(SchemaModel):
    """对话会话响应"""
    id: int
    session_uuid: str
    title: Optional[str] = None
    status: str
    message_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatMessageRequest(SchemaModel):
    """发送消息请求"""
    session_id: Optional[int] = Field(None, description="会话 ID（为空则自动创建新会话）")
    content: str = Field(..., min_length=1, max_length=5000, description="消息内容")


class ChatMessageResponse(SchemaModel):
    """对话消息响应"""
    id: int
    session_id: int
    role: str
    content: str
    agent_used: Optional[str] = None
    tool_calls: Optional[list] = None
    tool_result: Optional[str] = None
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatHistoryResponse(SchemaModel):
    """对话历史响应（含会话信息和消息列表）"""
    session: ChatSessionResponse
    messages: list[ChatMessageResponse] = []


# ══════════════════════════════════════════════════════════════
# 五、系统运维
# ══════════════════════════════════════════════════════════════
class OperationLogResponse(SchemaModel):
    """操作日志响应"""
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    module: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    description: Optional[str] = None
    ip_address: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════
# 六、通用模型
# ══════════════════════════════════════════════════════════════
class ApiResponse(SchemaModel):
    """统一 API 响应"""
    code: int = 200
    message: str = "success"
    data: Optional[dict | list] = None


class PageParams(SchemaModel):
    """分页查询参数"""
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量")


class PageResponse(SchemaModel):
    """分页响应"""
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list


class HealthResponse(SchemaModel):
    """健康检查响应"""
    status: str = "healthy"
    app_name: str
    version: str
    database: Optional[str] = None
    redis: Optional[str] = None
    minio: Optional[str] = None
