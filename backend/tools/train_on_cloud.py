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
import os
import sys
from datetime import datetime

# ── 默认路径 ──────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_YAML = os.path.join(
    PROJECT_ROOT, "datasets", "fdd", "yolo_dataset", "data.yaml"
)
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "runs", "cloud_train")


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
    parser.add_argument("--name", type=str, default=None, help="实验名称（默认：自动生成时间戳）")

    # ── 数据增强参数 ──
    parser.add_argument("--mosaic", type=float, default=1.0, help="Mosaic 增强概率（默认：1.0）")
    parser.add_argument("--mixup", type=float, default=0.0, help="MixUp 增强概率（默认：0.0）")
    parser.add_argument("--fliplr", type=float, default=0.5, help="水平翻转概率（默认：0.5）")

    args = parser.parse_args()

    # ── 检查 data.yaml ──
    if not os.path.exists(args.data):
        print(f"[错误] data.yaml 不存在：{args.data}")
        sys.exit(1)

    # ── 生成实验名称 ──
    if args.name is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.name = f"{args.model}_{timestamp}"

    print("=" * 60)
    print(f"  YOLOv11 云端训练")
    print(f"  模型：{args.model}")
    print(f"  数据：{args.data}")
    print(f"  轮数：{args.epochs}")
    print(f"  Batch：{args.batch}")
    print(f"  设备：{args.device}")
    print(f"  优化器：{args.optimizer}")
    print(f"  学习率：{args.lr0}")
    print(f"  输出：{args.output}/{args.name}")
    print("=" * 60)

    # ── 加载模型并开始训练 ──
    from ultralytics import YOLO

    model = YOLO(f"{args.model}.pt")

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

    # ── 输出训练结果摘要 ──
    print("\n" + "=" * 60)
    print("  训练完成！")
    print(f"  输出目录：{os.path.join(args.output, args.name)}")
    print(f"  最优权重：{os.path.join(args.output, args.name, 'weights', 'best.pt')}")
    print(f"  训练日志：{os.path.join(args.output, args.name, 'results.csv')}")
    print("=" * 60)


if __name__ == "__main__":
    main()