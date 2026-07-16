"""训练 API 测试。"""

import json
import sys
import types

from app.config.settings import settings
from app.entity.db_models import (
    DetectionScene,
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
)


def _auth_headers(client, db_session):
    """注册并登录测试用户，返回认证请求头。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "training_scene_user",
            "email": "training_scene@example.com",
            "password": "123456",
        },
    )
    user = db_session.query(User).filter_by(username="training_scene_user").one()
    role = db_session.query(Role).filter_by(name="system_admin").one()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    response = client.post(
        "/api/auth/login",
        json={"username": "training_scene_user", "password": "123456"},
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


def test_validate_export_and_download_model(db_session, tmp_path, monkeypatch):
    """评估指标应入库，导出目录应包含权重和报告，下载优先 best.pt。"""
    scene = DetectionScene(
        name="day7_fdd",
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
        task_uuid="day7-eval",
        status="completed",
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

        def __init__(self, weights):
            assert weights.endswith("best.pt")
            self.callbacks = {}

        def add_callback(self, event, callback):
            self.callbacks[event] = callback

        def val(self, **kwargs):
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
    model_version = (
        db_session.query(ModelVersion)
        .filter(ModelVersion.training_task_id == task.id)
        .one()
    )
    assert model_version.map50 == 0.83

    exported = TrainingService.export_model(
        db_session,
        task.id,
        version="v1.1.0",
        description="增强调优版本",
        set_default=True,
        upload_minio=False,
    )
    assert "error" not in exported
    export_dir = tmp_path.parent / "models" / "day7_fdd_v1.1.0"
    assert (export_dir / "best.pt").read_bytes() == b"model-weights"
    assert (export_dir / "confusion_matrix.png").exists()
    report = json.loads((export_dir / "eval_report.json").read_text(encoding="utf-8"))
    assert report["training_config"]["augment_config"] == {"mosaic": 0.5}
    assert exported["is_default"] is True

    download = TrainingService.get_model_download_path(db_session, task.id)
    assert download["filename"] == "best_day7-eval.pt"
