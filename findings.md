# Day7 实施发现

## 文档需求
需求真源：`docs/9. Day07-基于YOLOv11的目标检测智能体平台-模型评估 + 调优 + 导出.md`。

### 后端
- 新增独立评估脚本 `backend/tools/evaluate_model.py`：支持权重、data.yaml、split、conf、IoU、imgsz、device、输出目录参数，执行 `YOLO.val()` 并生成总体/分类指标与 JSON 报告。
- `TrainingService` 新增：
  - `validate_model(db, task_id, split, conf, iou)`：仅评估已完成任务，读取 `best.pt` 和数据集，返回 Precision、Recall、mAP50、mAP50-95 及分类指标，并创建/更新 `ModelVersion`。
  - `export_model(...)`：将权重、报告、可用图表复制到 `models/{scene}_{version}`，可上传 MinIO，可设为场景默认版本。
  - `get_model_download_path(...)`：优先 `best.pt`，回退 `last.pt`。
- 新增四个鉴权接口：`POST validate/{task_id}`、`POST export/{task_id}`、`GET download/{task_id}`、`POST predict`（multipart 测试图片推理）。
- 新增验证/导出请求与响应 schema；复用现有 `ModelVersion` 字段。
- 测试图片接口需限制 JPEG/PNG/BMP/WebP，返回检测框、类别计数、标注图 base64 与推理耗时，并始终清理临时文件。

### 前端
- 在 `TrainingPage.vue` 的已完成任务区域增加评估、导出、下载权重、测试验证按钮。
- 评估展示四项总体指标及按 AP50 降序的分类表，AP50 < 0.5 的类别高亮。
- 导出对话框包含版本、描述、设为默认、上传 MinIO。
- 测试对话框包含单图上传、conf/IoU 滑块、标注结果、对象数、耗时和检测表。
- API 客户端使用 Axios `/api` baseURL，因此业务路径应为 `/training/...`；下载使用鉴权 Blob。

### 调优范围
- Day7 的“调优”主体是根据评估结果调整现有训练参数并重新训练；文档未要求实现自动超参数搜索。
- 现有 `augment_config` 必须真实合并到 Ultralytics 训练参数，否则调优配置无效。

## 现有实现
- 探索代理的隔离 worktree 基于 `main`，而用户主工作区位于 `development`；代理看到的占位前端/缺失训练文件不能直接代表当前工作区。
- 已在主工作区确认存在 `backend/app/training/training_service.py`、`backend/app/api/training.py`、`backend/tests/test_training.py` 与 `frontend/src/views/TrainingPage.vue`；后续均以这些实际文件为基线。
- 主工作区训练路由已注册，现有 7 个接口为 scenes/start/tasks/status/metrics/stop/results。
- `TrainingService._run_training()` 构造 `train_kwargs` 时未合并 `augment_config`，所以当前调优中的增强参数会被持久化但不生效。
- 训练输出目录由当前进程工作目录与 `settings.TRAIN_OUTPUT_DIR` 组合；评估、导出、下载应统一复用同一解析逻辑。
- `ModelVersion` 的字段已覆盖 Day7 所需持久化内容，无需数据库迁移。
- 现有 status/metrics/stop/results API 未校验任务是否属于当前用户；新增接口至少必须执行归属校验，最好同步收紧现有任务接口以避免模型文件越权访问。
- 主工作区 `TrainingPage.vue` 已具备 Day6 的任务列表、创建表单、轮询和 ECharts 训练曲线；Day7 应增量扩展，不重写现有流程。
- 前端已有 Element Plus、ECharts、Axios 和 JWT 注入，后端已有 multipart、Ultralytics、OpenCV、Pillow、MinIO 依赖，无需新增依赖。
- Day7 示例代码需要做两处稳健化：`results.box.ap` 可能是 NumPy 数组，不能直接用于布尔判断；导出应先执行验证再复制图表，否则本次验证新生成的图表不会进入导出目录。
- 当前训练测试仅覆盖场景列表、CSV epoch 解析和实时 loss 提取；新增流程应通过 mock 避免真实加载 YOLO 或连接 MinIO。
- 训练创建表单尚未暴露 `augment_config`；为满足调优，需要增加常用增强参数并随训练请求提交。
- 独立评估脚本需同时支持 `args.yaml` 和 `datasets/*/yolo_dataset/data.yaml` 自动查找，并对相对路径做正确解析。
- API 预测逻辑可按文档使用临时文件 + `YOLO.predict()` + OpenCV JPEG/base64；实现时增加空文件、阈值范围和任务归属校验。

## 差距与决策
- 不实现文档未要求的自动超参数 sweep；聚焦“评估驱动的手工调优 + 重新训练”。
- 采用现有训练架构和响应风格，避免另起模型管理子系统。
- 下一步直接盘点 `development` 主工作区的实际文件与差异。

## 验证记录
- `python -m compileall -q backend/app backend/tools backend/tests`：通过。
- `npm --prefix frontend run build`：通过；仅报告大 chunk 警告。
- `npm --prefix frontend run test:run`：2 个测试文件、5 个测试通过。
- 后端 pytest 尚未执行：系统 Python 3.13 未安装 pytest，项目内未找到虚拟环境。
