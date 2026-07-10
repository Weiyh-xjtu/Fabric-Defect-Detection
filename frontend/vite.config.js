import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => ({
  plugins: [
    vue({
      template: {
        transformAssetUrls: {
          // 以 / 开头的资源指向 public 目录，保持原始 URL，
          // 不编译成模块导入（否则 Vitest 中会解析失败）
          includeAbsolute: false,
        },
      },
    }),
  ],

  // ── 路径别名 ─────────────────────────────────────
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },

  // ── CSS 预处理器配置 ──────────────────────────────
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `@use "@/assets/styles/variables.scss" as *;`,
      },
    },
  },

  // ── 开发服务器配置 ────────────────────────────────
  server: {
    port: 5173,
    open: true, // 启动时自动打开浏览器

    // API 代理：将 /api 开头的请求转发到后端
    proxy: {
      '/api': {
        target: loadEnv(mode, __dirname, '').VITE_API_BASE_URL,
        changeOrigin: true,
      },
    },
  },

  // ── Vitest 测试配置 ───────────────────────────────
  test: {
    // 使用 happy-dom 模拟浏览器环境
    environment: 'happy-dom',
    // 全局 setup 文件
    setupFiles: ['./tests/setup.js'],
    // 测试文件匹配模式
    include: ['tests/**/*.{test,spec}.{js,ts}'],
    // 覆盖率（可选）
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
    },
  },
}))
