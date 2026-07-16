/**
 * 智能体对话状态管理
 * 管理对话会话列表、当前会话消息等
 *
 * 持久化策略：
 *   - 消息内容以后端数据库为准（chat_sessions/chat_messages），刷新后按
 *     currentSessionId 从后端重新拉取，避免前端与后端记录不一致。
 *   - 仅把 currentSessionId 存入 localStorage，用于刷新后自动恢复上次会话。
 */
import { defineStore } from 'pinia'
import {
  deleteChatSession,
  getChatSessionHistory,
  getChatSessions,
} from '@/api/chat'

const CURRENT_SESSION_KEY = 'rsod_chat_current_session'

/** 后端存储的一条消息 → 前端渲染用的消息对象。 */
function normalizeHistoryMessage(item) {
  const message = {
    role: item.role,
    content: item.content || '',
  }
  if (item.role === 'assistant') {
    if (item.agent_used) message.agent = item.agent_used
    // 还原工具调用链摘要。
    const chain = buildToolChainFromHistory(item)
    if (chain.length) message.toolChain = chain
    // 还原检测结果卡片：统计信息来自 tool_result（已剥离 base64），
    // 标注图/视频用后端换签的 MinIO 短期 URL 还原。
    const detection = buildDetectionFromHistory(item)
    if (detection) message.detectionResult = detection
  }
  return message
}

/** 从数据库的 tool_calls / tool_result 字段重建只读的工具调用链。 */
function buildToolChainFromHistory(item) {
  const calls = Array.isArray(item.tool_calls) ? item.tool_calls : []
  return calls.map((call) => ({
    tool: call.tool || call.name || 'unknown',
    status: 'done',
    summary: '',
  }))
}

/** 解析历史消息的 tool_result，取出首个检测结果对象。 */
function parseHistoryDetection(item) {
  if (!item.tool_result) return null
  let results
  try {
    results = JSON.parse(item.tool_result)
  } catch {
    return null
  }
  if (!Array.isArray(results)) return null
  for (const entry of results) {
    let result
    try {
      result = typeof entry.result === 'string' ? JSON.parse(entry.result) : entry.result
    } catch {
      continue
    }
    if (result && typeof result === 'object' && 'total_objects' in result) {
      return result
    }
  }
  return null
}

/**
 * 重建检测结果卡片数据：合并 tool_result 的统计信息与 attachments 的 MinIO URL。
 *
 * 后端 attachments 已把 object_name 实时换签为短期 URL：
 *   - type=image  → 单图 annotated_image_url
 *   - type=images → 批量，按 image_path 匹配回每张图的 annotated_image_url
 *   - type=video  → annotated_video_url
 */
function buildDetectionFromHistory(item) {
  const detection = parseHistoryDetection(item)
  if (!detection) return null
  const attachments = Array.isArray(item.attachments) ? item.attachments : []

  const video = attachments.find((a) => a.type === 'video')
  if (video?.url) detection.annotated_video_url = video.url

  const single = attachments.find((a) => a.type === 'image')
  if (single?.url) detection.annotated_image_url = single.url

  const batch = attachments.find((a) => a.type === 'images')
  if (batch && Array.isArray(detection.annotated_images)) {
    const urlByPath = new Map(
      (batch.images || []).map((img) => [img.image_path, img.url]),
    )
    detection.annotated_images = detection.annotated_images.map((img) => ({
      ...img,
      annotated_image_url: urlByPath.get(img.image_path) || img.annotated_image_url || null,
    }))
  }
  return detection
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    // 当前会话 ID
    currentSessionId: localStorage.getItem(CURRENT_SESSION_KEY) || null,

    // 当前会话的消息列表
    messages: [],

    // 会话列表
    sessions: [],

    // 是否正在等待 AI 响应
    isLoading: false,

    // 会话列表/历史是否加载中
    isSessionsLoading: false,
    isHistoryLoading: false,

    // 中断函数（用于取消 SSE 流式请求）
    abortController: null,
  }),

  getters: {
    /** 消息数量 */
    messageCount: (state) => state.messages.length,

    /** 是否有会话 */
    hasSession: (state) => state.sessions.length > 0,
  },

  actions: {
    /** 添加一条消息 */
    addMessage(message) {
      this.messages.push(message)
    },

    /** 更新最后一条 AI 消息（用于流式追加） */
    updateLastAssistantMessage(content) {
      const lastMsg = this.messages[this.messages.length - 1]
      if (lastMsg && lastMsg.role === 'assistant') {
        lastMsg.content = content
      }
    },

    /** 设置加载状态 */
    setLoading(loading) {
      this.isLoading = loading
    },

    /** 记录当前会话 ID 并持久化，供刷新后恢复。 */
    setCurrentSessionId(sessionId) {
      this.currentSessionId = sessionId
      if (sessionId) {
        localStorage.setItem(CURRENT_SESSION_KEY, sessionId)
      } else {
        localStorage.removeItem(CURRENT_SESSION_KEY)
      }
    },

    /** 中断当前流式请求 */
    abort() {
      if (this.abortController) {
        this.abortController()
        this.abortController = null
        this.isLoading = false
      }
    },

    /** 拉取当前用户的历史会话列表。 */
    async fetchSessions() {
      this.isSessionsLoading = true
      try {
        this.sessions = await getChatSessions()
      } finally {
        this.isSessionsLoading = false
      }
      return this.sessions
    },

    /** 加载指定会话的消息历史并切换为当前会话。 */
    async loadSession(sessionUuid) {
      if (!sessionUuid) return
      this.abort()
      this.isHistoryLoading = true
      try {
        const data = await getChatSessionHistory(sessionUuid)
        this.messages = (data.messages || []).map(normalizeHistoryMessage)
        this.setCurrentSessionId(sessionUuid)
      } finally {
        this.isHistoryLoading = false
      }
      return this.messages
    },

    /** 删除会话；若删除的是当前会话则清空对话区。 */
    async removeSession(sessionUuid) {
      await deleteChatSession(sessionUuid)
      this.sessions = this.sessions.filter(
        (item) => item.session_uuid !== sessionUuid,
      )
      if (this.currentSessionId === sessionUuid) {
        this.newChat()
      }
    },

    /** 新建对话 */
    newChat() {
      this.setCurrentSessionId(null)
      this.messages = []
      this.abort()
    },

    /** 清除所有状态（退出登录时调用） */
    clear() {
      this.setCurrentSessionId(null)
      this.messages = []
      this.sessions = []
      this.abort()
    },
  },
})
