/** 数据看板 API。 */
import request from '@/utils/request'

// FastAPI 的多值查询参数要求重复键（class_name=a&class_name=b），
// 而 Axios 默认会序列化成 class_name[]=a，需显式关闭方括号索引。
const repeatKeySerializer = { indexes: null }

/**
 * 组装看板通用查询参数。
 * 传入 start/end 时按自定义时间段查询，否则回退到最近 days 天；
 * classNames 为缺陷类别数组，用于按缺陷过滤。
 */
function buildParams({ days = 30, start, end, classNames } = {}) {
  const params = {}
  if (start && end) {
    params.start_date = start
    params.end_date = end
  } else {
    params.days = days
  }
  if (Array.isArray(classNames) && classNames.length) {
    params.class_name = classNames
  }
  return params
}

export function getStatistics(options = {}) {
  return request.get('/dashboard/statistics', {
    params: buildParams(options),
    paramsSerializer: repeatKeySerializer,
  })
}

export function getTrend(options = {}) {
  return request.get('/dashboard/trend', {
    params: buildParams(options),
    paramsSerializer: repeatKeySerializer,
  })
}

export function getClassDistribution(options = {}) {
  return request.get('/dashboard/class-dist', {
    params: buildParams(options),
    paramsSerializer: repeatKeySerializer,
  })
}

export function getSceneDistribution(options = {}) {
  return request.get('/dashboard/scene-dist', {
    params: buildParams(options),
    paramsSerializer: repeatKeySerializer,
  })
}

export function getTypeDistribution(options = {}) {
  return request.get('/dashboard/type-dist', {
    params: buildParams(options),
    paramsSerializer: repeatKeySerializer,
  })
}

export function getDefectTrend(options = {}) {
  const params = buildParams(options)
  if (options.topN) params.top_n = options.topN
  return request.get('/dashboard/defect-trend', {
    params,
    paramsSerializer: repeatKeySerializer,
  })
}

export function getDefectOptions(options = {}) {
  // 缺陷下拉不受已选缺陷影响，剔除 classNames 再请求。
  const { classNames, ...rest } = options
  return request.get('/dashboard/defect-options', {
    params: buildParams(rest),
    paramsSerializer: repeatKeySerializer,
  })
}
