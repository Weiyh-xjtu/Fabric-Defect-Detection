/** 数据集管理 API。 */
import request from '@/utils/request'

/** 获取数据集列表（含归属场景与类别中英文名）。 */
export function getDatasets() {
  return request.get('/datasets')
}

/**
 * 修改数据集名称配置（双写 data.yaml 与场景表）。
 * 已登记数据集仅 displayName/classNamesCn 生效；
 * 未登记数据集可传 newName（改数据集名）与 newClassNames（改英文类别名）。
 */
export function updateDatasetNames(name, { displayName, classNamesCn, newName, newClassNames } = {}) {
  return request.put(`/datasets/${name}/names`, {
    display_name: displayName || null,
    class_names_cn: classNamesCn || {},
    new_name: newName || null,
    new_class_names: newClassNames || null,
  })
}

/** 把未登记数据集登记为检测场景。 */
export function registerDataset(name, { displayName, category, description } = {}) {
  return request.post(`/datasets/${name}/register`, {
    display_name: displayName,
    category: category || 'industry',
    description: description || null,
  })
}

/** 上传数据集 zip 包（第一段：暂存解析，返回 upload_id 与探测结果）。 */
export function uploadDataset(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/datasets/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000,
  })
}

/**
 * 确认上传（第二段：落盘，registerScene 为 false 时仅上传不登记场景）。
 * classNames 在提交时可修改英文类别名；登记后锁定。
 */
export function commitDatasetUpload(uploadId, payload) {
  return request.post(`/datasets/upload/${uploadId}/commit`, {
    scene_name: payload.sceneName,
    display_name: payload.displayName || '',
    category: payload.category || 'industry',
    class_names: payload.classNames,
    class_names_cn: payload.classNamesCn || {},
    description: payload.description || null,
    overwrite_classes: payload.overwriteClasses || false,
    register_scene: payload.registerScene !== false,
  }, { timeout: 600000 })
}

/** 数据集体检评估；force 为 true 时忽略缓存重新体检。 */
export function evaluateDataset(name, { force = false } = {}) {
  return request.post(`/datasets/${name}/evaluate`, null, {
    params: force ? { force: true } : {},
    timeout: 300000,
  })
}
