<template>
  <div class="login-page">
    <!-- 左：品牌 hero — 织物网格上扫描的检测框 -->
    <div class="login-hero">
      <div class="hero-weave" aria-hidden="true">
        <div class="scan-box">
          <span class="scan-label">DEFECT · 0.94</span>
        </div>
        <div class="scan-line"></div>
      </div>
      <div class="hero-copy">
        <span class="hero-mark">WEFT</span>
        <h1>布面缺陷，逐帧看见</h1>
        <p>基于 YOLOv11 的布匹表面缺陷智能检测平台。<br />实时验布、批量质检、缺陷溯源与智能问答，一处完成。</p>
        <ul class="hero-stats">
          <li><b class="mono">6</b><span>缺陷类别</span></li>
          <li><b class="mono">4</b><span>检测模式</span></li>
          <li><b class="mono">YOLOv11</b><span>检测引擎</span></li>
        </ul>
      </div>
    </div>

    <!-- 右：登录卡 -->
    <div class="login-panel">
      <div class="login-card">
        <div class="login-header">
          <span class="login-mark">WEFT</span>
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
}

// ── 左侧 hero ──────────────────────────────────────
.login-hero {
  position: relative;
  flex: 1 1 58%;
  min-width: 0;
  display: flex;
  align-items: flex-end;
  padding: 56px;
  overflow: hidden;
  background: $indigo-deep;
  color: #fff;
}

// 织物经纬网格底
.hero-weave {
  position: absolute;
  inset: 0;
  @include weave-grid(rgba(255, 255, 255, 0.06), 26px);

  &::after {
    // 靛蓝渐晕，让底部文字更清晰
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(
      120% 90% at 30% 15%,
      rgba(232, 97, 60, 0.16) 0%,
      transparent 45%
    ),
    linear-gradient(180deg, transparent 40%, rgba(15, 22, 42, 0.85) 100%);
  }
}

// 签名：悬停扫描的检测框
.scan-box {
  position: absolute;
  top: 24%;
  left: 30%;
  width: 190px;
  height: 130px;
  border: 2px solid $signal-orange;
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgba(232, 97, 60, 0.25);
  animation: box-drift 7s ease-in-out infinite;

  // bbox 角标加重
  &::before,
  &::after {
    content: '';
    position: absolute;
    width: 14px;
    height: 14px;
    border: 3px solid $signal-orange;
  }
  &::before { top: -2px; left: -2px; border-right: 0; border-bottom: 0; }
  &::after { right: -2px; bottom: -2px; border-left: 0; border-top: 0; }
}

.scan-label {
  position: absolute;
  top: -24px;
  left: -2px;
  padding: 2px 8px;
  background: $signal-orange;
  color: #fff;
  font-family: $font-mono;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-radius: 2px;
}

// 扫描线，纵向巡检
.scan-line {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(232, 97, 60, 0.9), transparent);
  animation: scan-sweep 4s linear infinite;
}

@keyframes scan-sweep {
  0% { transform: translateY(0); opacity: 0; }
  10% { opacity: 1; }
  90% { opacity: 1; }
  100% { transform: translateY(100vh); opacity: 0; }
}

@keyframes box-drift {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(60px, 40px); }
}

.hero-copy {
  position: relative;
  z-index: 1;
  max-width: 480px;
}

.hero-mark {
  font-family: $font-display;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.32em;
  color: $signal-orange;
}

.hero-copy h1 {
  margin: 14px 0 16px;
  font-family: $font-display;
  font-size: 40px;
  font-weight: 600;
  line-height: 1.15;
  letter-spacing: -0.02em;
}

.hero-copy p {
  font-size: 14px;
  line-height: 1.7;
  color: #c3cbe0;
}

.hero-stats {
  display: flex;
  gap: 40px;
  margin-top: 32px;

  li {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  b {
    font-size: 26px;
    font-weight: 600;
    color: #fff;
  }

  span {
    font-family: $font-mono;
    font-size: 11px;
    letter-spacing: 0.05em;
    color: #8b93a7;
  }
}

// ── 右侧登录面板 ───────────────────────────────────
.login-panel {
  flex: 0 0 clamp(380px, 40%, 480px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  background: $panel-bg;
}

.login-card {
  width: 100%;
  max-width: 340px;
}

.login-header {
  margin-bottom: 28px;

  .login-mark {
    font-family: $font-display;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.3em;
    color: $signal-orange;
  }

  h2 {
    margin: 10px 0 6px;
    font-family: $font-display;
    font-size: 24px;
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

@media (max-width: 860px) {
  .login-hero { display: none; }
  .login-panel { flex: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .scan-box, .scan-line { animation: none; }
}
</style>
