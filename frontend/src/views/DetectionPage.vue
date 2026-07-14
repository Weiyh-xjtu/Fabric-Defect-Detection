<template>
  <div class="detection-page">
    <!-- ── 页面标题 ── -->
    <div class="page-header">
      <h2>摄像头实时检测</h2>
      <el-tag :type="statusTagType" size="large">
        {{ statusText }}
      </el-tag>
    </div>

    <!-- ── 主体区域 ── -->
    <div class="main-content">
      <!-- 左侧：视频预览 -->
      <div class="preview-panel">
        <div class="video-wrapper">
          <!-- 原始视频（隐藏，用于获取帧） -->
          <video
            ref="videoRef"
            autoplay
            playsinline
            muted
            style="display: none"
          ></video>

          <!-- 标注画面 Canvas（显示标注后的帧） -->
          <canvas
            ref="canvasRef"
            class="preview-canvas"
            :width="canvasWidth"
            :height="canvasHeight"
          ></canvas>

          <!-- 未开启时的占位 -->
          <div v-if="!isRunning" class="placeholder">
            <p>点击下方按钮开启摄像头</p>
          </div>
        </div>

        <!-- FPS 和帧数信息 -->
        <div v-if="isRunning" class="video-stats">
          <el-tag type="success">FPS: {{ currentFps }}</el-tag>
          <el-tag type="info">帧: {{ frameCount }}</el-tag>
          <el-tag type="info">推理: {{ inferenceTime }}ms</el-tag>
        </div>
      </div>

      <!-- 右侧：检测结果 -->
      <div class="result-panel">
        <el-card class="stats-card" shadow="never">
          <template #header>
            <span>实时检测统计</span>
          </template>

          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-value">{{ objectCount }}</div>
              <div class="stat-label">当前目标数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ currentFps }}</div>
              <div class="stat-label">实时 FPS</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ inferenceTime }}</div>
              <div class="stat-label">推理耗时(ms)</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ frameCount }}</div>
              <div class="stat-label">已处理帧</div>
            </div>
          </div>
        </el-card>

        <el-card class="detections-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span>当前帧目标列表</span>
              <el-tag size="small"
                >{{ currentDetections.length }} 个目标</el-tag
              >
            </div>
          </template>

          <div v-if="currentDetections.length === 0" class="empty-state">
            暂无检测目标
          </div>

          <div v-else class="detection-list">
            <div
              v-for="(det, index) in currentDetections"
              :key="index"
              class="detection-item"
            >
              <div class="det-info">
                <span class="det-class">{{ det.class_name }}</span>
                <el-progress
                  :percentage="Math.round(det.confidence * 100)"
                  :stroke-width="6"
                  :show-text="true"
                  style="width: 120px"
                />
              </div>
              <div class="det-bbox">
                [{{ det.bbox.map((v) => Math.round(v)).join(", ") }}]
              </div>
            </div>
          </div>
        </el-card>

        <!-- 类别分布统计 -->
        <el-card
          v-if="Object.keys(classDistribution).length > 0"
          class="distribution-card"
          shadow="never"
        >
          <template #header>
            <span>类别分布</span>
          </template>
          <div class="distribution-list">
            <div
              v-for="(count, className) in classDistribution"
              :key="className"
              class="distribution-item"
            >
              <span class="class-name">{{ className }}</span>
              <el-tag size="small" type="primary">{{ count }}</el-tag>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- ── 控制栏 ── -->
    <div class="control-bar">
      <el-button
        v-if="!isRunning"
        type="primary"
        size="large"
        @click="startCamera"
        :loading="isConnecting"
      >
        开启摄像头
      </el-button>
      <el-button v-else type="danger" size="large" @click="stopCamera">
        停止检测
      </el-button>

      <el-divider direction="vertical" />

      <!-- GPU/CPU 模式选择 -->
      <span class="control-label">推理模式：</span>
      <el-radio-group v-model="detectMode" :disabled="isRunning">
        <el-radio-button label="cpu">CPU 节能</el-radio-button>
        <el-radio-button label="gpu">GPU 加速</el-radio-button>
      </el-radio-group>

      <el-divider direction="vertical" />

      <!-- 置信度阈值 -->
      <span class="control-label">置信度：</span>
      <el-slider
        v-model="confThreshold"
        :min="0.1"
        :max="0.9"
        :step="0.05"
        :disabled="isRunning"
        style="width: 150px"
        show-input
      />
    </div>
  </div>
</template>

<script setup>
/**
 * DetectionPage.vue — 摄像头实时检测独立页面
 *
 * 功能：
 *   - 浏览器 getUserMedia() 获取摄像头画面
 *   - WebSocket 实时发送帧数据到后端
 *   - 接收后端返回的标注帧并渲染到 Canvas
 *   - GPU/CPU 双模式切换
 *   - 实时统计：FPS、目标数、推理耗时、类别分布
 */
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, ref } from "vue";

// ── 响应式状态 ──
const videoRef = ref(null);
const canvasRef = ref(null);

// 摄像头状态
const isRunning = ref(false);
const isConnecting = ref(false);

// 检测配置
const detectMode = ref("cpu"); // cpu 或 gpu
const confThreshold = ref(0.25);

// 实时统计
const currentFps = ref(0);
const frameCount = ref(0);
const inferenceTime = ref(0);
const objectCount = ref(0);
const currentDetections = ref([]);

// Canvas 尺寸
const canvasWidth = ref(640);
const canvasHeight = ref(480);

// WebSocket
let ws = null;
let mediaStream = null;

// ── 计算属性 ──
const statusText = computed(() => {
  if (isConnecting.value) return "连接中...";
  if (isRunning.value) return "运行中";
  return "未启动";
});

const statusTagType = computed(() => {
  if (isConnecting.value) return "warning";
  if (isRunning.value) return "success";
  return "info";
});

const classDistribution = computed(() => {
  const dist = {};
  for (const det of currentDetections.value) {
    dist[det.class_name] = (dist[det.class_name] || 0) + 1;
  }
  return dist;
});

/** 开启摄像头 */
async function startCamera() {
  try {
    isConnecting.value = true;

    // 1. 获取摄像头权限
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        facingMode: "user",
      },
      audio: false,
    });

    // 2. 将媒体流绑定到隐藏的 video 元素
    videoRef.value.srcObject = mediaStream;
    await videoRef.value.play();

    // 3. 更新 Canvas 尺寸
    canvasWidth.value = videoRef.value.videoWidth || 640;
    canvasHeight.value = videoRef.value.videoHeight || 480;

    // 4. 建立 WebSocket 连接
    connectWebSocket();

    isRunning.value = true;
    ElMessage.success("摄像头已开启");
  } catch (err) {
    console.error("[摄像头开启失败]", err);
    ElMessage.error(`摄像头开启失败: ${err.message}`);
    isConnecting.value = false;
  }
}

/** 建立 WebSocket 连接 */
function connectWebSocket() {
  // 构造 WebSocket URL
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host; // 开发环境通过 Vite proxy 转发
  const wsUrl = `${protocol}//${host}/api/detection/camera`;

  // 获取 Token
  const token = localStorage.getItem("rsod_token");

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
      console.log("[WebSocket] 连接已建立");

      ws.send(
        JSON.stringify({
          type: "config",
          mode: detectMode.value,
          conf: confThreshold.value,
        }),
      );
    };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);

      if (data.type === "result") {
        // 收到标注帧
        renderAnnotatedFrame(data.annotated_frame);

        // 更新统计信息
        currentFps.value = data.fps || 0;
        frameCount.value = data.frame_count || 0;
        inferenceTime.value = data.inference_time || 0;
        objectCount.value = data.object_count || 0;
        currentDetections.value = data.detections || [];
      } else if (data.type === "config_ok") {
        console.log("[WebSocket] 配置确认:", data.message);
        requestAnimationFrame(sendSingleFrame);
      } else if (data.type === "error") {
        console.error("[WebSocket] 服务端错误:", data.message);
        ElMessage.error(data.message);
      }
    } catch (err) {
      console.error("[WebSocket] 消息解析失败:", err);
    }
  };

  ws.onclose = () => {
    console.log("[WebSocket] 连接已关闭");
    isConnecting.value = false;
  };

  ws.onerror = (err) => {
    console.error("[WebSocket] 连接错误:", err);
    ElMessage.error("WebSocket 连接失败，请检查后端服务");
    isConnecting.value = false;
  };
}

/** 发送单帧 */
function sendSingleFrame() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!videoRef.value || videoRef.value.readyState < 2) return;

  const targetSize = detectMode.value === "cpu" ? 416 : 640;
  const tempCanvas = document.createElement("canvas");
  tempCanvas.width = targetSize;
  tempCanvas.height = targetSize;
  const ctx = tempCanvas.getContext("2d");

  const vw = videoRef.value.videoWidth;
  const vh = videoRef.value.videoHeight;
  const scale = Math.min(targetSize / vw, targetSize / vh);
  const x = (targetSize - vw * scale) / 2;
  const y = (targetSize - vh * scale) / 2;
  ctx.drawImage(videoRef.value, x, y, vw * scale, vh * scale);

  const dataUrl = tempCanvas.toDataURL("image/jpeg", 0.6);
  const base64Data = dataUrl.split(",")[1];

  ws.send(
    JSON.stringify({
      type: "frame",
      data: base64Data,
    }),
  );
}

/** 渲染标注帧到 Canvas */
function renderAnnotatedFrame(annotatedBase64) {
  if (!canvasRef.value) return;

  const img = new Image();
  img.onload = () => {
    const ctx = canvasRef.value.getContext("2d");
    canvasRef.value.width = img.width;
    canvasRef.value.height = img.height;
    ctx.drawImage(img, 0, 0);

    requestAnimationFrame(sendSingleFrame);
  };
  img.src = `data:image/jpeg;base64,${annotatedBase64}`;
}

/** 停止摄像头 */
function stopCamera() {
  // 关闭 WebSocket
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "close" }));
    ws.close();
    ws = null;
  }

  // 停止摄像头
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }

  // 重置状态
  isRunning.value = false;
  isConnecting.value = false;
  currentFps.value = 0;
  inferenceTime.value = 0;
  objectCount.value = 0;
  currentDetections.value = [];

  // 清空 Canvas
  if (canvasRef.value) {
    const ctx = canvasRef.value.getContext("2d");
    ctx.clearRect(0, 0, canvasRef.value.width, canvasRef.value.height);
  }

  ElMessage.info("摄像头已停止");
}

// ── 组件销毁时清理 ──
onBeforeUnmount(() => {
  stopCamera();
});
</script>

<style lang="scss" scoped>
.detection-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 20px;
  background: #f5f5f5;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;

  h2 {
    margin: 0;
  }
}

.main-content {
  display: flex;
  gap: 20px;
  flex: 1;
  overflow: hidden;
}

/* 左侧预览区 */
.preview-panel {
  flex: 3;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.video-wrapper {
  position: relative;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-canvas {
  width: 100%;
  height: auto;
  display: block;
}

.placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 16px;
}

.video-stats {
  display: flex;
  gap: 8px;
}

/* 右侧结果区 */
.result-panel {
  flex: 2;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.stat-item {
  text-align: center;
  padding: 12px;
  background: #f9f9f9;
  border-radius: 8px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #409eff;
}

.stat-label {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.empty-state {
  text-align: center;
  color: #999;
  padding: 20px;
}

.detection-list {
  max-height: 300px;
  overflow-y: auto;
}

.detection-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;

  &:last-child {
    border-bottom: none;
  }
}

.det-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.det-class {
  font-weight: 600;
  min-width: 80px;
}

.det-bbox {
  font-size: 12px;
  color: #999;
  font-family: monospace;
}

.distribution-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.distribution-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: #f5f5f5;
  border-radius: 4px;
}

.class-name {
  font-weight: 500;
}

/* 控制栏 */
.control-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 0;
  border-top: 1px solid #e0e0e0;
  margin-top: 16px;
}

.control-label {
  font-size: 14px;
  color: #666;
  white-space: nowrap;
}
</style>