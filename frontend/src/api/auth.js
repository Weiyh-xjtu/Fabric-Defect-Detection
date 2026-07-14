/**
 * 认证相关 API 接口
 */
import request from '@/utils/request'

/**
 * 用户注册
 * @param {Object} data - { username, email, password }
 */
export function registerApi(data) {
  return request.post('/auth/register', data)
}

/**
 * 用户登录
 * @param {Object} data - { username, password }
 * @returns {Promise} - { access_token, token_type, user }
 */
export function loginApi(data) {
  return request.post('/auth/login', data)
}

/** 使用 HttpOnly Refresh Cookie 续期登录会话。 */
export function refreshSessionApi() {
  return request.post('/auth/refresh', null, { skipGlobalError: true })
}

/** 清除服务端 Refresh Cookie。 */
export function logoutApi() {
  return request.post('/auth/logout', null, { skipGlobalError: true })
}

/**
 * 获取当前用户信息（需要 Token）
 */
export function getUserInfoApi() {
  return request.get('/auth/me')
}
