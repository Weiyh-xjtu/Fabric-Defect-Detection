"""
检测 API 路由 — 快捷检测接口（跳过 LLM，直接调用 YOLO）

接口列表：
  - POST /api/detection/single     单图检测
  - POST /api/detection/batch      批量检测
  - POST /api/detection/zip        ZIP 文件检测
  - GET  /api/detection/status/:id 查询任务状态
"""

import asyncio
import base64
import ipaddress
import os
import tempfile
import threading
import time
from urllib.parse import urlparse

import cv2
import numpy as np
import torch

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import DetectionTask
from app.services.detection_service import detection_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/detection", tags=["快捷检测"])


@router.post("/single", summary="单图检测")
async def detect_single_api(
    file: UploadFile = File(..., description="检测图片"),
    conf: float = Form(0.25, description="置信度阈值"),
    scene_id: int = Form(None, description="场景 ID"),
    current_user=Depends(get_current_user),
):
    """
    快捷单图检测（跳过 LLM，直接调用 YOLO）
    """
    suffix = os.path.splitext(file.filename)[1] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = detection_service.detect_single(
            image_path=tmp_path,
            conf=conf,
            scene_id=scene_id,
            user_id=current_user.id,
            original_filename=os.path.basename(file.filename or tmp_path),
        )
        result["filename"] = file.filename
        return result
    finally:
        os.unlink(tmp_path)


@router.post("/batch", summary="批量检测")
async def detect_batch_api(
    files: list[UploadFile] = File(..., description="多张图片"),
    conf: float = Form(0.25),
    scene_id: int = Form(None),
    current_user=Depends(get_current_user),
):
    """
    快捷批量检测
    """
    temp_paths = []
    original_filenames = []
    try:
        for file in files:
            suffix = os.path.splitext(file.filename)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                temp_paths.append(tmp.name)
                original_filenames.append(
                    os.path.basename(file.filename or tmp.name)
                )

        result = detection_service.detect_batch(
            image_paths=temp_paths,
            conf=conf,
            scene_id=scene_id,
            user_id=current_user.id,
            original_filenames=original_filenames,
        )
        return result
    finally:
        for path in temp_paths:
            try:
                os.unlink(path)
            except Exception:
                pass


@router.post("/zip", summary="ZIP 文件检测")
async def detect_zip_api(
    file: UploadFile = File(..., description="ZIP 压缩包"),
    conf: float = Form(0.25),
    scene_id: int = Form(None),
    current_user=Depends(get_current_user),
):
    """
    快捷 ZIP 检测：解压 ZIP 并批量检测其中所有图片
    """
    suffix = os.path.splitext(file.filename)[1] or ".zip"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = detection_service.detect_zip(
            zip_path=tmp_path,
            conf=conf,
            scene_id=scene_id,
            user_id=current_user.id,
            original_filename=os.path.basename(file.filename or tmp_path),
        )
        return result
    finally:
        os.unlink(tmp_path)


@router.get("/status/{task_id}", summary="查询检测任务状态")
async def get_detection_status(
    task_id: int,
    current_user=Depends(get_current_user),
):
    """查询检测任务状态"""
    db = SessionLocal()
    try:
        task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
        if not task:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "任务不存在"},
            )
        return {
            "task_id": task.id,
            "status": task.status,
            "task_type": task.task_type,
            "total_images": task.total_images,
            "total_objects": task.total_objects,
            "completed_at": (
                task.completed_at.isoformat() if task.completed_at else None
            ),
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }
    finally:
        db.close()

# ── Redis 视频任务进度存储 ──
from app.storage.redis_client import redis_client


@router.post("/video", summary="视频检测")
async def detect_video_api(
    file: UploadFile = File(..., description="视频文件（mp4/avi/mov）"),
    conf: float = Form(0.25, ge=0.1, le=0.9, description="置信度阈值"),
    frame_sample_rate: int = Form(
        5, ge=1, description="帧采样间隔（每 N 帧取 1 帧）"
    ),
    max_frames: int = Form(50, ge=1, le=500, description="最多处理的关键帧数量"),
    scene_id: int = Form(None, description="场景 ID"),
    current_user=Depends(get_current_user),
):
    """
    视频检测：上传视频文件，后台异步处理，通过 status 接口轮询进度

    支持格式：mp4, avi, mov, mkv, wmv, flv
    文件大小限制：50MB
    """
    # ── 校验文件格式 ──
    allowed_video_types = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv"}
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in allowed_video_types:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": f"不支持的视频格式: {suffix}，"
                f"支持的格式: {', '.join(allowed_video_types)}"
            },
        )

    content = await file.read()
    max_file_size = 50 * 1024 * 1024
    if len(content) > max_file_size:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"error": "视频文件不能超过 50MB"},
        )

    # ── 保存视频到临时文件 ──
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    logger.info(
        "视频文件已保存: %s (%.2f MB), 用户: %s",
        tmp_path,
        len(content) / (1024 * 1024),
        current_user.username,
    )

    # ── 先创建检测任务记录 ──
    user_id = current_user.id
    db = SessionLocal()
    try:
        task = DetectionTask(
            user_id=user_id,
            scene_id=scene_id or 1,
            task_type="video",
            status="processing",
            conf_threshold=conf,
        )
        db.add(task)
        db.flush()
        task_id = task.id
        db.commit()
    finally:
        db.close()

    # ── 初始化进度信息 ──
    redis_client.set_json(f"video_task:{task_id}", {
        "status": "processing",
        "progress": 0,
        "message": "视频处理中...",
    }, expire=3600)

    def run_video_detection():
        """后台线程：执行视频检测"""
        try:
            result = detection_service.detect_video(
                video_path=tmp_path,
                conf=conf,
                frame_sample_rate=frame_sample_rate,
                max_frames=max_frames,
                scene_id=scene_id,
                user_id=user_id,
                task_id=task_id,
            )

            if "error" in result:
                redis_client.set_json(f"video_task:{task_id}", {
                    "status": "failed",
                    "progress": 0,
                    "message": result["error"],
                }, expire=3600)
            else:
                redis_client.set_json(f"video_task:{task_id}", {
                    "status": "completed",
                    "progress": 100,
                    "message": f"检测完成，共处理 {result['processed_frames']} 帧，"
                    f"发现 {result['total_objects']} 个目标",
                    "result": result,
                }, expire=3600)
        except Exception as e:
            logger.error("视频后台检测异常: %s", str(e), exc_info=True)
            redis_client.set_json(f"video_task:{task_id}", {
                "status": "failed",
                "progress": 0,
                "message": f"视频检测异常: {str(e)}",
            }, expire=3600)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    thread = threading.Thread(target=run_video_detection, daemon=True)
    thread.start()

    return {
        "task_id": task_id,
        "status": "processing",
        "message": "视频已上传，正在后台处理中，请通过 status 接口轮询进度",
        "filename": file.filename,
    }


@router.get("/video/status/{task_id}", summary="查询视频检测进度")
async def get_video_detection_status(
    task_id: int,
    current_user=Depends(get_current_user),
):
    """
    查询视频检测任务的实时进度和结果

    轮询间隔建议：1-2 秒
    """
    # 从 Redis 获取进度信息
    progress_info = redis_client.get_json(f"video_task:{task_id}")

    if progress_info:
        return {
            "task_id": task_id,
            **progress_info,
        }

    # 回退：从数据库查询
    db = SessionLocal()
    try:
        task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
        if not task:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "任务不存在"},
            )

        result = {
            "task_id": task.id,
            "status": task.status,
            "task_type": task.task_type,
            "total_images": task.total_images,
            "total_objects": task.total_objects or 0,
        }

        # 如果已完成，查询完整结果
        if task.status == "completed":
            from app.entity.db_models import DetectionResult

            results = (
                db.query(DetectionResult)
                .filter(DetectionResult.task_id == task_id)
                .all()
            )

            class_counts = {}
            for r in results:
                class_counts[r.class_name] = class_counts.get(r.class_name, 0) + 1

            result["class_counts"] = class_counts
            result["total_inference_time"] = task.total_inference_time

        return result
    finally:
        db.close()

# ── 单帧缓冲区（CPU 模式优化）──
# 键为 WebSocket 连接 ID，值为最新帧数据
_camera_frame_buffer = {}


def _resolve_camera_device(mode: str) -> torch.device:
    """Resolve a camera inference device without changing process CUDA visibility."""
    normalized_mode = mode.strip().lower()
    if normalized_mode == "cpu":
        return torch.device("cpu")
    if normalized_mode == "gpu":
        if not torch.cuda.is_available():
            raise RuntimeError("GPU 模式不可用：PyTorch 当前未检测到 CUDA 设备")
        return torch.device("cuda:0")
    raise ValueError(f"不支持的检测模式: {mode}")


def _validate_ip_camera_url(camera_url: str) -> str:
    """Validate an Android IP Webcam URL before the backend opens it."""
    if not isinstance(camera_url, str):
        raise ValueError("手机摄像头地址必须是字符串")

    normalized_url = camera_url.strip()
    if not normalized_url:
        raise ValueError("请填写手机摄像头地址")
    if len(normalized_url) > 2048:
        raise ValueError("手机摄像头地址过长")

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("手机摄像头地址仅支持 http 或 https")
    if not parsed.hostname:
        raise ValueError("手机摄像头地址缺少主机 IP")
    if parsed.username or parsed.password:
        raise ValueError("手机摄像头地址不能包含用户名或密码")
    if parsed.fragment:
        raise ValueError("手机摄像头地址不能包含 # 片段")

    try:
        host_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("请填写手机显示的局域网 IP 地址，例如 http://192.168.1.23:8080/video") from exc

    if not host_ip.is_private:
        raise ValueError("手机摄像头地址必须是局域网私有 IP")
    if host_ip.is_loopback or host_ip.is_link_local or host_ip.is_multicast:
        raise ValueError("手机摄像头地址不能是本机、链路本地或组播地址")
    if host_ip.is_unspecified or host_ip.is_reserved:
        raise ValueError("手机摄像头地址不是可用的局域网 IP")

    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("手机摄像头端口无效") from exc

    return normalized_url


def _open_ip_camera_capture(camera_url: str):
    cap = cv2.VideoCapture()
    for prop_name, value in (
        ("CAP_PROP_OPEN_TIMEOUT_MSEC", 5000),
        ("CAP_PROP_READ_TIMEOUT_MSEC", 5000),
        ("CAP_PROP_BUFFERSIZE", 1),
    ):
        prop = getattr(cv2, prop_name, None)
        if prop is not None:
            cap.set(prop, value)
    cap.open(camera_url)
    return cap


def _detect_camera_frame(
    *,
    model,
    frame: np.ndarray,
    mode: str,
    conf: float,
    iou: float,
    inference_device: torch.device,
) -> tuple[str, list[dict], float]:
    imgsz = 416 if mode == "cpu" else 640

    results = model.predict(
        source=frame,
        conf=conf,
        iou=iou,
        imgsz=imgsz,
        device=inference_device,
        save=False,
        verbose=False,
        half=False,
    )
    result = results[0]
    inference_time = float(result.speed.get("inference", 0))

    annotated_img = result.plot()
    _, buffer = cv2.imencode(".jpg", annotated_img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    annotated_b64 = base64.b64encode(buffer).decode("utf-8")

    detections = []
    if result.boxes is not None and len(result.boxes) > 0:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            cls_name = model.names.get(cls_id, f"class_{cls_id}")
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(
                {
                    "class_name": cls_name,
                    "class_id": cls_id,
                    "confidence": round(confidence, 4),
                    "bbox": [
                        round(x1, 1),
                        round(y1, 1),
                        round(x2, 1),
                        round(y2, 1),
                    ],
                }
            )

    return annotated_b64, detections, inference_time


@router.websocket("/camera")
async def camera_detection_ws(websocket: WebSocket):
    """
    摄像头实时检测 WebSocket 接口

    通信协议：
      前端发送：
        - {"type": "config", "source": "browser", "mode": "cpu/gpu", "conf": 0.25}
        - {"type": "config", "source": "ip_webcam", "camera_url": "http://192.168.1.23:8080/video", ...}
        - {"type": "frame", "data": "<base64>"}  本机摄像头发送帧
        - {"type": "pull_frame"}                  手机摄像头请求后端拉取一帧
        - {"type": "close"}                       关闭连接

      后端返回：
        - {"type": "config_ok", "source": "...", "mode": "..."}
        - {"type": "result", "annotated_frame": "<base64>", ...}
        - {"type": "error", "message": "..."}
    """
    await websocket.accept()
    connection_id = id(websocket)
    logger.info("摄像头 WebSocket 连接建立: connection_id=%d", connection_id)

    mode = "cpu"
    conf = 0.25
    iou = 0.45
    scene_id = None
    source = "browser"
    model = None
    inference_device = torch.device("cpu")
    ip_camera_capture = None

    frame_count = 0
    fps_start_time = time.time()
    fps_frame_count = 0
    current_fps = 0.0

    async def send_detection_result(frame: np.ndarray):
        nonlocal current_fps, fps_frame_count, fps_start_time, frame_count

        annotated_b64, detections, inference_time = await asyncio.to_thread(
            _detect_camera_frame,
            model=model,
            frame=frame,
            mode=mode,
            conf=conf,
            iou=iou,
            inference_device=inference_device,
        )

        fps_frame_count += 1
        elapsed = time.time() - fps_start_time
        if elapsed >= 1.0:
            current_fps = fps_frame_count / elapsed
            fps_frame_count = 0
            fps_start_time = time.time()

        frame_count += 1

        await websocket.send_json(
            {
                "type": "result",
                "annotated_frame": annotated_b64,
                "detections": detections,
                "object_count": len(detections),
                "inference_time": round(inference_time, 2),
                "fps": round(current_fps, 1),
                "frame_count": frame_count,
            }
        )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "config":
                mode = data.get("mode", "cpu")
                conf = data.get("conf", 0.25)
                iou = data.get("iou", 0.45)
                scene_id = data.get("scene_id")
                source = data.get("source", "browser")

                if ip_camera_capture is not None:
                    await asyncio.to_thread(ip_camera_capture.release)
                    ip_camera_capture = None

                if source not in {"browser", "ip_webcam"}:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"不支持的摄像头来源: {source}",
                        }
                    )
                    continue

                try:
                    inference_device = _resolve_camera_device(mode)
                    model = await asyncio.to_thread(detection_service._get_model, scene_id)

                    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    await asyncio.to_thread(
                        model.predict,
                        source=dummy_frame,
                        conf=conf,
                        iou=iou,
                        imgsz=640,
                        device=inference_device,
                        save=False,
                        verbose=False,
                    )
                    logger.info("摄像头模型预热完成, 来源: %s, 模式: %s", source, mode)
                except Exception as e:
                    logger.error("模型加载失败: %s", str(e))
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"模型加载失败: {str(e)}",
                        }
                    )
                    continue

                config_response = {
                    "type": "config_ok",
                    "source": source,
                    "mode": mode,
                    "message": f"配置成功，模式: {mode}",
                }

                if source == "ip_webcam":
                    try:
                        camera_url = _validate_ip_camera_url(data.get("camera_url", ""))
                        ip_camera_capture = await asyncio.to_thread(
                            _open_ip_camera_capture, camera_url
                        )
                        if not ip_camera_capture.isOpened():
                            raise RuntimeError("无法打开手机摄像头视频流")

                        ok, frame = await asyncio.to_thread(ip_camera_capture.read)
                        if not ok or frame is None:
                            raise RuntimeError("无法读取手机摄像头画面")

                        height, width = frame.shape[:2]
                        config_response.update(
                            {
                                "width": width,
                                "height": height,
                                "message": f"手机摄像头连接成功，模式: {mode}",
                            }
                        )
                        logger.info("手机摄像头连接成功: %s", camera_url)
                    except Exception as e:
                        if ip_camera_capture is not None:
                            await asyncio.to_thread(ip_camera_capture.release)
                            ip_camera_capture = None
                        logger.error("手机摄像头连接失败: %s", str(e))
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"手机摄像头连接失败: {str(e)}",
                            }
                        )
                        continue

                await websocket.send_json(config_response)

            elif msg_type == "frame":
                if model is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "请先发送 config 消息初始化模型",
                        }
                    )
                    continue
                if source != "browser":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "当前不是本机摄像头模式，请发送 pull_frame",
                        }
                    )
                    continue

                frame_b64 = data.get("data", "")
                if not frame_b64:
                    continue

                try:
                    img_bytes = base64.b64decode(frame_b64)
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    await send_detection_result(frame)
                except Exception as e:
                    logger.error("摄像头帧处理异常: %s", str(e))
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"帧处理失败: {str(e)}",
                        }
                    )

            elif msg_type == "pull_frame":
                if model is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "请先发送 config 消息初始化模型",
                        }
                    )
                    continue
                if source != "ip_webcam" or ip_camera_capture is None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "请先连接手机摄像头",
                        }
                    )
                    continue

                try:
                    ok, frame = await asyncio.to_thread(ip_camera_capture.read)
                    if not ok or frame is None:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "无法从手机摄像头读取画面，请检查手机是否仍在同一 Wi-Fi 并保持 IP Webcam 开启",
                            }
                        )
                        continue
                    await send_detection_result(frame)
                except Exception as e:
                    logger.error("手机摄像头帧处理异常: %s", str(e))
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"手机摄像头帧处理失败: {str(e)}",
                        }
                    )

            elif msg_type == "close":
                logger.info(
                    "摄像头 WebSocket 主动关闭: connection_id=%d", connection_id
                )
                break

    except WebSocketDisconnect:
        logger.info("摄像头 WebSocket 断开: connection_id=%d", connection_id)
    except Exception as e:
        logger.error("摄像头 WebSocket 异常: %s", str(e), exc_info=True)
    finally:
        if ip_camera_capture is not None:
            await asyncio.to_thread(ip_camera_capture.release)
        _camera_frame_buffer.pop(connection_id, None)
        logger.info(
            "摄像头 WebSocket 关闭, 共处理 %d 帧: connection_id=%d",
            frame_count,
            connection_id,
        )
