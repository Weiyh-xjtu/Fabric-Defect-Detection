import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AUTH_SESSION_EXPIRED_EVENT,
  decodeAccessTokenPayload,
  getTokenExpirationTime,
  isTokenExpired,
  persistAuthSession,
  scheduleAuthExpiration,
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

  it('能够解析 JWT 过期时间', () => {
    const token = createToken(2_000_000_000)

    expect(decodeAccessTokenPayload(token)).toMatchObject({ sub: '1' })
    expect(getTokenExpirationTime(token)).toBe(2_000_000_000_000)
  })

  it('能够区分有效和过期 Token', () => {
    const token = createToken(2_000)

    expect(isTokenExpired(token, 1_999_000)).toBe(false)
    expect(isTokenExpired(token, 2_000_000)).toBe(true)
    expect(isTokenExpired('invalid-token')).toBe(true)
  })

  it('到达 JWT exp 时触发统一过期事件并清理本地会话', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    const expirationSeconds = Math.floor(Date.now() / 1000) + 30
    const token = createToken(expirationSeconds)
    const listener = vi.fn()
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, listener, { once: true })

    persistAuthSession(token, { id: 1, username: 'tester' })
    scheduleAuthExpiration(token)
    vi.advanceTimersByTime(30_000)

    expect(listener).toHaveBeenCalledOnce()
    expect(localStorage.getItem('rsod_token')).toBeNull()
    expect(localStorage.getItem('rsod_user')).toBeNull()
  })
})
