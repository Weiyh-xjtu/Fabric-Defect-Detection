"""YOLO 数据集体检器。

由 ``tools/verify_dataset.py`` 的核心校验逻辑提炼而来，供应用内
数据集管理模块调用（tools 目录按约定不被应用导入）。

输出结构（JSON 可序列化）：
    - summary: 总体统计（图像/标注/目标/空标注/平均每图标注）
    - splits: 各 split 的图像/标注/目标统计
    - class_distribution: [{class_id, name, count, ratio}]
    - bbox_stats: 边界框统计（平均/最大/最小尺寸、小目标与大目标占比）
    - issues: [{level: error|warning, message, samples: [...]}]
    - suggestions: [str]
    - passed: 是否通过（无 error 级问题）
"""

from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# 类别不平衡告警阈值（最多类/最少类）
IMBALANCE_RATIO = 10
# 小目标面积阈值（归一化面积）
SMALL_BOX_AREA = 0.001
# 大目标面积阈值
LARGE_BOX_AREA = 0.5
# 问题清单每项最多保留的样本数
MAX_SAMPLES = 10


def validate_dataset(dataset_dir: str | Path, class_names: dict[int, str] | None = None) -> dict:
    """校验 YOLO 数据集并生成报告。

    Args:
        dataset_dir: yolo_dataset 目录（含 images/ labels/）。
        class_names: 类别 id → 英文名映射；缺省时报告以 class_id 显示。
    """
    dataset_path = Path(dataset_dir)
    class_names = class_names or {}

    total_images = 0
    total_labels = 0
    total_annotations = 0
    empty_labels = 0
    missing_labels: list[str] = []
    missing_images: list[str] = []
    invalid_format: list[str] = []
    out_of_range: list[str] = []
    class_distribution: dict[int, int] = {}
    splits: dict[str, dict] = {}
    bbox = {
        "total": 0,
        "avg_width": 0.0,
        "avg_height": 0.0,
        "max_width": 0.0,
        "max_height": 0.0,
        "min_width": float("inf"),
        "min_height": float("inf"),
        "small_boxes": 0,
        "large_boxes": 0,
    }
    missing_dirs: list[str] = []

    for split in ["train", "val", "test"]:
        img_dir = dataset_path / "images" / split
        lbl_dir = dataset_path / "labels" / split
        split_result = {"images": 0, "labels": 0, "annotations": 0}

        if not img_dir.exists() or not lbl_dir.exists():
            # test 可选；train/val 缺失记为问题
            if split != "test":
                missing_dirs.append(split)
            splits[split] = split_result
            continue

        image_files = {f.stem for f in img_dir.iterdir() if f.suffix.lower() in IMAGE_EXTS}
        label_files = {f.stem for f in lbl_dir.iterdir() if f.suffix == ".txt"}
        missing_labels.extend(f"{split}/{name}" for name in sorted(image_files - label_files))
        missing_images.extend(f"{split}/{name}" for name in sorted(label_files - image_files))

        split_result["images"] = len(image_files)
        split_result["labels"] = len(label_files)
        total_images += len(image_files)
        total_labels += len(label_files)

        bbox_widths: list[float] = []
        bbox_heights: list[float] = []

        for label_file in sorted(lbl_dir.glob("*.txt")):
            content = label_file.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                empty_labels += 1
                continue
            for line_num, line in enumerate(content.split("\n"), 1):
                parts = line.strip().split()
                if len(parts) != 5:
                    invalid_format.append(
                        f"{split}/{label_file.name}:{line_num} (期望 5 个值, 实际 {len(parts)})"
                    )
                    continue
                try:
                    class_id = int(parts[0])
                except ValueError:
                    invalid_format.append(f"{split}/{label_file.name}:{line_num} (class_id 非整数)")
                    continue
                class_distribution[class_id] = class_distribution.get(class_id, 0) + 1
                total_annotations += 1
                split_result["annotations"] += 1
                try:
                    coords = [float(v) for v in parts[1:]]
                except ValueError:
                    invalid_format.append(f"{split}/{label_file.name}:{line_num} (坐标值非浮点数)")
                    continue
                for i, v in enumerate(coords):
                    if v < 0 or v > 1:
                        field = ["x_center", "y_center", "width", "height"][i]
                        out_of_range.append(f"{split}/{label_file.name}:{line_num} {field}={v:.6f}")
                        break
                bbox_widths.append(coords[2])
                bbox_heights.append(coords[3])

        if bbox_widths:
            bbox["total"] += len(bbox_widths)
            bbox["avg_width"] += sum(bbox_widths)
            bbox["avg_height"] += sum(bbox_heights)
            bbox["max_width"] = max(bbox["max_width"], max(bbox_widths))
            bbox["max_height"] = max(bbox["max_height"], max(bbox_heights))
            bbox["min_width"] = min(bbox["min_width"], min(bbox_widths))
            bbox["min_height"] = min(bbox["min_height"], min(bbox_heights))
            bbox["small_boxes"] += sum(
                1 for w, h in zip(bbox_widths, bbox_heights) if w * h < SMALL_BOX_AREA
            )
            bbox["large_boxes"] += sum(
                1 for w, h in zip(bbox_widths, bbox_heights) if w * h > LARGE_BOX_AREA
            )
        splits[split] = split_result

    if bbox["total"] > 0:
        bbox["avg_width"] /= bbox["total"]
        bbox["avg_height"] /= bbox["total"]
    else:
        bbox["min_width"] = 0.0
        bbox["min_height"] = 0.0

    # ── 问题清单 ──
    issues: list[dict] = []

    def _issue(level: str, message: str, samples: list[str] | None = None) -> None:
        issues.append({
            "level": level,
            "message": message,
            "samples": (samples or [])[:MAX_SAMPLES],
        })

    if missing_dirs:
        _issue("error", f"缺少必需目录：{', '.join(missing_dirs)}（images/ 与 labels/ 下需同时存在）")
    if total_images == 0:
        _issue("error", "未找到任何图像文件")
    if invalid_format:
        _issue("error", f"标注格式错误 {len(invalid_format)} 处", invalid_format)
    if out_of_range:
        _issue("warning", f"归一化坐标越界 {len(out_of_range)} 处", out_of_range)
    if missing_labels:
        _issue("warning", f"{len(missing_labels)} 张图像缺少标注文件", missing_labels)
    if missing_images:
        _issue("warning", f"{len(missing_images)} 个标注文件缺少对应图像", missing_images)
    if empty_labels:
        _issue("warning", f"{empty_labels} 个空标注文件（负样本，若非有意为之请补充标注）")
    # 类别 id 超出 names 定义
    if class_names:
        unknown_ids = sorted(cid for cid in class_distribution if cid not in class_names)
        if unknown_ids:
            _issue("error", f"标注中出现未在 data.yaml names 定义的类别 id：{unknown_ids}")

    # ── 建议 ──
    suggestions: list[str] = []
    counts = [c for c in class_distribution.values() if c > 0]
    if len(counts) >= 2 and min(counts) > 0 and max(counts) / min(counts) > IMBALANCE_RATIO:
        rare_ids = [cid for cid, c in class_distribution.items() if c == min(counts)]
        rare_names = [class_names.get(cid, str(cid)) for cid in rare_ids]
        suggestions.append(
            f"类别严重不平衡（最多/最少 = {max(counts) / min(counts):.1f}:1），"
            f"建议为稀有类别（{', '.join(rare_names)}）扩充样本或启用过采样/数据增强"
        )
    if bbox["total"] > 0:
        small_ratio = bbox["small_boxes"] / bbox["total"]
        if small_ratio > 0.3:
            suggestions.append(
                f"小目标占比 {small_ratio:.0%}（面积 < 0.1%），建议提高训练分辨率（img_size ≥ 1024）或启用切片推理"
            )
    val_images = splits.get("val", {}).get("images", 0)
    if total_images > 0 and val_images == 0:
        suggestions.append("缺少验证集，训练时无法评估泛化性能，建议按 8:1:1 重新划分")
    elif total_images > 0 and val_images / max(total_images, 1) < 0.05:
        suggestions.append("验证集占比不足 5%，评估指标可能不稳定，建议增加验证集样本")
    if splits.get("test", {}).get("images", 0) == 0 and total_images > 0:
        suggestions.append("未提供测试集（可选），如需独立评估最终模型建议划分 test 集")
    if 0 < total_images < 100:
        suggestions.append(f"图像总数仅 {total_images} 张，样本量偏小，建议扩充数据或使用较小模型（yolo11n）配合数据增强")
    if class_names:
        absent = [name for cid, name in class_names.items() if class_distribution.get(cid, 0) == 0]
        if absent and total_annotations > 0:
            suggestions.append(f"类别 {', '.join(absent)} 在标注中未出现，模型将无法学习这些类别")
    if not suggestions and not issues:
        suggestions.append("数据集结构与标注质量良好，可直接用于训练")

    passed = not any(i["level"] == "error" for i in issues)

    return {
        "summary": {
            "total_images": total_images,
            "total_labels": total_labels,
            "total_annotations": total_annotations,
            "empty_labels": empty_labels,
            "avg_annotations_per_image": (
                round(total_annotations / total_images, 2) if total_images else 0
            ),
        },
        "splits": splits,
        "class_distribution": [
            {
                "class_id": cid,
                "name": class_names.get(cid, str(cid)),
                "count": count,
                "ratio": round(count / total_annotations, 4) if total_annotations else 0,
            }
            for cid, count in sorted(class_distribution.items())
        ],
        "bbox_stats": {
            "total": bbox["total"],
            "avg_width": round(bbox["avg_width"], 4),
            "avg_height": round(bbox["avg_height"], 4),
            "max_width": round(bbox["max_width"], 4),
            "max_height": round(bbox["max_height"], 4),
            "min_width": round(bbox["min_width"], 4),
            "min_height": round(bbox["min_height"], 4),
            "small_boxes": bbox["small_boxes"],
            "large_boxes": bbox["large_boxes"],
            "small_ratio": round(bbox["small_boxes"] / bbox["total"], 4) if bbox["total"] else 0,
        },
        "issues": issues,
        "suggestions": suggestions,
        "passed": passed,
    }
