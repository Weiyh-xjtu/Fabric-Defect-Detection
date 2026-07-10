# -*- coding: utf-8 -*-
"""
AITEX 掩码 → YOLO TXT 数据集格式转换脚本

功能：
    1. 检查缺陷图与掩码图的匹配度（支持一图多掩码，无掩码的缺陷图剔除）
    2. 从像素级掩码中提取连通区域边界框，转换为 YOLO 归一化坐标
    3. 无缺陷图作为负样本（生成空 TXT 标注文件）
    4. 缺陷图/无缺陷图分别按 8:1:1 比例划分训练集/验证集/测试集（分层划分）
    5. 生成 YOLO 训练所需的 data.yaml 配置文件

使用方式：
    cd backend
    python tools/convert_aitex.py

处理流程：
    Defect_images + Mask_images + NODefect_images
        → datasets/fabric_defect/
              ├── images/{train,val,test}/
              ├── labels/{train,val,test}/
              └── data.yaml

数据集：AITEX Fabric Image Database（织物表面缺陷检测）
类别：defect（各缺陷类型样本过少，统一合并为单类）
"""

import os
import random
import shutil

import cv2
import numpy as np

# 项目根目录路径（tools/convert_aitex.py → backend/）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 原始数据目录配置
RAW_DIR = r"D:\魏源昊\实习 数据集\AITEX Fabric Image Database"
DEFECT_IMAGE_DIR = os.path.join(RAW_DIR, "Defect_images")
MASK_DIR = os.path.join(RAW_DIR, "Mask_images")
NODEFECT_IMAGE_DIR = os.path.join(RAW_DIR, "NODefect_images")

# YOLO格式输出目录
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "datasets", "fabric_defect")

# 数据集类别配置（缺陷类型编码在文件名中，但每类样本过少，合并为单类）
CLASS_NAMES = ["defect"]

# 掩码二值化阈值（掩码为黑底白色缺陷区域）
MASK_THRESHOLD = 127

# 最小边界框尺寸（像素），过滤掩码中的噪点
MIN_BOX_SIZE = 3

# 支持的图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# 划分比例与随机种子
SPLIT_RATIOS = {"train": 0.8, "val": 0.1}  # 剩余为 test
RANDOM_SEED = 42


def find_masks(stem: str) -> list:
    """
    查找一张缺陷图对应的所有掩码文件

    AITEX 命名规则：
        单掩码：0001_002_00.png → 0001_002_00_mask.png
        多掩码：0044_019_04.png → 0044_019_04_mask1.png, 0044_019_04_mask2.png

    参数：
        stem: 缺陷图文件名（不含扩展名）

    返回：
        掩码文件完整路径列表（可能为空）
    """
    masks = []
    single = os.path.join(MASK_DIR, f"{stem}_mask.png")
    if os.path.exists(single):
        masks.append(single)

    # 多掩码情况：_mask1, _mask2, ...
    idx = 1
    while True:
        multi = os.path.join(MASK_DIR, f"{stem}_mask{idx}.png")
        if not os.path.exists(multi):
            break
        masks.append(multi)
        idx += 1

    return masks


def mask_to_yolo(mask_paths: list, class_id: int) -> list:
    """
    从掩码图中提取缺陷边界框，转换为 YOLO 归一化标注行

    转换逻辑：
        1. 读取所有掩码并二值化，多个掩码取并集
        2. cv2.findContours 提取连通区域轮廓
        3. cv2.boundingRect 获取像素边界框
        4. 套用归一化公式转换为 YOLO 格式

    转换公式（与 Day05 讲义 3.3 节一致）：
        x_center = (xmin + xmax) / 2.0 / image_width
        y_center = (ymin + ymax) / 2.0 / image_height
        width    = (xmax - xmin) / image_width
        height   = (ymax - ymin) / image_height

    参数：
        mask_paths: 掩码文件路径列表（同一张图可能有多个掩码）
        class_id: 类别编号

    返回：
        YOLO 标注行列表，掩码全黑（无缺陷区域）时为空列表
    """
    merged = None
    for path in mask_paths:
        # cv2.imread 无法处理含中文的路径，改用 fromfile + imdecode
        mask = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            continue
        _, binary = cv2.threshold(mask, MASK_THRESHOLD, 255, cv2.THRESH_BINARY)
        merged = binary if merged is None else cv2.bitwise_or(merged, binary)

    if merged is None:
        return []

    img_height, img_width = merged.shape

    contours, _ = cv2.findContours(
        merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    yolo_lines = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        # 过滤噪点（过小的连通区域）
        if w < MIN_BOX_SIZE or h < MIN_BOX_SIZE:
            continue

        # 像素坐标 → YOLO 归一化坐标
        x_center = (x + w / 2.0) / img_width
        y_center = (y + h / 2.0) / img_height
        width = w / img_width
        height = h / img_height

        yolo_lines.append(
            f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"
        )

    return yolo_lines


def split_files(files: list) -> dict:
    """
    将文件列表按 8:1:1 比例随机划分为 train/val/test

    参数：
        files: 文件名列表

    返回：
        {"train": [...], "val": [...], "test": [...]}
    """
    files = sorted(files)  # 先排序保证 shuffle 结果可重复
    random.shuffle(files)

    total = len(files)
    train_end = int(total * SPLIT_RATIOS["train"])
    val_end = train_end + int(total * SPLIT_RATIOS["val"])

    return {
        "train": files[:train_end],
        "val": files[train_end:val_end],
        "test": files[val_end:],
    }


def main():
    """主函数：执行完整的数据集处理流程"""
    print("=" * 70)
    print("      AITEX 织物缺陷数据集处理流程（掩码 → YOLO）")
    print("=" * 70)

    random.seed(RANDOM_SEED)

    # ── 步骤1：收集缺陷图并转换掩码标注 ──
    print("\n[1] 掩码转 YOLO 标注")

    # {文件名: [标注行]}，仅收录有有效掩码的缺陷图
    defect_labels = {}
    skipped = []

    for f in sorted(os.listdir(DEFECT_IMAGE_DIR)):
        ext = os.path.splitext(f)[1].lower()
        if ext not in IMAGE_EXTS:
            continue

        stem = os.path.splitext(f)[0]
        mask_paths = find_masks(stem)
        if not mask_paths:
            skipped.append(f"{f}（无掩码）")
            continue

        yolo_lines = mask_to_yolo(mask_paths, class_id=0)
        if not yolo_lines:
            skipped.append(f"{f}（掩码全黑）")
            continue

        defect_labels[f] = yolo_lines

    print(f"  缺陷图转换成功: {len(defect_labels)} 张")
    for item in skipped:
        print(f"  已剔除: {item}")

    # ── 步骤2：收集无缺陷图（负样本） ──
    print("\n[2] 收集负样本")

    # {文件名: 完整路径}（NODefect_images 下有按布料编号分的子目录）
    nodefect_files = {}
    for root, _, files in os.walk(NODEFECT_IMAGE_DIR):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTS:
                nodefect_files[f] = os.path.join(root, f)

    print(f"  负样本: {len(nodefect_files)} 张")

    # ── 步骤3：分层划分数据集 ──
    print("\n[3] 划分数据集（缺陷图与负样本分别按 8:1:1 划分）")

    defect_splits = split_files(list(defect_labels.keys()))
    nodefect_splits = split_files(list(nodefect_files.keys()))

    for split_name in ["train", "val", "test"]:
        img_out = os.path.join(OUTPUT_DIR, "images", split_name)
        lbl_out = os.path.join(OUTPUT_DIR, "labels", split_name)
        os.makedirs(img_out, exist_ok=True)
        os.makedirs(lbl_out, exist_ok=True)

        # 缺陷图：复制图片 + 写入标注
        for filename in defect_splits[split_name]:
            basename = os.path.splitext(filename)[0]
            shutil.copy2(
                os.path.join(DEFECT_IMAGE_DIR, filename),
                os.path.join(img_out, filename),
            )
            with open(
                os.path.join(lbl_out, f"{basename}.txt"), "w", encoding="utf-8"
            ) as f:
                f.write("\n".join(defect_labels[filename]))

        # 负样本：复制图片 + 空标注文件
        for filename in nodefect_splits[split_name]:
            basename = os.path.splitext(filename)[0]
            shutil.copy2(
                nodefect_files[filename], os.path.join(img_out, filename)
            )
            open(
                os.path.join(lbl_out, f"{basename}.txt"), "w", encoding="utf-8"
            ).close()

        total = len(defect_splits[split_name]) + len(nodefect_splits[split_name])
        print(
            f"  {split_name}: {total} 张"
            f"（缺陷 {len(defect_splits[split_name])}"
            f" + 负样本 {len(nodefect_splits[split_name])}）"
        )

    # ── 步骤4：生成 data.yaml 配置文件 ──
    print("\n[4] 生成 data.yaml")

    # path 使用绝对路径，避免 ultralytics 相对路径解析歧义
    yaml_content = f"""path: {OUTPUT_DIR.replace(os.sep, '/')}
train: images/train
val: images/val
test: images/test
nc: {len(CLASS_NAMES)}
names:
"""
    for i, name in enumerate(CLASS_NAMES):
        yaml_content += f"  {i}: {name}\n"

    yaml_path = os.path.join(OUTPUT_DIR, "data.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_content)
    print(f"  配置文件已生成: {yaml_path}")

    # 输出完成信息
    print("\n" + "=" * 70)
    print(f"  处理完成！输出目录: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
