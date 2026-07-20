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

  it('统计查询结果不再被误判为检测结果卡片，而是生成查询面板', () => {
    const chain = []
    beginToolStep(chain, { tool: 'query_detection_statistics', input: {} })
    const info = completeToolStep(chain, {
      tool: 'query_detection_statistics',
      result: JSON.stringify({
        period: 'recent_days',
        days: 7,
        from: '2026-07-13T00:00:00',
        to: '2026-07-20T12:00:00',
        scene: '布匹检测',
        task_type: 'all',
        total_tasks: 12,
        completed_tasks: 10,
        status_counts: { completed: 10, failed: 2 },
        success_rate: 83.33,
        total_images: 40,
        total_objects: 5,
        average_inference_time_ms: 123.4,
        task_type_counts: { single: 8, batch: 3, video: 1, other: 0 },
      }),
    })

    expect(info.detection).toBeNull()
    expect(info.query).toBeTruthy()
    expect(chain[0].query).toBe(info.query)
    // 摘要行只给场景与时间范围的值，不带"统计场景/时间范围"标签
    expect(info.summary).toBe('布匹检测 · 2026-07-13 ~ 2026-07-20')
    const labels = info.query.fields.map((f) => f.label)
    expect(labels).toContain('任务总数')
    // 场景与时间范围已在摘要行展示，面板中不再重复
    expect(labels).not.toContain('统计场景')
    expect(labels).not.toContain('时间范围')
    // 推理耗时属于检测执行指标，与查询任务无关，不进入面板
    expect(labels).not.toContain('推理耗时')
    expect(labels.join()).not.toContain('耗时')
  })

  it('缺陷维度统计生成缺陷相关字段的查询面板', () => {
    const info = parseToolResult(
      'query_detection_statistics',
      JSON.stringify({
        defect: '破洞',
        scene: '布匹检测',
        from: '2026-07-01',
        to: '2026-07-20',
        matched_tasks: 3,
        matched_images: 5,
        defect_count: 9,
      }),
    )
    expect(info.detection).toBeNull()
    const byLabel = Object.fromEntries(info.query.fields.map((f) => [f.label, f.value]))
    expect(byLabel['缺陷类别']).toBe('破洞')
    expect(byLabel['缺陷目标数']).toBe(9)
  })

  it('趋势查询也生成查询面板并汇总每日数据', () => {
    const info = parseToolResult(
      'query_detection_trends',
      JSON.stringify({
        days: 7,
        from: '2026-07-13',
        to: '2026-07-20',
        scene: '布匹检测',
        daily: [
          { date: '2026-07-18', tasks: 2, objects: 4 },
          { date: '2026-07-19', tasks: 1, objects: 3 },
        ],
        class_distribution: [{ class_name: 'hole', count: 7 }],
      }),
    )
    expect(info.query).toBeTruthy()
    const byLabel = Object.fromEntries(info.query.fields.map((f) => [f.label, f.value]))
    expect(byLabel['检出目标总数']).toBe(7)
    expect(byLabel['任务总数']).toBe(3)
    expect(byLabel['缺陷类别数']).toBe(1)
  })

  it('用户与角色查询生成对应的查询面板', () => {
    const users = parseToolResult(
      'query_system_users',
      JSON.stringify({ total: 6, items: [], role_filter: 'system_admin', filtered_count: 2 }),
    )
    expect(users.query.fields.map((f) => f.label)).toEqual(
      expect.arrayContaining(['用户总数', '角色筛选', '筛选后数量']),
    )

    const roles = parseToolResult(
      'query_system_roles',
      JSON.stringify({ roles: [{ name: 'system_admin' }, { name: 'quality_inspector' }] }),
    )
    expect(roles.query.fields[0]).toEqual({ label: '角色数量', value: 2 })
  })
})
