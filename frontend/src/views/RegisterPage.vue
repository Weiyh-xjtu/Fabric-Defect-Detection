<template>
  <div class="register-page">
    <div class="register-card">
      <div class="register-header">
        <span class="register-mark">FIRESIGHT</span>
        <h2>创建账号</h2>
        <p>注册后即可进入布面缺陷检测工作台</p>
      </div>

      <el-form
        ref="formRef"
        :model="registerForm"
        :rules="registerRules"
        label-width="0"
        size="large"
        @submit.prevent="handleRegister"
      >
        <el-form-item prop="username">
          <el-input
            v-model="registerForm.username"
            placeholder="请输入用户名"
            prefix-icon="User"
          />
        </el-form-item>

        <el-form-item prop="email">
          <el-input
            v-model="registerForm.email"
            placeholder="请输入邮箱"
            prefix-icon="Message"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="registerForm.password"
            type="password"
            placeholder="请输入密码（至少 6 位）"
            prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item prop="confirmPassword">
          <el-input
            v-model="registerForm.confirmPassword"
            type="password"
            placeholder="请确认密码"
            prefix-icon="Lock"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            class="register-btn"
            :loading="loading"
            @click="handleRegister"
          >
            注 册
          </el-button>
        </el-form-item>
      </el-form>

      <div class="register-footer">
        <span>已有账号？</span>
        <router-link to="/login">立即登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { registerApi } from '@/api/auth'

const router = useRouter()
const formRef = ref(null)
const loading = ref(false)

/** 注册表单数据 */
const registerForm = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

/** 确认密码验证器 */
const validateConfirmPassword = (rule, value, callback) => {
  if (value !== registerForm.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

/** 表单验证规则 */
const registerRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名长度为 3-50 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 个字符', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' },
  ],
}

/** 处理注册 */
async function handleRegister() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await registerApi({
      username: registerForm.username,
      email: registerForm.email,
      password: registerForm.password,
    })

    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } catch {
    // 错误已在 Axios 拦截器中统一处理
  } finally {
    loading.value = false
  }
}
</script>

<style lang="scss" scoped>
.register-page {
  position: relative;
  width: 100%;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(120% 90% at 18% 0%, rgba(223, 107, 78, 0.12) 0%, transparent 44%),
    radial-gradient(120% 110% at 100% 100%, rgba(111, 127, 155, 0.16) 0%, transparent 54%),
    linear-gradient(135deg, #fbfcfe 0%, #f1f4f8 100%);
  @include weave-grid(rgba(47, 58, 79, 0.035), 26px);
}

.register-card {
  position: relative;
  z-index: 1;
  width: 420px;
  padding: 40px;
  background: #fff;
  border-radius: $border-radius-lg;
  box-shadow: $shadow-lg;
}

.register-header {
  text-align: center;
  margin-bottom: 32px;

  .register-mark {
    font-family: $font-display;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.3em;
    color: $signal-orange;
  }

  h2 {
    margin-top: 10px;
    font-family: $font-display;
    font-size: 22px;
    color: $text-primary;
    margin-bottom: 8px;
  }

  p {
    font-size: 13px;
    color: $text-secondary;
  }
}

.register-btn {
  width: 100%;
}

.register-footer {
  text-align: center;
  font-size: 13px;
  color: $text-secondary;

  a {
    color: $primary-color;
    margin-left: 4px;

    &:hover {
      text-decoration: underline;
    }
  }
}
</style>
