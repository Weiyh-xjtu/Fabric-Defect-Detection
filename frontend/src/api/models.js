import request from '@/utils/request'

export function getModelVersions(params = {}) {
  return request.get('/models', { params })
}

export function getCurrentModel() {
  return request.get('/models/current', { skipGlobalError: true })
}

export function activateModel(modelVersionId) {
  return request.post(`/models/${modelVersionId}/activate`)
}

export function testModel(modelVersionId, formData) {
  return request.post(`/models/${modelVersionId}/test`, formData, { timeout: 120000 })
}

export function evaluateModel(modelVersionId, payload) {
  return request.post(`/models/${modelVersionId}/evaluate`, payload)
}

export function getModelEvaluation(modelVersionId) {
  return request.get(`/models/${modelVersionId}/evaluation`)
}
