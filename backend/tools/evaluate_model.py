"""独立模型评估工具。

示例：
    python tools/evaluate_model.py -w runs/train/task_xxxxxxxx/weights/best.pt
    python tools/evaluate_model.py -w best.pt -d datasets/fdd/yolo_dataset/data.yaml --split test
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_data_yaml(weights_path: str) -> str:
    """优先从训练参数查找 data.yaml，再扫描标准数据集目录。"""
    weights = Path(weights_path).expanduser().resolve()
    task_dir = weights.parent.parent
    args_yaml = task_dir / "args.yaml"
    if args_yaml.exists():
        for line in args_yaml.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("data:"):
                continue
            raw_path = line.split(":", 1)[1].strip().strip("'\"")
            data_path = Path(raw_path).expanduser()
            candidates = [data_path]
            if not data_path.is_absolute():
                candidates.extend((task_dir / data_path, PROJECT_ROOT / data_path))
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate.resolve())

    datasets_dir = PROJECT_ROOT / "datasets"
    if datasets_dir.exists():
        for data_yaml in sorted(datasets_dir.glob("*/yolo_dataset/data.yaml")):
            return str(data_yaml.resolve())
    return ""


def _class_name(class_names, class_id: int) -> str:
    if isinstance(class_names, dict):
        return class_names.get(class_id, f"class_{class_id}")
    if class_id < len(class_names):
        return class_names[class_id]
    return f"class_{class_id}"


def parse_evaluation_results(results, class_names, instance_values=None) -> dict:
    """将 Ultralytics 验证结果转换为 JSON 可序列化报告。"""
    box = getattr(results, "box", None)
    if box is None:
        raise ValueError("评估结果中缺少目标检测指标")

    report = {
        "overall": {
            "precision": float(box.mp),
            "recall": float(box.mr),
            "map50": float(box.map50),
            "map50_95": float(box.map),
            "map75": float(box.map75) if hasattr(box, "map75") else None,
        },
        "per_class": {},
    }
    ap_values = getattr(box, "ap", None)
    ap50_values = getattr(box, "ap50", None)
    if ap_values is None or ap50_values is None:
        return report

    if instance_values is None:
        instance_values = getattr(results, "nt_per_class", None)
    if instance_values is None:
        instance_values = getattr(box, "nt_per_class", None)

    ap_class_ids = getattr(box, "ap_class_index", None)
    if ap_class_ids is None:
        ap_class_ids = range(len(ap50_values))

    for metric_index, ap50 in enumerate(ap50_values):
        class_id = int(ap_class_ids[metric_index])
        metrics = {
            "ap50": float(ap50),
            "ap50_95": float(ap_values[metric_index]),
            "instances": None,
        }
        if instance_values is not None and class_id < len(instance_values):
            metrics["instances"] = int(instance_values[class_id])
        report["per_class"][_class_name(class_names, class_id)] = metrics
    return report


def print_report(report: dict) -> None:
    """在终端打印总体指标、分类 AP 和弱势类别。"""
    overall = report["overall"]
    print("\n模型评估报告")
    print("=" * 52)
    print(f"{'Precision':<18}{overall['precision']:>10.4f}")
    print(f"{'Recall':<18}{overall['recall']:>10.4f}")
    print(f"{'mAP@50':<18}{overall['map50']:>10.4f}")
    print(f"{'mAP@50-95':<18}{overall['map50_95']:>10.4f}")

    per_class = sorted(
        report["per_class"].items(),
        key=lambda item: item[1]["ap50"],
        reverse=True,
    )
    if per_class:
        print("\n每类 AP")
        print(f"{'类别':<22}{'AP@50':>10}{'AP@50-95':>12}{'样本数':>10}")
        for class_name, metrics in per_class:
            instances = metrics.get("instances")
            instance_text = "-" if instances is None else str(instances)
            print(
                f"{class_name:<22}{metrics['ap50']:>10.4f}"
                f"{metrics['ap50_95']:>12.4f}{instance_text:>10}"
            )
        weak_classes = [name for name, metrics in per_class if metrics["ap50"] < 0.5]
        if weak_classes:
            print(f"\n弱势类别（AP@50 < 0.5）：{', '.join(weak_classes)}")


def run_evaluation(
    weights_path: str,
    data_yaml: str,
    split: str = "val",
    conf: float = 0.001,
    iou: float = 0.6,
    img_size: int = 640,
    device: str = "cpu",
    output_dir: str = None,
) -> dict:
    """运行 YOLO 验证并生成图表与 eval_report.json。"""
    from ultralytics import YOLO

    weights = Path(weights_path).expanduser().resolve()
    data = Path(data_yaml).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve() if output_dir else weights.parent.parent
    model = YOLO(str(weights))
    validation_stats = {}

    def capture_validation_stats(validator: object) -> None:
        validation_stats["nt_per_class"] = getattr(validator, "nt_per_class", None)

    model.add_callback("on_val_end", capture_validation_stats)
    results = model.val(
        data=str(data),
        split=split,
        conf=conf,
        iou=iou,
        imgsz=img_size,
        device=device,
        save_json=True,
        save_txt=True,
        save_conf=True,
        plots=True,
        project=str(output),
        name="eval",
        exist_ok=True,
        verbose=True,
    )
    report = parse_evaluation_results(
        results,
        model.names,
        validation_stats.get("nt_per_class"),
    )
    print_report(report)

    report_path = output / "eval" / "eval_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n评估报告已保存到：{report_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLOv11 模型评估工具")
    parser.add_argument("--weights", "-w", required=True, help="模型权重路径")
    parser.add_argument("--data", "-d", help="data.yaml 路径（默认自动查找）")
    parser.add_argument(
        "--split",
        "-s",
        choices=("train", "val", "test"),
        default="val",
        help="评估数据集划分",
    )
    parser.add_argument("--conf", type=float, default=0.001, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=0.6, help="NMS IoU 阈值")
    parser.add_argument("--imgsz", type=int, default=640, help="输入图像尺寸")
    parser.add_argument("--device", default="cpu", help="评估设备，如 cpu 或 0")
    parser.add_argument("--output", "-o", help="评估输出目录")
    args = parser.parse_args()

    weights = Path(args.weights).expanduser()
    if not weights.exists():
        parser.error(f"模型权重不存在：{weights}")
    if not 0 <= args.conf <= 1 or not 0 <= args.iou <= 1:
        parser.error("conf 和 iou 必须在 0 到 1 之间")

    data_yaml = args.data or find_data_yaml(str(weights))
    if not data_yaml or not Path(data_yaml).expanduser().exists():
        parser.error("未找到 data.yaml，请使用 --data 参数指定")

    run_evaluation(
        weights_path=str(weights),
        data_yaml=data_yaml,
        split=args.split,
        conf=args.conf,
        iou=args.iou,
        img_size=args.imgsz,
        device=args.device,
        output_dir=args.output,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
