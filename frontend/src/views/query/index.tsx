import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/query'
const columns = [
  "模板编号",
  "模板名称",
  "查询语句",
  "参数定义",
  "缓存时间",
  "结果上限",
  "执行超时",
  "模板版本"
]
const actions = [
  "提交审批",
  "发布模板",
  "停用模板"
]
const statuses = [
  "草稿",
  "待审批",
  "已发布",
  "已停用"
]

type Row = Record<string, string | number | null>

function confirmTone(state: unknown): string {
  if (state === '已确认') return 'ok'
  if (state === '待重新确认') return 'warn'
  return 'muted'
}

function fmt(value: unknown): string {
  if (!value) return '—'
  return String(value).replace('T', ' ')
}

export default function QueryView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState({ keyword: '', status: '', indicator_code: '' })
  const [draftFilters, setDraftFilters] = useState({ keyword: '', status: '', indicator_code: '' })

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.status) query.set('status', filters.status)
    if (filters.indicator_code) query.set('indicator_code', filters.indicator_code)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('查询模板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合查询配置列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '查询模板总数', value: total },
    { label: '待重新确认', value: rows.filter((row) => row['指标确认状态'] === '待重新确认').length },
    { label: '消费已切换', value: rows.filter((row) => row['消费状态'] === '已切换').length },
  ], [rows, total])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json().catch(() => ({ ok: false, message: '接口未返回可读结果' }))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.detail ?? payload.message ?? '聚合查询配置动作未生效，请稍后重试')
      }
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合查询配置操作失败')
    }
  }

  return (
    <section className="page" data-module="query">
      <header className="page-head">
        <div>
          <h2>聚合查询配置管理</h2>
          <p className="page-desc">
            查询模板引用业务指标口径时，会同步展示指标版本、确认状态与消费切换结果；指标口径变化后必须先重新确认。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出聚合查询配置清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          setFilters(draftFilters)
        }}
      >
        <label className="filter-item">
          <span>模板编号</span>
          <input
            value={draftFilters.keyword}
            placeholder="按模板编号检索"
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, keyword: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select
            value={draftFilters.status}
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, status: event.target.value }))}
          >
            <option value="">全部</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>引用指标编号</span>
          <input
            value={draftFilters.indicator_code}
            placeholder="如 INDI-0001"
            onChange={(event) => setDraftFilters((prev) => ({ ...prev, indicator_code: event.target.value }))}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => {
            const empty = { keyword: '', status: '', indicator_code: '' }
            setDraftFilters(empty)
            setFilters(empty)
          }}
        >
          重置条件
        </button>
      </form>

      {(errorMessage || notice) && (
        <div className="notice-bar">
          {errorMessage && <span className="error-text">{errorMessage}</span>}
          {notice && <span className="success-text">{notice}</span>}
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>引用指标</th>
            <th>指标版本</th>
            <th>确认状态</th>
            <th>最后确认时间</th>
            <th>消费状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row['引用指标编号'] ?? '—'}</td>
              <td>{row['引用指标版本'] ?? '—'}</td>
              <td>
                <span className={`status-badge ${confirmTone(row['指标确认状态'])}`}>
                  {row['指标确认状态'] ?? '—'}
                </span>
              </td>
              <td>{fmt(row['指标最后确认时间'])}</td>
              <td>{row['消费状态'] ?? '—'}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 6}>暂无聚合查询配置数据，可先登记查询模板</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条聚合查询配置记录</span>
      </footer>
    </section>
  )
}
