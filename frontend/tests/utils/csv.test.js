/** CSV 工具单元测试。 */
import { downloadCsv, toCsv } from '@/utils/csv'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('toCsv', () => {
  it('按 CRLF 拼接表头与数据行', () => {
    const csv = toCsv(['日期', '任务数'], [['2026-07-01', 3], ['2026-07-02', 5]])
    expect(csv).toBe('日期,任务数\r\n2026-07-01,3\r\n2026-07-02,5')
  })

  it('转义包含逗号、引号和换行的单元格', () => {
    const csv = toCsv(['a'], [['x,y'], ['he said "hi"'], ['line1\nline2']])
    expect(csv.split('\r\n')).toEqual([
      'a',
      '"x,y"',
      '"he said ""hi"""',
      '"line1\nline2"',
    ])
  })

  it('null 和 undefined 输出为空单元格', () => {
    expect(toCsv(['a', 'b'], [[null, undefined]])).toBe('a,b\r\n,')
  })
})

describe('downloadCsv', () => {
  let clickSpy
  let capturedBlob

  beforeEach(() => {
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    capturedBlob = null
    globalThis.URL.createObjectURL = vi.fn((blob) => {
      capturedBlob = blob
      return 'blob:mock'
    })
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    clickSpy.mockRestore()
  })

  it('生成带 BOM 的 UTF-8 CSV 并触发下载', async () => {
    downloadCsv('report', 'a,b\r\n1,2')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock')
    const text = await capturedBlob.text()
    expect(text).toBe('\ufeffa,b\r\n1,2')
  })

  it('文件名自动补全 .csv 后缀', () => {
    const appendSpy = vi.spyOn(document.body, 'appendChild')
    downloadCsv('数据看板-汇总', 'a')
    const link = appendSpy.mock.calls.at(-1)[0]
    expect(link.download).toBe('数据看板-汇总.csv')
    appendSpy.mockRestore()
  })
})
