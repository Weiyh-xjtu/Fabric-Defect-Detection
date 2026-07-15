/**
 * SSE 流式处理工具。
 * 支持对象参数调用，并兼容旧的 (url, body, callbacks) 调用形式。
 */

export function streamChat(optionsOrUrl, requestBody, callbacks = {}) {
  const options = typeof optionsOrUrl === "string"
    ? { ...requestBody, ...callbacks, url: optionsOrUrl }
    : (optionsOrUrl || {})
  const {
    message,
    image_path,
    attachments,
    session_id,
    onMessage,
    onThinking,
    onToolStart,
    onToolEnd,
    onTextChunk,
    onDone,
    onError,
    signal,
    url = "/api/chat/stream",
  } = options

  const controller = signal ? null : new AbortController()
  const requestSignal = signal || controller.signal
  const token = localStorage.getItem("rsod_token")
  const body = {
    message,
    session_id,
    ...(image_path ? { image_path } : {}),
    ...(attachments?.length ? { attachments } : {}),
  }

  ;(async () => {
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
        signal: requestSignal,
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      if (!response.body) throw new Error("服务器未返回 SSE 流")

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let finished = false
      const dispatch = (event) => {
        onMessage?.(event)
        switch (event.type) {
          case "thinking": onThinking?.(event); break
          case "tool_start":
          case "tool_call": onToolStart?.(event); break
          case "tool_end":
          case "tool_result": onToolEnd?.(event); break
          case "text_chunk": onTextChunk?.(event); break
          case "done": onDone?.(event); finished = true; break
          case "error": onError?.(new Error(event.content || "请求失败"), event); break
          default: break
        }
      }

      while (!finished) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue
          const data = line.slice(6).trim()
          if (data === "[DONE]") {
            finished = true
            onDone?.({ type: "done" })
            break
          }
          try { dispatch(JSON.parse(data)) } catch (_) { /* 保留不完整/非法事件的容错 */ }
        }
      }
    } catch (error) {
      if (error.name !== "AbortError") onError?.(error)
    }
  })()

  return () => {
    if (controller) controller.abort()
  }
}

export const TOOL_NAME_MAP = {
  detect_single_image: "单图检测",
  detect_batch_images: "批量检测",
  detect_zip_images_file: "ZIP 检测",
  detect_video_file: "视频检测",
  list_session_attachments: "会话附件查询",
  search_knowledge: "知识库检索",
  query_detection_statistics: "统计查询",
  query_detection_trends: "趋势查询",
  query_system_users: "用户查询",
  query_system_roles: "角色查询",
}
