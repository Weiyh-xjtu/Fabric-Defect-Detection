/** 数据看板 API。 */
import request from '@/utils/request'

export function getStatistics(days = 30) {
  return request.get('/dashboard/statistics', { params: { days } })
}

export function getTrend(days = 30) {
  return request.get('/dashboard/trend', { params: { days } })
}

export function getClassDistribution(days = 30) {
  return request.get('/dashboard/class-dist', { params: { days } })
}

export function getSceneDistribution(days = 30) {
  return request.get('/dashboard/scene-dist', { params: { days } })
}

export function getTypeDistribution(days = 30) {
  return request.get('/dashboard/type-dist', { params: { days } })
}
