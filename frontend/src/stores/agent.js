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
  updateChatSessionTitle,
} from '@/api/chat'
import { parseToolResult, QUERY_TOOLS } from '@/utils/toolChain'

const CURRENT_SESSION_KEY = 'rsod_chat_current_session'

/** 后端存储的一条消息 → 前端渲染用的消息对象。 */
function normalizeHistoryMessage(item) {
  const message = {
    role: item.role,
    content: item.content || '',
  }
  if (item.role === 'user') {
    restoreUserAttachments(message, item.attachments)
  } else if (item.role === 'assistant') {
    if (item.agent_used) {
      // 并行历史消息 agent_used 为逗号连接的多专家名
      message.agents = item.agent_used
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean)
      message.agent = message.agents[0]
    }
    // 还原工具调用链摘要。
    const chain = buildToolChainFromHistory(item)
    if (chain.length) message.toolChain = chain
    // 还原检测结果卡片：统计信息来自 tool_result（已剥离 base64），
    // 标注图/视频用后端换签的 MinIO 短期 URL 还原。并行一轮可能多张卡片。
    const detections = buildDetectionsFromHistory(item)
    if (detections.length) {
      message.detectionResults = detections
      message.detectionResult = detections[0] // 兼容单结果渲染回退
    }
  }
  return message
}

/** 把用户消息的 MinIO 原始附件 URL 还原为 ChatPage 现有渲染字段。 */
function restoreUserAttachments(message, attachments) {
  const originals = (Array.isArray(attachments) ? attachments : []).filter(
    (item) => item?.source === 'user' && item.url,
  )
  const images = originals.filter((item) => item.type === 'image')
  if (images.length === 1) {
    message.image = images[0].filename || 'image'
    message.imagePreview = images[0].url
  } else if (images.length > 1) {
    message.images = images.map((item) => item.url)
  }

  const video = originals.find((item) => item.type === 'video')
  if (video) message.videoUrl = video.url

  const files = originals.filter(
    (item) => !['image', 'video'].includes(item.type),
  )
  if (files.length) {
    message.fileAttachments = files.map(
      (item) => item.filename || '附件',
    )
  }
}

/** 解析历史消息的 tool_result 字段为 [{tool, result}] 数组。 */
function parseHistoryToolResults(item) {
  if (!item.tool_result) return []
  try {
    const results = JSON.parse(item.tool_result)
    return Array.isArray(results) ? results : []
  } catch {
    return []
  }
}

/**
 * 从数据库的 tool_calls / tool_result 字段重建只读的工具调用链。
 *
 * 复用 parseToolResult 还原每步的摘要、失败状态与知识库检索片段
 * （step.knowledge），保证刷新后知识库来源卡片不丢失。
 */
function buildToolChainFromHistory(item) {
  const calls = Array.isArray(item.tool_calls) ? item.tool_calls : []
  const results = parseHistoryToolResults(item)
  const consumed = new Set()

  return calls.map((call) => {
    const tool = call.tool || call.name || 'unknown'
    const step = { tool, status: 'done', summary: '' }
    if (call.agent) step.agent = call.agent // 并行历史还原步骤的专家归属徽标
    // 按顺序匹配同名工具的首个未消费结果，兼容同一工具被多次调用。
    const idx = results.findIndex(
      (entry, i) => !consumed.has(i) && entry.tool === tool,
    )
    if (idx !== -1) {
      consumed.add(idx)
      const raw = results[idx].result
      const info = parseToolResult(
        tool,
        typeof raw === 'string' ? raw : JSON.stringify(raw),
      )
      step.status = info.error ? 'error' : 'done'
      step.summary = info.summary
      if (info.knowledge) step.knowledge = info.knowledge
      if (info.query) step.query = info.query
    }
    return step
  })
}

/** 解析历史消息的 tool_result，取出全部检测结果对象（记住各自来源工具）。 */
function parseHistoryDetections(item) {
  const found = []
  for (const entry of parseHistoryToolResults(item)) {
    // 分析类查询工具的统计结果也带 total_objects，但不是检测结果卡片
    if (QUERY_TOOLS.has(entry.tool)) continue
    let result
    try {
      result = typeof entry.result === 'string' ? JSON.parse(entry.result) : entry.result
    } catch {
      continue
    }
    if (result && typeof result === 'object' && 'total_objects' in result) {
      found.push({ tool: entry.tool, detection: result })
    }
  }
  return found
}

/**
 * 重建检测结果卡片数据：合并 tool_result 的统计信息与 attachments 的 MinIO URL。
 *
 * 后端 attachments 已把 object_name 实时换签为短期 URL，且每条引用带来源 tool：
 *   - type=image  → 单图 annotated_image_url
 *   - type=images → 批量，按 image_path 匹配回每张图的 annotated_image_url
 *   - type=video  → annotated_video_url
 * 并行一轮可能有多个检测结果，按 tool + 顺序（consumed-set）逐一匹配对应引用，
 * 避免多张卡片抢用同一 URL。返回卡片数组（无检测结果时为空数组）。
 */
function buildDetectionsFromHistory(item) {
  const found = parseHistoryDetections(item)
  if (!found.length) return []
  const attachments = Array.isArray(item.attachments) ? item.attachments : []
  const consumed = new Set()
  const takeRef = (tool, type) => {
    const idx = attachments.findIndex(
      (a, i) =>
        !consumed.has(i) &&
        a.type === type &&
        (!a.tool || !tool || a.tool === tool),
    )
    if (idx === -1) return null
    consumed.add(idx)
    return attachments[idx]
  }
  return found.map(({ tool, detection }) => {
    const video = takeRef(tool, 'video')
    if (video?.url) detection.annotated_video_url = video.url

    const single = takeRef(tool, 'image')
    if (single?.url) detection.annotated_image_url = single.url

    const batch = takeRef(tool, 'images')
    if (batch && Array.isArray(detection.annotated_images)) {
      const urlByPath = new Map(
        (batch.images || []).map((img) => [img.image_path, img.url]),
      )
      detection.annotated_images = detection.annotated_images.map((img) => ({
        ...img,
        annotated_image_url:
          urlByPath.get(img.image_path) || img.annotated_image_url || null,
      }))
    }
    return detection
  })
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

    /** 修改会话标题并同步本地会话列表。 */
    async renameSession(sessionUuid, title) {
      const updated = await updateChatSessionTitle(sessionUuid, title)
      const session = this.sessions.find(
        (item) => item.session_uuid === sessionUuid,
      )
      if (session) Object.assign(session, updated)
      return updated
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
