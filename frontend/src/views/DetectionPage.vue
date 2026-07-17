<template>
  <div class="detection-page">
    <div class="page-header">
      <div>
        <h2>摄像头实时检测</h2>
        <p>支持本机摄像头和 Wi-Fi 手机摄像头，逐帧检测并显示标注结果</p>
      </div>
      <el-tag :type="statusTagType" size="large">{{ statusText }}</el-tag>
    </div>

    <el-card class="camera-source-card" shadow="never">
      <div class="source-row">
        <span class="control-label">摄像头来源：</span>
        <el-radio-group
          v-model="cameraSource"
          :disabled="isRunning || isConnecting"
        >
          <el-radio-button value="browser">本机摄像头</el-radio-button>
          <el-radio-button value="ip_webcam">Wi-Fi 手机摄像头</el-radio-button>
        </el-radio-group>

        <el-input
          v-if="cameraSource === 'ip_webcam'"
          v-model="ipCameraUrl"
          :disabled="isRunning || isConnecting"
          class="ip-camera-input"
          placeholder="例如：http://192.168.1.23:8080/video"
          clearable
        />
      </div>
      <div v-if="cameraSource === 'ip_webcam'" class="source-help">
        请先在手机 IP Webcam 中启动服务器，并填写手机显示地址后追加 /video。
      </div>
    </el-card>

    <div class="main-content">
      <section class="preview-panel">
        <div class="video-wrapper">
          <video
            ref="videoRef"
            autoplay
            playsinline
            muted
            class="source-video"
          ></video>
          <canvas
            ref="canvasRef"
            class="preview-canvas"
            :width="canvasWidth"
            :height="canvasHeight"
          ></canvas>

          <div v-if="!isRunning" class="placeholder">
            <span>{{ isConnecting ? "正在初始化模型..." : placeholderText }}</span>
          </div>
        </div>

        <div v-if="isRunning" class="video-stats">
          <el-tag type="success">FPS: {{ currentFps }}</el-tag>
          <el-tag type="info">帧: {{ frameCount }}</el-tag>
          <el-tag type="info">推理: {{ inferenceTime }}ms</el-tag>
        </div>
      </section>

      <aside class="result-panel">
        <el-card shadow="never">
          <template #header>实时检测统计</template>
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
              <el-tag size="small">{{ currentDetections.length }} 个目标</el-tag>
            </div>
          </template>

          <div v-if="currentDetections.length === 0" class="empty-state">
            暂无检测目标
          </div>
          <div v-else class="detection-list">
            <div
              v-for="(det, index) in currentDetections"
              :key="`${det.class_id}-${index}`"
              class="detection-item"
            >
              <div class="det-info">
                <span class="det-class">{{ det.class_name }}</span>
                <el-progress
                  :percentage="Math.round(det.confidence * 100)"
                  :stroke-width="6"
                  :show-text="true"
                />
              </div>
              <div class="det-bbox">
                [{{ det.bbox.map((value) => Math.round(value)).join(", ") }}]
              </div>
            </div>
          </div>
        </el-card>

        <el-card
          v-if="Object.keys(classDistribution).length"
          class="distribution-card"
          shadow="never"
        >
          <template #header>类别分布</template>
          <div class="distribution-list">
            <div
              v-for="(count, className) in classDistribution"
              :key="className"
              class="distribution-item"
            >
              <span>{{ className }}</span>
              <el-tag size="small" type="primary">{{ count }}</el-tag>
            </div>
          </div>
        </el-card>
      </aside>
    </div>

    <div class="control-bar">
      <el-button
        v-if="!isRunning && !isConnecting"
        type="primary"
        size="large"
        @click="startCamera"
      >
        {{ startButtonText }}
      </el-button>
      <el-button v-else type="danger" size="large" @click="stopCamera()">
        {{ isConnecting ? "取消连接" : "停止检测" }}
      </el-button>

      <el-divider direction="vertical" />

      <span class="control-label">推理模式：</span>
      <el-radio-group
        v-model="detectMode"
        :disabled="isRunning || isConnecting"
      >
        <el-radio-button value="cpu">CPU 节能</el-radio-button>
        <el-radio-button value="gpu">GPU 加速</el-radio-button>
      </el-radio-group>

      <el-divider direction="vertical" />

      <span class="control-label">置信度：</span>
      <el-slider
        v-model="confThreshold"
        :min="0.1"
        :max="0.9"
        :step="0.05"
        :disabled="isRunning || isConnecting"
        class="confidence-slider"
        show-input
      />
    </div>
  </div>
</template>

<script setup>
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, ref } from "vue";
import { useUserStore } from "@/stores/user";

const userStore = useUserStore();

const videoRef = ref(null);
const canvasRef = ref(null);

const isRunning = ref(false);
const isConnecting = ref(false);
const detectMode = ref("cpu");
const confThreshold = ref(0.25);
const cameraSource = ref("browser");
const ipCameraUrl = ref("");

const currentFps = ref(0);
const frameCount = ref(0);
const inferenceTime = ref(0);
const objectCount = ref(0);
const currentDetections = ref([]);

const canvasWidth = ref(640);
const canvasHeight = ref(480);

let ws = null;
let mediaStream = null;
let captureCanvas = null;
let nextFrameRequest = null;

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

const startButtonText = computed(() =>
  cameraSource.value === "ip_webcam" ? "连接手机摄像头" : "开启本机摄像头",
);

const placeholderText = computed(() =>
  cameraSource.value === "ip_webcam"
    ? "输入手机 IP Webcam 地址后开始检测"
    : "点击下方按钮开启本机摄像头",
);

const classDistribution = computed(() => {
  const distribution = {};
  for (const detection of currentDetections.value) {
    distribution[detection.class_name] =
      (distribution[detection.class_name] || 0) + 1;
  }
  return distribution;
});

function resetStats() {
  currentFps.value = 0;
  frameCount.value = 0;
  inferenceTime.value = 0;
  objectCount.value = 0;
  currentDetections.value = [];
}

function releaseMediaStream() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (videoRef.value) {
    videoRef.value.srcObject = null;
  }
}

function clearCanvas() {
  if (!canvasRef.value) return;
  const context = canvasRef.value.getContext("2d");
  context.clearRect(0, 0, canvasRef.value.width, canvasRef.value.height);
}

function scheduleNextFrame() {
  if (!isRunning.value) return;
  if (nextFrameRequest !== null) {
    cancelAnimationFrame(nextFrameRequest);
  }
  nextFrameRequest = requestAnimationFrame(sendNextFrameRequest);
}

async function startCamera() {
  if (cameraSource.value === "ip_webcam") {
    await startIpWebcam();
    return;
  }
  await startBrowserCamera();
}

async function startBrowserCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    ElMessage.error("当前浏览器不支持摄像头访问");
    return;
  }

  try {
    isConnecting.value = true;
    resetStats();

    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        facingMode: "user",
      },
      audio: false,
    });

    videoRef.value.srcObject = mediaStream;
    await videoRef.value.play();

    canvasWidth.value = videoRef.value.videoWidth || 640;
    canvasHeight.value = videoRef.value.videoHeight || 480;
    connectWebSocket();
  } catch (error) {
    console.error("[本机摄像头开启失败]", error);
    releaseMediaStream();
    isConnecting.value = false;
    ElMessage.error(`本机摄像头开启失败: ${error.message || error}`);
  }
}

async function startIpWebcam() {
  const cameraUrl = ipCameraUrl.value.trim();
  if (!cameraUrl) {
    ElMessage.warning("请填写手机 IP Webcam 地址");
    return;
  }

  try {
    new URL(cameraUrl);
  } catch {
    ElMessage.warning("手机摄像头地址格式不正确");
    return;
  }

  try {
    isConnecting.value = true;
    resetStats();
    releaseMediaStream();
    canvasWidth.value = 640;
    canvasHeight.value = 480;
    connectWebSocket();
  } catch (error) {
    console.error("[手机摄像头连接失败]", error);
    isConnecting.value = false;
    ElMessage.error(`手机摄像头连接失败: ${error.message || error}`);
  }
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/api/detection/camera`,
  );
  ws = socket;

  socket.onopen = () => {
    const config = {
      type: "config",
      access_token: userStore.token,
      source: cameraSource.value,
      mode: detectMode.value,
      conf: confThreshold.value,
    };
    if (cameraSource.value === "ip_webcam") {
      config.camera_url = ipCameraUrl.value.trim();
    }
    socket.send(JSON.stringify(config));
  };

  socket.onmessage = (event) => {
    if (ws !== socket) return;

    try {
      const data = JSON.parse(event.data);

      if (data.type === "config_ok") {
        isConnecting.value = false;
        isRunning.value = true;
        const sourceLabel = data.source === "ip_webcam" ? "手机摄像头" : "本机摄像头";
        ElMessage.success(`${sourceLabel}已开启（${data.mode.toUpperCase()} 模式）`);
        scheduleNextFrame();
        return;
      }

      if (data.type === "result") {
        currentFps.value = data.fps || 0;
        frameCount.value = data.frame_count || 0;
        inferenceTime.value = data.inference_time || 0;
        objectCount.value = data.object_count || 0;
        currentDetections.value = data.detections || [];
        renderAnnotatedFrame(data.annotated_frame);
        return;
      }

      if (data.type === "error") {
        handleSocketFailure(data.message || "摄像头检测失败", socket);
      }
    } catch (error) {
      console.error("[WebSocket 消息解析失败]", error);
      scheduleNextFrame();
    }
  };

  socket.onerror = () => {
    handleSocketFailure("WebSocket 连接失败，请检查后端服务", socket);
  };

  socket.onclose = () => {
    if (ws !== socket) return;
    ws = null;
    const wasActive = isRunning.value || isConnecting.value;
    isRunning.value = false;
    isConnecting.value = false;
    releaseMediaStream();
    if (wasActive) {
      ElMessage.warning("摄像头检测连接已断开");
    }
  };
}

function handleSocketFailure(message, socket) {
  if (ws !== socket) return;
  ws = null;
  isRunning.value = false;
  isConnecting.value = false;
  releaseMediaStream();
  resetStats();
  clearCanvas();
  if (socket.readyState < WebSocket.CLOSING) {
    socket.close();
  }
  ElMessage.error(message);
}

function sendNextFrameRequest() {
  nextFrameRequest = null;
  if (cameraSource.value === "ip_webcam") {
    requestIpWebcamFrame();
    return;
  }
  sendBrowserFrame();
}

function requestIpWebcamFrame() {
  if (!isRunning.value || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "pull_frame" }));
}

function sendBrowserFrame() {
  if (!isRunning.value || !ws || ws.readyState !== WebSocket.OPEN) return;

  const video = videoRef.value;
  if (!video || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
    scheduleNextFrame();
    return;
  }

  const targetSize = detectMode.value === "cpu" ? 416 : 640;
  if (!captureCanvas) {
    captureCanvas = document.createElement("canvas");
  }
  captureCanvas.width = targetSize;
  captureCanvas.height = targetSize;

  const context = captureCanvas.getContext("2d");
  const videoWidth = video.videoWidth;
  const videoHeight = video.videoHeight;
  if (!videoWidth || !videoHeight) {
    scheduleNextFrame();
    return;
  }

  const scale = Math.min(targetSize / videoWidth, targetSize / videoHeight);
  const drawWidth = videoWidth * scale;
  const drawHeight = videoHeight * scale;
  const offsetX = (targetSize - drawWidth) / 2;
  const offsetY = (targetSize - drawHeight) / 2;

  context.fillStyle = "#000";
  context.fillRect(0, 0, targetSize, targetSize);
  context.drawImage(video, offsetX, offsetY, drawWidth, drawHeight);

  const base64Data = captureCanvas.toDataURL("image/jpeg", 0.6).split(",")[1];
  ws.send(JSON.stringify({ type: "frame", data: base64Data }));
}

function renderAnnotatedFrame(annotatedBase64) {
  if (!canvasRef.value || !annotatedBase64) {
    scheduleNextFrame();
    return;
  }

  const image = new Image();
  image.onload = () => {
    if (!canvasRef.value || !isRunning.value) return;
    const context = canvasRef.value.getContext("2d");
    canvasRef.value.width = image.width;
    canvasRef.value.height = image.height;
    context.drawImage(image, 0, 0);
    scheduleNextFrame();
  };
  image.onerror = scheduleNextFrame;
  image.src = `data:image/jpeg;base64,${annotatedBase64}`;
}

function stopCamera(showMessage = true) {
  const hadResources = Boolean(ws || mediaStream || isRunning.value || isConnecting.value);
  const socket = ws;
  ws = null;

  if (nextFrameRequest !== null) {
    cancelAnimationFrame(nextFrameRequest);
    nextFrameRequest = null;
  }

  if (socket) {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "close" }));
    }
    if (socket.readyState < WebSocket.CLOSING) {
      socket.close();
    }
  }

  releaseMediaStream();
  isRunning.value = false;
  isConnecting.value = false;
  resetStats();
  clearCanvas();

  if (showMessage && hadResources) {
    ElMessage.info("摄像头已停止");
  }
}

onBeforeUnmount(() => stopCamera(false));
</script>

<style lang="scss" scoped>
.detection-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 20px;
  background: #f5f5f5;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;

  h2,
  p {
    margin: 0;
  }

  p {
    margin-top: 6px;
    color: #909399;
    font-size: 13px;
  }
}

.camera-source-card {
  margin-bottom: 16px;
}

.source-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.ip-camera-input {
  width: min(460px, 100%);
}

.source-help {
  margin-top: 8px;
  color: #909399;
  font-size: 13px;
}

.main-content {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 20px;
}

.preview-panel {
  display: flex;
  flex: 3;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
}

.video-wrapper {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 400px;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: #000;
  border-radius: 8px;
}

.source-video {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.preview-canvas {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #a8abb2;
  background: #111;
}

.video-stats,
.distribution-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.result-panel {
  display: flex;
  flex: 2;
  min-width: 320px;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.stat-item {
  padding: 12px;
  text-align: center;
  background: #f9f9f9;
  border-radius: 8px;
}

.stat-value {
  color: #409eff;
  font-size: 24px;
  font-weight: 700;
}

.stat-label {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.card-header,
.detection-item,
.det-info,
.distribution-item {
  display: flex;
  align-items: center;
}

.card-header,
.detection-item {
  justify-content: space-between;
}

.empty-state {
  padding: 24px;
  color: #909399;
  text-align: center;
}

.detection-list {
  max-height: 320px;
  overflow-y: auto;
}

.detection-item {
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;

  &:last-child {
    border-bottom: 0;
  }
}

.det-info {
  flex: 1;
  min-width: 0;
  gap: 12px;

  :deep(.el-progress) {
    width: 130px;
  }
}

.det-class {
  min-width: 80px;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.det-bbox {
  color: #909399;
  font-family: monospace;
  font-size: 12px;
}

.distribution-item {
  gap: 6px;
  padding: 5px 8px;
  background: #f5f5f5;
  border-radius: 4px;
}

.control-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e0e0e0;
}

.control-label {
  color: #606266;
  font-size: 14px;
  white-space: nowrap;
}

.confidence-slider {
  width: 220px;
}

@media (max-width: 1100px) {
  .main-content {
    overflow-y: auto;
    flex-direction: column;
  }

  .result-panel {
    min-width: 0;
    overflow: visible;
  }

  .control-bar {
    flex-wrap: wrap;
  }
}

@media (max-width: 640px) {
  .detection-page {
    padding: 12px;
  }

  .video-wrapper {
    min-height: 280px;
  }

  .control-bar :deep(.el-divider--vertical) {
    display: none;
  }

  .confidence-slider {
    width: 100%;
  }
}
</style>
