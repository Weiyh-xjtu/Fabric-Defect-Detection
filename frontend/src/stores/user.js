/**
 * 用户状态管理
 * 管理用户登录信息、Token、角色等
 */
import { defineStore } from 'pinia'
import { loginApi, getUserInfoApi } from '@/api/auth'
import {
  clearAuthSession,
  getStoredAuthSession,
  isTokenExpired,
  persistAuthSession,
  scheduleAuthExpiration,
  USER_KEY,
} from '@/utils/authSession'

function createInitialState() {
  return getStoredAuthSession()
}

export const useUserStore = defineStore('user', {
  state: createInitialState,

  getters: {
    /** 是否已登录 */
    isLoggedIn: (state) => !!state.token && !isTokenExpired(state.token),

    /** 用户名 */
    username: (state) => state.user?.username || '',

    /** 用户头像 */
    avatar: (state) => state.user?.avatar || '',

    /** 用户角色列表 */
    roles: (state) => state.user?.roles || [],

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
      scheduleAuthExpiration(res.access_token)

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
      } catch {
        this.logout()
      }
    },

    /** 页面刷新后恢复 JWT 的过期定时器。 */
    initializeSession() {
      if (this.token) {
        scheduleAuthExpiration(this.token)
      }
    },

    /**
     * 退出登录
     */
    logout() {
      this.token = ''
      this.user = null
      clearAuthSession()
    },
  },
})
