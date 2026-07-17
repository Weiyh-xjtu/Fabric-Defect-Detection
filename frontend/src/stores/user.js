/**
 * 用户状态管理
 * 管理用户登录信息、Token、角色等
 */
import { defineStore } from 'pinia'
import {
  getUserInfoApi,
  loginApi,
  logoutApi,
  refreshSessionApi,
} from '@/api/auth'
import {
  clearAuthSession,
  getStoredAuthSession,
  isSessionInactive,
  persistAuthSession,
  persistRefreshedAuthSession,
  startAuthSessionMonitoring,
  TOKEN_KEY,
  USER_KEY,
} from '@/utils/authSession'

function createInitialState() {
  return getStoredAuthSession()
}

export const useUserStore = defineStore('user', {
  state: createInitialState,

  getters: {
    /** 是否已登录 */
    isLoggedIn: (state) => !!state.token && !isSessionInactive(),

    /** 用户名 */
    username: (state) => state.user?.username || '',

    /** 用户头像 */
    avatar: (state) => state.user?.avatar || '',

    /** 用户角色列表 */
    roles: (state) => state.user?.roles || [],

    /** 用户权限列表 */
    permissions: (state) => state.user?.permissions || [],

    /** 是否拥有指定权限 */
    hasPermission: (state) => (permission) => (
      !!state.user?.is_superuser
      || (state.user?.permissions || []).includes(permission)
    ),

    /** 是否拥有任一指定权限 */
    hasAnyPermission: (state) => (permissions) => (
      !!state.user?.is_superuser
      || permissions.some((permission) => (
        (state.user?.permissions || []).includes(permission)
      ))
    ),

    /** 是否为管理员 */
    isSuperuser: (state) => state.user?.is_superuser || false,
  },

  actions: {
    /**
     * 用户登录
     * @param {Object} credentials - { username, password }
     */
    async login(credentials) {
      const res = await loginApi(credentials)

      this.token = res.access_token
      this.user = res.user
      persistAuthSession(res.access_token, res.user)
      startAuthSessionMonitoring(
        res.access_token,
        () => this.refreshSession(),
      )

      return res
    },

    /**
     * 获取最新用户信息
     */
    async fetchUserInfo() {
      try {
        const user = await getUserInfoApi()
        this.user = user
        localStorage.setItem(USER_KEY, JSON.stringify(user))
      } catch (error) {
        if (error.response?.status === 401) {
          this.clearSession()
        }
        throw error
      }
    },

    /** 使用 Refresh Cookie 取得新的 Access Token。 */
    async refreshSession() {
      const res = await refreshSessionApi()
      if (!this.applyRefreshedSession(res)) {
        throw new Error('当前登录会话已失效')
      }
      return res
    },

    /** 应用自动续期返回的新 Token 和用户信息。 */
    applyRefreshedSession(res) {
      if (
        !this.token
        || !localStorage.getItem(TOKEN_KEY)
        || isSessionInactive()
      ) {
        return false
      }
      this.token = res.access_token
      this.user = res.user
      persistRefreshedAuthSession(res.access_token, res.user)
      return true
    },

    /** 页面刷新后恢复闲置监控。 */
    initializeSession() {
      if (this.token) {
        startAuthSessionMonitoring(
          this.token,
          () => this.refreshSession(),
        )
      }
    },

    /** 仅清理本地登录状态。 */
    clearSession() {
      this.token = ''
      this.user = null
      clearAuthSession()
    },

    /**
     * 退出登录
     */
    logout() {
      void logoutApi().catch(() => null)
      this.clearSession()
    },
  },
})
