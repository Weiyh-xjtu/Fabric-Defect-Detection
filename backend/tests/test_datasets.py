"""数据集管理 API 测试。"""

import io
import json
import zipfile
from pathlib import Path

import pytest

import app.services.dataset_service as ds_module
from app.entity.db_models import DetectionScene, Role, User, UserRole


def _admin_headers(client, db_session, username: str) -> dict[str, str]:
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
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": "123456"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
)


@pytest.fixture()
def datasets_root(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    root.mkdir()
    monkeypatch.setattr(ds_module, "_datasets_root", lambda: root)
    monkeypatch.setattr(ds_module, "STAGING_DIR", tmp_path / "staging")
    return root


def _make_dataset(root: Path, name: str, *, names_cn: bool = True) -> Path:
    yolo = root / name / "yolo_dataset"
    for split in ("train", "val"):
        (yolo / "images" / split).mkdir(parents=True)
        (yolo / "labels" / split).mkdir(parents=True)
        (yolo / "images" / split / "a.png").write_bytes(PNG_1PX)
        (yolo / "labels" / split / "a.txt").write_text(
            "0 0.5 0.5 0.2 0.2\n1 0.3 0.3 0.1 0.1\n", encoding="utf-8"
        )
    lines = [
        "train: images/train",
        "val: images/val",
        "",
        "nc: 2",
        "names:",
        "  0: hole",
        "  1: stain",
    ]
    if names_cn:
        lines += ["names_cn:", "  0: 破洞", "  1: 污渍"]
    (yolo / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return yolo


def _get_or_create_scene(db_session, name: str, **kwargs) -> DetectionScene:
    scene = db_session.query(DetectionScene).filter_by(name=name).first()
    if scene is None:
        scene = DetectionScene(
            name=name,
            display_name=kwargs.get("display_name", name),
            category=kwargs.get("category", "industry"),
            class_names=kwargs.get("class_names", ["hole", "stain"]),
            class_names_cn=kwargs.get("class_names_cn"),
            # 停用，避免污染共享测试库中按启用场景断言的用例
            is_active=False,
        )
        db_session.add(scene)
    else:
        for key, value in kwargs.items():
            setattr(scene, key, value)
    db_session.commit()
    return scene


def _deactivate_scene(db_session, name: str) -> None:
    """commit_upload 会创建启用场景；测试后停用避免影响其他用例。"""
    db_session.query(DetectionScene).filter_by(name=name).update({"is_active": False})
    db_session.commit()


def _make_upload_zip(*, with_yaml: bool = True, flat: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if flat:
            for i in range(10):
                zf.writestr(f"img_{i}.png", PNG_1PX)
                zf.writestr(f"img_{i}.txt", "0 0.5 0.5 0.2 0.2\n")
        else:
            for split in ("train", "valid"):
                for i in range(3):
                    zf.writestr(f"ds/{split}/images/{split}_{i}.png", PNG_1PX)
                    zf.writestr(f"ds/{split}/labels/{split}_{i}.txt", "1 0.4 0.4 0.2 0.2\n")
            if with_yaml:
                zf.writestr(
                    "ds/data.yaml",
                    "nc: 2\nnames: ['broken', 'dirty']\nnames_cn:\n  0: 断裂\n  1: 脏污\n",
                )
    return buf.getvalue()


def test_datasets_require_model_permission(client) -> None:
    client.post(
        "/api/auth/register",
        json={
            "username": "dataset_normal_user",
            "email": "dataset_normal_user@example.com",
            "password": "123456",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "dataset_normal_user", "password": "123456"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/datasets", headers=headers).status_code == 403


def test_list_datasets_joins_scene_and_prefers_scene_cn(
    client, db_session, datasets_root
) -> None:
    _make_dataset(datasets_root, "ds_list_demo")
    _get_or_create_scene(
        db_session,
        "ds_list_demo",
        display_name="演示场景",
        class_names_cn={"hole": "场景表破洞"},
    )
    headers = _admin_headers(client, db_session, "dataset_list_admin")

    response = client.get("/api/datasets", headers=headers)

    assert response.status_code == 200
    items = {item["name"]: item for item in response.json()["items"]}
    item = items["ds_list_demo"]
    assert item["ready"] is True
    assert item["scene"]["display_name"] == "演示场景"
    assert item["class_names"] == ["hole", "stain"]
    # 场景表优先，yaml 兜底
    assert item["class_names_cn"] == {"hole": "场景表破洞", "stain": "污渍"}
    assert item["image_counts"] == {"train": 1, "val": 1, "test": 0}


def test_update_names_writes_yaml_and_scene(client, db_session, datasets_root) -> None:
    yolo = _make_dataset(datasets_root, "ds_rename_demo")
    _get_or_create_scene(
        db_session,
        "ds_rename_demo",
        display_name="旧名",
        class_names_cn={"hole": "旧破洞"},
    )
    headers = _admin_headers(client, db_session, "dataset_rename_admin")

    response = client.put(
        "/api/datasets/ds_rename_demo/names",
        headers=headers,
        json={
            "display_name": "新显示名",
            "class_names_cn": {"hole": "新破洞", "stain": "新污渍"},
        },
    )

    assert response.status_code == 200
    assert response.json()["scene_synced"] is True
    db_session.expire_all()
    scene = db_session.query(DetectionScene).filter_by(name="ds_rename_demo").one()
    assert scene.display_name == "新显示名"
    assert scene.class_names_cn == {"hole": "新破洞", "stain": "新污渍"}
    yaml_text = (yolo / "data.yaml").read_text(encoding="utf-8")
    assert "新破洞" in yaml_text and "新污渍" in yaml_text
    assert "旧破洞" not in yaml_text
    # 英文 names 段保持不变
    assert "0: hole" in yaml_text and "1: stain" in yaml_text


def test_update_names_rejects_unknown_english_class(
    client, db_session, datasets_root
) -> None:
    _make_dataset(datasets_root, "ds_lock_demo")
    headers = _admin_headers(client, db_session, "dataset_lock_admin")

    response = client.put(
        "/api/datasets/ds_lock_demo/names",
        headers=headers,
        json={"class_names_cn": {"renamed_class": "改名"}},
    )

    assert response.status_code == 400
    assert "不可修改" in response.json()["message"]


def test_upload_stage_detects_structure_and_classes(client, db_session, datasets_root) -> None:
    headers = _admin_headers(client, db_session, "dataset_upload_admin")

    response = client.post(
        "/api/datasets/upload",
        headers=headers,
        files={"file": ("pack.zip", _make_upload_zip(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["structure"] == "roboflow"
    assert payload["class_names"] == ["broken", "dirty"]
    assert payload["class_names_cn"] == {"broken": "断裂", "dirty": "脏污"}
    assert payload["image_count"] == 6
    assert payload["upload_id"]


def test_upload_commit_renames_classes_and_creates_scene(
    client, db_session, datasets_root
) -> None:
    headers = _admin_headers(client, db_session, "dataset_commit_admin")
    upload = client.post(
        "/api/datasets/upload",
        headers=headers,
        files={"file": ("pack.zip", _make_upload_zip(), "application/zip")},
    ).json()

    # 提交时改英文名（唯一可改时机）
    response = client.post(
        f"/api/datasets/upload/{upload['upload_id']}/commit",
        headers=headers,
        json={
            "scene_name": "ds_commit_demo",
            "display_name": "上传场景",
            "category": "industry",
            "class_names": ["thread_break", "oil_stain"],
            "class_names_cn": {"thread_break": "断线", "oil_stain": "油污"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["class_names"] == ["thread_break", "oil_stain"]
    assert payload["split_stats"]["train"] == 3
    assert payload["split_stats"]["val"] == 3
    scene = db_session.query(DetectionScene).filter_by(name="ds_commit_demo").one()
    assert scene.display_name == "上传场景"
    assert scene.class_names == ["thread_break", "oil_stain"]
    assert scene.class_names_cn == {"thread_break": "断线", "oil_stain": "油污"}
    yaml_text = (
        datasets_root / "ds_commit_demo" / "yolo_dataset" / "data.yaml"
    ).read_text(encoding="utf-8")
    assert "0: thread_break" in yaml_text
    assert "0: 断线" in yaml_text
    # 图片与标注已归一化落盘
    assert (
        datasets_root / "ds_commit_demo" / "yolo_dataset" / "images" / "train"
    ).is_dir()
    _deactivate_scene(db_session, "ds_commit_demo")


def test_upload_commit_conflicting_classes_requires_overwrite(
    client, db_session, datasets_root
) -> None:
    _get_or_create_scene(
        db_session,
        "ds_conflict_demo",
        class_names=["old_a", "old_b"],
    )
    headers = _admin_headers(client, db_session, "dataset_conflict_admin")
    upload = client.post(
        "/api/datasets/upload",
        headers=headers,
        files={"file": ("pack.zip", _make_upload_zip(), "application/zip")},
    ).json()
    body = {
        "scene_name": "ds_conflict_demo",
        "display_name": "冲突场景",
        "category": "industry",
        "class_names": ["broken", "dirty"],
        "class_names_cn": {},
    }

    rejected = client.post(
        f"/api/datasets/upload/{upload['upload_id']}/commit", headers=headers, json=body
    )
    assert rejected.status_code == 400
    assert "类别不同" in rejected.json()["message"]

    accepted = client.post(
        f"/api/datasets/upload/{upload['upload_id']}/commit",
        headers=headers,
        json={**body, "overwrite_classes": True},
    )
    assert accepted.status_code == 200
    db_session.expire_all()
    scene = db_session.query(DetectionScene).filter_by(name="ds_conflict_demo").one()
    assert scene.class_names == ["broken", "dirty"]
    _deactivate_scene(db_session, "ds_conflict_demo")


def test_upload_flat_zip_auto_splits(client, db_session, datasets_root) -> None:
    headers = _admin_headers(client, db_session, "dataset_flat_admin")
    upload = client.post(
        "/api/datasets/upload",
        headers=headers,
        files={"file": ("flat.zip", _make_upload_zip(flat=True), "application/zip")},
    ).json()
    assert upload["structure"] == "flat"
    # 无 data.yaml 时英文名从标注推断为 class_N 占位
    assert upload["class_names"] == ["class_0"]

    response = client.post(
        f"/api/datasets/upload/{upload['upload_id']}/commit",
        headers=headers,
        json={
            "scene_name": "ds_flat_demo",
            "display_name": "平铺数据集",
            "category": "industry",
            "class_names": ["hole"],
            "class_names_cn": {"hole": "破洞"},
        },
    )

    assert response.status_code == 200
    stats = response.json()["split_stats"]
    assert stats["train"] + stats["val"] + stats["test"] == 10
    _deactivate_scene(db_session, "ds_flat_demo")


def test_evaluate_returns_report_and_caches(client, db_session, datasets_root) -> None:
    yolo = _make_dataset(datasets_root, "ds_eval_demo")
    headers = _admin_headers(client, db_session, "dataset_eval_admin")

    first = client.post("/api/datasets/ds_eval_demo/evaluate", headers=headers)
    assert first.status_code == 200
    report = first.json()
    assert report["cached"] is False
    assert report["passed"] is True
    assert report["summary"]["total_images"] == 2
    assert report["summary"]["total_annotations"] == 4
    names = {c["name"] for c in report["class_distribution"]}
    assert names == {"hole", "stain"}
    assert (yolo / "verify_report.json").is_file()

    second = client.post("/api/datasets/ds_eval_demo/evaluate", headers=headers)
    assert second.json()["cached"] is True

    forced = client.post("/api/datasets/ds_eval_demo/evaluate?force=true", headers=headers)
    assert forced.json()["cached"] is False


def test_evaluate_flags_errors_and_suggestions(client, db_session, datasets_root) -> None:
    yolo = _make_dataset(datasets_root, "ds_eval_bad")
    # 制造问题：越界坐标 + 未定义类别 id + 缺标注图片
    (yolo / "labels" / "train" / "a.txt").write_text(
        "0 0.5 0.5 0.2 0.2\n9 1.5 0.5 0.2 0.2\n", encoding="utf-8"
    )
    (yolo / "images" / "train" / "b.png").write_bytes(PNG_1PX)
    headers = _admin_headers(client, db_session, "dataset_eval_bad_admin")

    response = client.post("/api/datasets/ds_eval_bad/evaluate", headers=headers)

    assert response.status_code == 200
    report = response.json()
    assert report["passed"] is False
    messages = " ".join(i["message"] for i in report["issues"])
    assert "未在 data.yaml" in messages
    assert "坐标越界" in messages or "越界" in messages
    assert "缺少标注" in messages
    assert report["suggestions"]  # 样本量小必然有建议


def test_evaluate_missing_dataset_returns_404(client, db_session, datasets_root) -> None:
    headers = _admin_headers(client, db_session, "dataset_eval_404_admin")
    assert (
        client.post("/api/datasets/ds_not_exist/evaluate", headers=headers).status_code
        == 404
    )


def test_unregistered_dataset_can_rename_classes_and_dataset(
    client, db_session, datasets_root
) -> None:
    """未登记数据集（无场景记录）可改英文类别名与数据集名。"""
    _make_dataset(datasets_root, "ds_free_demo")
    headers = _admin_headers(client, db_session, "dataset_free_admin")

    response = client.put(
        "/api/datasets/ds_free_demo/names",
        headers=headers,
        json={
            "new_name": "ds_free_renamed",
            "new_class_names": ["big_hole", "oil_mark"],
            "class_names_cn": {"big_hole": "大洞", "oil_mark": "油渍"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "ds_free_renamed"
    assert payload["class_names"] == ["big_hole", "oil_mark"]
    assert payload["scene_synced"] is False
    # 目录已改名，yaml 内 names/names_cn/path 均已更新
    assert not (datasets_root / "ds_free_demo").exists()
    yaml_text = (
        datasets_root / "ds_free_renamed" / "yolo_dataset" / "data.yaml"
    ).read_text(encoding="utf-8")
    assert "0: big_hole" in yaml_text and "1: oil_mark" in yaml_text
    assert "hole" in yaml_text  # big_hole 含 hole 子串，检查旧独立名不在
    assert "0: hole" not in yaml_text
    assert "0: 大洞" in yaml_text


def test_registered_dataset_rejects_class_and_dataset_rename(
    client, db_session, datasets_root
) -> None:
    _make_dataset(datasets_root, "ds_locked_demo")
    _get_or_create_scene(db_session, "ds_locked_demo")
    headers = _admin_headers(client, db_session, "dataset_locked_admin")

    renamed_classes = client.put(
        "/api/datasets/ds_locked_demo/names",
        headers=headers,
        json={"new_class_names": ["a", "b"]},
    )
    renamed_dataset = client.put(
        "/api/datasets/ds_locked_demo/names",
        headers=headers,
        json={"new_name": "ds_locked_other"},
    )

    assert renamed_classes.status_code == 400
    assert "已锁定" in renamed_classes.json()["message"]
    assert renamed_dataset.status_code == 400
    assert "已锁定" in renamed_dataset.json()["message"]


def test_rename_rejects_duplicate_names(client, db_session, datasets_root) -> None:
    _make_dataset(datasets_root, "ds_dup_a")
    _make_dataset(datasets_root, "ds_dup_b")
    _get_or_create_scene(db_session, "ds_dup_scene")
    headers = _admin_headers(client, db_session, "dataset_dup_admin")

    to_existing_dir = client.put(
        "/api/datasets/ds_dup_a/names",
        headers=headers,
        json={"new_name": "ds_dup_b"},
    )
    to_existing_scene = client.put(
        "/api/datasets/ds_dup_a/names",
        headers=headers,
        json={"new_name": "ds_dup_scene"},
    )

    assert to_existing_dir.status_code == 400
    assert "不可重名" in to_existing_dir.json()["message"]
    assert to_existing_scene.status_code == 400
    assert "已被占用" in to_existing_scene.json()["message"]


def test_register_unregistered_dataset(client, db_session, datasets_root) -> None:
    _make_dataset(datasets_root, "ds_register_demo")
    headers = _admin_headers(client, db_session, "dataset_register_admin")

    response = client.post(
        "/api/datasets/ds_register_demo/register",
        headers=headers,
        json={"display_name": "登记场景", "category": "industry"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "登记场景"
    assert payload["class_names"] == ["hole", "stain"]
    # 类别中文名从 data.yaml 带入
    assert payload["class_names_cn"] == {"hole": "破洞", "stain": "污渍"}
    scene = db_session.query(DetectionScene).filter_by(name="ds_register_demo").one()
    assert scene.class_names == ["hole", "stain"]

    # 重复登记被拒
    again = client.post(
        "/api/datasets/ds_register_demo/register",
        headers=headers,
        json={"display_name": "再登记", "category": "industry"},
    )
    assert again.status_code == 400
    assert "重复登记" in again.json()["message"]
    _deactivate_scene(db_session, "ds_register_demo")


def test_upload_commit_without_register_keeps_unregistered(
    client, db_session, datasets_root
) -> None:
    headers = _admin_headers(client, db_session, "dataset_noreg_admin")
    upload = client.post(
        "/api/datasets/upload",
        headers=headers,
        files={"file": ("pack.zip", _make_upload_zip(), "application/zip")},
    ).json()

    response = client.post(
        f"/api/datasets/upload/{upload['upload_id']}/commit",
        headers=headers,
        json={
            "scene_name": "ds_noreg_demo",
            "category": "industry",
            "class_names": ["broken", "dirty"],
            "class_names_cn": {},
            "register_scene": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["registered"] is False
    assert payload["scene_id"] is None
    # 未建场景记录，保持未登记状态
    assert (
        db_session.query(DetectionScene).filter_by(name="ds_noreg_demo").first() is None
    )
    # 数据已落盘
    assert (datasets_root / "ds_noreg_demo" / "yolo_dataset" / "data.yaml").is_file()

    # 未登记 → 仍可改英文名，随后可登记
    rename = client.put(
        "/api/datasets/ds_noreg_demo/names",
        headers=headers,
        json={"new_class_names": ["thread_break", "oil_stain"]},
    )
    assert rename.status_code == 200
    register = client.post(
        "/api/datasets/ds_noreg_demo/register",
        headers=headers,
        json={"display_name": "后补登记", "category": "industry"},
    )
    assert register.status_code == 200
    assert register.json()["class_names"] == ["thread_break", "oil_stain"]
    _deactivate_scene(db_session, "ds_noreg_demo")
