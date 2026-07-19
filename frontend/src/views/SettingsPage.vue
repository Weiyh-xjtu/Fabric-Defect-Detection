<template>
  <div class="settings-page">
    <div class="page-header">
      <h2>系统设置</h2>
      <p>维护个人资料、登录密码与查看系统信息</p>
    </div>

    <el-row :gutter="24" class="settings-row">
      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>个人信息</template>
          <el-form ref="profileFormRef" :model="profileForm" :rules="profileRules" label-width="80px">
            <el-form-item label="头像">
              <div class="avatar-editor">
                <el-avatar :size="88" :src="profileForm.avatar || undefined">
                  {{ profileForm.username?.charAt(0)?.toUpperCase() }}
                </el-avatar>
                <div class="avatar-actions">
                  <div>
                    <input
                      ref="avatarInputRef"
                      class="avatar-file-input"
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      @change="handleAvatarSelected"
                    />
                    <el-button
                      type="primary"
                      plain
                      :loading="avatarLoading"
                      @click="chooseAvatar"
                    >
                      {{ profileForm.avatar ? '更换头像' : '上传头像' }}
                    </el-button>
                    <el-button
                      v-if="profileForm.avatar"
                      :disabled="avatarLoading"
                      @click="removeAvatar"
                    >
                      恢复默认
                    </el-button>
                  </div>
                  <span>支持 JPG、PNG、WebP，文件不超过 5 MB</span>
                </div>
              </div>
            </el-form-item>
            <el-form-item label="用户名" prop="username">
              <el-input
                v-model="profileForm.username"
                maxlength="50"
                show-word-limit
                placeholder="请输入用户名"
              />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="profileForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="profileForm.phone" maxlength="20" placeholder="请输入手机号" />
            </el-form-item>
            <el-form-item label="注册时间">
              <el-input :model-value="formatDate(profileForm.created_at)" disabled />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="profileLoading" @click="updateProfile">保存修改</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :xl="12">
        <el-card shadow="hover">
          <template #header>修改密码</template>
          <el-form ref="passwordFormRef" :model="passwordForm" :rules="passwordRules" label-width="100px">
            <el-form-item label="当前密码" prop="old_password">
              <el-input v-model="passwordForm.old_password" type="password" show-password autocomplete="current-password" />
            </el-form-item>
            <el-form-item label="新密码" prop="new_password">
              <el-input v-model="passwordForm.new_password" type="password" show-password autocomplete="new-password" />
            </el-form-item>
            <el-form-item label="确认新密码" prop="confirm_password">
              <el-input v-model="passwordForm.confirm_password" type="password" show-password autocomplete="new-password" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="passwordLoading" @click="changePassword">修改密码</el-button>
              <el-button @click="resetPasswordForm">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="hover" class="about-card">
      <template #header>关于系统</template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="系统名称">Fabric Defect Detection Platform</el-descriptions-item>
        <el-descriptions-item label="版本号">v0.1.0</el-descriptions-item>
        <el-descriptions-item label="检测模型">YOLO11n</el-descriptions-item>
        <el-descriptions-item label="前端框架">Vue 3 + Element Plus</el-descriptions-item>
        <el-descriptions-item label="后端框架">FastAPI + SQLAlchemy</el-descriptions-item>
        <el-descriptions-item label="基础设施">PostgreSQL + Redis + MinIO</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { removeUserAvatar, uploadUserAvatar } from '@/api/user'
import { useUserStore } from '@/stores/user'
import request from '@/utils/request'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const userStore = useUserStore()
const profileFormRef = ref(null)
const passwordFormRef = ref(null)
const avatarInputRef = ref(null)
const profileLoading = ref(false)
const passwordLoading = ref(false)
const avatarLoading = ref(false)
const profileForm = reactive({
  username: '',
  email: '',
  phone: '',
  avatar: '',
  created_at: null,
})
const passwordForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const profileRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        const normalized = value?.trim() || ''
        normalized.length >= 3 && normalized.length <= 50
          ? callback()
          : callback(new Error('用户名长度必须为 3-50 个字符'))
      },
      trigger: ['blur', 'change'],
    },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效邮箱', trigger: ['blur', 'change'] },
  ],
}
const passwordRules = {
  old_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        value === passwordForm.new_password
          ? callback()
          : callback(new Error('两次输入的密码不一致'))
      },
      trigger: ['blur', 'change'],
    },
  ],
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

async function loadUserInfo() {
  try {
    const user = await request.get('/auth/me')
    profileForm.username = user.username
    profileForm.email = user.email
    profileForm.phone = user.phone || ''
    profileForm.avatar = user.avatar || ''
    profileForm.created_at = user.created_at
  } catch (error) {
    console.error('[用户信息加载失败]', error)
  }
}

function chooseAvatar() {
  avatarInputRef.value?.click()
}

async function handleAvatarSelected(event) {
  const input = event.target
  const file = input.files?.[0]
  if (!file) return

  const allowedTypes = ['image/jpeg', 'image/png', 'image/webp']
  if (!allowedTypes.includes(file.type)) {
    ElMessage.warning('头像仅支持 JPG、PNG 或 WebP 格式')
    input.value = ''
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.warning('头像图片不能超过 5 MB')
    input.value = ''
    return
  }

  avatarLoading.value = true
  try {
    const result = await uploadUserAvatar(file)
    profileForm.avatar = result.user?.avatar || ''
    await userStore.fetchUserInfo()
    profileForm.avatar = userStore.avatar || profileForm.avatar
    ElMessage.success('头像已更新')
  } catch (error) {
    console.error('[头像上传失败]', error)
  } finally {
    avatarLoading.value = false
    input.value = ''
  }
}

async function removeAvatar() {
  try {
    await ElMessageBox.confirm(
      '确定移除当前头像并恢复为用户名首字母吗？',
      '恢复默认头像',
      {
        type: 'warning',
        confirmButtonText: '恢复默认',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  avatarLoading.value = true
  try {
    await removeUserAvatar()
    profileForm.avatar = ''
    await userStore.fetchUserInfo()
    ElMessage.success('已恢复默认头像')
  } catch (error) {
    console.error('[头像移除失败]', error)
  } finally {
    avatarLoading.value = false
  }
}

async function updateProfile() {
  const valid = await profileFormRef.value.validate().catch(() => false)
  if (!valid) return
  profileLoading.value = true
  try {
    const result = await request.put('/user/profile', null, {
      params: {
        username: profileForm.username,
        email: profileForm.email,
        phone: profileForm.phone,
      },
    })
    profileForm.username = result.user.username
    profileForm.email = result.user.email
    profileForm.phone = result.user.phone || ''
    await userStore.fetchUserInfo()
    ElMessage.success('个人信息已更新')
  } catch (error) {
    console.error('[个人信息更新失败]', error)
  } finally {
    profileLoading.value = false
  }
}

async function changePassword() {
  const valid = await passwordFormRef.value.validate().catch(() => false)
  if (!valid) return
  passwordLoading.value = true
  try {
    await request.put('/user/password', null, {
      params: {
        old_password: passwordForm.old_password,
        new_password: passwordForm.new_password,
      },
    })
    ElMessage.success('密码修改成功，请重新登录')
    resetPasswordForm()
    userStore.logout()
    await router.replace('/login')
  } catch (error) {
    console.error('[密码修改失败]', error)
  } finally {
    passwordLoading.value = false
  }
}

function resetPasswordForm() {
  passwordForm.old_password = ''
  passwordForm.new_password = ''
  passwordForm.confirm_password = ''
  passwordFormRef.value?.resetFields()
}

onMounted(loadUserInfo)

defineExpose({
  avatarLoading,
  profileFormRef,
  profileForm,
  handleAvatarSelected,
  removeAvatar,
  updateProfile,
})
</script>

<style lang="scss" scoped>
.settings-page {
  padding: 20px;
}

.page-header {
  margin-bottom: 20px;
  h2 { margin: 0 0 4px; }
  p { margin: 0; color: $text-secondary; font-size: 14px; }
}
.settings-row { row-gap: 24px; }
.about-card { margin-top: 24px; }
.avatar-editor {
  display: flex;
  align-items: center;
  gap: 18px;

  .el-avatar {
    flex-shrink: 0;
    font-size: 30px;
  }
}
.avatar-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;

  span {
    color: $text-secondary;
    font-size: 12px;
    line-height: 1.5;
  }
}
.avatar-file-input { display: none; }
@media (max-width: 768px) {
  .avatar-editor { align-items: flex-start; }
  .avatar-actions > div {
    display: flex;
    flex-direction: column;
    gap: 8px;

    .el-button { margin-left: 0; }
  }
  .about-card :deep(.el-descriptions__body) { overflow-x: auto; }
}
</style>
