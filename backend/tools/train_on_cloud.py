"""
云端独立训练脚本

用途：在 AutoDL 等 GPU 云平台上独立运行 YOLOv11 训练
不依赖 FastAPI 后端，直接执行即可

使用方式：
    # 基本用法（使用默认参数）
    python tools/train_on_cloud.py

    # 自定义参数
    python tools/train_on_cloud.py --model yolov11s --epochs 100 --batch 16

    # 指定数据集路径
    python tools/train_on_cloud.py --data /root/autodl-tmp/datasets/rsod/yolo_dataset/data.yaml

    # 完整参数示例
    python tools/train_on_cloud.py \
        --model yolov11n \
        --epochs 100 \
        --batch 16 \
        --imgsz 640 \
        --device 0 \
        --optimizer SGD \
        --lr0 0.01 \
        --data datasets/fdd/yolo_dataset/data.yaml \
        --output runs/cloud_train
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime

# ── 默认路径 ──────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_YAML = os.path.join(
    PROJECT_ROOT, "datasets", "fdd", "yolo_dataset", "data.yaml"
)
# 与后端 settings.TRAIN_OUTPUT_DIR 对齐：产物放在 runs/train 下、目录名为 task_<uuid>，
# 这样把整个 runs/train 拿回本地后，后端启动即可自动扫描恢复训练记录。
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "runs", "train")

# 恢复入库所需、但 Ultralytics 产物里没有的归属信息的默认值
DEFAULT_USER_ID = 1
DEFAULT_SCENE = "fdd"


def _infer_scene(data_yaml):
    """从 data.yaml 路径推断场景名。

    约定路径：.../datasets/<scene>/yolo_dataset/data.yaml，
    取 datasets 后一级目录名作为场景名；无法推断时返回 None。
    """
    parts = os.path.abspath(data_yaml).replace("\\", "/").split("/")
    if "datasets" in parts:
        idx = parts.index("datasets")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _write_task_meta(output_dir, task_uuid, args, started_at, completed_at):
    """写入与后端 rescan 兼容的 meta.json（产物自描述，供数据库重建后恢复）。

    字段与 app/training/training_service.py 中 _write_task_meta 保持一致：
    后端恢复时优先按 scene_id 匹配场景，云端无数据库故置空，改由 scene_name 匹配。
    """
    dataset_path = os.path.dirname(os.path.abspath(args.data))
    meta = {
        "task_uuid": task_uuid,
        "user_id": args.user_id,
        "scene_id": None,          # 云端无数据库，交由 scene_name 匹配
        "status": "completed",
        "model_name": args.model,
        "epochs": args.epochs,
        "img_size": args.imgsz,
        "batch_size": args.batch,
        "device": args.device,
        "optimizer": args.optimizer,
        "lr0": args.lr0,
        "augment_config": {
            "mosaic": args.mosaic,
            "mixup": args.mixup,
            "fliplr": args.fliplr,
        },
        "current_epoch": args.epochs,
        "progress": 100,
        "dataset_path": dataset_path,
        "dataset_size": None,
        "data_yaml": os.path.abspath(args.data),
        "error_message": None,
        "created_at": started_at.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "scene_name": args.scene,
        "_meta_version": 1,
    }
    meta_path = os.path.join(output_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return meta_path


def main():
    """主函数：解析参数并启动训练"""
    parser = argparse.ArgumentParser(
        description="YOLOv11 云端独立训练脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── 模型参数 ──
    parser.add_argument(
        "--model", "-m",
        type=str,
        default="yolo11n",
        choices=["yolo11n", "yolo11s", "yolo11m", "yolo11l", "yolo11x"],
        help="基础模型（默认：yolo11n）",
    )

    # ── 训练参数 ──
    parser.add_argument("--epochs", "-e", type=int, default=100, help="训练轮数（默认：100）")
    parser.add_argument("--batch", "-b", type=int, default=16, help="批次大小（默认：16）")
    parser.add_argument("--imgsz", type=int, default=640, help="图像尺寸（默认：640）")
    parser.add_argument("--device", type=str, default="0", help="训练设备（默认：0）")
    parser.add_argument("--optimizer", type=str, default="SGD", help="优化器（默认：SGD）")
    parser.add_argument("--lr0", type=float, default=0.01, help="初始学习率（默认：0.01）")

    # ── 路径参数 ──
    parser.add_argument("--data", "-d", type=str, default=DEFAULT_DATA_YAML, help="data.yaml 路径")
    parser.add_argument("--output", "-o", type=str, default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--name", type=str, default=None, help="实验名称（默认：task_<随机uuid>，便于后端自动恢复）")

    # ── 恢复归属参数（写入 meta.json，供数据库重建后恢复识别）──
    parser.add_argument("--user-id", type=int, default=DEFAULT_USER_ID, help=f"归属用户 ID（默认：{DEFAULT_USER_ID}）")
    parser.add_argument("--scene", type=str, default=None, help="关联场景名（默认从 data.yaml 路径推断，推断失败回退 fdd）")

    # ── 数据增强参数 ──
    parser.add_argument("--mosaic", type=float, default=1.0, help="Mosaic 增强概率（默认：1.0）")
    parser.add_argument("--mixup", type=float, default=0.0, help="MixUp 增强概率（默认：0.0）")
    parser.add_argument("--fliplr", type=float, default=0.5, help="水平翻转概率（默认：0.5）")

    args = parser.parse_args()

    # ── 检查 data.yaml ──
    if not os.path.exists(args.data):
        print(f"[错误] data.yaml 不存在：{args.data}")
        sys.exit(1)

    # ── 确定场景名：未指定则从 data.yaml 路径推断，推断失败回退默认 ──
    if args.scene is None:
        args.scene = _infer_scene(args.data) or DEFAULT_SCENE

    # ── 生成实验名称 ──
    # 默认使用 task_<uuid> 命名，与后端恢复扫描（runs/train/task_*）约定一致。
    task_uuid = uuid.uuid4().hex[:8]
    if args.name is None:
        args.name = f"task_{task_uuid}"
    elif args.name.startswith("task_"):
        task_uuid = args.name[len("task_"):]
    else:
        print(f"[提示] 实验名 '{args.name}' 未以 task_ 开头，后端恢复扫描将忽略该目录")

    print("=" * 60)
    print(f"  YOLOv11 云端训练")
    print(f"  模型：{args.model}")
    print(f"  数据：{args.data}")
    print(f"  轮数：{args.epochs}")
    print(f"  Batch：{args.batch}")
    print(f"  设备：{args.device}")
    print(f"  优化器：{args.optimizer}")
    print(f"  学习率：{args.lr0}")
    print(f"  归属用户：{args.user_id}  场景：{args.scene}")
    print(f"  输出：{args.output}/{args.name}")
    print("=" * 60)

    # ── 加载模型并开始训练 ──
    from ultralytics import YOLO

    model = YOLO(f"{args.model}.pt")

    started_at = datetime.now()
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        optimizer=args.optimizer,
        lr0=args.lr0,
        project=args.output,
        name=args.name,
        exist_ok=True,
        verbose=True,
        save=True,
        plots=True,           # 云端训练开启自动绘图
        mosaic=args.mosaic,
        mixup=args.mixup,
        fliplr=args.fliplr,
    )
    completed_at = datetime.now()

    # ── 写入 meta.json（供后端数据库重建后自动恢复训练记录）──
    output_dir = os.path.join(args.output, args.name)
    meta_path = _write_task_meta(output_dir, task_uuid, args, started_at, completed_at)

    # ── 输出训练结果摘要 ──
    print("\n" + "=" * 60)
    print("  训练完成！")
    print(f"  输出目录：{output_dir}")
    print(f"  最优权重：{os.path.join(output_dir, 'weights', 'best.pt')}")
    print(f"  训练日志：{os.path.join(output_dir, 'results.csv')}")
    print(f"  恢复元数据：{meta_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()