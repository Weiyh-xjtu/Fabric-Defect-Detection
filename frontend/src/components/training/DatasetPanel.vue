<template>
  <div class="dataset-panel">
    <!-- ── 工具栏 ── -->
    <el-card class="list-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>数据集列表</span>
          <div>
            <el-button text @click="fetchDatasets">
              <el-icon><Refresh /></el-icon>刷新
            </el-button>
            <el-button type="primary" @click="openUploadDialog">
              <el-icon><Upload /></el-icon>上传数据集
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="datasets" stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="name" label="数据集" width="140" />
        <el-table-column label="归属场景" min-width="150">
          <template #default="{ row }">
            <template v-if="row.scene">
              {{ row.scene.display_name }}
              <el-tag v-if="!row.scene.is_active" type="info" size="small">停用</el-tag>
            </template>
            <el-tag v-else type="warning" size="small">未登记</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="缺陷类别" min-width="240">
          <template #default="{ row }">
            <el-tag
              v-for="name in row.class_names"
              :key="name"
              size="small"
              class="class-tag"
            >
              {{ row.class_names_cn[name] || name }}
            </el-tag>
            <span v-if="!row.class_names.length" class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="图片数（train/val/test）" width="180">
          <template #default="{ row }">
            {{ row.image_counts.train }} / {{ row.image_counts.val }} / {{ row.image_counts.test }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.ready ? 'success' : 'info'" size="small">
              {{ row.ready ? '就绪' : '未就绪' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.scene"
              size="small"
              text
              type="warning"
              :disabled="!row.ready"
              @click="openRegisterDialog(row)"
            >
              登记
            </el-button>
            <el-button size="small" text type="primary" :disabled="!row.ready" @click="openEditDialog(row)">
              编辑名称
            </el-button>
            <el-button size="small" text type="primary" :disabled="!row.ready" @click="openEvaluate(row)">
              评估
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ── 编辑名称对话框：已登记仅中文名可改，未登记全部可改 ── -->
    <el-dialog v-model="editVisible" title="编辑名称" width="560px">
      <el-alert
        v-if="!editForm.sceneSynced"
        title="该数据集未登记场景，尚未参与训练：数据集名与英文类别名均可修改（不可与已有重名）"
        type="info"
        :closable="false"
        class="stage-alert"
      />
      <el-form label-width="110px">
        <el-form-item v-if="!editForm.sceneSynced" label="数据集名">
          <el-input v-model="editForm.newName" placeholder="小写字母/数字/下划线" maxlength="50" />
        </el-form-item>
        <el-form-item v-if="editForm.sceneSynced" label="场景显示名">
          <el-input v-model="editForm.displayName" maxlength="100" />
        </el-form-item>
        <el-form-item label="类别名称">
          <div class="cn-name-rows">
            <div v-for="(name, idx) in editForm.classNames" :key="idx" class="cn-name-row">
              <template v-if="editForm.sceneSynced">
                <el-tooltip content="英文名已写入模型权重，修改需重新训练" placement="left">
                  <span class="en-name"><el-icon><Lock /></el-icon>{{ name }}</span>
                </el-tooltip>
              </template>
              <el-tooltip v-else content="未登记数据集可修改英文名，登记后锁定" placement="left">
                <el-input v-model="editForm.newClassNames[idx]" class="en-name-input" placeholder="英文名" maxlength="50" />
              </el-tooltip>
              <el-input
                v-model="editForm.classNamesCn[name]"
                placeholder="中文名（可留空）"
                maxlength="50"
              />
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- ── 登记对话框 ── -->
    <el-dialog v-model="registerVisible" title="登记为检测场景" width="480px">
      <el-alert
        title="登记后场景将出现在训练与检测流程中，英文类别名与数据集名随之锁定"
        type="warning"
        :closable="false"
        class="stage-alert"
      />
      <el-form label-width="110px">
        <el-form-item label="数据集">
          <span>{{ registerForm.name }}</span>
        </el-form-item>
        <el-form-item label="场景显示名" required>
          <el-input v-model="registerForm.displayName" placeholder="如：织物缺陷检测" maxlength="100" />
        </el-form-item>
        <el-form-item label="场景分类">
          <el-select v-model="registerForm.category">
            <el-option label="工业" value="industry" />
            <el-option label="农业" value="agriculture" />
            <el-option label="遥感" value="remote_sensing" />
            <el-option label="医疗" value="medical" />
            <el-option label="交通" value="traffic" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="registering" @click="submitRegister">确认登记</el-button>
      </template>
    </el-dialog>

    <!-- ── 上传对话框：两段式 ── -->
    <el-dialog v-model="uploadVisible" :title="uploadStep === 1 ? '上传数据集包' : '配置数据集'" width="620px" :close-on-click-modal="false">
      <!-- 第一段：选择 zip 上传解析 -->
      <template v-if="uploadStep === 1">
        <el-upload
          drag
          :auto-upload="false"
          :limit="1"
          accept=".zip"
          :on-change="handleFileChange"
          :file-list="fileList"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽 zip 包到此处，或<em>点击选择</em></div>
          <template #tip>
            <div class="el-upload__tip">
              支持：标准 YOLO（images/+labels/）、Roboflow 导出（train/valid/test）、图片标注平铺（自动 8:1:1 划分）
            </div>
          </template>
        </el-upload>
        <div class="dialog-actions">
          <el-button @click="uploadVisible = false">取消</el-button>
          <el-button type="primary" :loading="uploading" :disabled="!pendingFile" @click="submitStage">
            上传并解析
          </el-button>
        </div>
      </template>

      <!-- 第二段：配置场景与类别名后提交 -->
      <template v-else>
        <el-alert
          :title="`已解析：${stageResult.structure} 结构，${stageResult.image_count} 张图片，${commitForm.classNames.length} 个类别`"
          type="success"
          :closable="false"
          class="stage-alert"
        />
        <el-form label-width="110px">
          <el-form-item label="仅上传">
            <el-checkbox v-model="commitForm.uploadOnly">
              仅上传暂不登记场景（可先编辑数据集名与类别名，之后再登记）
            </el-checkbox>
          </el-form-item>
          <el-form-item :label="commitForm.uploadOnly ? '数据集名' : '场景标识'" required>
            <el-input v-model="commitForm.sceneName" placeholder="小写字母/数字/下划线，即数据集目录名" maxlength="50" />
          </el-form-item>
          <el-form-item v-if="!commitForm.uploadOnly" label="场景显示名" required>
            <el-input v-model="commitForm.displayName" placeholder="如：织物缺陷检测" maxlength="100" />
          </el-form-item>
          <el-form-item v-if="!commitForm.uploadOnly" label="场景分类">
            <el-select v-model="commitForm.category">
              <el-option label="工业" value="industry" />
              <el-option label="农业" value="agriculture" />
              <el-option label="遥感" value="remote_sensing" />
              <el-option label="医疗" value="medical" />
              <el-option label="交通" value="traffic" />
            </el-select>
          </el-form-item>
          <el-form-item label="类别名称">
            <div class="cn-name-rows">
              <div v-for="(name, idx) in commitForm.classNames" :key="idx" class="cn-name-row">
                <el-tooltip content="英文标签名在登记场景前可修改，登记后锁定" placement="left">
                  <el-input v-model="commitForm.classNames[idx]" class="en-name-input" placeholder="英文名" maxlength="50" />
                </el-tooltip>
                <el-input v-model="commitForm.classNamesCn[idx]" placeholder="中文名（可留空）" maxlength="50" />
              </div>
            </div>
          </el-form-item>
        </el-form>
        <div class="dialog-actions">
          <el-button @click="uploadStep = 1">上一步</el-button>
          <el-button type="primary" :loading="committing" @click="submitCommit">确认提交</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- ── 评估报告抽屉 ── -->
    <el-drawer v-model="reportVisible" :title="`数据集评估 — ${reportDataset}`" size="560px">
      <div v-loading="evaluating" class="report-body">
        <template v-if="report">
          <div class="report-meta">
            <el-tag :type="report.passed ? 'success' : 'danger'" size="small">
              {{ report.passed ? '通过' : '存在问题' }}
            </el-tag>
            <span class="muted">
              {{ report.cached ? '（缓存报告' : '（生成于' }} {{ report.generated_at }}）
            </span>
            <el-button size="small" text type="primary" @click="runEvaluate(reportDataset, true)">
              重新评估
            </el-button>
          </div>

          <div class="stat-grid">
            <div class="stat-item">
              <div class="stat-value">{{ report.summary.total_images }}</div>
              <div class="stat-label">图像总数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ report.summary.total_annotations }}</div>
              <div class="stat-label">标注目标</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ report.summary.avg_annotations_per_image }}</div>
              <div class="stat-label">平均每图标注</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ report.summary.empty_labels }}</div>
              <div class="stat-label">空标注文件</div>
            </div>
          </div>

          <h4>Split 分布</h4>
          <el-table :data="splitRows" size="small">
            <el-table-column prop="split" label="Split" width="80" />
            <el-table-column prop="images" label="图像" />
            <el-table-column prop="labels" label="标注文件" />
            <el-table-column prop="annotations" label="目标数" />
          </el-table>

          <h4>类别分布</h4>
          <el-table :data="report.class_distribution" size="small">
            <el-table-column prop="name" label="类别" min-width="120" />
            <el-table-column prop="count" label="数量" width="90" />
            <el-table-column label="占比" width="90">
              <template #default="{ row }">{{ (row.ratio * 100).toFixed(1) }}%</template>
            </el-table-column>
          </el-table>

          <template v-if="report.issues.length">
            <h4>问题</h4>
            <el-alert
              v-for="(issue, idx) in report.issues"
              :key="idx"
              :title="issue.message"
              :type="issue.level === 'error' ? 'error' : 'warning'"
              :closable="false"
              class="issue-alert"
            >
              <div v-if="issue.samples.length" class="issue-samples">
                <div v-for="s in issue.samples" :key="s">{{ s }}</div>
              </div>
            </el-alert>
          </template>

          <template v-if="report.suggestions.length">
            <h4>建议</h4>
            <ul class="suggestion-list">
              <li v-for="(s, idx) in report.suggestions" :key="idx">{{ s }}</li>
            </ul>
          </template>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Lock, Refresh, Upload, UploadFilled } from '@element-plus/icons-vue'
import {
  commitDatasetUpload,
  evaluateDataset,
  getDatasets,
  registerDataset,
  updateDatasetNames,
  uploadDataset,
} from '@/api/datasets'
import { getApiErrorMessage } from '@/utils/request'

const emit = defineEmits(['scenes-changed'])

const datasets = ref([])
const loading = ref(false)

async function fetchDatasets() {
  loading.value = true
  try {
    const result = await getDatasets()
    datasets.value = result.items || []
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

// ── 编辑名称 ──
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({
  name: '',
  newName: '',
  displayName: '',
  sceneSynced: false,
  classNames: [],
  newClassNames: [],
  classNamesCn: {},
})

function openEditDialog(row) {
  editForm.value = {
    name: row.name,
    newName: row.name,
    displayName: row.scene?.display_name || '',
    sceneSynced: !!row.scene,
    classNames: [...row.class_names],
    newClassNames: [...row.class_names],
    classNamesCn: { ...row.class_names_cn },
  }
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    const form = editForm.value
    const payload = {
      displayName: form.displayName,
      classNamesCn: form.classNamesCn,
    }
    if (!form.sceneSynced) {
      // 未登记：支持改数据集名与英文类别名；中文名映射键跟随新英文名
      const renamed = form.newClassNames.map((n) => n.trim())
      payload.classNamesCn = {}
      form.classNames.forEach((oldName, idx) => {
        const cn = form.classNamesCn[oldName]
        if (cn) payload.classNamesCn[renamed[idx]] = cn
      })
      if (form.newName.trim() !== form.name) payload.newName = form.newName.trim()
      if (renamed.some((n, idx) => n !== form.classNames[idx])) payload.newClassNames = renamed
    }
    await updateDatasetNames(form.name, payload)
    ElMessage.success('名称已更新')
    editVisible.value = false
    fetchDatasets()
    emit('scenes-changed')
  } catch {
    // 拦截器已提示
  } finally {
    saving.value = false
  }
}

// ── 登记 ──
const registerVisible = ref(false)
const registering = ref(false)
const registerForm = ref({ name: '', displayName: '', category: 'industry' })

function openRegisterDialog(row) {
  registerForm.value = { name: row.name, displayName: '', category: 'industry' }
  registerVisible.value = true
}

async function submitRegister() {
  if (!registerForm.value.displayName.trim()) {
    ElMessage.warning('请填写场景显示名')
    return
  }
  registering.value = true
  try {
    await registerDataset(registerForm.value.name, {
      displayName: registerForm.value.displayName,
      category: registerForm.value.category,
    })
    ElMessage.success('已登记为检测场景，英文类别名与数据集名已锁定')
    registerVisible.value = false
    fetchDatasets()
    emit('scenes-changed')
  } catch {
    // 拦截器已提示
  } finally {
    registering.value = false
  }
}

// ── 两段式上传 ──
const uploadVisible = ref(false)
const uploadStep = ref(1)
const uploading = ref(false)
const committing = ref(false)
const pendingFile = ref(null)
const fileList = ref([])
const stageResult = ref({})
const commitForm = ref({
  sceneName: '',
  displayName: '',
  category: 'industry',
  classNames: [],
  classNamesCn: [],
  uploadOnly: false,
})

function openUploadDialog() {
  uploadStep.value = 1
  pendingFile.value = null
  fileList.value = []
  uploadVisible.value = true
}

function handleFileChange(file) {
  pendingFile.value = file.raw
  fileList.value = [file]
}

async function submitStage() {
  uploading.value = true
  try {
    const result = await uploadDataset(pendingFile.value)
    stageResult.value = result
    commitForm.value = {
      sceneName: '',
      displayName: '',
      category: 'industry',
      classNames: [...result.class_names],
      classNamesCn: result.class_names.map((n) => result.class_names_cn?.[n] || ''),
      uploadOnly: false,
    }
    uploadStep.value = 2
  } catch {
    // 拦截器已提示
  } finally {
    uploading.value = false
  }
}

async function submitCommit(overwriteClasses = false) {
  const form = commitForm.value
  if (!form.sceneName || (!form.uploadOnly && !form.displayName)) {
    ElMessage.warning(form.uploadOnly ? '请填写数据集名' : '请填写场景标识与显示名')
    return
  }
  committing.value = true
  try {
    const classNamesCn = {}
    form.classNames.forEach((name, idx) => {
      if (form.classNamesCn[idx]) classNamesCn[name.trim()] = form.classNamesCn[idx]
    })
    const result = await commitDatasetUpload(stageResult.value.upload_id, {
      sceneName: form.sceneName,
      displayName: form.displayName,
      category: form.category,
      classNames: form.classNames.map((n) => n.trim()),
      classNamesCn,
      overwriteClasses: overwriteClasses === true,
      registerScene: !form.uploadOnly,
    })
    ElMessage.success(
      form.uploadOnly
        ? `数据集 ${result.name} 已上传（未登记），可继续编辑后登记`
        : `数据集 ${result.name} 已就绪：train ${result.split_stats.train} / val ${result.split_stats.val} / test ${result.split_stats.test}`,
    )
    uploadVisible.value = false
    fetchDatasets()
    emit('scenes-changed')
  } catch (error) {
    const message = getApiErrorMessage(error?.response, '')
    // 类别冲突时给出强制覆盖确认
    if (message.includes('类别不同')) {
      try {
        await ElMessageBox.confirm(`${message}`, '类别冲突', {
          confirmButtonText: '强制覆盖',
          cancelButtonText: '取消',
          type: 'warning',
        })
        await submitCommit(true)
      } catch {
        // 用户取消
      }
    }
  } finally {
    committing.value = false
  }
}

// ── 评估 ──
const reportVisible = ref(false)
const evaluating = ref(false)
const report = ref(null)
const reportDataset = ref('')

const splitRows = computed(() => {
  if (!report.value) return []
  return ['train', 'val', 'test'].map((split) => ({
    split,
    ...(report.value.splits[split] || { images: 0, labels: 0, annotations: 0 }),
  }))
})

function openEvaluate(row) {
  reportDataset.value = row.name
  report.value = null
  reportVisible.value = true
  runEvaluate(row.name, false)
}

async function runEvaluate(name, force) {
  evaluating.value = true
  try {
    report.value = await evaluateDataset(name, { force })
  } catch {
    // 拦截器已提示
  } finally {
    evaluating.value = false
  }
}

onMounted(fetchDatasets)

defineExpose({ fetchDatasets })
</script>

<style lang="scss" scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.class-tag {
  margin: 2px 4px 2px 0;
}

.muted {
  color: $text-secondary;
  font-size: 12px;
}

.cn-name-rows {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cn-name-row {
  display: flex;
  align-items: center;
  gap: 12px;

  .en-name {
    width: 150px;
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-family: $font-mono;
    font-size: 13px;
    color: $text-regular;
  }

  .en-name-input {
    width: 160px;
    flex-shrink: 0;
  }
}

.dialog-actions {
  margin-top: 16px;
  text-align: right;
}

.stage-alert {
  margin-bottom: 16px;
}

.report-body {
  min-height: 200px;

  h4 {
    margin: 18px 0 8px;
  }
}

.report-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.stat-item {
  border: 1px solid #e8ecf3;
  border-radius: 6px;
  padding: 10px;
  text-align: center;

  .stat-value {
    font-size: 20px;
    font-weight: 700;
    color: $text-primary;
  }

  .stat-label {
    font-size: 12px;
    color: $text-secondary;
    margin-top: 2px;
  }
}

.issue-alert {
  margin-bottom: 8px;
}

.issue-samples {
  font-family: $font-mono;
  font-size: 12px;
  max-height: 120px;
  overflow-y: auto;
}

.suggestion-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: $text-regular;

  li {
    margin-bottom: 6px;
  }
}
</style>
