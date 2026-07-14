/**
 * 前端认证会话管理。
 *
 * JWT 的签名仍由后端校验；前端只读取 exp，用于及时清理过期状态和改善交互。
 */
export const TOKEN_KEY = 'rsod_token'
export const USER_KEY = 'rsod_user'
export const AUTH_SESSION_EXPIRED_EVENT = 'rsod:auth-session-expired'

const MAX_TIMEOUT_MS = 2_147_483_647

let expirationTimer = null
let sessionInvalidated = false

function decodeBase64Url(value) {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
  return atob(padded)
}

/** 解析 JWT payload；Token 无效时返回 null。 */
export function decodeAccessTokenPayload(token) {
  if (typeof token !== 'string') return null

  const segments = token.split('.')
  if (segments.length !== 3) return null

  try {
    return JSON.parse(decodeBase64Url(segments[1]))
  } catch {
    return null
  }
}

/** 获取 JWT 的过期时间戳（毫秒）。 */
export function getTokenExpirationTime(token) {
  const payload = decodeAccessTokenPayload(token)
  const expiration = Number(payload?.exp)
  return Number.isFinite(expiration) ? expiration * 1000 : null
}

/** 判断 JWT 是否已经过期；无法解析的 Token 视为无效。 */
export function isTokenExpired(token, now = Date.now()) {
  const expirationTime = getTokenExpirationTime(token)
  return expirationTime === null || expirationTime <= now
}

/** 读取本地保存的认证信息。 */
export function getStoredAuthSession() {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  let user = null

  try {
    user = JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    localStorage.removeItem(USER_KEY)
  }

  return { token, user }
}

/** 保存新的登录会话。 */
export function persistAuthSession(token, user) {
  sessionInvalidated = false
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 取消前端过期定时器。 */
export function cancelAuthExpiration() {
  if (expirationTimer !== null) {
    clearTimeout(expirationTimer)
    expirationTimer = null
  }
}

/** 清除会话，用于用户主动退出。 */
export function clearAuthSession() {
  sessionInvalidated = true
  cancelAuthExpiration()
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/**
 * 将当前会话标记为过期，并通知应用统一跳转登录页。
 * 重复的并发 401 只会触发一次通知。
 */
export function expireAuthSession(options = {}) {
  if (sessionInvalidated) return

  sessionInvalidated = true
  cancelAuthExpiration()
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)

  window.dispatchEvent(
    new CustomEvent(AUTH_SESSION_EXPIRED_EVENT, {
      detail: {
        notify: options.notify !== false,
        redirect: options.redirect !== false,
      },
    }),
  )
}

/** 读取仍然有效的本地 Token；过期时统一触发会话失效。 */
export function getValidStoredToken(options = {}) {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  if (token && isTokenExpired(token)) {
    expireAuthSession(options)
    return ''
  }
  return token
}

/** 根据 JWT exp 安排精确的前端登出时间。 */
export function scheduleAuthExpiration(token) {
  cancelAuthExpiration()
  if (!token) return

  const expirationTime = getTokenExpirationTime(token)
  if (expirationTime === null) {
    expireAuthSession()
    return
  }

  const scheduleNextCheck = () => {
    const remaining = expirationTime - Date.now()
    if (remaining <= 0) {
      expireAuthSession()
      return
    }

    expirationTimer = setTimeout(
      scheduleNextCheck,
      Math.min(remaining, MAX_TIMEOUT_MS),
    )
  }

  scheduleNextCheck()
}
