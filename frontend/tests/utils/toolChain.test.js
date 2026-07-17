import { describe, expect, it } from 'vitest'

import {
  AGENT_NAME_MAP,
  beginToolStep,
  completeToolStep,
  parseToolResult,
  toolDisplayName,
} from '@/utils/toolChain'

describe('工具调用链', () => {
  it('记录调用开始并按工具名匹配对应的进行中步骤', () => {
    const chain = []
    beginToolStep(chain, { tool: 'list_session_attachments', input: {} })
    beginToolStep(chain, { tool: 'detect_batch_images', input: { image_paths: ['a.jpg'] } })

    expect(chain).toHaveLength(2)
    expect(chain.every((step) => step.status === 'running')).toBe(true)

    completeToolStep(chain, {
      tool: 'list_session_attachments',
      result: JSON.stringify({ available_files: 5, rounds: [] }),
    })

    expect(chain[0].status).toBe('done')
    expect(chain[0].summary).toContain('5 个可用附件')
    expect(chain[1].status).toBe('running')
  })

  it('检测结果生成摘要并返回卡片数据', () => {
    const chain = []
    beginToolStep(chain, { tool: 'detect_single_image', input: {} })
    const info = completeToolStep(chain, {
      tool: 'detect_single_image',
      result: JSON.stringify({ total_objects: 3, detections: [] }),
    })

    expect(info.detection).toEqual({ total_objects: 3, detections: [] })
    expect(chain[0].status).toBe('done')
    expect(chain[0].summary).toBe('检出 3 个目标')
  })

  it('知识库检索结果挂载来源与检索模式', () => {
    const chain = []
    beginToolStep(chain, { tool: 'search_knowledge', input: { query: '破洞' } })
    const info = completeToolStep(chain, {
      tool: 'search_knowledge',
      result: JSON.stringify({
        retrieval_mode: 'pgvector',
        fallback_reason: null,
        sources: ['fabric_defects.md'],
        results: [{ content: '破洞处理方法', source: 'fabric_defects.md', score: 0.87 }],
      }),
    })

    expect(info.knowledge.retrieval_mode).toBe('pgvector')
    expect(chain[0].knowledge.results).toHaveLength(1)
    expect(chain[0].summary).toBe('向量检索 · 命中 1 条片段')
  })

  it('词法降级模式在摘要中如实标注', () => {
    const info = parseToolResult(
      'search_knowledge',
      JSON.stringify({ retrieval_mode: 'lexical_fallback', results: [] }),
    )
    expect(info.summary).toBe('词法检索 · 命中 0 条片段')
  })

  it('错误结果标记步骤失败', () => {
    const chain = []
    beginToolStep(chain, { tool: 'detect_single_image', input: {} })
    const info = completeToolStep(chain, {
      tool: 'detect_single_image',
      result: JSON.stringify({ error: '图片文件不存在' }),
    })

    expect(info.error).toBe('图片文件不存在')
    expect(chain[0].status).toBe('error')
    expect(chain[0].summary).toBe('图片文件不存在')
  })

  it('非 JSON 结果不抛异常且步骤置为完成', () => {
    const chain = []
    beginToolStep(chain, { tool: 'query_system_roles', input: {} })
    expect(() =>
      completeToolStep(chain, { tool: 'query_system_roles', result: 'plain text' }),
    ).not.toThrow()
    expect(chain[0].status).toBe('done')
  })

  it('工具名与专家名映射为中文显示', () => {
    expect(toolDisplayName('search_knowledge')).toBe('知识库检索')
    expect(toolDisplayName('list_session_attachments')).toBe('会话附件查询')
    expect(toolDisplayName('unknown_tool')).toBe('unknown_tool')
    expect(AGENT_NAME_MAP.qa).toBe('知识问答')
  })

  it('beginToolStep 记录事件中的专家归属', () => {
    const chain = []
    beginToolStep(chain, { tool: 'detect_single_image', input: {}, agent: 'detection' })
    expect(chain[0].agent).toBe('detection')
  })

  it('并行事件按专家加工具名匹配运行中的步骤', () => {
    const chain = []
    beginToolStep(chain, { tool: 'detect_single_image', input: {}, agent: 'detection' })
    beginToolStep(chain, { tool: 'search_knowledge', input: {}, agent: 'qa' })

    // 乱序完成：qa 先返回
    completeToolStep(chain, {
      tool: 'search_knowledge',
      agent: 'qa',
      result: JSON.stringify({ retrieval_mode: 'pgvector', results: [] }),
    })
    expect(chain[1].status).toBe('done')
    expect(chain[0].status).toBe('running')

    completeToolStep(chain, {
      tool: 'detect_single_image',
      agent: 'detection',
      result: JSON.stringify({ total_objects: 1 }),
    })
    expect(chain[0].status).toBe('done')
  })

  it('无 agent 字段的旧事件仍按工具名匹配', () => {
    const chain = []
    beginToolStep(chain, { tool: 'detect_single_image', input: {}, agent: 'detection' })
    completeToolStep(chain, {
      tool: 'detect_single_image',
      result: JSON.stringify({ total_objects: 2 }),
    })
    expect(chain[0].status).toBe('done')
    expect(chain[0].summary).toBe('检出 2 个目标')
  })
})
