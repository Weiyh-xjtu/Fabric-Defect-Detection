/**
 * Axios 请求封装
 * - 统一 baseURL 配置
 * - 请求拦截器：自动注入 JWT Token
 * - 响应拦截器：统一错误处理、Token 过期处理
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { expireAuthSession, isTokenExpired } from '@/utils/authSession'

/** 从 FastAPI 默认响应或项目统一响应中提取可读错误信息。 */
export function getApiErrorMessage(response, fallback) {
  const payload = response?.data
  const detail = payload?.detail

  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail[0]?.msg || String(detail[0])
  }
  if (Array.isArray(payload?.data) && payload.data.length > 0) {
    return String(payload.data[0])
  }
  if (typeof payload?.message === 'string' && payload.message) {
    return payload.message
  }
  return fallback
}

/** 登录和注册接口的 401 属于业务错误，不代表已有会话过期。 */
export function isAuthenticationEndpoint(url = '') {
  const pathWithoutQuery = url.split('?')[0].replace(/\/$/, '')
  return ['/auth/login', '/auth/register'].some((path) => pathWithoutQuery.endsWith(path))
}

// ── 创建 Axios 实例 ──────────────────────────────────
const request = axios.create({
  baseURL: '/api',          // 配合 Vite proxy，实际请求转发到后端
  timeout: 30000,           // 请求超时 30 秒
  // 不在此处硬编码 Content-Type：让 Axios 按请求体自动推断
  //   - 普通对象 → application/json
  //   - FormData → multipart/form-data; boundary=...（文件上传必须）
})

// ── 请求拦截器 ──────────────────────────────────────
request.interceptors.request.use(
  (config) => {
    // 从 Pinia store 获取 Token，自动注入请求头
    const userStore = useUserStore()
    if (userStore.token) {
      if (isTokenExpired(userStore.token)) {
        expireAuthSession()
      } else if (!isAuthenticationEndpoint(config.url)) {
        config.headers.Authorization = `Bearer ${userStore.token}`
      }
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// ── 响应拦截器 ──────────────────────────────────────
request.interceptors.response.use(
  (response) => {
    // 请求成功，直接返回响应数据
    return response.data
  },
  (error) => {
    const { response } = error

    if (response) {
      const requestUrl = error.config?.url || response.config?.url || ''

      if (response.status === 401 && !isAuthenticationEndpoint(requestUrl)) {
        expireAuthSession()
      } else {
        const fallbackMessages = {
          400: '请求参数错误',
          401: '用户名或密码错误',
          403: '没有权限执行此操作',
          404: '请求的资源不存在',
          422: '参数验证失败',
          500: '服务器内部错误',
        }
        ElMessage.error(
          getApiErrorMessage(
            response,
            fallbackMessages[response.status] || `请求失败 (${response.status})`,
          ),
        )
      }
    } else {
      // 网络错误或请求超时
      ElMessage.error('网络连接异常，请检查后端服务是否启动')
    }

    return Promise.reject(error)
  }
)

export default request
