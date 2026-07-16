/**
 * agent store 历史还原测试
 *
 * 重点验证：从后端历史消息重建检测结果卡片时，统计信息取自
 * tool_result（已剥离 base64），标注图/视频用后端换签的 MinIO URL 还原。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const getChatSessionHistory = vi.fn()

vi.mock('@/api/chat', () => ({
  getChatSessions: vi.fn(),
  deleteChatSession: vi.fn(),
  getChatSessionHistory: (...args) => getChatSessionHistory(...args),
}))

import { useAgentStore } from '@/stores/agent'

describe('agent store 历史还原', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getChatSessionHistory.mockReset()
  })

  it('单图历史用 MinIO URL 还原标注图', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '检测完成',
          agent_used: 'detection',
          tool_calls: [{ tool: 'detect_single_image' }],
          tool_result: JSON.stringify([
            {
              tool: 'detect_single_image',
              result: JSON.stringify({ total_objects: 2, class_counts: { hole: 2 } }),
            },
          ]),
          attachments: [
            { tool: 'detect_single_image', type: 'image', url: 'http://minio/rsod/a.jpg?fresh=1' },
          ],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-1')

    const msg = store.messages[0]
    expect(msg.detectionResult.total_objects).toBe(2)
    expect(msg.detectionResult.annotated_image_url).toBe('http://minio/rsod/a.jpg?fresh=1')
    expect(msg.toolChain).toHaveLength(1)
  })

  it('批量历史按 image_path 匹配回每张图的 URL', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '批量完成',
          tool_calls: [{ tool: 'detect_batch_images' }],
          tool_result: JSON.stringify([
            {
              tool: 'detect_batch_images',
              result: JSON.stringify({
                total_objects: 3,
                annotated_images: [{ image_path: 'b.jpg' }, { image_path: 'c.jpg' }],
              }),
            },
          ]),
          attachments: [
            {
              tool: 'detect_batch_images',
              type: 'images',
              images: [
                { image_path: 'b.jpg', url: 'http://minio/rsod/b.jpg?fresh=1' },
                { image_path: 'c.jpg', url: 'http://minio/rsod/c.jpg?fresh=1' },
              ],
            },
          ],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-2')

    const images = store.messages[0].detectionResult.annotated_images
    expect(images.find((i) => i.image_path === 'b.jpg').annotated_image_url).toBe(
      'http://minio/rsod/b.jpg?fresh=1',
    )
    expect(images.find((i) => i.image_path === 'c.jpg').annotated_image_url).toBe(
      'http://minio/rsod/c.jpg?fresh=1',
    )
  })

  it('视频历史用 MinIO URL 还原标注视频', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '视频完成',
          tool_calls: [{ tool: 'detect_video_file' }],
          tool_result: JSON.stringify([
            {
              tool: 'detect_video_file',
              result: JSON.stringify({ type: 'video', total_objects: 5 }),
            },
          ]),
          attachments: [
            { tool: 'detect_video_file', type: 'video', url: 'http://minio/rsod/v.mp4?fresh=1' },
          ],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-3')

    expect(store.messages[0].detectionResult.annotated_video_url).toBe(
      'http://minio/rsod/v.mp4?fresh=1',
    )
  })

  it('用户原始图片和视频从 MinIO URL 还原到消息预览', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'user',
          content: '请检测单图',
          attachments: [
            {
              source: 'user',
              type: 'image',
              filename: 'fabric.jpg',
              url: 'http://minio/rsod/original.jpg?fresh=1',
            },
          ],
        },
        {
          role: 'user',
          content: '请批量检测',
          attachments: [
            { source: 'user', type: 'image', filename: 'a.jpg', url: 'http://minio/rsod/a-original.jpg?fresh=1' },
            { source: 'user', type: 'image', filename: 'b.jpg', url: 'http://minio/rsod/b-original.jpg?fresh=1' },
          ],
        },
        {
          role: 'user',
          content: '请检测视频',
          attachments: [
            {
              source: 'user',
              type: 'video',
              filename: 'fabric.mp4',
              url: 'http://minio/rsod/original.mp4?fresh=1',
            },
          ],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-user-originals')

    expect(store.messages[0].image).toBe('fabric.jpg')
    expect(store.messages[0].imagePreview).toBe(
      'http://minio/rsod/original.jpg?fresh=1',
    )
    expect(store.messages[1].images).toEqual([
      'http://minio/rsod/a-original.jpg?fresh=1',
      'http://minio/rsod/b-original.jpg?fresh=1',
    ])
    expect(store.messages[2].videoUrl).toBe(
      'http://minio/rsod/original.mp4?fresh=1',
    )
  })

  it('用户 ZIP 附件在历史中恢复文件名', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'user',
          content: '请检测 ZIP',
          attachments: [
            {
              source: 'user',
              type: 'zip',
              filename: 'images.zip',
              url: 'http://minio/rsod/images.zip?fresh=1',
            },
          ],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-user-zip')

    expect(store.messages[0].fileAttachments).toEqual(['images.zip'])
  })

  it('知识库检索历史还原片段与检索模式到工具链', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '根据知识库……',
          agent_used: 'qa',
          tool_calls: [{ tool: 'search_knowledge' }],
          tool_result: JSON.stringify([
            {
              tool: 'search_knowledge',
              result: JSON.stringify({
                retrieval_mode: 'pgvector',
                results: [
                  { source: 'defects.md', score: 0.87, content: '破洞是常见织物缺陷……' },
                ],
              }),
            },
          ]),
          attachments: [],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-kb')

    const step = store.messages[0].toolChain[0]
    expect(step.status).toBe('done')
    expect(step.summary).toBe('向量检索 · 命中 1 条片段')
    expect(step.knowledge.retrieval_mode).toBe('pgvector')
    expect(step.knowledge.results[0].source).toBe('defects.md')
    expect(step.knowledge.results[0].content).toContain('破洞')
  })

  it('工具执行失败的历史步骤标记为 error 并显示原因', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '出错了',
          tool_calls: [{ tool: 'detect_single_image' }],
          tool_result: JSON.stringify([
            {
              tool: 'detect_single_image',
              result: JSON.stringify({ error: '文件不存在' }),
            },
          ]),
          attachments: [],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-err')

    const step = store.messages[0].toolChain[0]
    expect(step.status).toBe('error')
    expect(step.summary).toBe('文件不存在')
  })

  it('无附件时仍不报错，仅重建统计卡片', async () => {
    getChatSessionHistory.mockResolvedValue({
      messages: [
        {
          role: 'assistant',
          content: '完成',
          tool_calls: [{ tool: 'detect_single_image' }],
          tool_result: JSON.stringify([
            { tool: 'detect_single_image', result: JSON.stringify({ total_objects: 1 }) },
          ]),
          attachments: [],
        },
      ],
    })

    const store = useAgentStore()
    await store.loadSession('uuid-4')

    expect(store.messages[0].detectionResult.total_objects).toBe(1)
    expect(store.messages[0].detectionResult.annotated_image_url).toBeUndefined()
  })
})
