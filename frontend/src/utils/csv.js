/** CSV 序列化与浏览器端下载工具。 */

/** 将单元格值转义为 CSV 安全格式（处理逗号、引号、换行）。 */
function escapeCell(value) {
  if (value === null || value === undefined) return ''
  const text = String(value)
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`
  return text
}

/** 将表头与数据行序列化为 CSV 文本（不含 BOM）。 */
export function toCsv(headers, rows) {
  const lines = [headers.map(escapeCell).join(',')]
  for (const row of rows) {
    lines.push(row.map(escapeCell).join(','))
  }
  return lines.join('\r\n')
}

/** 触发浏览器下载 CSV；添加 BOM 使 Excel 正确识别 UTF-8 中文。 */
export function downloadCsv(filename, content) {
  const blob = new Blob(['\ufeff', content], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/** 一步完成序列化与下载。 */
export function exportCsv(filename, headers, rows) {
  downloadCsv(filename, toCsv(headers, rows))
}
