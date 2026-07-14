/**
 * 前端滑动登录会话管理。
 *
 * Access Token 仍由后端校验；前端记录真实用户操作，连续闲置 30 分钟后退出。
 */
export const TOKEN_KEY = 'rsod_token'
export const USER_KEY = 'rsod_user'
export const LAST_ACTIVITY_KEY = 'rsod_last_activity'
export const LAST_REFRESH_KEY = 'rsod_last_refresh'
export const AUTH_SESSION_EXPIRED_EVENT = 'rsod:auth-session-expired'
export const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000
export const SESSION_REFRESH_INTERVAL_MS = 5 * 60 * 1000

const ACTIVITY_PERSIST_INTERVAL_MS = 5 * 1000
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart']

let inactivityTimer = null
let refreshSessionCallback = null
let refreshInFlight = null
let listenersAttached = false
let currentActivityTime = 0
let lastPersistedActivityTime = 0
let sessionInvalidated = false

function decodeBase64Url(value) {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
  return atob(padded)
}

function readTimestamp(key) {
  const value = Number(localStorage.getItem(key))
  return Number.isFinite(value) && value > 0 ? value : 0
}

function writeActivityTime(timestamp, force = false) {
  currentActivityTime = timestamp
  if (
    force
    || timestamp - lastPersistedActivityTime >= ACTIVITY_PERSIST_INTERVAL_MS
  ) {
    localStorage.setItem(LAST_ACTIVITY_KEY, String(timestamp))
    lastPersistedActivityTime = timestamp
  }
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

/** 判断 Access Token 是否已过期；过期后仍可尝试使用 Refresh Cookie 续期。 */
export function isTokenExpired(token, now = Date.now()) {
  const expirationTime = getTokenExpirationTime(token)
  return expirationTime === null || expirationTime <= now
}

/** 获取最近一次真实用户操作时间。 */
export function getLastActivityTime() {
  return Math.max(currentActivityTime, readTimestamp(LAST_ACTIVITY_KEY))
}

/** 判断是否已经连续闲置 30 分钟。 */
export function isSessionInactive(now = Date.now()) {
  const lastActivity = getLastActivityTime()
  return lastActivity > 0 && now - lastActivity >= INACTIVITY_TIMEOUT_MS
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

  currentActivityTime = readTimestamp(LAST_ACTIVITY_KEY)
  lastPersistedActivityTime = currentActivityTime
  return { token, user }
}

/** 保存新登录会话，并从登录时刻开始计算闲置时间。 */
export function persistAuthSession(token, user) {
  const now = Date.now()
  sessionInvalidated = false
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  localStorage.setItem(LAST_REFRESH_KEY, String(now))
  writeActivityTime(now, true)
}

/** 保存续期后的 Access Token，但不把后台续期本身计为用户操作。 */
export function persistRefreshedAuthSession(token, user) {
  sessionInvalidated = false
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  localStorage.setItem(LAST_REFRESH_KEY, String(Date.now()))
}

function cancelInactivityTimer() {
  if (inactivityTimer !== null) {
    clearTimeout(inactivityTimer)
    inactivityTimer = null
  }
}

function detachActivityListeners() {
  if (!listenersAttached) return

  for (const eventName of ACTIVITY_EVENTS) {
    window.removeEventListener(eventName, handleUserActivity)
  }
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  listenersAttached = false
}

/** 停止闲置监控。 */
export function stopAuthSessionMonitoring() {
  cancelInactivityTimer()
  detachActivityListeners()
  refreshSessionCallback = null
  refreshInFlight = null
}

function clearStoredSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  localStorage.removeItem(LAST_ACTIVITY_KEY)
  localStorage.removeItem(LAST_REFRESH_KEY)
  currentActivityTime = 0
  lastPersistedActivityTime = 0
}

/** 清除会话，用于用户主动退出。 */
export function clearAuthSession() {
  sessionInvalidated = true
  stopAuthSessionMonitoring()
  clearStoredSession()
}

/** 将当前会话标记为超时，并通知应用统一跳转登录页。 */
export function expireAuthSession(options = {}) {
  if (sessionInvalidated) return

  sessionInvalidated = true
  stopAuthSessionMonitoring()
  clearStoredSession()

  window.dispatchEvent(
    new CustomEvent(AUTH_SESSION_EXPIRED_EVENT, {
      detail: {
        notify: options.notify !== false,
        redirect: options.redirect !== false,
      },
    }),
  )
}

/** 读取仍处于活跃期的本地 Token。 */
export function getValidStoredToken(options = {}) {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  if (token && isSessionInactive()) {
    expireAuthSession(options)
    return ''
  }
  return token
}

function scheduleInactivityLogout() {
  cancelInactivityTimer()
  const lastActivity = getLastActivityTime()
  if (!lastActivity) return

  const remaining = lastActivity + INACTIVITY_TIMEOUT_MS - Date.now()
  if (remaining <= 0) {
    expireAuthSession()
    return
  }

  inactivityTimer = setTimeout(() => {
    if (isSessionInactive()) {
      expireAuthSession()
    } else {
      // 其他标签页可能更新了共享的最近操作时间。
      scheduleInactivityLogout()
    }
  }, remaining)
}

async function requestSessionRefresh() {
  if (
    !refreshSessionCallback
    || refreshInFlight
    || isSessionInactive()
  ) {
    return refreshInFlight
  }

  refreshInFlight = Promise.resolve(refreshSessionCallback())
    .catch(() => null)
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

function handleUserActivity() {
  const now = Date.now()
  if (isSessionInactive(now)) {
    expireAuthSession()
    return
  }

  writeActivityTime(now)
  scheduleInactivityLogout()

  const lastRefresh = readTimestamp(LAST_REFRESH_KEY)
  if (now - lastRefresh >= SESSION_REFRESH_INTERVAL_MS) {
    void requestSessionRefresh()
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === 'visible') {
    handleUserActivity()
  }
}

function attachActivityListeners() {
  if (listenersAttached) return

  for (const eventName of ACTIVITY_EVENTS) {
    window.addEventListener(eventName, handleUserActivity, { passive: true })
  }
  document.addEventListener('visibilitychange', handleVisibilityChange)
  listenersAttached = true
}

/** 启动闲置计时，并在用户持续操作时周期性刷新 Access Token。 */
export function startAuthSessionMonitoring(token, refreshCallback) {
  if (!token) return

  refreshSessionCallback = refreshCallback
  const now = Date.now()
  if (!getLastActivityTime()) {
    writeActivityTime(now, true)
  }
  if (isSessionInactive(now)) {
    expireAuthSession()
    return
  }

  attachActivityListeners()
  scheduleInactivityLogout()

  if (isTokenExpired(token)) {
    void requestSessionRefresh()
  }
}
