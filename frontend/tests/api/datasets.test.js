/**
 * 数据集管理 API 测试
 *
 * 覆盖：
 *   - 列表与评估的 URL/参数组装
 *   - 中文名修改的字段映射（camelCase → snake_case）
 *   - 两段式上传：FormData 上传与 commit 请求体（英文名仅提交时可改）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn(() => Promise.resolve({}))
const post = vi.fn(() => Promise.resolve({}))
const put = vi.fn(() => Promise.resolve({}))

vi.mock('@/utils/request', () => ({
  default: {
    get: (...args) => get(...args),
    post: (...args) => post(...args),
    put: (...args) => put(...args),
  },
}))

let api

beforeEach(async () => {
  vi.clearAllMocks()
  api = await import('@/api/datasets')
})

describe('数据集管理 API', () => {
  it('getDatasets 请求列表', async () => {
    await api.getDatasets()
    expect(get).toHaveBeenCalledWith('/datasets')
  })

  it('updateDatasetNames 映射为 snake_case 请求体', async () => {
    await api.updateDatasetNames('fdd', {
      displayName: '织物缺陷',
      classNamesCn: { hole: '破洞' },
    })
    expect(put).toHaveBeenCalledWith('/datasets/fdd/names', {
      display_name: '织物缺陷',
      class_names_cn: { hole: '破洞' },
      new_name: null,
      new_class_names: null,
    })
  })

  it('updateDatasetNames 未登记数据集可携带新名称', async () => {
    await api.updateDatasetNames('ds_a', {
      newName: 'ds_b',
      newClassNames: ['big_hole'],
      classNamesCn: { big_hole: '大洞' },
    })
    expect(put).toHaveBeenCalledWith('/datasets/ds_a/names', {
      display_name: null,
      class_names_cn: { big_hole: '大洞' },
      new_name: 'ds_b',
      new_class_names: ['big_hole'],
    })
  })

  it('registerDataset 登记为场景', async () => {
    await api.registerDataset('ds_a', { displayName: '新场景', category: 'industry' })
    expect(post).toHaveBeenCalledWith('/datasets/ds_a/register', {
      display_name: '新场景',
      category: 'industry',
      description: null,
    })
  })

  it('uploadDataset 以 FormData 上传', async () => {
    const file = new File(['zip'], 'pack.zip')
    await api.uploadDataset(file)
    const [url, body, config] = post.mock.calls[0]
    expect(url).toBe('/datasets/upload')
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('file')).toBe(file)
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
  })

  it('commitDatasetUpload 携带英文名与中文名映射', async () => {
    await api.commitDatasetUpload('upload-1', {
      sceneName: 'fabric_new',
      displayName: '新织物场景',
      classNames: ['thread_break'],
      classNamesCn: { thread_break: '断线' },
      overwriteClasses: true,
    })
    const [url, body] = post.mock.calls[0]
    expect(url).toBe('/datasets/upload/upload-1/commit')
    expect(body).toEqual({
      scene_name: 'fabric_new',
      display_name: '新织物场景',
      category: 'industry',
      class_names: ['thread_break'],
      class_names_cn: { thread_break: '断线' },
      description: null,
      overwrite_classes: true,
      register_scene: true,
    })
  })

  it('commitDatasetUpload 仅上传不登记时 register_scene 为 false', async () => {
    await api.commitDatasetUpload('upload-2', {
      sceneName: 'raw_pack',
      classNames: ['hole'],
      registerScene: false,
    })
    const [, body] = post.mock.calls[0]
    expect(body.register_scene).toBe(false)
    expect(body.display_name).toBe('')
  })

  it('evaluateDataset 默认走缓存，force 时携带参数', async () => {
    await api.evaluateDataset('fdd')
    expect(post.mock.calls[0][0]).toBe('/datasets/fdd/evaluate')
    expect(post.mock.calls[0][2].params).toEqual({})

    await api.evaluateDataset('fdd', { force: true })
    expect(post.mock.calls[1][2].params).toEqual({ force: true })
  })
})
