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

export function archiveModel(modelVersionId) {
  return request.post(`/models/${modelVersionId}/archive`)
}

export function unarchiveModel(modelVersionId) {
  return request.post(`/models/${modelVersionId}/unarchive`)
}

export function backupModel(modelVersionId) {
  return request.post(`/models/${modelVersionId}/backup`, null, { timeout: 180000 })
}

export function restoreModel(modelVersionId) {
  return request.post(`/models/${modelVersionId}/restore`, null, { timeout: 180000 })
}

export function deleteModel(modelVersionId) {
  return request.delete(`/models/${modelVersionId}`)
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
