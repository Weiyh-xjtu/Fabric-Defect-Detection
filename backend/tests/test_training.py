"""训练 API 测试。"""

from concurrent.futures import Future
import json
import sys
import types

import pytest

import app.api.training as training_api
from app.config.settings import settings
from app.entity.db_models import (
    DetectionScene,
    ModelEvaluation,
    ModelVersion,
    Role,
    TrainingMetric,
    TrainingTask,
    User,
    UserRole,
)
from app.training.training_service import (
    TrainingService,
    _get_augmentation_kwargs,
    _get_epoch_loss_metrics,
    _resolve_eval_device,
    _running_evaluations,
)


def _auth_headers(client, db_session, username: str = "training_scene_user"):
    """注册并登录测试用户，返回认证请求头。"""
    client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "123456",
        },
    )
    user = db_session.query(User).filter_by(username=username).one()
    role = db_session.query(Role).filter_by(name="system_admin").one()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "123456"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_training_scenes_returns_active_scenes(client, db_session):
    """训练场景接口仅返回数据库中启用的场景。"""
    active_scene = DetectionScene(
        name="fdd",
        display_name="织物缺陷检测",
        category="industry",
        class_names=["defect"],
        is_active=True,
    )
    inactive_scene = DetectionScene(
        name="archived",
        display_name="已停用场景",
        category="industry",
        class_names=["defect"],
        is_active=False,
    )
    db_session.add_all([active_scene, inactive_scene])
    db_session.commit()

    response = client.get(
        "/api/training/scenes", headers=_auth_headers(client, db_session)
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": active_scene.id,
                "name": "fdd",
                "display_name": "织物缺陷检测",
            }
        ]
    }


def test_export_api_schedules_backup_after_response_work(
    client,
    db_session,
    monkeypatch,
) -> None:
    """导出接口不应同步等待远程备份，备份应交给后台任务。"""
    username = "async_export_admin"
    headers = _auth_headers(client, db_session, username)
    user = db_session.query(User).filter_by(username=username).one()
    scene = DetectionScene(
        name="async_export_scene",
        display_name="异步导出场景",
        category="industry",
        class_names=["defect"],
        is_active=False,
    )
    db_session.add(scene)
    db_session.flush()
    task = TrainingTask(
        user_id=user.id,
        scene_id=scene.id,
        task_uuid="async-export-task",
        status="completed",
    )
    db_session.add(task)
    db_session.commit()
    captured = {}

    def fake_export_model(**kwargs):
        captured["upload_minio"] = kwargs["upload_minio"]
        return {
            "model_version_id": 99,
            "version": "v1.0.0",
            "model_name": "async-model",
            "model_path": "models/async/best.pt",
            "export_dir": "models/async",
            "minio_url": None,
            "file_size": 10,
            "evaluation": {"map50": 0.8, "per_class": {}},
            "is_default": False,
            "is_global_default": False,
            "message": "模型已导出",
        }

    monkeypatch.setattr(training_api.training_service, "export_model", fake_export_model)
    monkeypatch.setattr(settings, "MODEL_TASK_PROCESS_ISOLATION", False)
    monkeypatch.setattr(
        training_api,
        "_schedule_export_backup",
        lambda model_version_id: captured.update(backup_id=model_version_id),
    )

    response = client.post(
        f"/api/training/export/{task.id}",
        headers=headers,
        json={"upload_minio": True},
    )

    assert response.status_code == 200
    assert response.json()["message"].endswith("备份将在后台完成")
    assert captured == {"upload_minio": False, "backup_id": 99}


@pytest.mark.asyncio
async def test_export_uses_isolated_model_process(monkeypatch) -> None:
    """生产模式下导出应提交到模型进程池，而不是占用 Web 工作线程。"""
    completed = Future()
    completed.set_result({"version": "v1.0.0"})
    captured = {}

    def fake_submit(func, *args, **kwargs):
        captured.update(func=func, args=args, kwargs=kwargs)
        return completed

    monkeypatch.setattr(settings, "MODEL_TASK_PROCESS_ISOLATION", True)
    monkeypatch.setattr(training_api, "submit_model_task", fake_submit)

    result = await training_api._run_export_model(
        7,
        version="v1.0.0",
        description="isolated",
        set_default=True,
    )

    assert result == {"version": "v1.0.0"}
    assert captured == {
        "func": training_api._export_model_in_worker,
        "args": (7,),
        "kwargs": {
            "version": "v1.0.0",
            "description": "isolated",
            "set_default": True,
        },
    }


def test_cancelled_task_can_predict_with_saved_weights(
    client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    """中断任务保存的权重可以通过训练页图片测试接口推理。"""
    username = "cancelled_predict_admin"
    headers = _auth_headers(client, db_session, username)
    user = db_session.query(User).filter_by(username=username).one()
    scene = DetectionScene(
        name="cancelled_predict_scene",
        display_name="中断模型测试场景",
        category="industry",
        class_names=["defect"],
        is_active=True,
    )
    db_session.add(scene)
    db_session.flush()
    task = TrainingTask(
        user_id=user.id,
        scene_id=scene.id,
        task_uuid="cancelled-predict-task",
        status="cancelled",
        img_size=640,
    )
    db_session.add(task)
    db_session.commit()

    weights_dir = tmp_path / f"task_{task.task_uuid}" / "weights"
    weights_dir.mkdir(parents=True)
    (weights_dir / "best.pt").write_bytes(b"model-weights")
    monkeypatch.setattr(settings, "TRAIN_OUTPUT_DIR", str(tmp_path))

    class FakeResult:
        boxes = None
        speed = {"inference": 12.34}

        @staticmethod
        def plot() -> object:
            return object()

    class FakeYOLO:
        names = {0: "defect"}

        def __init__(self, weights: str) -> None:
            assert weights.endswith("best.pt")

        @staticmethod
        def predict(**kwargs: object) -> list[FakeResult]:
            assert kwargs["imgsz"] == 640
            return [FakeResult()]

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))
    monkeypatch.setattr(
        training_api.cv2,
        "imencode",
        lambda *_args, **_kwargs: (True, b"annotated-image"),
    )

    response = client.post(
        "/api/training/predict",
        headers=headers,
        data={"task_id": task.id, "conf": 0.25, "iou": 0.45},
        files={"file": ("sample.jpg", b"image-content", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == task.id
    assert response.json()["total_objects"] == 0
    assert response.json()["inference_time"] == 12.34


def test_parse_final_results_does_not_add_an_extra_epoch(db_session, tmp_path):
    """Ultralytics 的 results.csv 使用从 1 开始的 epoch 编号。"""
    task_id = 1
    task_uuid = "epoch-test"
    results_dir = tmp_path / f"task_{task_uuid}"
    results_dir.mkdir()
    (results_dir / "results.csv").write_text(
        "epoch,train/box_loss\n1,2.5\n2,2.4\n3,2.3\n4,2.2\n5,2.1\n",
        encoding="utf-8",
    )

    for epoch in range(1, 6):
        db_session.add(TrainingMetric(task_id=task_id, epoch=epoch))
    db_session.commit()

    TrainingService._parse_final_results(
        db_session,
        task_id=task_id,
        task_uuid=task_uuid,
        config={},
        project_path=str(tmp_path),
    )

    metrics = (
        db_session.query(TrainingMetric)
        .filter(TrainingMetric.task_id == task_id)
        .order_by(TrainingMetric.epoch.asc())
        .all()
    )
    assert [metric.epoch for metric in metrics] == [1, 2, 3, 4, 5]
    assert metrics[-1].box_loss == 2.1


def test_epoch_loss_metrics_use_trainer_average_loss():
    """训练回调应使用 trainer.tloss，而不是验证指标字典。"""
    class Trainer:
        tloss = [2.313, 3.624, 1.876]

        @staticmethod
        def label_loss_items(losses):
            assert losses == [2.313, 3.624, 1.876]
            return {
                "train/box_loss": losses[0],
                "train/cls_loss": losses[1],
                "train/dfl_loss": losses[2],
            }

    assert _get_epoch_loss_metrics(Trainer()) == {
        "train/box_loss": 2.313,
        "train/cls_loss": 3.624,
        "train/dfl_loss": 1.876,
    }


def test_augmentation_config_only_applies_supported_keys():
    """调优参数应传给 Ultralytics，但不能覆盖训练产物路径等核心配置。"""
    assert _get_augmentation_kwargs(
        {
            "mosaic": 0.8,
            "mixup": 0.1,
            "fliplr": 0.5,
            "project": "unexpected",
            "data": "unexpected.yaml",
        }
    ) == {"mosaic": 0.8, "mixup": 0.1, "fliplr": 0.5}


@pytest.mark.parametrize("task_status", ["completed", "cancelled"])
def test_validate_export_and_download_model(
    db_session,
    tmp_path,
    monkeypatch,
    task_status: str,
) -> None:
    """完成和中断任务均可评估、导出，且下载优先 best.pt。"""
    scene_name = f"day7_fdd_{task_status}"
    task_uuid = f"day7-eval-{task_status}"
    scene = DetectionScene(
        name=scene_name,
        display_name="Day7 织物缺陷检测",
        category="industry",
        class_names=["defect"],
        is_active=True,
    )
    db_session.add(scene)
    db_session.commit()

    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text("path: .\ntrain: images/train\nval: images/val\nnames: [defect]\n", encoding="utf-8")
    task = TrainingTask(
        user_id=1,
        scene_id=scene.id,
        task_uuid=task_uuid,
        status=task_status,
        model_name="yolo11n",
        epochs=5,
        img_size=640,
        batch_size=2,
        device="cpu",
        optimizer="SGD",
        lr0=0.01,
        data_yaml=str(data_yaml),
        dataset_path=str(tmp_path),
        augment_config={"mosaic": 0.5},
    )
    db_session.add(task)
    db_session.commit()

    task_dir = tmp_path / f"task_{task.task_uuid}"
    weights_dir = task_dir / "weights"
    weights_dir.mkdir(parents=True)
    (weights_dir / "best.pt").write_bytes(b"model-weights")
    (task_dir / "confusion_matrix.png").write_bytes(b"plot")
    monkeypatch.setattr(settings, "TRAIN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.training.training_service._backend_dir",
        lambda: tmp_path.parent,
    )

    class BoxMetrics:
        mp = 0.81
        mr = 0.72
        map50 = 0.83
        map = 0.51
        ap50 = [0.83]
        ap = [0.51]
        ap_class_index = [0]

    class FakeYOLO:
        names = {0: "defect"}
        val_calls = 0

        def __init__(self, weights):
            assert weights.endswith("best.pt")
            self.callbacks = {}

        def add_callback(self, event, callback):
            self.callbacks[event] = callback

        def val(self, **kwargs):
            type(self).val_calls += 1
            assert kwargs["data"] == str(data_yaml)
            assert kwargs["plots"] is True
            validator = types.SimpleNamespace(nt_per_class=[12])
            self.callbacks["on_val_end"](validator)
            return types.SimpleNamespace(box=BoxMetrics())

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))

    validation = TrainingService.validate_model(db_session, task.id)
    assert validation["overall"]["map50"] == 0.83
    assert validation["per_class"] == {
        "defect": {"ap50": 0.83, "ap50_95": 0.51, "instances": 12}
    }
    assert "confusion_matrix.png" in validation["artifacts"]
    model_version = (
        db_session.query(ModelVersion)
        .filter(ModelVersion.training_task_id == task.id)
        .one()
    )
    assert model_version.map50 == 0.83
    evaluation_record = db_session.query(ModelEvaluation).filter_by(
        training_task_id=task.id
    ).one()
    assert evaluation_record.weight_sha256
    assert evaluation_record.dataset_fingerprint
    assert evaluation_record.split == "val"
    assert evaluation_record.conf == 0.001
    assert evaluation_record.iou == 0.6
    assert evaluation_record.imgsz == 640

    cached_validation = TrainingService.validate_model(db_session, task.id)
    assert cached_validation["cached"] is True
    assert cached_validation["evaluation_id"] == evaluation_record.id
    assert FakeYOLO.val_calls == 1

    exported = TrainingService.export_model(
        db_session,
        task.id,
        version="v1.1.0",
        description="增强调优版本",
        set_default=True,
        upload_minio=False,
    )
    assert "error" not in exported
    export_dir = tmp_path.parent / "models" / f"{scene_name}_v1.1.0"
    assert (export_dir / "best.pt").read_bytes() == b"model-weights"
    assert (export_dir / "confusion_matrix.png").exists()
    report = json.loads((export_dir / "eval_report.json").read_text(encoding="utf-8"))
    assert report["training_config"]["augment_config"] == {"mosaic": 0.5}
    assert exported["is_default"] is True
    # 导出应复用完全匹配的评估记录，不再次执行 model.val()。
    assert FakeYOLO.val_calls == 1

    data_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\nnames: [defect]\n# changed\n",
        encoding="utf-8",
    )
    refreshed_validation = TrainingService.validate_model(db_session, task.id)
    assert refreshed_validation["cached"] is False
    assert FakeYOLO.val_calls == 2
    assert db_session.query(ModelEvaluation).filter_by(
        training_task_id=task.id
    ).count() == 2

    download = TrainingService.get_model_download_path(db_session, task.id)
    assert download["filename"] == f"best_{task_uuid}.pt"


def test_resolve_eval_device_follows_training_device_with_cpu_fallback(monkeypatch):
    """评估设备跟随训练设备；无 CUDA 或编号越界时回退 CPU。"""
    # CPU 训练 → 直接 CPU，不依赖 torch
    assert _resolve_eval_device("cpu") == "cpu"
    assert _resolve_eval_device(None) == "cpu"
    assert _resolve_eval_device("") == "cpu"

    # GPU 训练但本机无 CUDA → 回退 CPU
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: False,
            device_count=lambda: 0,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert _resolve_eval_device("0") == "cpu"

    # CUDA 可用且编号存在 → 沿用训练设备
    fake_torch.cuda.is_available = lambda: True
    fake_torch.cuda.device_count = lambda: 1
    assert _resolve_eval_device("0") == "0"
    assert _resolve_eval_device("cuda:0") == "cuda:0"

    # CUDA 可用但训练用的显卡编号在本机不存在 → 回退 CPU
    assert _resolve_eval_device("1") == "cpu"

    # torch 导入/探测异常 → 回退 CPU
    class BrokenCuda:
        def is_available(self):
            raise RuntimeError("driver error")

    monkeypatch.setitem(
        sys.modules, "torch", types.SimpleNamespace(cuda=BrokenCuda())
    )
    assert _resolve_eval_device("0") == "cpu"


def _make_completed_task(
    db_session,
    tmp_path,
    monkeypatch,
    task_uuid: str,
    status: str = "completed",
) -> TrainingTask:
    """构造一个带权重与 data.yaml 的可用训练任务。"""
    scene = DetectionScene(
        name=f"scene_{task_uuid}",
        display_name="异步评估场景",
        category="industry",
        class_names=["defect"],
        is_active=True,
    )
    db_session.add(scene)
    db_session.commit()

    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\nnames: [defect]\n",
        encoding="utf-8",
    )
    task = TrainingTask(
        user_id=1,
        scene_id=scene.id,
        task_uuid=task_uuid,
        status=status,
        model_name="yolo11n",
        epochs=5,
        img_size=640,
        batch_size=2,
        device="cpu",
        optimizer="SGD",
        lr0=0.01,
        data_yaml=str(data_yaml),
        dataset_path=str(tmp_path),
    )
    db_session.add(task)
    db_session.commit()

    weights_dir = tmp_path / f"task_{task_uuid}" / "weights"
    weights_dir.mkdir(parents=True)
    (weights_dir / "best.pt").write_bytes(b"model-weights")
    monkeypatch.setattr(settings, "TRAIN_OUTPUT_DIR", str(tmp_path))
    return task


@pytest.mark.parametrize("task_status", ["completed", "cancelled"])
def test_start_validation_runs_in_background_and_reports_status(
    db_session,
    tmp_path,
    monkeypatch,
    task_status: str,
) -> None:
    """完成和中断任务的异步评估均能返回报告。"""
    task = _make_completed_task(
        db_session,
        tmp_path,
        monkeypatch,
        f"async-eval-{task_status}",
        status=task_status,
    )

    class BoxMetrics:
        mp = 0.81
        mr = 0.72
        map50 = 0.83
        map = 0.51
        ap50 = [0.83]
        ap = [0.51]
        ap_class_index = [0]

    class FakeYOLO:
        names = {0: "defect"}

        def __init__(self, weights):
            self.callbacks = {}

        def add_callback(self, event, callback):
            self.callbacks[event] = callback

        def val(self, **kwargs):
            assert kwargs["device"] == "cpu"
            validator = types.SimpleNamespace(nt_per_class=[12])
            self.callbacks["on_val_end"](validator)
            return types.SimpleNamespace(box=BoxMetrics())

    monkeypatch.setitem(
        sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO)
    )
    monkeypatch.setattr(settings, "MODEL_TASK_PROCESS_ISOLATION", False)
    # 后台线程使用独立会话；测试中让它复用当前测试会话
    monkeypatch.setattr(
        "app.training.training_service.SessionLocal", lambda: db_session
    )
    monkeypatch.setattr(db_session, "close", lambda: None)
    _running_evaluations.pop(task.id, None)

    started = TrainingService.start_validation(db_session, task.id)
    assert started == {
        "task_id": task.id,
        "status": "running",
        "split": "val",
        "message": "评估任务已启动，请轮询评估状态获取结果",
        "cached": False,
        "report": None,
    }

    _running_evaluations[task.id]["thread"].join(timeout=10)

    status = TrainingService.get_validation_status(task.id)
    assert status["status"] == "completed"
    assert status["report"]["overall"]["map50"] == 0.83
    assert status["report"]["per_class"] == {
        "defect": {"ap50": 0.83, "ap50_95": 0.51, "instances": 12}
    }
    assert status["started_at"] is not None
    assert status["completed_at"] is not None
    _running_evaluations.pop(task.id, None)

    cached_start = TrainingService.start_validation(db_session, task.id)
    assert cached_start["status"] == "completed"
    assert cached_start["cached"] is True
    assert cached_start["report"]["overall"]["map50"] == 0.83
    _running_evaluations.pop(task.id, None)

    persisted_status = TrainingService.get_validation_status(task.id, db=db_session)
    assert persisted_status["status"] == "completed"
    assert persisted_status["cached"] is True


def test_start_validation_rejects_duplicate_and_invalid_tasks(
    db_session, tmp_path, monkeypatch
):
    """评估进行中重复启动应报错；未完成任务与未知任务直接拒绝。"""
    task = _make_completed_task(db_session, tmp_path, monkeypatch, "dup-eval")

    _running_evaluations[task.id] = {
        "status": "running",
        "split": "val",
        "report": None,
        "error": None,
        "started_at": "2026-07-17T00:00:00",
        "completed_at": None,
        "thread": None,
    }
    duplicated = TrainingService.start_validation(db_session, task.id)
    assert duplicated == {"error": "该任务已有评估正在进行，请等待完成"}
    _running_evaluations.pop(task.id, None)

    task.status = "running"
    db_session.commit()
    not_completed = TrainingService.start_validation(db_session, task.id)
    assert "当前状态不能评估模型" in not_completed["error"]

    missing = TrainingService.start_validation(db_session, task_id=987654)
    assert missing == {"error": "训练任务不存在"}


def test_get_validation_status_defaults_to_idle():
    """注册表中没有记录（如服务重启后）应返回 idle。"""
    _running_evaluations.pop(424242, None)
    assert TrainingService.get_validation_status(424242) == {
        "task_id": 424242,
        "status": "idle",
        "cached": False,
    }
