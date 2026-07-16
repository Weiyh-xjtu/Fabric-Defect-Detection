/**
 * 对话会话相关 API 接口
 *
 * 对应后端 /api/chat/sessions 系列接口，用于历史会话列表、
 * 单会话消息历史加载与会话删除。
 */
import request from "@/utils/request";

/**
 * 获取当前用户的会话列表
 * @returns {Promise<Array>} - [{ id, session_uuid, title, message_count, last_message_at, created_at }]
 */
export function getChatSessions() {
  return request.get("/chat/sessions");
}

/**
 * 获取指定会话的完整消息历史
 * @param {string} sessionUuid - 会话唯一标识
 * @returns {Promise} - { session, messages }
 */
export function getChatSessionHistory(sessionUuid) {
  return request.get(`/chat/sessions/${sessionUuid}`);
}

/**
 * 删除指定会话
 * @param {string} sessionUuid - 会话唯一标识
 * @returns {Promise}
 */
export function deleteChatSession(sessionUuid) {
  return request.delete(`/chat/sessions/${sessionUuid}`);
}
