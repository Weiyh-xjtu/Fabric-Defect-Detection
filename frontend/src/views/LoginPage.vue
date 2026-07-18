<template>
  <div class="login-page">
    <!-- 左：品牌 hero — 验布监视器 -->
    <div class="login-hero">
      <div class="hero-inner">
        <header class="hero-top">
          <span class="hero-mark">FIRESIGHT</span>
          <span class="hero-mark-sub">FABRIC INSPECTION</span>
        </header>

        <h1 class="hero-title">布面缺陷，<em>逐帧看见</em></h1>
        <p class="hero-desc">
          基于 YOLOv11 的布匹表面缺陷智能检测平台——实时验布、批量质检、缺陷溯源与智能问答，一处完成。
        </p>

        <!-- 验布监视器视口 -->
        <div class="monitor" aria-hidden="true">
          <div class="monitor-bar">
            <span class="monitor-dot"></span>
            <span class="monitor-text">LINE-03 · 验布中</span>
            <span class="monitor-speed mono">23 m/min</span>
          </div>
          <div class="monitor-view">
            <div class="fabric-texture"></div>
            <div class="scan-line"></div>
            <div class="defect-box box-hole">
              <span class="defect-tag">破洞 0.94</span>
            </div>
            <div class="defect-box box-stain">
              <span class="defect-tag tag-gold">污渍 0.87</span>
            </div>
            <div class="defect-box box-thread">
              <span class="defect-tag tag-green">断纱 0.79</span>
            </div>
          </div>
          <div class="monitor-readout mono">
            <span>FRAME 08421</span>
            <span class="readout-alert">DEFECTS 3</span>
            <span>CONF ≥ 0.60</span>
            <span>YOLOv11-S</span>
          </div>
        </div>

        <ul class="hero-stats">
          <li><b class="mono">6</b><span>缺陷类别</span></li>
          <li><b class="mono">4</b><span>检测模式</span></li>
          <li><b class="mono">实时</b><span>逐帧标注</span></li>
          <li><b class="mono">AI</b><span>质检问答</span></li>
        </ul>
      </div>
    </div>

    <!-- 右：登录卡 -->
    <div class="login-panel">
      <div class="login-card">
        <div class="login-header">
          <span class="login-mark"><span class="login-mark-text">FIRESIGHT</span></span>
          <h2>登录工作台</h2>
          <p>输入账号，进入你的验布工位</p>
        </div>

        <el-form
          ref="formRef"
          :model="loginForm"
          :rules="loginRules"
          label-width="0"
          size="large"
          @submit.prevent="handleLogin"
        >
          <el-form-item prop="username">
            <el-input
              v-model="loginForm.username"
              placeholder="用户名"
              prefix-icon="User"
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="loginForm.password"
              type="password"
              placeholder="密码"
              prefix-icon="Lock"
              show-password
              @keyup.enter="handleLogin"
            />
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              class="login-btn"
              :loading="loading"
              @click="handleLogin"
            >
              登 录
            </el-button>
          </el-form-item>
        </el-form>

        <div class="login-footer">
          <span>还没有账号？</span>
          <router-link to="/register">立即注册</router-link>
        </div>

        <ul class="login-features">
          <li>
            <span class="feature-swatch swatch-orange"></span>
            <span>破洞、污渍、断纱等 6 类缺陷自动标注</span>
          </li>
          <li>
            <span class="feature-swatch swatch-green"></span>
            <span>图片 / 批量 ZIP / 视频 / 摄像头四种模式</span>
          </li>
          <li>
            <span class="feature-swatch swatch-gold"></span>
            <span>检测记录可查询、可统计、可对话追问</span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref(null)
const loading = ref(false)

/** 登录表单数据 */
const loginForm = reactive({
  username: '',
  password: '',
})

/** 表单验证规则 */
const loginRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名长度为 3-50 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 个字符', trigger: 'blur' },
  ],
}

/** 处理登录 */
async function handleLogin() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login({
      username: loginForm.username,
      password: loginForm.password,
    })

    ElMessage.success('登录成功')

    // 跳转到目标页面（如果有 redirect 参数）或首页
    const redirect = route.query.redirect || '/'
    router.push(redirect)
  } catch {
    // 错误已在 Axios 拦截器中统一处理
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  width: 100%;
  height: 100vh;
  display: flex;
  overflow: hidden;
}

// ── 左侧 hero — 验布监视器 ─────────────────────────
.login-hero {
  position: relative;
  flex: 1 1 56%;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 5%;
  overflow: hidden;
  background:
    radial-gradient(120% 90% at 18% 0%, rgba(223, 107, 78, 0.12) 0%, transparent 44%),
    radial-gradient(120% 110% at 100% 100%, rgba(111, 127, 155, 0.16) 0%, transparent 54%),
    linear-gradient(135deg, #fbfcfe 0%, #f1f4f8 100%);
  color: $text-primary;

  // 织物经纬网格覆盖整个背景
  &::before {
    content: '';
    position: absolute;
    inset: 0;
    @include weave-grid(rgba(47, 58, 79, 0.035), 30px);
    pointer-events: none;
  }
}

.hero-inner {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 540px;
}

.hero-top {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 30px;
}

.hero-mark {
  font-family: $font-display;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.34em;
  color: $text-primary;
}

.hero-mark-sub {
  font-family: $font-mono;
  font-size: 11px;
  letter-spacing: 0.24em;
  color: $signal-orange;
}

.hero-title {
  font-family: $font-display;
  font-size: 42px;
  font-weight: 600;
  line-height: 1.12;
  letter-spacing: -0.02em;

  em {
    font-style: normal;
    color: $signal-orange;
  }
}

.hero-desc {
  margin: 18px 0 30px;
  max-width: 460px;
  font-size: 14px;
  line-height: 1.75;
  color: $text-regular;
}

// ── 验布监视器 ─────────────────────────────────────
.monitor {
  border: 1px solid #e2e7f0;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 18px 42px rgba(47, 58, 79, 0.12);
  overflow: hidden;
}

.monitor-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #e2e7f0;
  background: rgba(255, 255, 255, 0.62);
}

.monitor-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: $loom-green;
  box-shadow: 0 0 0 0 rgba(107, 143, 113, 0.6);
  animation: pulse 2s ease-out infinite;
}

.monitor-text {
  font-family: $font-mono;
  font-size: 12px;
  color: $text-regular;
}

.monitor-speed {
  margin-left: auto;
  font-size: 12px;
  color: $signal-orange;
}

.monitor-view {
  position: relative;
  height: 260px;
  overflow: hidden;
  background:
    linear-gradient(115deg, #2a3352 0%, #1d2540 60%, #232c49 100%);
}

// 布面斜纹理
.fabric-texture {
  position: absolute;
  inset: 0;
  background-image:
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.03) 0 2px, transparent 2px 5px),
    repeating-linear-gradient(-45deg, rgba(0, 0, 0, 0.08) 0 2px, transparent 2px 6px);
  opacity: 0.8;
}

// 缺陷检测框（bbox）
.defect-box {
  position: absolute;
  border: 2px solid $signal-orange;
  border-radius: 2px;
  box-shadow: 0 0 12px rgba(223, 107, 78, 0.24);

  &::before,
  &::after {
    content: '';
    position: absolute;
    width: 10px;
    height: 10px;
    border: 3px solid currentColor;
  }
  &::before { top: -2px; left: -2px; border-right: 0; border-bottom: 0; }
  &::after { right: -2px; bottom: -2px; border-left: 0; border-top: 0; }
}

.box-hole {
  top: 20%;
  left: 14%;
  width: 122px;
  height: 84px;
  color: $signal-orange;
  animation: box-breathe 3.5s ease-in-out infinite;
}

.box-stain {
  top: 46%;
  left: 52%;
  width: 96px;
  height: 72px;
  color: $thread-gold;
  border-color: $thread-gold;
  box-shadow: 0 0 12px rgba(217, 164, 65, 0.3);
  animation: box-breathe 3.5s ease-in-out infinite 0.6s;
}

.box-thread {
  top: 14%;
  left: 70%;
  width: 70px;
  height: 46px;
  color: $loom-green;
  border-color: $loom-green;
  box-shadow: 0 0 12px rgba(107, 143, 113, 0.3);
  animation: box-breathe 3.5s ease-in-out infinite 1.2s;
}

.defect-tag {
  position: absolute;
  top: -22px;
  left: -2px;
  padding: 2px 7px;
  background: $signal-orange;
  color: #fff;
  font-family: $font-mono;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  border-radius: 2px;
}
.tag-gold { background: $thread-gold; color: #2a2000; }
.tag-green { background: $loom-green; }

.scan-line {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 40px;
  background: linear-gradient(180deg, rgba(223, 107, 78, 0) 0%, rgba(223, 107, 78, 0.2) 100%);
  border-bottom: 1px solid rgba(223, 107, 78, 0.62);
  animation: scan-sweep 3.4s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.monitor-readout {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  padding: 9px 14px;
  border-top: 1px solid #e2e7f0;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: $text-secondary;
  background: rgba(255, 255, 255, 0.62);
}

.readout-alert { color: $signal-orange; }

@keyframes scan-sweep {
  0% { transform: translateY(-40px); opacity: 0; }
  12% { opacity: 1; }
  88% { opacity: 1; }
  100% { transform: translateY(260px); opacity: 0; }
}

@keyframes box-breathe {
  0%, 100% { opacity: 0.7; }
  50% { opacity: 1; }
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(107, 143, 113, 0.6); }
  70% { box-shadow: 0 0 0 7px rgba(107, 143, 113, 0); }
  100% { box-shadow: 0 0 0 0 rgba(107, 143, 113, 0); }
}

.hero-stats {
  display: flex;
  gap: 44px;
  margin-top: 30px;

  li {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  b {
    font-size: 24px;
    font-weight: 600;
    color: $text-primary;
  }

  span {
    font-family: $font-mono;
    font-size: 11px;
    letter-spacing: 0.05em;
    color: $text-secondary;
  }
}

// ── 右侧登录面板 ───────────────────────────────────
.login-panel {
  flex: 0 0 clamp(400px, 40%, 500px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  background: $panel-bg;
}

.login-card {
  width: 100%;
  max-width: 348px;
}

.login-header {
  margin-bottom: 28px;

  .login-mark {
    @include bbox-corners($signal-orange, 6px, 2px);
    display: inline-flex;
    padding: 4px 9px;
  }

  .login-mark-text {
    font-family: $font-display;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.24em;
    color: $signal-orange;
  }

  h2 {
    margin: 16px 0 6px;
    font-family: $font-display;
    font-size: 25px;
    font-weight: 600;
    color: $text-primary;
  }

  p {
    font-size: 13px;
    color: $text-secondary;
  }
}

.login-btn {
  width: 100%;
}

.login-footer {
  text-align: center;
  font-size: 13px;
  color: $text-secondary;

  a {
    color: $signal-orange;
    font-weight: 600;
    margin-left: 4px;

    &:hover {
      text-decoration: underline;
    }
  }
}

.login-features {
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid #eceef3;

  li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 0;
    font-size: 13px;
    color: $text-regular;
  }
}

.feature-swatch {
  flex-shrink: 0;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}
.swatch-orange { background: $signal-orange; }
.swatch-green { background: $loom-green; }
.swatch-gold { background: $thread-gold; }

@media (max-width: 900px) {
  .login-hero { display: none; }
  .login-panel { flex: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .scan-line, .defect-box, .monitor-dot { animation: none; }
}
</style>
