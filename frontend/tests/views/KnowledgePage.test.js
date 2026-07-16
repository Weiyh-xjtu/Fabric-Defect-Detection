/**
 * KnowledgePage 视图测试
 *
 * 重点验证：挂载后渲染文件列表、删除调用 deleteKnowledgeFile、
 * 重建状态文案随 stats.rebuild 渲染。API 层整体 mock，不发真实请求；
 * 使用真实 Element Plus 组件以忠实反映 title 属性与作用域插槽的行为。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

const listKnowledgeFiles = vi.fn()
const uploadKnowledgeFiles = vi.fn()
const deleteKnowledgeFile = vi.fn()
const getKnowledgeStats = vi.fn()
const rebuildKnowledge = vi.fn()

vi.mock('@/api/knowledge', () => ({
  listKnowledgeFiles: (...args) => listKnowledgeFiles(...args),
  uploadKnowledgeFiles: (...args) => uploadKnowledgeFiles(...args),
  deleteKnowledgeFile: (...args) => deleteKnowledgeFile(...args),
  getKnowledgeStats: (...args) => getKnowledgeStats(...args),
  rebuildKnowledge: (...args) => rebuildKnowledge(...args),
}))

vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus')
  return {
    ...actual,
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn(() => Promise.resolve()) },
  }
})

import KnowledgePage from '@/views/KnowledgePage.vue'
import { ElMessageBox } from 'element-plus'

function mountPage() {
  return mount(KnowledgePage, { global: { plugins: [ElementPlus] } })
}

describe('KnowledgePage', () => {
  beforeEach(() => {
    listKnowledgeFiles.mockReset()
    deleteKnowledgeFile.mockReset()
    getKnowledgeStats.mockReset()
    rebuildKnowledge.mockReset()
    ElMessageBox.confirm.mockReset().mockResolvedValue()

    listKnowledgeFiles.mockResolvedValue({
      files: [
        { name: 'guide.md', ext: '.md', size: 1024, modified_at: '2026-07-16T10:00:00' },
        { name: 'spec.pdf', ext: '.pdf', size: 2048, modified_at: '2026-07-16T11:00:00' },
      ],
    })
    getKnowledgeStats.mockResolvedValue({
      documents: 2,
      vector_chunks: 12,
      mode: 'pgvector',
      rebuild: { status: 'success', detail: null, updated_at: '2026-07-16T11:05:00' },
    })
    rebuildKnowledge.mockResolvedValue({ status: 'running', detail: null, updated_at: null })
  })

  it('挂载后加载并渲染文件列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listKnowledgeFiles).toHaveBeenCalled()
    expect(getKnowledgeStats).toHaveBeenCalled()
    expect(wrapper.text()).toContain('知识库文件（2）')
    expect(wrapper.text()).toContain('guide.md')
    expect(wrapper.text()).toContain('spec.pdf')
  })

  it('重建状态成功时渲染对应文案', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('向量索引已是最新')
    expect(wrapper.text()).toContain('向量检索')
  })

  it('点击删除会调用 deleteKnowledgeFile', async () => {
    deleteKnowledgeFile.mockResolvedValue({ deleted: 'guide.md', rebuild: { status: 'running' } })
    const wrapper = mountPage()
    await flushPromises()

    const deleteBtn = wrapper.findAll('button').find((b) => b.text() === '删除')
    expect(deleteBtn).toBeTruthy()
    await deleteBtn.trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalled()
    expect(deleteKnowledgeFile).toHaveBeenCalledWith('guide.md')
  })

  it('点击立即重建会调用 rebuildKnowledge', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const rebuildBtn = wrapper.findAll('button').find((b) => b.text().includes('立即重建索引'))
    expect(rebuildBtn).toBeTruthy()
    await rebuildBtn.trigger('click')
    await flushPromises()

    expect(rebuildKnowledge).toHaveBeenCalled()
  })
})
