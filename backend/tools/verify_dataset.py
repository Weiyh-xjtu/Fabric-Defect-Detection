#!/usr/bin/env python3
"""
YOLO 数据集验证脚本（增强版）

功能：
    对转换后的 YOLO 格式数据集进行全面验证，确保数据质量满足训练要求

验证内容：
    1. 目录结构是否符合 YOLO 规范（images/train、images/val、labels/train、labels/val）
    2. 图像与标注文件是否一一对应（文件名匹配检查）
    3. 标注文件格式是否正确（每行必须为 5 个值：class_id x_center y_center width height）
    4. 类别 ID 是否有效（必须为整数）
    5. 归一化坐标是否在 [0, 1] 范围内（YOLO格式要求）
    6. 统计各类别的标注数量和占比（生成分布直方图）
    7. 类别不平衡检测和警告（比例 > 10:1 视为严重不平衡）
    8. 边界框统计（平均尺寸、最小/最大尺寸、小目标占比）
    9. 每个 split（train/val/test）的详细统计

使用方式：
    cd rsod-agent-platform/backend
    python tools/verify_dataset.py

    或指定数据集目录：
    python tools/verify_dataset.py /path/to/dataset

输出：
    详细的验证报告，包含统计信息、警告和建议

数据集格式要求：
    yolo_dataset/
    ├── data.yaml          # 数据集配置文件（必须包含 names 字段）
    ├── images/
    │   ├── train/        # 训练集图片
    │   ├── val/          # 验证集图片
    │   └── test/         # 测试集图片（可选）
    └── labels/
        ├── train/        # 训练集标注（.txt）
        ├── val/          # 验证集标注（.txt）
        └── test/         # 测试集标注（.txt）

YOLO 标注格式：
    每行一个目标，格式为：
        class_id x_center y_center width height
    其中坐标为归一化值（0~1）
"""

import os
import sys
from pathlib import Path

# 支持的图片扩展名集合
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def load_yaml_classes(dataset_dir: Path) -> dict:
    """
    加载 data.yaml 中的类别定义（纯文本解析，不依赖 yaml 库）

    参数：
        dataset_dir: 数据集目录路径（Path 对象）

    返回：
        类别 ID 到类别名称的映射字典，格式 {0: "aircraft", 1: "oiltank", ...}

    解析逻辑：
        1. 查找 data.yaml 文件
        2. 逐行读取，找到 "names:" 字段开始的部分
        3. 解析缩进的类别定义（格式：数字ID: 类别名称）
        4. 返回解析结果
    """
    yaml_path = dataset_dir / "data.yaml"
    if not yaml_path.exists():
        return {}

    try:
        names = {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            in_names = False  # 是否进入 names 字段区域
            for line in f:
                line = line.strip()
                # 找到 names: 字段，开始解析
                if line.startswith("names:"):
                    in_names = True
                    continue
                # 在 names 区域内，解析类别定义
                if in_names and line:
                    # 检查是否为数字开头的类别定义
                    if line[0].isdigit():
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            class_id = int(parts[0].strip())
                            class_name = parts[1].strip()
                            names[class_id] = class_name
                # 遇到空行，结束 names 解析
                elif in_names and not line:
                    break
        return names
    except Exception:
        return {}


def verify_dataset(dataset_dir: str) -> dict:
    """
    验证 YOLO 数据集完整性，返回详细的验证结果

    参数：
        dataset_dir: 数据集目录路径（字符串）

    返回：
        验证结果字典，包含以下字段：
            total_images: 图像总数
            total_labels: 标注文件总数
            total_annotations: 标注目标总数
            missing_labels: 缺少标注的图像列表
            missing_images: 缺少图像的标注列表
            empty_labels: 空标注文件数量
            invalid_format: 格式错误列表
            out_of_range: 坐标越界列表
            class_distribution: 类别分布字典
            class_names: 类别名称映射
            bbox_stats: 边界框统计信息
            split_stats: 各 split 的统计信息
            has_warnings: 是否存在警告
    """
    # 初始化验证结果字典
    results = {
        "total_images": 0,  # 图像总数
        "total_labels": 0,  # 标注文件总数
        "total_annotations": 0,  # 标注目标总数
        "missing_labels": [],  # 缺少标注的图像列表
        "missing_images": [],  # 缺少图像的标注列表
        "empty_labels": 0,  # 空标注文件数量
        "invalid_format": [],  # 格式错误列表
        "out_of_range": [],  # 坐标越界列表
        "class_distribution": {},  # 类别分布 {class_id: count}
        "class_names": {},  # 类别名称映射
        "bbox_stats": {  # 边界框统计
            "total": 0,  # 边界框总数
            "avg_width": 0,  # 平均宽度（累加和，最后求平均）
            "avg_height": 0,  # 平均高度（累加和，最后求平均）
            "max_width": 0,  # 最大宽度
            "max_height": 0,  # 最大高度
            "min_width": float("inf"),  # 最小宽度（初始化为无穷大）
            "min_height": float("inf"),  # 最小高度（初始化为无穷大）
            "small_boxes": 0,  # 小目标数量（面积 < 0.001）
            "large_boxes": 0,  # 大目标数量（面积 > 0.5）
        },
        "split_stats": {},  # 各 split 的统计信息
        "has_warnings": False,  # 是否存在警告
    }

    # 将字符串路径转换为 Path 对象
    dataset_path = Path(dataset_dir)
    # 加载类别名称映射
    class_names = load_yaml_classes(dataset_path)
    results["class_names"] = class_names

    # 遍历每个 split（train/val/test）
    for split in ["train", "val", "test"]:
        img_dir = dataset_path / "images" / split  # 图片目录
        lbl_dir = dataset_path / "labels" / split  # 标注目录

        # 初始化当前 split 的统计结果
        split_result = {
            "images": 0,  # 图片数量
            "labels": 0,  # 标注数量
            "annotations": 0,  # 标注目标数量
            "missing_labels": 0,  # 缺少标注的图片数
            "missing_images": 0,  # 缺少图片的标注数
            "class_distribution": {},  # 类别分布
        }

        # 检查图片目录是否存在（test 目录可选）
        if not img_dir.exists():
            if split != "test":
                print(f"[警告] 缺少目录: {img_dir}")
            results["split_stats"][split] = split_result
            continue

        # 检查标注目录是否存在
        if not lbl_dir.exists():
            print(f"[警告] 缺少目录: {lbl_dir}")
            results["split_stats"][split] = split_result
            continue

        # 获取所有图片和标注文件的文件名（不含扩展名）
        image_files = {
            f.stem for f in img_dir.iterdir() if f.suffix.lower() in IMAGE_EXTS
        }
        label_files = {f.stem for f in lbl_dir.iterdir() if f.suffix == ".txt"}

        # 找出不匹配的文件
        missing_labels = image_files - label_files  # 有图片无标注
        missing_images = label_files - image_files  # 有标注无图片

        # 更新统计信息
        split_result["images"] = len(image_files)
        split_result["labels"] = len(label_files)
        split_result["missing_labels"] = len(missing_labels)
        split_result["missing_images"] = len(missing_images)

        # 更新全局统计信息
        results["missing_labels"].extend([f"{split}/{name}" for name in missing_labels])
        results["missing_images"].extend([f"{split}/{name}" for name in missing_images])
        results["total_images"] += len(image_files)
        results["total_labels"] += len(label_files)

        # 存储边界框尺寸信息（用于后续统计）
        bbox_widths = []
        bbox_heights = []

        # 遍历所有标注文件，验证格式和内容
        for label_file in lbl_dir.glob("*.txt"):
            content = label_file.read_text(encoding="utf-8").strip()

            # 检查标注文件是否为空
            if not content:
                results["empty_labels"] += 1
                continue

            # 逐行解析标注内容
            for line_num, line in enumerate(content.split("\n"), 1):
                parts = line.strip().split()

                # 检查每行是否包含 5 个值
                if len(parts) != 5:
                    results["invalid_format"].append(
                        f"{split}/{label_file.name}:{line_num} (期望 5 个值, 实际 {len(parts)})"
                    )
                    continue

                # 检查 class_id 是否为整数
                try:
                    class_id = int(parts[0])
                except ValueError:
                    results["invalid_format"].append(
                        f"{split}/{label_file.name}:{line_num} (class_id 非整数)"
                    )
                    continue

                # 更新类别分布统计
                results["class_distribution"][class_id] = (
                    results["class_distribution"].get(class_id, 0) + 1
                )
                split_result["class_distribution"][class_id] = (
                    split_result["class_distribution"].get(class_id, 0) + 1
                )
                # 更新标注目标总数
                results["total_annotations"] += 1
                split_result["annotations"] += 1

                # 检查坐标值是否为浮点数且在有效范围
                try:
                    coords = [float(v) for v in parts[1:]]
                    x_center, y_center, width, height = coords

                    # 检查归一化坐标是否在 [0, 1] 范围内
                    for i, v in enumerate(coords):
                        if v < 0 or v > 1:
                            field_names = ["x_center", "y_center", "width", "height"]
                            results["out_of_range"].append(
                                f"{split}/{label_file.name}:{line_num} {field_names[i]}={v:.6f}"
                            )
                            break

                    # 收集边界框尺寸信息
                    bbox_widths.append(width)
                    bbox_heights.append(height)

                except ValueError:
                    results["invalid_format"].append(
                        f"{split}/{label_file.name}:{line_num} (坐标值非浮点数)"
                    )

        # 更新边界框统计信息
        if bbox_widths:
            results["bbox_stats"]["total"] += len(bbox_widths)
            results["bbox_stats"]["avg_width"] += sum(bbox_widths)
            results["bbox_stats"]["avg_height"] += sum(bbox_heights)
            results["bbox_stats"]["max_width"] = max(
                results["bbox_stats"]["max_width"], max(bbox_widths)
            )
            results["bbox_stats"]["max_height"] = max(
                results["bbox_stats"]["max_height"], max(bbox_heights)
            )
            results["bbox_stats"]["min_width"] = min(
                results["bbox_stats"]["min_width"], min(bbox_widths)
            )
            results["bbox_stats"]["min_height"] = min(
                results["bbox_stats"]["min_height"], min(bbox_heights)
            )
            # 统计小目标（面积 < 0.001）和大目标（面积 > 0.5）
            results["bbox_stats"]["small_boxes"] += sum(
                1 for w, h in zip(bbox_widths, bbox_heights) if w * h < 0.001
            )
            results["bbox_stats"]["large_boxes"] += sum(
                1 for w, h in zip(bbox_widths, bbox_heights) if w * h > 0.5
            )

        # 保存当前 split 的统计结果
        results["split_stats"][split] = split_result

    # 计算平均宽度和高度（除以总数）
    if results["bbox_stats"]["total"] > 0:
        results["bbox_stats"]["avg_width"] /= results["bbox_stats"]["total"]
        results["bbox_stats"]["avg_height"] /= results["bbox_stats"]["total"]

    return results


def print_report(results: dict):
    """
    打印格式化的验证报告

    参数：
        results: verify_dataset() 返回的验证结果字典

    输出格式：
        - 总体统计（图像数、标注数、目标数）
        - Split 统计（train/val/test 各数据集的详细信息）
        - 警告信息（缺少标注、格式错误、坐标越界等）
        - 类别分布（柱状图可视化）
        - 边界框统计（尺寸分布、小目标占比）
        - 最终结论（通过/存在问题）
    """
    print("\n" + "=" * 70)
    print("              YOLO 数据集验证报告")
    print("=" * 70)

    # ── 总体统计 ──
    print("\n  [总体统计]")
    print(f"    图像总数：{results['total_images']}")
    print(f"    标注文件数：{results['total_labels']}")
    print(f"    标注目标数：{results['total_annotations']}")
    print(f"    空标注文件：{results['empty_labels']}")
    print(
        f"    平均每图标注：{results['total_annotations'] / results['total_images']:.2f}"
        if results["total_images"] > 0
        else "    平均每图标注：N/A"
    )

    # ── Split 统计 ──
    print("\n  [Split 统计]")
    for split in ["train", "val", "test"]:
        stats = results["split_stats"].get(split, {})
        print(
            f"    {split}: {stats.get('images', 0)} 图像, {stats.get('labels', 0)} 标注, {stats.get('annotations', 0)} 目标"
        )

    # ── 缺少标注文件警告 ──
    if results["missing_labels"]:
        print(f"\n  [警告] 缺少标注文件 ({len(results['missing_labels'])} 个)：")
        for name in results["missing_labels"][:5]:
            print(f"    - {name}")
        if len(results["missing_labels"]) > 5:
            print(f"    ... 还有 {len(results['missing_labels']) - 5} 个")
        results["has_warnings"] = True

    # ── 缺少图像文件警告 ──
    if results["missing_images"]:
        print(f"\n  [警告] 缺少图像文件 ({len(results['missing_images'])} 个)：")
        for name in results["missing_images"][:5]:
            print(f"    - {name}")
        if len(results["missing_images"]) > 5:
            print(f"    ... 还有 {len(results['missing_images']) - 5} 个")
        results["has_warnings"] = True

    # ── 格式错误警告 ──
    if results["invalid_format"]:
        print(f"\n  [错误] 格式错误 ({len(results['invalid_format'])} 处)：")
        for item in results["invalid_format"][:5]:
            print(f"    - {item}")
        if len(results["invalid_format"]) > 5:
            print(f"    ... 还有 {len(results['invalid_format']) - 5} 处")
        results["has_warnings"] = True

    # ── 坐标越界警告 ──
    if results["out_of_range"]:
        print(f"\n  [警告] 坐标越界 ({len(results['out_of_range'])} 处)：")
        for item in results["out_of_range"][:5]:
            print(f"    - {item}")
        if len(results["out_of_range"]) > 5:
            print(f"    ... 还有 {len(results['out_of_range']) - 5} 处")
        results["has_warnings"] = True

    # ── 类别分布统计 ──
    print("\n  [类别分布]")
    class_names = results["class_names"]
    total = results["total_annotations"] or 1

    if results["class_distribution"]:
        # 计算类别不平衡比例
        max_count = max(results["class_distribution"].values())
        min_count = min(results["class_distribution"].values())
        imbalance_ratio = max_count / min_count if min_count > 0 else float("inf")

        # 打印类别分布（带柱状图）
        for class_id in sorted(results["class_distribution"].keys()):
            count = results["class_distribution"][class_id]
            percentage = (count / total) * 100
            class_name = class_names.get(class_id, f"class_{class_id}")
            # 计算柱状图长度（最大40个字符）
            bar_length = int((count / max_count) * 40) if max_count > 0 else 0
            bar = "█" * bar_length + "░" * (40 - bar_length)
            print(
                f"    {class_id:2d}. {class_name:12s} {count:6d} 个 ({percentage:5.2f}%) {bar}"
            )

        # 类别不平衡警告
        if imbalance_ratio > 10:
            print(
                f"\n  [警告] 类别严重不平衡！最大/最小类别比例 = {imbalance_ratio:.1f}:1"
            )
            print("         建议：增加少数类样本或使用数据增强技术")
            results["has_warnings"] = True
        elif imbalance_ratio > 5:
            print(f"\n  [提示] 类别存在一定不平衡，比例 = {imbalance_ratio:.1f}:1")

    # ── 边界框统计 ──
    print("\n  [边界框统计]")
    bbox = results["bbox_stats"]
    if bbox["total"] > 0:
        print(f"    边界框总数：{bbox['total']}")
        print(f"    平均宽度：{bbox['avg_width']:.4f}")
        print(f"    平均高度：{bbox['avg_height']:.4f}")
        print(f"    最小宽度：{bbox['min_width']:.4f}")
        print(f"    最小高度：{bbox['min_height']:.4f}")
        print(f"    最大宽度：{bbox['max_width']:.4f}")
        print(f"    最大高度：{bbox['max_height']:.4f}")
        print(
            f"    小目标（面积<0.001）：{bbox['small_boxes']} 个 ({bbox['small_boxes'] / bbox['total'] * 100:.1f}%)"
        )
        print(
            f"    大目标（面积>0.5）：{bbox['large_boxes']} 个 ({bbox['large_boxes'] / bbox['total'] * 100:.1f}%)"
        )

        # 小目标占比提示
        if bbox["small_boxes"] / bbox["total"] > 0.3:
            print("\n  [提示] 小目标占比较高（>30%），建议使用合适的锚框配置")

    # ── 最终结论 ──
    print(f"\n{'─' * 70}")
    if results["has_warnings"]:
        print("  结果：⚠ 数据集存在问题或警告，请根据上述信息修复")
    else:
        print("  结果：✓ 数据集验证通过，可以开始训练")
    print(f"{'─' * 70}\n")


if __name__ == "__main__":
    """主函数：执行数据集验证流程"""
    # 计算项目根目录（tools/verify_dataset.py → rsod-agent-platform/）
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 默认数据集目录
    DATASET_DIR = os.path.join(PROJECT_ROOT, "datasets\\fdd\\yolo_dataset")

    # 支持通过命令行参数指定数据集目录
    if len(sys.argv) > 1:
        DATASET_DIR = sys.argv[1]

    # 检查数据集目录是否存在
    if not os.path.exists(DATASET_DIR):
        print(f"[错误] 数据集目录不存在: {DATASET_DIR}")
        sys.exit(1)

    # 执行验证并打印报告
    results = verify_dataset(DATASET_DIR)
    print_report(results)