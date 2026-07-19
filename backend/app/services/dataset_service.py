"""数据集管理服务。

职责：
  - 扫描 backend/datasets/ 下的数据集目录，关联归属场景
  - 中文名/显示名双写（data.yaml 的 names_cn 与 detection_scenes 表）
  - 两段式上传：暂存解析（可改英文标签名）→ 确认落盘（英文名此后锁定）
  - 数据集体检评估与报告缓存

目录约定：datasets/{场景名}/yolo_dataset/（data.yaml + images/ + labels/），
与训练启动逻辑（app/api/training.py）保持一致。
"""

import json
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.config.settings import BACKEND_DIR
from app.core.logger import get_logger
from app.entity.db_models import DetectionScene, TrainingTask
from app.training.dataset_splitter import DatasetSplitter
from app.training.dataset_validator import IMAGE_EXTS, validate_dataset

logger = get_logger(__name__)

# 上传暂存目录（与 chat 上传隔离）
STAGING_DIR = Path(tempfile.gettempdir()) / "dataset_uploads"
# 暂存包最长保留时间（秒），过期由下一次上传顺带清理
STAGING_TTL = 6 * 3600
# zip 大小上限
MAX_ZIP_SIZE = 2 * 1024 * 1024 * 1024
# 场景标识（即目录名）规则
SCENE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
# 评估报告缓存文件名
REPORT_FILENAME = "verify_report.json"


def _datasets_root() -> Path:
    return Path(BACKEND_DIR) / "datasets"


def _yolo_dir(dataset_name: str) -> Path:
    return _datasets_root() / dataset_name / "yolo_dataset"


def _safe_dataset_dir(dataset_name: str) -> Path:
    """校验数据集名并返回其根目录，防止路径穿越。"""
    if not SCENE_NAME_PATTERN.match(dataset_name):
        raise ValueError(f"非法数据集名：{dataset_name}")
    return _datasets_root() / dataset_name


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("解析 %s 失败：%s", path, exc)
        return {}


def _names_to_list(names) -> list[str]:
    """data.yaml 的 names 可能是 list 或 {id: name} dict，统一为按 id 排序的列表。"""
    if isinstance(names, list):
        return [str(n) for n in names]
    if isinstance(names, dict):
        return [str(names[k]) for k in sorted(names, key=int)]
    return []


def _cn_map_from_yaml(data: dict, class_names: list[str]) -> dict[str, str]:
    """data.yaml 的 names_cn 是 {id: 中文名}，转为 {英文名: 中文名}。"""
    raw = data.get("names_cn")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(class_names) and value:
            result[class_names[idx]] = str(value)
    return result


def _write_data_yaml(
    yolo_dir: Path,
    class_names: list[str],
    class_names_cn: dict[str, str] | None,
    *,
    has_test: bool,
) -> None:
    """生成标准 data.yaml（names 用 id 映射，names_cn 为平台扩展字段）。"""
    lines = [
        f"path: {yolo_dir.resolve().as_posix()}",
        "",
        "train: images/train",
        "val: images/val",
    ]
    if has_test:
        lines.append("test: images/test")
    lines += ["", f"nc: {len(class_names)}", "", "names:"]
    lines += [f"  {i}: {name}" for i, name in enumerate(class_names)]
    cn_map = class_names_cn or {}
    if cn_map:
        lines += ["", "names_cn:"]
        lines += [
            f"  {i}: {cn_map[name]}"
            for i, name in enumerate(class_names)
            if cn_map.get(name)
        ]
    (yolo_dir / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_yaml_names_cn(yaml_path: Path, class_names: list[str], cn_map: dict[str, str]) -> None:
    """只重写/追加 data.yaml 的 names_cn 段，保留其余内容原样。"""
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # 删除旧 names_cn 段（顶层键起，到下一个非缩进行为止）
    result: list[str] = []
    skipping = False
    for line in lines:
        if skipping:
            if line.strip() and not line.startswith((" ", "\t", "#")):
                skipping = False
            elif line.strip().startswith("#") and not line.startswith((" ", "\t")):
                skipping = False
            else:
                continue
        if line.split("#", 1)[0].strip().startswith("names_cn:") and not line.startswith((" ", "\t")):
            skipping = True
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    entries = [
        f"  {i}: {cn_map[name]}"
        for i, name in enumerate(class_names)
        if cn_map.get(name)
    ]
    if entries:
        result += ["", "names_cn:"] + entries
    yaml_path.write_text("\n".join(result) + "\n", encoding="utf-8")


def _count_images(yolo_dir: Path) -> dict[str, int]:
    counts = {}
    for split in ["train", "val", "test"]:
        split_dir = yolo_dir / "images" / split
        counts[split] = (
            sum(1 for f in split_dir.iterdir() if f.suffix.lower() in IMAGE_EXTS)
            if split_dir.is_dir()
            else 0
        )
    return counts


class DatasetService:
    """数据集管理业务逻辑。"""

    # ── 列表 ──

    def list_datasets(self, db: Session) -> list[dict]:
        """扫描 datasets 目录，逐个关联场景表返回数据集概览。"""
        root = _datasets_root()
        if not root.is_dir():
            return []
        scenes = {s.name: s for s in db.query(DetectionScene).all()}
        items: list[dict] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            yolo_dir = entry / "yolo_dataset"
            yaml_path = yolo_dir / "data.yaml"
            ready = yaml_path.is_file()
            class_names: list[str] = []
            yaml_cn: dict[str, str] = {}
            counts = {"train": 0, "val": 0, "test": 0}
            if ready:
                data = _read_yaml(yaml_path)
                class_names = _names_to_list(data.get("names"))
                yaml_cn = _cn_map_from_yaml(data, class_names)
                counts = _count_images(yolo_dir)
            scene = scenes.get(entry.name)
            scene_cn = (
                scene.class_names_cn if scene and isinstance(scene.class_names_cn, dict) else {}
            )
            report_path = yolo_dir / REPORT_FILENAME
            items.append({
                "name": entry.name,
                "ready": ready,
                "scene": (
                    {
                        "id": scene.id,
                        "display_name": scene.display_name,
                        "category": scene.category,
                        "is_active": scene.is_active,
                    }
                    if scene
                    else None
                ),
                "class_names": class_names,
                # 场景表优先（看板/Agent 实际使用的口径），yaml 兜底
                "class_names_cn": {
                    name: scene_cn.get(name) or yaml_cn.get(name) or ""
                    for name in class_names
                },
                "image_counts": counts,
                "total_images": sum(counts.values()),
                "has_report": report_path.is_file(),
            })
        return items

    # ── 中文名/显示名修改（英文名已锁定） ──

    def update_names(
        self,
        db: Session,
        dataset_name: str,
        *,
        display_name: str | None,
        class_names_cn: dict[str, str],
    ) -> dict:
        """双写场景显示名与类别中文名（data.yaml + detection_scenes）。

        英文类别名不可修改：入参 cn 映射的键必须是 data.yaml 中已有的英文名。
        """
        dataset_dir = _safe_dataset_dir(dataset_name)
        yaml_path = dataset_dir / "yolo_dataset" / "data.yaml"
        if not yaml_path.is_file():
            raise FileNotFoundError(f"数据集 {dataset_name} 未就绪（缺少 yolo_dataset/data.yaml）")
        data = _read_yaml(yaml_path)
        class_names = _names_to_list(data.get("names"))
        unknown = [k for k in class_names_cn if k not in class_names]
        if unknown:
            raise ValueError(f"以下类别不存在于数据集中（英文名不可修改）：{unknown}")
        cn_map = {k: str(v).strip() for k, v in class_names_cn.items() if str(v).strip()}

        _update_yaml_names_cn(yaml_path, class_names, cn_map)

        scene = db.query(DetectionScene).filter(DetectionScene.name == dataset_name).first()
        if scene:
            merged = dict(scene.class_names_cn) if isinstance(scene.class_names_cn, dict) else {}
            for name in class_names:
                if cn_map.get(name):
                    merged[name] = cn_map[name]
                else:
                    merged.pop(name, None)
            scene.class_names_cn = merged
            if display_name and display_name.strip():
                scene.display_name = display_name.strip()
            scene.updated_at = datetime.now()
            db.commit()
        logger.info("数据集 %s 名称已更新（scene=%s）", dataset_name, bool(scene))
        return {
            "name": dataset_name,
            "display_name": scene.display_name if scene else None,
            "class_names": class_names,
            "class_names_cn": cn_map,
            "scene_synced": scene is not None,
        }

    # ── 两段式上传 ──

    def stage_upload(self, file_bytes: bytes, filename: str) -> dict:
        """暂存 zip 并解析结构与类别，返回 upload_id 供确认提交。"""
        if not filename.lower().endswith(".zip"):
            raise ValueError("仅支持 zip 格式数据集包")
        if len(file_bytes) > MAX_ZIP_SIZE:
            raise ValueError("数据集包超过 2GB 上限")
        self._cleanup_staging()
        upload_id = uuid.uuid4().hex
        staging = STAGING_DIR / upload_id
        extract_dir = staging / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        zip_path = staging / "package.zip"
        zip_path.write_bytes(file_bytes)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    target = (extract_dir / member).resolve()
                    if not target.is_relative_to(extract_dir.resolve()):
                        raise ValueError(f"zip 内含非法路径：{member}")
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ValueError("zip 文件损坏或格式错误") from exc
        except ValueError:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        try:
            info = self._detect_structure(extract_dir)
        except ValueError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        (staging / "meta.json").write_text(
            json.dumps({"structure": info["structure"], "root": info["root"]}),
            encoding="utf-8",
        )
        return {
            "upload_id": upload_id,
            "structure": info["structure"],
            "class_names": info["class_names"],
            "class_names_cn": info["class_names_cn"],
            "image_count": info["image_count"],
            "expires_in": STAGING_TTL,
        }

    def commit_upload(
        self,
        db: Session,
        upload_id: str,
        *,
        scene_name: str,
        display_name: str,
        category: str,
        class_names: list[str],
        class_names_cn: dict[str, str],
        description: str | None,
        user_id: int,
        overwrite_classes: bool = False,
    ) -> dict:
        """确认提交：归一化落盘 + 写 data.yaml + upsert 场景表。

        此步骤是英文类别名唯一可修改的时机（class_names 按 id 顺序重命名），
        落盘后英文名即锁定。
        """
        if not SCENE_NAME_PATTERN.match(scene_name):
            raise ValueError("场景标识需为小写字母开头，仅含小写字母/数字/下划线（2~50 字符）")
        if not display_name.strip():
            raise ValueError("场景显示名不能为空")
        staging = STAGING_DIR / upload_id
        meta_path = staging / "meta.json"
        if not meta_path.is_file():
            raise FileNotFoundError("上传已过期或不存在，请重新上传")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        extract_root = Path(meta["root"])
        detected = self._detect_structure(extract_root)
        if len(class_names) != len(detected["class_names"]):
            raise ValueError(
                f"类别数量不匹配：数据集含 {len(detected['class_names'])} 类，提交了 {len(class_names)} 个名称"
            )
        cleaned = [n.strip() for n in class_names]
        if any(not n for n in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("类别英文名不能为空且不能重复")

        target_root = _datasets_root() / scene_name
        yolo_dir = target_root / "yolo_dataset"
        if yolo_dir.exists():
            raise ValueError(f"数据集目录 {scene_name} 已存在，如需更新请先联系管理员处理旧数据")

        # 类别冲突检查：同名场景已有模型版本时，静默改类别会导致口径错乱
        scene = db.query(DetectionScene).filter(DetectionScene.name == scene_name).first()
        if scene and not overwrite_classes:
            old = scene.class_names if isinstance(scene.class_names, list) else []
            if old and old != cleaned:
                raise ValueError(
                    f"场景 {scene_name} 已存在且类别不同（现有：{old}）。"
                    "确认覆盖类别配置请勾选强制覆盖后重试"
                )

        # ── 归一化落盘 ──
        yolo_dir.mkdir(parents=True)
        try:
            stats = self._normalize_to(extract_root, detected["structure"], yolo_dir)
            cn_map = {
                k: str(v).strip()
                for k, v in class_names_cn.items()
                if k in cleaned and str(v).strip()
            }
            has_test = (yolo_dir / "images" / "test").is_dir() and any(
                (yolo_dir / "images" / "test").iterdir()
            )
            _write_data_yaml(yolo_dir, cleaned, cn_map, has_test=has_test)
        except Exception:
            shutil.rmtree(target_root, ignore_errors=True)
            raise

        # ── upsert 场景 ──
        if scene:
            scene.display_name = display_name.strip()
            scene.category = category
            scene.class_names = cleaned
            scene.class_names_cn = cn_map
            if description:
                scene.description = description
            scene.updated_at = datetime.now()
        else:
            scene = DetectionScene(
                name=scene_name,
                display_name=display_name.strip(),
                description=description,
                category=category,
                class_names=cleaned,
                class_names_cn=cn_map,
                is_active=True,
                created_by=user_id,
            )
            db.add(scene)
        db.commit()
        db.refresh(scene)
        shutil.rmtree(staging, ignore_errors=True)
        logger.info(
            "数据集 %s 上传完成：train=%s val=%s test=%s，场景 id=%d",
            scene_name, stats.get("train"), stats.get("val"), stats.get("test"), scene.id,
        )
        return {
            "name": scene_name,
            "scene_id": scene.id,
            "split_stats": {k: stats.get(k, 0) for k in ("train", "val", "test")},
            "missing_labels": len(stats.get("missing_labels", [])),
            "class_names": cleaned,
            "class_names_cn": cn_map,
        }

    # ── 评估 ──

    def evaluate(self, db: Session, dataset_name: str, *, force: bool = False) -> dict:
        """体检数据集并缓存报告；force=False 时优先返回缓存。"""
        dataset_dir = _safe_dataset_dir(dataset_name)
        yolo_dir = dataset_dir / "yolo_dataset"
        yaml_path = yolo_dir / "data.yaml"
        if not yaml_path.is_file():
            raise FileNotFoundError(f"数据集 {dataset_name} 未就绪（缺少 yolo_dataset/data.yaml）")
        report_path = yolo_dir / REPORT_FILENAME
        if not force and report_path.is_file():
            try:
                cached = json.loads(report_path.read_text(encoding="utf-8"))
                cached["cached"] = True
                return cached
            except Exception:
                pass
        data = _read_yaml(yaml_path)
        class_names = _names_to_list(data.get("names"))
        report = validate_dataset(yolo_dir, dict(enumerate(class_names)))
        report["dataset"] = dataset_name
        report["generated_at"] = datetime.now().isoformat(timespec="seconds")
        # 已训练提示：类别变化会影响已有权重
        trained = (
            db.query(TrainingTask.id)
            .join(DetectionScene, DetectionScene.id == TrainingTask.scene_id)
            .filter(DetectionScene.name == dataset_name)
            .first()
        )
        report["has_trained"] = trained is not None
        try:
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("评估报告缓存写入失败：%s", exc)
        report["cached"] = False
        return report

    # ── 内部工具 ──

    @staticmethod
    def _cleanup_staging() -> None:
        """清理过期暂存包。"""
        if not STAGING_DIR.is_dir():
            return
        now = time.time()
        for entry in STAGING_DIR.iterdir():
            try:
                if entry.is_dir() and now - entry.stat().st_mtime > STAGING_TTL:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _detect_structure(extract_dir: Path) -> dict:
        """识别 zip 内部结构，返回结构类型、根目录、类别与图片数。

        支持三种结构：
          - standard: images/ + labels/（内部可再分 split）
          - roboflow: train/valid(val)/test 平级，各含 images/labels
          - flat: 图片与 .txt 标注平铺（或 images/ labels/ 平铺无 split）
        """
        extract_dir = Path(extract_dir)
        # zip 可能带单层包裹目录，向下穿透
        root = extract_dir
        for _ in range(3):
            children = [c for c in root.iterdir() if not c.name.startswith("__MACOSX")]
            if len(children) == 1 and children[0].is_dir():
                root = children[0]
            else:
                break

        def _dir_names(path: Path) -> set[str]:
            return {d.name for d in path.iterdir() if d.is_dir()}

        subdirs = _dir_names(root)
        structure = None
        if {"images", "labels"}.issubset(subdirs):
            structure = "standard"
        elif "train" in subdirs and ({"valid", "val"} & subdirs):
            structure = "roboflow"
        else:
            has_images = any(f.suffix.lower() in IMAGE_EXTS for f in root.iterdir() if f.is_file())
            if has_images:
                structure = "flat"
        if structure is None:
            raise ValueError(
                "无法识别数据集结构：请提供 images/+labels/ 目录、"
                "Roboflow 导出（train/valid/test）或图片与标注平铺的 zip 包"
            )

        # 类别：优先 data.yaml，其次从标注文件推断
        class_names: list[str] = []
        class_names_cn: dict[str, str] = {}
        for yaml_file in sorted(root.rglob("data.yaml")):
            data = _read_yaml(yaml_file)
            names = _names_to_list(data.get("names"))
            if names:
                class_names = names
                class_names_cn = _cn_map_from_yaml(data, names)
                break
        if not class_names:
            max_id = -1
            for txt in list(root.rglob("*.txt"))[:2000]:
                try:
                    for line in txt.read_text(encoding="utf-8", errors="replace").splitlines():
                        parts = line.split()
                        if len(parts) == 5:
                            max_id = max(max_id, int(parts[0]))
                except (ValueError, OSError):
                    continue
            class_names = [f"class_{i}" for i in range(max_id + 1)]

        image_count = sum(
            1 for f in root.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTS
        )
        if image_count == 0:
            raise ValueError("zip 包中未找到任何图片文件")
        return {
            "structure": structure,
            "root": str(root),
            "class_names": class_names,
            "class_names_cn": class_names_cn,
            "image_count": image_count,
        }

    @staticmethod
    def _normalize_to(root: Path, structure: str, yolo_dir: Path) -> dict:
        """把三种来源结构归一化为标准 YOLO 目录，返回划分统计。"""
        root = Path(root)
        if structure == "standard":
            img_root = root / "images"
            lbl_root = root / "labels"
            img_subdirs = {d.name for d in img_root.iterdir() if d.is_dir()}
            if {"train", "val"}.issubset(img_subdirs):
                return DatasetSplitter._organize_from_split_dirs(
                    str(img_root), str(lbl_root), str(yolo_dir)
                )
            return DatasetSplitter.organize_dataset(str(img_root), str(lbl_root), str(yolo_dir))
        if structure == "roboflow":
            stats = {"train": 0, "val": 0, "test": 0, "missing_labels": []}
            mapping = {"train": "train", "valid": "val", "val": "val", "test": "test"}
            for src_name, dst_name in mapping.items():
                src = root / src_name
                if not src.is_dir():
                    continue
                src_img = src / "images" if (src / "images").is_dir() else src
                src_lbl = src / "labels" if (src / "labels").is_dir() else src
                dst_img = yolo_dir / "images" / dst_name
                dst_lbl = yolo_dir / "labels" / dst_name
                dst_img.mkdir(parents=True, exist_ok=True)
                dst_lbl.mkdir(parents=True, exist_ok=True)
                for f in src_img.iterdir():
                    if f.suffix.lower() in IMAGE_EXTS:
                        shutil.copy2(f, dst_img / f.name)
                        lbl = src_lbl / f"{f.stem}.txt"
                        if lbl.is_file():
                            shutil.copy2(lbl, dst_lbl / lbl.name)
                        else:
                            (dst_lbl / f"{f.stem}.txt").touch()
                            stats["missing_labels"].append(f.name)
                        stats[dst_name] += 1
            return stats
        # flat：图片与标注平铺，自动 8:1:1 划分
        return DatasetSplitter.organize_dataset(str(root), str(root), str(yolo_dir))


dataset_service = DatasetService()
