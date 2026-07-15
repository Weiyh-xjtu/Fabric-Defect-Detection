/**
 * SSE 流式处理工具（Day 11 增强版）
 *
 * 支持的事件类型：
 *   - thinking: Agent 正在思考
 *   - tool_start: 开始调用工具
 *   - tool_end: 工具调用完成
 *   - text_chunk: LLM 回复文本片段
 *   - done: 对话完成
 *   - error: 出错
 */

/**
 * 发起 SSE 流式对话请求
 * @param {Object} options
 * @param {string} options.message - 用户消息
 * @param {string} [options.image_path] - 图片路径
 * @param {string} [options.session_id] - 会话 ID
 * @param {Function} options.onThinking - thinking 事件回调
 * @param {Function} options.onToolStart - tool_start 事件回调
 * @param {Function} options.onToolEnd - tool_end 事件回调
 * @param {Function} options.onTextChunk - text_chunk 事件回调
 * @param {Function} options.onDone - done 事件回调
 * @param {Function} options.onError - error 事件回调
 * @param {AbortSignal} [options.signal] - AbortController 信号
 */
export async function streamChat(options) {
  const {
    message,
    image_path,
    session_id,
    onThinking,
    onToolStart,
    onToolEnd,
    onTextChunk,
    onDone,
    onError,
    signal,
  } = options;

  const token = localStorage.getItem("token");

  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, image_path, session_id }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // 按行解析 SSE 事件
    const lines = buffer.split("\n");
    buffer = lines.pop(); // 保留不完整的行

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;

      const data = line.slice(6).trim();
      if (data === "[DONE]") {
        return;
      }

      try {
        const event = JSON.parse(data);

        switch (event.type) {
          case "thinking":
            onThinking?.(event);
            break;
          case "tool_start":
            onToolStart?.(event);
            break;
          case "tool_end":
            onToolEnd?.(event);
            break;
          case "text_chunk":
            onTextChunk?.(event);
            break;
          case "done":
            onDone?.(event);
            break;
          case "error":
            onError?.(event);
            break;
        }
      } catch (e) {
        // JSON 解析失败，忽略
      }
    }
  }
}

/**
 * 工具名称中文映射
 */
export const TOOL_NAME_MAP = {
  detect_single_image: "单图检测",
  detect_batch_images: "批量检测",
  detect_zip_images_file: "ZIP 检测",
  detect_video_file: "视频检测",
  search_knowledge: "知识库检索",
  query_detection_stats: "统计查询",
  query_detection_history: "历史查询",
  query_user_list: "用户查询",
};