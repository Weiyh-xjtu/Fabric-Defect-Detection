/** 检测历史记录 API。 */
import request from '@/utils/request'

export function getTaskList(params) {
  return request.get('/history/tasks', { params })
}

export function getTaskDetail(taskId) {
  return request.get(`/history/tasks/${taskId}`)
}

export function deleteTask(taskId) {
  return request.delete(`/history/tasks/${taskId}`)
}

export function getHistorySummary() {
  return request.get('/history/summary')
}

export function getScenes() {
  return request.get('/history/scenes')
}
