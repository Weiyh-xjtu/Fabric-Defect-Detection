"""
数据集格式转换器

职责：
  - 将 VOC XML 格式转换为 YOLO TXT 格式
  - 将 COCO JSON 格式转换为 YOLO TXT 格式
  - 将 LabelMe JSON 格式转换为 YOLO TXT 格式
  - 所有转换均输出 YOLO 归一化坐标格式

使用方式：
  from app.training.data_converter import DataConverter

  converter = DataConverter()
  converter.voc_to_yolo(
      xml_dir="datasets/rsod/raw/annotations",
      output_dir="datasets/rsod/yolo_dataset/labels/train",
      class_mapping={"aircraft": 0, "oiltank": 1, "overpass": 2, "playground": 3}
  )
"""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core.logger import get_logger

logger = get_logger(__name__)


class DataConverter:
    """数据集格式转换器 — 将各种标注格式统一转换为 YOLO TXT 格式"""

    @staticmethod
    def voc_to_yolo(xml_dir: str, output_dir: str, class_mapping: dict) -> dict:
        """
        VOC XML 格式 → YOLO TXT 格式

        转换逻辑：
          1. 遍历 xml_dir 下所有 .xml 文件
          2. 解析每个 XML 获取图像尺寸和目标边界框
          3. 将像素坐标转换为 YOLO 归一化坐标
          4. 输出到 output_dir，每张图一个 .txt 文件

        Args:
            xml_dir: VOC XML 标注文件目录
            output_dir: YOLO TXT 输出目录
            class_mapping: 类别映射字典 {类名字符串: 类别ID整数}
                          例如 {"airplane": 0, "storage-tank": 1}

        Returns:
            转换统计信息字典 {"total": 总数, "converted": 成功数, "skipped": 跳过数}
        """
        os.makedirs(output_dir, exist_ok=True)

        stats = {"total": 0, "converted": 0, "skipped": 0, "errors": []}

        xml_files = list(Path(xml_dir).glob("*.xml"))
        if not xml_files:
            logger.warning("VOC 转换：目录 %s 下未找到 .xml 文件", xml_dir)
            return stats

        logger.info("VOC 转换开始：%d 个 XML 文件 → %s", len(xml_files), output_dir)

        for xml_file in xml_files:
            stats["total"] += 1
            try:
                tree = ET.parse(xml_file)
                root = tree.getroot()

                # 获取图像尺寸
                size = root.find("size")
                if size is None:
                    logger.warning("XML %s 缺少 <size> 标签，跳过", xml_file.name)
                    stats["skipped"] += 1
                    continue

                width_elem = size.find("width")
                height_elem = size.find("height")
                if width_elem is None or height_elem is None:
                    logger.warning(
                        "XML %s 缺少 width 或 height 标签，跳过", xml_file.name
                    )
                    stats["skipped"] += 1
                    continue

                if width_elem.text is None or height_elem.text is None:
                    logger.warning(
                        "XML %s 的 width 或 height 文本为空，跳过", xml_file.name
                    )
                    stats["skipped"] += 1
                    continue

                img_width = int(width_elem.text)
                img_height = int(height_elem.text)

                if img_width <= 0 or img_height <= 0:
                    logger.warning(
                        "XML %s 图像尺寸无效 (%d, %d)，跳过",
                        xml_file.name,
                        img_width,
                        img_height,
                    )
                    stats["skipped"] += 1
                    continue

                # 解析所有目标
                yolo_lines = []
                for obj in root.findall("object"):
                    name_elem = obj.find("name")
                    if name_elem is None or name_elem.text is None:
                        continue

                    class_name = name_elem.text.strip()
                    if class_name not in class_mapping:
                        logger.warning(
                            "XML %s 中类别 '%s' 不在映射表中，跳过该目标",
                            xml_file.name,
                            class_name,
                        )
                        continue

                    class_id = class_mapping[class_name]
                    bbox = obj.find("bndbox")
                    if bbox is None:
                        continue

                    xmin_elem = bbox.find("xmin")
                    ymin_elem = bbox.find("ymin")
                    xmax_elem = bbox.find("xmax")
                    ymax_elem = bbox.find("ymax")

                    if (
                        xmin_elem is None
                        or ymin_elem is None
                        or xmax_elem is None
                        or ymax_elem is None
                    ):
                        continue

                    if (
                        xmin_elem.text is None
                        or ymin_elem.text is None
                        or xmax_elem.text is None
                        or ymax_elem.text is None
                    ):
                        continue

                    xmin = float(xmin_elem.text)
                    ymin = float(ymin_elem.text)
                    xmax = float(xmax_elem.text)
                    ymax = float(ymax_elem.text)

                    # 边界值裁剪：确保坐标不超出图像范围
                    xmin = max(0, min(xmin, img_width))
                    ymin = max(0, min(ymin, img_height))
                    xmax = max(0, min(xmax, img_width))
                    ymax = max(0, min(ymax, img_height))

                    # 过滤无效框（宽或高为 0）
                    if xmax <= xmin or ymax <= ymin:
                        logger.warning(
                            "XML %s 中目标 '%s' 边界框无效，跳过",
                            xml_file.name,
                            class_name,
                        )
                        continue

                    # 像素坐标 → YOLO 归一化坐标
                    x_center = (xmin + xmax) / 2.0 / img_width
                    y_center = (ymin + ymax) / 2.0 / img_height
                    width = (xmax - xmin) / img_width
                    height = (ymax - ymin) / img_height

                    yolo_lines.append(
                        f"{class_id} {x_center:.6f} {y_center:.6f} "
                        f"{width:.6f} {height:.6f}"
                    )

                # 保存 YOLO 标注文件
                txt_file = Path(output_dir) / f"{xml_file.stem}.txt"
                with open(txt_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(yolo_lines))

                stats["converted"] += 1

            except ET.ParseError as e:
                logger.error("XML 解析失败 %s: %s", xml_file.name, str(e))
                stats["errors"].append(str(xml_file.name))
                stats["skipped"] += 1
            except Exception as e:
                logger.error("VOC 转换异常 %s: %s", xml_file.name, str(e))
                stats["errors"].append(str(xml_file.name))
                stats["skipped"] += 1

        logger.info(
            "VOC 转换完成：总计 %d, 成功 %d, 跳过 %d",
            stats["total"],
            stats["converted"],
            stats["skipped"],
        )
        return stats