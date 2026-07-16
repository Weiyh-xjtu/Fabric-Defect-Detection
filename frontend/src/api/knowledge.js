/**
 * 知识库管理相关 API 接口
 *
 * 对应后端 /api/knowledge 系列接口：文件列表、上传、删除、统计与
 * 手动重建。均走共享 axios 客户端（自动携带 JWT，baseURL 已含 /api）。
 */
import request from "@/utils/request";

/**
 * 获取知识库文件列表
 * @returns {Promise} - { directory, files: [{ name, ext, size, modified_at }] }
 */
export function listKnowledgeFiles() {
  return request.get("/knowledge/files");
}

/**
 * 上传一个或多个知识库文档（pdf/md/txt）
 * @param {FormData} formData - 含 files 字段的表单数据
 * @returns {Promise} - { uploaded: [...], rebuild: <status> }
 */
export function uploadKnowledgeFiles(formData) {
  return request.post("/knowledge/files", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/**
 * 删除指定知识库文件
 * @param {string} filename - 文件名
 * @returns {Promise} - { deleted, rebuild }
 */
export function deleteKnowledgeFile(filename) {
  return request.delete(`/knowledge/files/${encodeURIComponent(filename)}`);
}

/**
 * 获取知识库统计与重建状态
 * @returns {Promise} - { documents, total_chunks, vector_chunks, mode, directory, rebuild }
 */
export function getKnowledgeStats() {
  return request.get("/knowledge/stats");
}

/**
 * 手动触发一次后台向量索引重建
 * @returns {Promise} - <rebuild status>
 */
export function rebuildKnowledge() {
  return request.post("/knowledge/rebuild");
}
