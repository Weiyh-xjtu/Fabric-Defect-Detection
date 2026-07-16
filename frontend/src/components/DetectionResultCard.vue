<template>
  <div class="detection-result-card">
    <div class="card-header">
      <el-icon><DataAnalysis /></el-icon>
      <span>检测结果</span>
      <el-tag size="small" type="success">
        {{ result.total_objects ?? 0 }} 个目标
      </el-tag>
    </div>

    <div class="card-body">
      <!-- 视频模式：优先播放标注视频，上传失败时展示关键帧 -->
      <div v-if="isVideo" class="video-result">
        <div class="video-info">
          <el-tag type="info">时长: {{ result.duration_seconds ?? 0 }}s</el-tag>
          <el-tag type="info">FPS: {{ result.fps ?? 0 }}</el-tag>
          <el-tag type="info">处理帧: {{ result.processed_frames ?? 0 }}</el-tag>
          <el-tag type="success">目标: {{ result.total_objects ?? 0 }}</el-tag>
        </div>

        <div v-if="result.annotated_video_url" class="video-player">
          <video :src="result.annotated_video_url" controls preload="metadata"></video>
        </div>

        <div v-else-if="thumbnailFrames.length" class="frames-fallback">
          <p class="fallback-hint">
            标注视频暂不可用，以下为关键帧预览：
          </p>
          <div class="frames-container">
            <div
              v-for="frame in thumbnailFrames"
              :key="frame.frame_index"
              class="frame-card"
              @click="previewVideoFrame(frame)"
            >
              <img
                :src="`data:image/jpeg;base64,${frame.annotated_image_base64}`"
                :alt="`帧 ${frame.frame_index}`"
              />
              <div class="frame-info">
                <span>{{ frame.timestamp }}s</span>
                <span>{{ frame.object_count }} 个目标</span>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="video-empty">暂无可预览的视频或关键帧</div>
      </div>

      <!-- 单图模式：标注图 -->
      <div class="result-image" v-if="annotatedImageSrc && !isBatch && !isVideo">
        <img
          :src="annotatedImageSrc"
          alt="检测标注图"
          @click="previewImageSrc(annotatedImageSrc)"
        />
      </div>

      <!-- 批量模式：多图展示 -->
      <div class="result-images-grid" v-if="isBatch && batchImages.length > 0">
        <div
          v-for="(img, index) in batchImages"
          :key="index"
          class="grid-image"
          @click="previewImage(img)"
        >
          <img :src="img.src" :alt="img.name" />
          <span class="image-name">{{ img.name }}</span>
        </div>
      </div>

      <!-- 统计信息 -->
      <div class="result-stats">
        <div class="stat-item">
          <span class="stat-label">推理耗时</span>
          <span class="stat-value">{{ result.inference_time || result.total_inference_time || 0 }}ms</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">检测目标</span>
          <span class="stat-value">{{ result.total_objects ?? 0 }} 个</span>
        </div>
        <div class="stat-item" v-if="isBatch">
          <span class="stat-label">图片数量</span>
          <span class="stat-value">{{ result.total_images ?? batchImages.length }} 张</span>
        </div>

        <!-- 类别统计表格 -->
        <el-table
          v-if="classCountsArray.length > 0"
          :data="classCountsArray"
          size="small"
          style="margin-top: 12px"
        >
          <el-table-column prop="className" label="类别" />
          <el-table-column prop="count" label="数量" width="80" />
        </el-table>
      </div>
    </div>

    <!-- 全屏图片预览 -->
    <el-dialog v-model="showFullImage" title="检测标注图" width="80%">
      <img
        v-if="previewSrc"
        :src="previewSrc"
        style="width: 100%"
        alt="检测标注图"
      />
    </el-dialog>
  </div>
</template>

<script setup>
/**
 * DetectionResultCard — 检测结果卡片组件
 *
 * 在对话消息中展示检测结果，包含：
 *   - 标注图预览（单图/批量多图，点击可放大）
 *   - 目标总数和推理耗时
 *   - 各类别数量统计表格
 */
import { DataAnalysis } from "@element-plus/icons-vue";
import { computed, ref } from "vue";

const props = defineProps({
  result: {
    type: Object,
    required: true,
  },
});

const showFullImage = ref(false);
const previewSrc = ref(null);

/** 判断是否为视频检测结果 */
const isVideo = computed(() => props.result.type === "video");

/** 判断是否为批量检测结果 */
const isBatch = computed(() => {
  return (
    !isVideo.value &&
    Array.isArray(props.result.annotated_images) &&
    props.result.annotated_images.length > 0
  );
});

/** 单图模式：标注图 URL（优先使用 MinIO URL，否则用 base64） */
const annotatedImageSrc = computed(() => {
  if (props.result.annotated_image_url) {
    return props.result.annotated_image_url;
  }
  if (props.result.annotated_image_base64) {
    return `data:image/jpeg;base64,${props.result.annotated_image_base64}`;
  }
  return null;
});

/** 批量模式：标注图列表（优先 MinIO URL，回退 base64） */
const batchImages = computed(() => {
  if (!isBatch.value) return [];
  return props.result.annotated_images
    .map((img) => {
      let src = null;
      if (img.annotated_image_url) {
        src = img.annotated_image_url;
      } else if (img.annotated_image_base64) {
        src = `data:image/jpeg;base64,${img.annotated_image_base64}`;
      }
      return { name: img.image_path || "image", src };
    })
    .filter((img) => img.src);
});

/** 视频模式：仅展示后端实际返回了缩略图数据的前 6 帧 */
const thumbnailFrames = computed(() => {
  if (!Array.isArray(props.result.key_frames)) return [];
  return props.result.key_frames
    .filter((frame) => frame.annotated_image_base64)
    .slice(0, 6);
});

/** 点击预览图片（批量模式） */
function previewImage(img) {
  previewSrc.value = img.src;
  showFullImage.value = true;
}

/** 点击预览图片（单图模式） */
function previewImageSrc(src) {
  previewSrc.value = src;
  showFullImage.value = true;
}

/** 点击预览视频关键帧 */
function previewVideoFrame(frame) {
  previewImageSrc(
    `data:image/jpeg;base64,${frame.annotated_image_base64}`,
  );
}

/** 类别统计转为数组（用于 el-table） */
const classCountsArray = computed(() => {
  const counts = props.result.class_counts || {};
  return Object.entries(counts).map(([className, count]) => ({
    className,
    count,
  }));
});
</script>

<style lang="scss" scoped>
.detection-result-card {
  margin-top: 12px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e0e0e0;
  font-weight: 600;
  font-size: 14px;
}

.card-body {
  display: flex;
  gap: 16px;
  padding: 12px;
}

.video-result {
  flex: 1;
  min-width: 0;
}

.video-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.video-player video {
  width: 100%;
  max-height: 360px;
  border-radius: 8px;
  background: #000;
}

.fallback-hint {
  margin: 0 0 8px;
  color: #909399;
  font-size: 12px;
}

.frames-container {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}

.frame-card {
  overflow: hidden;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity 0.2s;

  &:hover {
    opacity: 0.8;
  }

  img {
    width: 100%;
    height: 120px;
    object-fit: cover;
  }
}

.frame-info {
  display: flex;
  justify-content: space-between;
  padding: 6px 8px;
  color: #666;
  font-size: 12px;
  background: #fafafa;
}

.video-empty {
  padding: 32px 12px;
  color: #909399;
  text-align: center;
  background: #fafafa;
  border-radius: 8px;
}

.result-image {
  flex: 1;
  min-width: 0;

  img {
    width: 100%;
    max-height: 300px;
    object-fit: contain;
    border-radius: 4px;
    cursor: pointer;
    transition: opacity 0.2s;

    &:hover {
      opacity: 0.8;
    }
  }
}

.result-images-grid {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;

  .grid-image {
    text-align: center;
    cursor: pointer;

    img {
      width: 100%;
      height: 100px;
      object-fit: cover;
      border-radius: 4px;
      border: 1px solid #e0e0e0;
      transition: opacity 0.2s;

      &:hover {
        opacity: 0.8;
      }
    }

    .image-name {
      display: block;
      font-size: 11px;
      color: #909399;
      margin-top: 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

.result-stats {
  flex: 0 0 180px;

  .stat-item {
    display: flex;
    justify-content: space-between;
    margin-bottom: 8px;
    font-size: 13px;
  }

  .stat-label {
    color: #909399;
  }

  .stat-value {
    font-weight: 600;
    color: #303133;
  }
}

@media (max-width: 900px) {
  .card-body {
    flex-direction: column;
  }

  .result-stats {
    flex-basis: auto;
  }
}
</style>
