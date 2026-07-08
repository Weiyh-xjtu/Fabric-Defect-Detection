/**
 * 应用入口文件
 * - 创建 Vue 应用实例
 * - 注册全局插件（Element Plus、Router、Pinia）
 * - 挂载应用
 */
import { createApp } from 'vue'

// 全局样式
import '@/assets/styles/global.scss'

// Element Plus
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

// 核心模块
import App from './App.vue'
import router from './router'
import pinia from './stores'

// ── 创建并配置应用 ────────────────────────────────────
const app = createApp(App)

// 注册插件
app.use(pinia)                          // 状态管理
app.use(router)                         // 路由
app.use(ElementPlus, { locale: zhCn })  // UI 组件库（中文语言包）

// 挂载到 DOM
app.mount('#app')
