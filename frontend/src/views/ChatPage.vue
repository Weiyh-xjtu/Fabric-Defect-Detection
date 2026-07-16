<template>
  <div class="chat-page">
    <!-- ── 对话主区域 ── -->
    <div class="chat-main">
    <!-- ── 消息列表区域 ── -->
    <div class="message-list" ref="messageListRef">
      <div
        v-for="(msg, index) in agentStore.messages"
        :key="index"
        :class="['message-item', `message-${msg.role}`]"
      >
        <!-- 用户消息 -->
        <div v-if="msg.role === 'user'" class="message-bubble user-bubble">
          <div class="message-content">{{ msg.content }}</div>
          <!-- 单张图片附件 -->
          <div v-if="msg.image" class="message-attachment">
            <img :src="msg.imagePreview" alt="附件图片" />
          </div>
          <!-- 多图附件（批量检测） -->
          <div v-if="msg.images && msg.images.length" class="message-attachments-grid">
            <img v-for="(src, i) in msg.images" :key="i" :src="src" alt="附件图片" />
          </div>
          <div v-if="msg.videoUrl" class="message-video">
            <video :src="msg.videoUrl" controls preload="metadata"></video>
          </div>
          <div v-if="msg.fileAttachments?.length" class="message-files">
            <span v-for="filename in msg.fileAttachments" :key="filename">
              📦 {{ filename }}
            </span>
          </div>
        </div>

        <!-- AI 消息 -->
        <div
          v-else-if="msg.role === 'assistant'"
          class="message-bubble assistant-bubble"
        >
          <!-- 处理该消息的专家 Agent -->
          <div v-if="msg.agent" class="agent-route">
            <el-tag size="small" effect="plain" type="warning">
              🤖 {{ agentLabel(msg.agent) }}
            </el-tag>
          </div>

          <div v-if="msg.loading" class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
          <div
            v-else
            class="message-content markdown-body"
            v-html="renderMarkdown(msg.content)"
          ></div>

          <!-- 工具调用链 -->
          <div v-if="msg.toolChain?.length" class="tool-chain">
            <div
              v-for="(step, i) in msg.toolChain"
              :key="i"
              class="tool-chain-step"
            >
              <div class="tool-chain-head">
                <span class="tool-chain-index">{{ i + 1 }}</span>
                <el-tag
                  size="small"
                  :type="step.status === 'done' ? 'success' : step.status === 'error' ? 'danger' : 'info'"
                >
                  🔧 {{ toolDisplayName(step.tool) }}
                </el-tag>
                <span :class="['tool-chain-status', `status-${step.status}`]">
                  {{ step.status === 'running' ? '执行中…' : step.status === 'error' ? '失败' : '完成' }}
                </span>
                <span v-if="step.summary" class="tool-chain-summary">{{ step.summary }}</span>
              </div>

              <!-- 知识库检索来源 -->
              <div v-if="step.knowledge" class="knowledge-sources">
                <div v-if="step.knowledge.fallback_reason" class="knowledge-fallback">
                  ⚠ 向量检索暂不可用，本次为本地词法检索
                </div>
                <el-collapse class="knowledge-collapse">
                  <el-collapse-item
                    v-for="(item, j) in step.knowledge.results"
                    :key="j"
                    :title="`📄 ${item.source} · 相关度 ${formatScore(item.score, step.knowledge.retrieval_mode)}`"
                  >
                    <div class="knowledge-snippet">{{ item.content }}</div>
                  </el-collapse-item>
                </el-collapse>
                <div v-if="!step.knowledge.results?.length" class="knowledge-empty">
                  知识库未命中任何片段
                </div>
              </div>
            </div>
          </div>

          <!-- 检测结果卡片 -->
          <DetectionResultCard
            v-if="msg.detectionResult"
            :result="msg.detectionResult"
          />
          <el-button
            v-if="msg.retryData"
            size="small"
            type="primary"
            plain
            @click="retryMessage(msg)"
          >重新发送</el-button>
        </div>
      </div>
    </div>

    <!-- ── 输入区域 ── -->
    <div class="composer-wrapper">
      <div class="input-area">
        <div v-if="selectedFiles.length" class="selected-files">
          <el-tag
            v-for="(file, index) in selectedFiles"
            :key="`${file.name}-${file.lastModified}`"
            closable
            @close="removeSelectedFile(index)"
          >
            {{ file.name }}
          </el-tag>
        </div>

        <!-- 文本输入框 -->
        <el-input
          v-model="inputText"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          resize="none"
          placeholder="输入消息，可附加单图、多图、ZIP 或视频..."
          @keydown.enter.exact.prevent="sendMessage"
          :disabled="agentStore.isLoading"
        />

        <div class="composer-actions">
          <div class="quick-actions">
            <!-- 附件按钮 -->
            <el-button
              class="attach-btn"
              @click="triggerFileInput"
              :disabled="agentStore.isLoading"
              circle
            >
              📎
            </el-button>
            <input
              ref="fileInputRef"
              type="file"
              accept="image/*,video/*,.zip"
              multiple
              style="display: none"
              @change="handleFileSelect"
            />
            <el-button
              @click="handleQuickDetect('single')"
              :disabled="agentStore.isLoading"
            >
              📷 单图检测
            </el-button>
            <el-button
              @click="handleQuickDetect('batch')"
              :disabled="agentStore.isLoading"
            >
              📁 批量/ZIP
            </el-button>
            <el-button
              @click="handleVideoDetect"
              :disabled="agentStore.isLoading"
            >
              🎬 视频
            </el-button>
            <el-button @click="openCameraDetection">📹 摄像头</el-button>
          </div>

          <!-- 发送/停止按钮 -->
          <el-button
            v-if="!agentStore.isLoading"
            class="send-btn"
            type="primary"
            @click="sendMessage"
            :disabled="!inputText.trim() && !selectedFiles.length"
          >
            发送
          </el-button>
          <el-button v-else class="send-btn" type="danger" @click="handleStop">
            停止
          </el-button>
        </div>
      </div>
    </div>
    </div>
  </div>
</template>

<script setup>
/**
 * ChatPage.vue — 智能对话界面
 *
 * 功能：
 *   - 消息气泡（用户/AI 区分）
 *   - 文件附件上传（单图/多图/ZIP/视频）
 *   - SSE 流式渲染 AI 回复
 *   - 检测结果卡片展示
 *   - 快捷操作栏（单图/批量/视频/摄像头）
 *   - 中断当前对话
 */
import {
  detectBatch,
  detectSingle,
  detectVideo,
  detectZip,
  getVideoStatus,
} from "@/api/detection";
import DetectionResultCard from "@/components/DetectionResultCard.vue";
import { useAgentStore } from "@/stores/agent";
import { renderMarkdown } from "@/utils/markdown";
import request from "@/utils/request";
import { streamChat } from "@/utils/stream";
import {
  AGENT_NAME_MAP,
  beginToolStep,
  completeToolStep,
  toolDisplayName,
} from "@/utils/toolChain";
import { ElMessage } from "element-plus";
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

// ── Store ──
const agentStore = useAgentStore();
const router = useRouter();

// ── 响应式状态 ──
const inputText = ref("");
const selectedFiles = ref([]);
const messageListRef = ref(null);
const fileInputRef = ref(null);

const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "bmp", "webp"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "avi", "mov", "mkv", "wmv", "flv"]);
const MAX_FILE_SIZES = {
  image: 10 * 1024 * 1024,
  zip: 100 * 1024 * 1024,
  video: 50 * 1024 * 1024,
};

// ── 计算属性 ──
const canSend = computed(() => {
  return inputText.value.trim() || selectedFiles.value.length;
});

// ── 方法 ──

/** 发送消息 */
async function sendMessage() {
  if (!canSend.value) return;

  const message = inputText.value.trim();
  const filesToSend = [...selectedFiles.value];
  const attachmentType = filesToSend.length
    ? getAttachmentType(filesToSend[0])
    : null;
  const effectiveMessage =
    message || getDefaultAttachmentInstruction(attachmentType, filesToSend.length);

  const userMessage = {
    role: "user",
    content: effectiveMessage,
  };
  if (attachmentType === "image" && filesToSend.length === 1) {
    userMessage.image = filesToSend[0].name;
    userMessage.imagePreview = URL.createObjectURL(filesToSend[0]);
  } else if (attachmentType === "image") {
    userMessage.images = filesToSend.map((file) => URL.createObjectURL(file));
  } else if (attachmentType === "video") {
    userMessage.videoUrl = URL.createObjectURL(filesToSend[0]);
  } else if (attachmentType === "zip") {
    userMessage.fileAttachments = filesToSend.map((file) => file.name);
  }

  // 添加用户消息到列表
  agentStore.addMessage(userMessage);

  // 清空输入
  inputText.value = "";
  selectedFiles.value = [];
  if (fileInputRef.value) fileInputRef.value.value = "";

  // 添加 AI 加载占位
  const assistantMessageIndex = agentStore.messages.length;
  agentStore.addMessage({
    role: "assistant",
    content: "",
    loading: true,
  });
  const assistantMessage = agentStore.messages[assistantMessageIndex];
  agentStore.setLoading(true);

  // 滚动到底部
  scrollToBottom();

  // ── 上传附件，获取供 Agent 工具调用的服务端路径 ──
  let serverAttachments = [];
  if (filesToSend.length) {
    try {
      const formData = new FormData();
      filesToSend.forEach((file) => formData.append("files", file));
      // 不设置 Content-Type，让 axios 自动添加 boundary
      const uploadResult = await request.post("/chat/upload", formData, {
        timeout: 180000,
      });
      serverAttachments = uploadResult.attachments || [];
      if (serverAttachments.length !== filesToSend.length) {
        throw new Error("后端返回的附件数量不一致");
      }
    } catch (err) {
      console.error("[附件上传失败]", err.response?.data || err.message || err);
      const errorMessage =
        err.response?.data?.message ||
        err.response?.data?.detail ||
        err.response?.data?.error ||
        err.message ||
        "未知错误";
      assistantMessage.content = `附件上传失败：${errorMessage}，请重试`;
      assistantMessage.loading = false;
      assistantMessage.error = true;
      agentStore.setLoading(false);
      return;
    }
  }

  // 发起 SSE 流式请求
  ensureChatSession();
  const requestBody = {
    message: effectiveMessage,
    session_id: agentStore.currentSessionId,
    ...(serverAttachments.length ? { attachments: serverAttachments } : {}),
  };

  let fullContent = "";

  const stop = streamChat("/api/chat/stream", requestBody, {
    onMessage: (data) => {
      if (data.type === "text_chunk") {
        fullContent += data.content;
        assistantMessage.content = fullContent;
        assistantMessage.loading = false;
        scrollToBottom();
      } else if (data.type === "session") {
        agentStore.setCurrentSessionId(data.session_id);
      } else if (data.type === "agent_route") {
        assistantMessage.agent = data.agent;
      } else if (data.type === "tool_call") {
        // 工具调用开始：追加到该消息的调用链
        if (!assistantMessage.toolChain) assistantMessage.toolChain = [];
        beginToolStep(assistantMessage.toolChain, data);
        scrollToBottom();
      } else if (data.type === "tool_result") {
        // 工具调用结束：更新调用链状态并分发结构化结果
        if (!assistantMessage.toolChain) assistantMessage.toolChain = [];
        const info = completeToolStep(assistantMessage.toolChain, data);
        if (info.detection) {
          assistantMessage.detectionResult = info.detection;
          assistantMessage.loading = false;
        }
        scrollToBottom();
      } else if (data.type === "error") {
        assistantMessage.content = data.content;
        assistantMessage.loading = false;
        assistantMessage.error = true;
      }
    },
    onDone: () => {
      if (assistantMessage.loading) {
        assistantMessage.loading = false;
      }
      // 工具报错且 LLM 未输出任何文字时给出兜底提示，避免空气泡
      if (
        !assistantMessage.content &&
        assistantMessage.toolChain?.some((step) => step.status === "error")
      ) {
        assistantMessage.content = "工具调用出现错误，请查看上方调用链详情。";
        assistantMessage.error = true;
      }
      agentStore.setLoading(false);
      // 刷新侧栏：新会话首次回复后应出现在历史列表，标题/时间同步更新
      refreshSessions();
    },
    onError: (err) => {
      assistantMessage.content = `抱歉，处理出错了：${err.message}`;
      assistantMessage.loading = false;
      assistantMessage.error = true;
      assistantMessage.retryData = { message: effectiveMessage, files: filesToSend };
      agentStore.setLoading(false);
      ElMessage.error("对话请求失败，请重试");
    },
  });

  // 保存 中断函数到 store
  agentStore.abortController = stop;
}

/** 停止生成 */
function handleStop() {
  agentStore.abort();
  const lastMsg = agentStore.messages[agentStore.messages.length - 1];
  if (lastMsg.loading) {
    lastMsg.loading = false;
    lastMsg.content += "\n[已停止生成]";
  }
}

/** 触发文件选择框 */
function triggerFileInput() {
  fileInputRef.value?.click();
}

/** 文件选择回调 */
function handleFileSelect(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  if (!validateAttachments(files)) {
    selectedFiles.value = [];
    event.target.value = "";
    return;
  }
  selectedFiles.value = files;
  ElMessage.info(`已选择 ${files.length} 个附件`);
}

function retryMessage(message) {
  const retryData = message.retryData;
  if (!retryData || agentStore.isLoading) return;
  message.retryData = null;
  inputText.value = retryData.message;
  selectedFiles.value = [...(retryData.files || [])];
  sendMessage();
}

function removeSelectedFile(index) {
  selectedFiles.value.splice(index, 1);
}

function getAttachmentType(file) {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  if (file.type.startsWith("image/") || IMAGE_EXTENSIONS.has(extension)) {
    return "image";
  }
  if (extension === "zip") return "zip";
  if (file.type.startsWith("video/") || VIDEO_EXTENSIONS.has(extension)) {
    return "video";
  }
  return null;
}

function validateAttachments(files) {
  const fileTypes = files.map(getAttachmentType);
  if (fileTypes.some((type) => !type)) {
    ElMessage.warning("仅支持图片、ZIP 和视频文件");
    return false;
  }
  if (new Set(fileTypes).size > 1) {
    ElMessage.warning("一次消息不能混合不同类型的附件");
    return false;
  }

  const attachmentType = fileTypes[0];
  if (["zip", "video"].includes(attachmentType) && files.length > 1) {
    ElMessage.warning("ZIP 或视频附件一次只能选择一个");
    return false;
  }
  if (attachmentType === "image" && files.length > 20) {
    ElMessage.warning("批量图片一次最多选择 20 张");
    return false;
  }
  if (files.some((file) => file.size > MAX_FILE_SIZES[attachmentType])) {
    const limitMb = MAX_FILE_SIZES[attachmentType] / (1024 * 1024);
    ElMessage.warning(`附件大小不能超过 ${limitMb}MB`);
    return false;
  }
  return true;
}

function getDefaultAttachmentInstruction(attachmentType, fileCount) {
  if (attachmentType === "image") {
    return fileCount === 1 ? "请检测这张图片" : "请批量检测这些图片";
  }
  if (attachmentType === "zip") return "请检测这个 ZIP 压缩包中的图片";
  if (attachmentType === "video") return "请检测这个视频";
  return "";
}

/** 滚动到底部 */
function scrollToBottom() {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight;
    }
  });
}

/**
 * 快捷单图检测流程：
 * 1. 用户点击"📷 单图检测"按钮
 * 2. 弹出文件选择框
 * 3. 选择图片后，调用 detectSingle API
 * 4. 将结果以"用户消息 + AI 结果卡片"的形式插入对话
 */
async function handleQuickDetect(type) {
  if (type === "single") {
    // 创建隐藏的文件选择器
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      // 添加用户消息（显示文件名）
      agentStore.addMessage({
        role: "user",
        content: `[快捷检测] ${file.name}`,
        image: file.name,
        imagePreview: URL.createObjectURL(file),
      });

      // 添加加载占位
      agentStore.addMessage({
        role: "assistant",
        content: "正在检测中...",
        loading: true,
      });

      // 构造 FormData 并调用 API
      const formData = new FormData();
      formData.append("file", file);
      formData.append("session_id", ensureChatSession());

      try {
        const result = await detectSingle(formData);
        const lastMsg = agentStore.messages[agentStore.messages.length - 1];
        lastMsg.content = `检测完成！发现 ${result.total_objects} 个目标。`;
        lastMsg.loading = false;
        lastMsg.detectionResult = result;
        // 快捷检测已在后端落库，刷新侧栏使新会话出现在历史列表。
        refreshSessions();
      } catch (err) {
        const lastMsg = agentStore.messages[agentStore.messages.length - 1];
        lastMsg.content = "检测失败，请重试";
        lastMsg.loading = false;
      }
    };
    input.click();
  } else if (type === "batch") {
    // 批量检测（支持多选 + ZIP）
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*,.zip";
    input.multiple = true;
    input.onchange = async (e) => {
      const files = Array.from(e.target.files);
      if (!files.length) return;

      const isZip = files.some((f) => f.name.endsWith(".zip"));
      const formData = new FormData();
      formData.append("session_id", ensureChatSession());

      if (isZip && files.length === 1) {
        // 单个 ZIP 文件
        formData.append("file", files[0]);
        agentStore.addMessage({
          role: "user",
          content: `[快捷检测] ZIP: ${files[0].name}`,
        });
      } else {
        // 多张图片
        files.forEach((f) => formData.append("files", f));
        const imagePreviews = files.map((f) => URL.createObjectURL(f));
        agentStore.addMessage({
          role: "user",
          content: `[快捷检测] ${files.length} 张图片`,
          images: imagePreviews,
        });
      }

      agentStore.addMessage({
        role: "assistant",
        content: "正在批量检测中...",
        loading: true,
      });

      try {
        const apiCall = isZip ? detectZip(formData) : detectBatch(formData);
        const result = await apiCall;
        const lastMsg = agentStore.messages[agentStore.messages.length - 1];

        // 检查是否有错误
        if (result.error) {
          lastMsg.content = `批量检测失败：${result.error}`;
          lastMsg.loading = false;
          lastMsg.error = true;
          return;
        }

        const totalObjects = result.total_objects ?? 0;
        lastMsg.content = `批量检测完成！共 ${totalObjects} 个目标。`;
        lastMsg.loading = false;
        lastMsg.detectionResult = result;
        // 快捷检测已在后端落库，刷新侧栏使新会话出现在历史列表。
        refreshSessions();
        console.log("[批量检测结果]", result);
      } catch (err) {
        console.error("[批量检测异常]", err);
        const lastMsg = agentStore.messages[agentStore.messages.length - 1];
        lastMsg.content = `批量检测失败：${err.message || err}`;
        lastMsg.loading = false;
        lastMsg.error = true;
      }
    };
    input.click();
  }
}

/**
 * 视频检测流程：
 * 1. 用户点击 "🎬 视频" 按钮
 * 2. 弹出文件选择框（限制视频格式）
 * 3. 选择视频后，上传到后端
 * 4. 后端返回 task_id，前端开始轮询进度
 * 5. 处理完成后，展示关键帧结果卡片
 */
const VIDEO_POLL_INTERVAL = 1500;
const VIDEO_POLL_TIMEOUT = 10 * 60 * 1000;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function ensureChatSession() {
  if (!agentStore.currentSessionId) {
    agentStore.setCurrentSessionId(crypto.randomUUID());
  }
  return agentStore.currentSessionId;
}

/** 专家 Agent 显示名 */
function agentLabel(agent) {
  return AGENT_NAME_MAP[agent] || agent;
}

/** 相关度展示：向量检索为 0-1 相似度，词法检索为整数得分 */
function formatScore(score, mode) {
  if (mode === "pgvector") {
    return `${Math.round(Number(score) * 100)}%`;
  }
  return `${score} 分`;
}

/** 轮询视频检测进度，完成后将结果写回对应消息 */
async function pollVideoProgress(taskId, resultMessage) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < VIDEO_POLL_TIMEOUT) {
    const statusResult = await getVideoStatus(taskId);

    if (statusResult.status === "completed") {
      const result = statusResult.result || statusResult;
      resultMessage.content =
        statusResult.message ||
        `视频检测完成！共发现 ${result.total_objects ?? 0} 个目标。`;
      resultMessage.loading = false;
      resultMessage.detectionResult = {
        ...result,
        type: "video",
      };
      return;
    }

    if (statusResult.status === "failed") {
      throw new Error(
        statusResult.message || statusResult.error || "视频处理失败",
      );
    }

    const progress = Number(statusResult.progress || 0);
    resultMessage.content = progress
      ? `视频处理中... ${progress}%`
      : statusResult.message || "视频处理中...";

    await wait(VIDEO_POLL_INTERVAL);
  }

  throw new Error("视频处理超时，请稍后在历史记录中查看结果");
}

async function handleVideoDetect() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".mp4,.avi,.mov,.mkv,.wmv,.flv,video/*";
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // 校验文件大小（50MB）
    const maxSize = 50 * 1024 * 1024;
    if (file.size > maxSize) {
      ElMessage.warning("视频文件不能超过 50MB");
      return;
    }

    // 创建视频的 Blob URL 用于预览
    const videoUrl = URL.createObjectURL(file);

    // 添加用户消息
    agentStore.addMessage({
      role: "user",
      content: `[视频检测] ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)}MB)`,
      videoUrl,
    });

    // 添加加载占位
    const resultMessageIndex = agentStore.messages.length;
    agentStore.addMessage({
      role: "assistant",
      content: "正在上传视频...",
      loading: true,
    });
    const resultMessage = agentStore.messages[resultMessageIndex];

    // 上传视频
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", ensureChatSession());

    try {
      const uploadResult = await detectVideo(formData);
      const taskId = uploadResult.task_id;

      // 更新加载消息
      if (!taskId) {
        throw new Error("后端未返回视频任务 ID");
      }

      resultMessage.content = "视频已上传，正在处理中...";

      // 开始轮询进度
      await pollVideoProgress(taskId, resultMessage);
      // 视频检测完成后已在后端落库，刷新侧栏。
      refreshSessions();
    } catch (err) {
      console.error("[视频检测失败]", err);
      resultMessage.content = `视频检测失败：${err.message || err}`;
      resultMessage.loading = false;
      resultMessage.error = true;
    }
  };
  input.click();
}

function openCameraDetection() {
  router.push("/detection");
}

const WELCOME_MESSAGE = {
  role: "assistant",
  content:
    "你好！我是 RSOD 目标检测智能体助手。\n\n你可以：\n- 上传一张图片，让我帮你检测目标\n- 使用下方的快捷按钮直接触发检测\n- 用自然语言描述你的需求\n\n试试发一张图片给我吧！",
};

/** 会话为空时展示欢迎语。 */
function showWelcomeIfEmpty() {
  if (agentStore.messages.length === 0) {
    agentStore.addMessage({ ...WELCOME_MESSAGE });
  }
}

/** 拉取历史会话列表，失败静默（侧栏为空不影响对话）。 */
async function refreshSessions() {
  try {
    await agentStore.fetchSessions();
  } catch (err) {
    console.error("[会话列表加载失败]", err);
  }
}

watch(
  () => agentStore.currentSessionId,
  (sessionId) => {
    if (!sessionId && agentStore.messages.length === 0) {
      showWelcomeIfEmpty();
      nextTick(scrollToBottom);
    }
  },
);

onMounted(async () => {
  await refreshSessions();
  // 刷新后若有已持久化的当前会话，从后端恢复其消息历史
  if (agentStore.currentSessionId && agentStore.messages.length === 0) {
    try {
      await agentStore.loadSession(agentStore.currentSessionId);
    } catch (err) {
      // 会话可能已被删除或失效，回退为新对话
      console.error("[恢复会话失败]", err);
      agentStore.setCurrentSessionId(null);
    }
  }
  showWelcomeIfEmpty();
  scrollToBottom();
});
</script>

<style lang="scss" scoped>
.chat-page {
  display: flex;
  flex-direction: row;
  height: 100%;
  background: #fff;
}

/* ── 对话主区域 ── */
.chat-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  height: 100%;
  font-size: 15px;
}

/* ── 消息列表 ── */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.message-item {
  display: flex;
  margin-bottom: 16px;

  &.message-user {
    justify-content: flex-end;
  }

  &.message-assistant {
    justify-content: flex-start;
  }
}

.message-bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 18px;
  font-size: 15px;
  line-height: 1.55;
  word-break: break-word;
}

.user-bubble {
  background: #f0f0f0;
  color: #1f2328;
}

.assistant-bubble {
  max-width: min(780px, 100%);
  padding: 0;
  color: #1f2328;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.message-content {
  white-space: pre-wrap;
}

.markdown-body {
  /* markdown 渲染后的 HTML 样式 */
  h1,
  h2,
  h3 {
    margin-top: 8px;
    margin-bottom: 4px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
  }
  th,
  td {
    border: 1px solid #e0e0e0;
    padding: 4px 8px;
  }
  code {
    background: #f0f0f0;
    padding: 2px 4px;
    border-radius: 3px;
  }
}

.typing-indicator {
  display: flex;
  gap: 4px;

  span {
    width: 6px;
    height: 6px;
    background: #999;
    border-radius: 50%;
    animation: typing 1.2s infinite;
  }

  span:nth-child(2) {
    animation-delay: 0.2s;
  }
  span:nth-child(3) {
    animation-delay: 0.4s;
  }
}

/* ── 输入区域 ── */
.composer-wrapper {
  flex-shrink: 0;
  padding: 16px 24px 20px;
  background: white;
  border-top: 1px solid #ebeef5;
}

.input-area {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: min(1480px, 100%);
  min-height: 132px;
  margin: 0 auto;
  padding: 18px 22px 16px;
  background: white;
  border: 1px solid #b8d8ff;
  border-radius: 28px;
  box-shadow: 0 8px 28px rgba(64, 158, 255, 0.08);

  .el-textarea {
    flex: 1;
  }

  :deep(.el-textarea__inner) {
    min-height: 54px !important;
    padding: 0;
    font-size: 15px;
    line-height: 1.6;
    background: transparent;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }

  .el-button {
    border-radius: 999px;
  }
}

.selected-files,
.composer-actions,
.quick-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.selected-files {
  flex-wrap: wrap;
}

.composer-actions {
  justify-content: space-between;
}

.quick-actions {
  flex-wrap: wrap;
  min-width: 0;
}

.attach-btn {
  flex-shrink: 0;
}

.send-btn {
  flex-shrink: 0;
  min-width: 76px;
}

/* ── 附件预览 ── */
.message-attachment {
  margin-top: 8px;

  img {
    max-width: 200px;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
  }
}

/* ── 多图附件网格 ── */
.message-attachments-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(80px, 1fr));
  gap: 8px;
  margin-top: 8px;

  img {
    width: 100%;
    height: 80px;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid #e0e0e0;
  }
}

.message-video {
  margin-top: 8px;

  video {
    display: block;
    width: min(420px, 100%);
    max-height: 260px;
    background: #000;
    border-radius: 8px;
  }
}

.message-files {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
  font-size: 13px;
}

/* ── 专家路由与工具调用链 ── */
.agent-route {
  margin-bottom: 6px;
}

.tool-chain {
  margin-top: 10px;
  padding: 8px 10px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-chain-step {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-chain-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
}

.tool-chain-index {
  width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 50%;
  background: #e8e8e8;
  color: #666;
  font-size: 11px;
  flex-shrink: 0;
}

.tool-chain-status {
  flex-shrink: 0;

  &.status-running {
    color: #409eff;
  }
  &.status-done {
    color: #67c23a;
  }
  &.status-error {
    color: #f56c6c;
  }
}

.tool-chain-summary {
  color: #888;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 知识库检索来源 ── */
.knowledge-sources {
  margin-left: 26px;
  font-size: 12px;
}

.knowledge-fallback {
  color: #e6a23c;
  margin-bottom: 4px;
}

.knowledge-collapse {
  border: none;

  :deep(.el-collapse-item__header) {
    font-size: 12px;
    height: 32px;
    line-height: 32px;
    color: #555;
  }

  :deep(.el-collapse-item__content) {
    padding-bottom: 8px;
  }
}

.knowledge-snippet {
  white-space: pre-wrap;
  font-size: 12px;
  color: #666;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  max-height: 200px;
  overflow-y: auto;
}

.knowledge-empty {
  color: #999;
}

@keyframes typing {
  0%,
  60%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-4px);
  }
}
</style>
