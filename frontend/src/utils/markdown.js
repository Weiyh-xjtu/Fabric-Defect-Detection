/**
 * Markdown 渲染工具
 * 用于 Day 11 智能体对话中 AI 回复的 Markdown 渲染
 */
import MarkdownIt from 'markdown-it'

// 创建 markdown-it 实例，启用 HTML 支持
const md = new MarkdownIt({
  html: false,        // 禁用 HTML 标签（安全考虑）
  linkify: true,      // 自动将 URL 转为链接
  typographer: true,  // 启用排版优化（如引号替换）
  breaks: true,       // 将 \n 转为 <br>
})

// 并行多专家回复的分节标题（后端 multi_agent.py 的 _section_header 生成，
// 形如 "\n\n---\n\n#### 🔍 检测专家\n\n"）。在渲染前把正文拆成按专家
// 分节的片段，由 ChatPage 用与单专家一致的 el-tag 胶囊渲染标签。
const SECTION_HEADER_RE = /####\s*\S{1,4}\s*(检测专家|数据分析|知识问答)\s*/g

const LABEL_TO_AGENT = {
  检测专家: 'detection',
  数据分析: 'analysis',
  知识问答: 'qa',
}

/**
 * 把 AI 回复按专家分节标题拆分。
 * 无分节标题时返回单个 { agent: null } 片段。
 * @param {string} text - 原始 Markdown 文本
 * @returns {Array<{agent: string|null, content: string}>}
 */
export function splitAgentSections(text) {
  if (!text) return [{ agent: null, content: '' }]
  const sections = []
  let last = 0
  let agent = null
  for (const m of text.matchAll(SECTION_HEADER_RE)) {
    // 节间的 "---" 分割线保留在上一节末尾，胶囊显示在分割线下方
    const before = text.slice(last, m.index).trim()
    if (before || agent) sections.push({ agent, content: before })
    agent = LABEL_TO_AGENT[m[1]]
    last = m.index + m[0].length
  }
  if (agent === null) return [{ agent: null, content: text }]
  sections.push({ agent, content: text.slice(last).trim() })
  return sections
}

/**
 * 将 Markdown 文本渲染为 HTML
 * @param {string} text - Markdown 文本
 * @returns {string} 渲染后的 HTML 字符串
 */
export function renderMarkdown(text) {
  if (!text) return ''
  return md.render(text)
}

export default md
