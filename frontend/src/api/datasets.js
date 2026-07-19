/** 数据集管理 API。 */
import request from '@/utils/request'

/** 获取数据集列表（含归属场景与类别中英文名）。 */
export function getDatasets() {
  return request.get('/datasets')
}

/**
 * 修改数据集显示名与类别中文名（双写 data.yaml 与场景表）。
 * 英文类别名已锁定，不在此接口的可改范围。
 */
export function updateDatasetNames(name, { displayName, classNamesCn } = {}) {
  return request.put(`/datasets/${name}/names`, {
    display_name: displayName || null,
    class_names_cn: classNamesCn || {},
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
 * 确认上传（第二段：落盘并登记场景）。
 * classNames 是英文类别名唯一可修改的时机，提交后锁定。
 */
export function commitDatasetUpload(uploadId, payload) {
  return request.post(`/datasets/upload/${uploadId}/commit`, {
    scene_name: payload.sceneName,
    display_name: payload.displayName,
    category: payload.category || 'industry',
    class_names: payload.classNames,
    class_names_cn: payload.classNamesCn || {},
    description: payload.description || null,
    overwrite_classes: payload.overwriteClasses || false,
  }, { timeout: 600000 })
}

/** 数据集体检评估；force 为 true 时忽略缓存重新体检。 */
export function evaluateDataset(name, { force = false } = {}) {
  return request.post(`/datasets/${name}/evaluate`, null, {
    params: force ? { force: true } : {},
    timeout: 300000,
  })
}
