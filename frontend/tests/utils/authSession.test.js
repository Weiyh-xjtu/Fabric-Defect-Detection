import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AUTH_SESSION_EXPIRED_EVENT,
  INACTIVITY_TIMEOUT_MS,
  SESSION_REFRESH_INTERVAL_MS,
  clearAuthSession,
  decodeAccessTokenPayload,
  getTokenExpirationTime,
  isTokenExpired,
  persistAuthSession,
  startAuthSessionMonitoring,
} from '@/utils/authSession'

function createToken(expirationSeconds) {
  const encode = (value) => btoa(JSON.stringify(value))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')

  return `${encode({ alg: 'HS256' })}.${encode({ sub: '1', exp: expirationSeconds })}.signature`
}

describe('认证会话工具', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useRealTimers()
  })

  afterEach(() => {
    clearAuthSession()
    vi.useRealTimers()
  })

  it('能够解析 JWT 过期时间', () => {
    const token = createToken(2_000_000_000)

    expect(decodeAccessTokenPayload(token)).toMatchObject({ sub: '1' })
    expect(getTokenExpirationTime(token)).toBe(2_000_000_000_000)
  })

  it('能够区分有效和过期的 Access Token', () => {
    const token = createToken(2_000)

    expect(isTokenExpired(token, 1_999_000)).toBe(false)
    expect(isTokenExpired(token, 2_000_000)).toBe(true)
    expect(isTokenExpired('invalid-token')).toBe(true)
  })

  it('连续闲置 30 分钟后触发统一退出事件', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    const token = createToken(Math.floor(Date.now() / 1000) + 3600)
    const listener = vi.fn()
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, listener, { once: true })

    persistAuthSession(token, { id: 1, username: 'tester' })
    startAuthSessionMonitoring(token, vi.fn())
    vi.advanceTimersByTime(INACTIVITY_TIMEOUT_MS)

    expect(listener).toHaveBeenCalledOnce()
    expect(localStorage.getItem('rsod_token')).toBeNull()
  })

  it('真实用户操作会重置闲置计时', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    const token = createToken(Math.floor(Date.now() / 1000) + 7200)
    const listener = vi.fn()
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, listener, { once: true })

    persistAuthSession(token, { id: 1, username: 'tester' })
    startAuthSessionMonitoring(token, vi.fn())
    vi.advanceTimersByTime(20 * 60 * 1000)
    window.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(20 * 60 * 1000)

    expect(listener).not.toHaveBeenCalled()
  })

  it('持续操作时会周期性请求续期', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    const token = createToken(Math.floor(Date.now() / 1000) + 7200)
    const refreshSession = vi.fn().mockResolvedValue(undefined)

    persistAuthSession(token, { id: 1, username: 'tester' })
    startAuthSessionMonitoring(token, refreshSession)
    vi.advanceTimersByTime(SESSION_REFRESH_INTERVAL_MS)
    window.dispatchEvent(new Event('keydown'))
    await Promise.resolve()

    expect(refreshSession).toHaveBeenCalledOnce()
  })
})
