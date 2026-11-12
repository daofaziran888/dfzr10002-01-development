import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/indicator'
const OPERATOR = '值班管理员'
const statuses = ['草稿', '待审批', '已发布']

type Row = {
  id: number
  status: string
  pending?: boolean
  '指标编号': string
  '指标名称': string
  '计算公式': string
  '统计粒度'?: string
  '时间范围'?: string
  '筛选条件'?: string
  '数据来源'?: string
  '口径版本': string
  version_seq: number
  is_current: boolean
  previous_version_id: number | null
  created_at: string
  published_at: string | null
  transitions?: { from: string | null; to: string; action: string; at: string; operator?: string }[]
}

type VersionBrief = {
  id: number
  '口径版本': string
  '指标编号': string
  '指标名称': string
  '计算公式': string
  status: string
  created_at: string
  published_at: string | null
}

type Consumer = {
  query_id: number
  '模板编号': string
  '模板名称': string
  '模板版本': string
  '引用指标版本': string | null
  '消费状态': string
  '指标确认状态': string
  confirmed_for_proposed: boolean
  decision: string | null
  last_confirmed_at: string | null
  last_confirmed_by: string | null
}

type Review = {
  indicator_id: number
  '指标编号': string
  '指标名称': string
  proposed_version: VersionBrief
  previous_stable_version: VersionBrief | null
  consumers: Consumer[]
  required_confirmations: number
  confirmed_count: number
  ready: boolean
  blockers: string[]
}

type VersionHistory = {
  '指标编号': string
  current_version_id: number | null
  versions: Row[]
}

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ')
}

function confirmTone(state: string | undefined): string {
  if (state === '已确认') return 'ok'
  if (state === '待重新确认') return 'warn'
  return 'muted'
}

export default function IndicatorView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState({ keyword: '', status: '' })
  const [draftFilters, setDraftFilters] = useState({ keyword: '', status: '' })
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [revisionTarget, setRevisionTarget] = useState<Row | null>(null)
  const [revisionFormula, setRevisionFormula] = useState('')
  const [history, setHistory] = useState<VersionHistory | null>(null)
  const [summary, setSummary] = useState({
    indicator_count: 0,
    published_count: 0,
    pending_versions: 0,
    archived_versions: 0,
  })

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters.keyword) query.set('keyword', filters.keyword)
    if (filters.status) query.set('status', filters.status)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('业务指标列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      // 看板统计按业务指标编号去重，不能用当前分页的数据近似。
      const summaryResponse = await request(`${ENDPOINT}/summary`)
      if (summaryResponse.ok) {
        setSummary(await summaryResponse.json())
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标口径管理列表读取失败')
    }
  }, [filters])

  // 筛选条件变化时重新读取列表。
  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => [
    { label: '业务指标总数', value: summary.indicator_count },
    { label: '已发布指标', value: summary.published_count },
    { label: '待审批版本', value: summary.pending_versions },
    { label: '历史留档版本', value: summary.archived_versions },
  ], [summary])

  async function postJson(path: string, body: Record<string, unknown>) {
    const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
    const payload = await response.json().catch(() => ({ ok: false, message: '接口未返回可读结果' }))
    if (!response.ok) {
      throw new Error(payload.detail ?? payload.message ?? `接口返回 ${response.status}`)
    }
    return payload
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const payload = await postJson(`${ENDPOINT}/${row.id}/actions`, { action })
      if (!payload.ok) throw new Error(payload.message)
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标口径管理操作失败')
    }
  }

  async function openReview(row: Row) {
    setErrorMessage('')
    setNotice('')
    setBusy(true)
    try {
      const response = await request(`${ENDPOINT}/${row.id}/publish-review`)
      const payload = await response.json().catch(() => ({ detail: '发布确认视图读取失败' }))
      if (!response.ok) throw new Error(payload.detail)
      setReview(payload as Review)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '发布确认视图读取失败')
    } finally {
      setBusy(false)
    }
  }

  async function refreshReview(indicatorId: number) {
    const response = await request(`${ENDPOINT}/${indicatorId}/publish-review`)
    if (!response.ok) return
    setReview((await response.json()) as Review)
  }

  async function confirmConsumer(consumer: Consumer, decision: '确认' | '拒绝') {
    if (!review) return
    setErrorMessage('')
    setNotice('')
    try {
      const payload = await postJson(`${ENDPOINT}/${review.indicator_id}/confirmations`, {
        query_id: consumer.query_id,
        decision,
        confirmed_by: OPERATOR,
        comment: decision === '确认' ? '已核对新口径，消费方完成切换准备' : '新口径与当前使用场景不匹配',
      })
      if (!payload.ok) throw new Error(payload.message)
      setNotice(payload.message)
      await Promise.all([refreshReview(review.indicator_id), reload()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '消费方确认提交失败')
    }
  }

  async function publishReviewed() {
    if (!review) return
    setErrorMessage('')
    setNotice('')
    try {
      const payload = await postJson(`${ENDPOINT}/${review.indicator_id}/actions`, { action: '发布指标' })
      if (!payload.ok) throw new Error(payload.message)
      setNotice(payload.message)
      setReview(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '发布指标失败')
    }
  }

  function openRevision(row: Row) {
    setRevisionTarget(row)
    setRevisionFormula(row['计算公式'])
  }

  async function submitRevision() {
    if (!revisionTarget) return
    setErrorMessage('')
    setNotice('')
    try {
      const payload = await postJson(`${ENDPOINT}/${revisionTarget.id}/revisions`, {
        '计算公式': revisionFormula,
      })
      if (!payload.ok) throw new Error(payload.message)
      setNotice(payload.message)
      setRevisionTarget(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '口径变更失败')
    }
  }

  async function openHistory(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/versions`)
      if (!response.ok) throw new Error('版本记录读取失败')
      setHistory((await response.json()) as VersionHistory)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本记录读取失败')
    }
  }

  function rowActions(row: Row) {
    if (row.status === '草稿') {
      return (
        <>
          <button className="link" type="button" onClick={() => void runAction('提交审批', row)}>提交审批</button>
          <button className="link" type="button" onClick={() => openRevision(row)}>编辑公式</button>
          <button className="link" type="button" onClick={() => void openHistory(row)}>版本记录</button>
        </>
      )
    }
    if (row.status === '待审批') {
      return (
        <>
          <button className="link strong" type="button" onClick={() => void openReview(row)}>
            发布前确认{row.pending ? ' •' : ''}
          </button>
          <button className="link" type="button" onClick={() => void openHistory(row)}>版本记录</button>
        </>
      )
    }
    return (
      <>
        {row.is_current ? (
          <button className="link" type="button" onClick={() => openRevision(row)}>变更口径</button>
        ) : (
          <span className="tiny muted">历史版本只读</span>
        )}
        <button className="link" type="button" onClick={() => void openHistory(row)}>版本记录</button>
      </>
    )
  }

  return (
    <section className="page" data-module="indicator">
      <header className="page-head">
        <div>
          <h2>指标口径管理</h2>
          <p className="page-desc">
            业务指标按 草稿 → 待审批 → 已发布 单向推进；发布前需汇总下游查询模板的消费方确认结果。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出指标口径管理清单
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
          <span>指标编号 / 名称</span>
          <input
            value={draftFilters.keyword}
            placeholder="按指标编号或名称检索"
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
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => {
            setDraftFilters({ keyword: '', status: '' })
            setFilters({ keyword: '', status: '' })
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
            <th>指标编号</th>
            <th>指标名称</th>
            <th>口径版本</th>
            <th>状态</th>
            <th>计算公式</th>
            <th>最近流转时间</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const last = row.transitions?.[row.transitions.length - 1]
            return (
              <tr key={String(row.id)} className={row.is_current ? '' : 'archived-row'}>
                <td>{row['指标编号']}</td>
                <td>{row['指标名称']}</td>
                <td>
                  <span className="version-tag">{row['口径版本']}</span>
                  {row.is_current && <span className="tiny muted"> 当前</span>}
                  {!row.is_current && <span className="tiny muted"> 留档</span>}
                </td>
                <td><span className={`status-badge ${row.status === '已发布' ? 'ok' : row.status === '待审批' ? 'warn' : 'muted'}`}>{row.status}</span></td>
                <td className="formula-cell">{row['计算公式']}</td>
                <td>{last ? `${fmt(last.at)} · ${last.action}` : fmt(row.created_at)}</td>
                <td className="row-actions">{rowActions(row)}</td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={7}>暂无指标口径管理数据，可先登记业务指标</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条指标口径管理记录</span>
      </footer>

      {review && (
        <section className="drawer">
          <header className="drawer-head">
            <div>
              <h3>发布前确认 · {review['指标名称']}（{review['指标编号']}）</h3>
              <p className="page-desc">
                拟发布版本 {review.proposed_version['口径版本']}；需要 {review.required_confirmations} 个消费方确认，
                已完成 {review.confirmed_count} 个。确认未齐时发布会明确拦截，不会静默通过。
              </p>
            </div>
            <button className="btn ghost" type="button" onClick={() => setReview(null)}>关闭</button>
          </header>

          <div className="version-grid">
            <article className="version-card proposed">
              <span className="version-label">拟发布版本</span>
              <strong>{review.proposed_version['口径版本']}（{review.proposed_version.status}）</strong>
              <p className="formula-cell">{review.proposed_version['计算公式']}</p>
              <span className="tiny muted">创建于 {fmt(review.proposed_version.created_at)}</span>
            </article>
            <article className="version-card">
              <span className="version-label">上一稳定版本</span>
              {review.previous_stable_version ? (
                <>
                  <strong>{review.previous_stable_version['口径版本']}（{review.previous_stable_version.status}）</strong>
                  <p className="formula-cell">{review.previous_stable_version['计算公式']}</p>
                  <span className="tiny muted">发布于 {fmt(review.previous_stable_version.published_at)}</span>
                </>
              ) : (
                <p className="muted">该指标尚无已发布的历史版本</p>
              )}
            </article>
          </div>

          <table className="data-table">
            <thead>
              <tr>
                <th>查询模板</th>
                <th>模板版本</th>
                <th>当前引用版本</th>
                <th>确认状态</th>
                <th>最后确认时间</th>
                <th>消费状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {review.consumers.map((consumer) => (
                <tr key={consumer.query_id}>
                  <td>{consumer['模板编号']} · {consumer['模板名称']}</td>
                  <td>{consumer['模板版本']}</td>
                  <td>{consumer['引用指标版本'] ?? '—'}</td>
                  <td>
                    <span className={`status-badge ${consumer.confirmed_for_proposed ? 'ok' : confirmTone(consumer['指标确认状态'])}`}>
                      {consumer.confirmed_for_proposed ? '已确认本版本' : consumer['指标确认状态']}
                    </span>
                  </td>
                  <td>{fmt(consumer.last_confirmed_at)}{consumer.last_confirmed_by ? ` · ${consumer.last_confirmed_by}` : ''}</td>
                  <td>{consumer['消费状态']}</td>
                  <td className="row-actions">
                    <button className="link" type="button" onClick={() => void confirmConsumer(consumer, '确认')}>
                      {consumer.confirmed_for_proposed ? '重新确认' : '确认'}
                    </button>
                    <button className="link danger" type="button" onClick={() => void confirmConsumer(consumer, '拒绝')}>拒绝</button>
                  </td>
                </tr>
              ))}
              {review.consumers.length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={7}>当前没有引用该口径的查询模板，可直接发布</td>
                </tr>
              )}
            </tbody>
          </table>

          {review.blockers.length > 0 && (
            <div className="blocker-box">
              <strong>发布拦截</strong>
              <ul>
                {review.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
              </ul>
            </div>
          )}

          <div className="drawer-foot">
            <button
              className="btn primary"
              type="button"
              disabled={!review.ready || busy}
              onClick={() => void publishReviewed()}
            >
              发布指标
            </button>
            {!review.ready && <span className="tiny muted">确认未完成前发布按钮不可用</span>}
          </div>
        </section>
      )}

      {revisionTarget && (
        <section className="drawer">
          <header className="drawer-head">
            <h3>变更口径 · {revisionTarget['指标名称']}（{revisionTarget['指标编号']}）</h3>
            <button className="btn ghost" type="button" onClick={() => setRevisionTarget(null)}>关闭</button>
          </header>
          {revisionTarget.status === '已发布' ? (
            <p className="page-desc">
              已发布版本将按当时口径继续留档；本次修改会生成一个新的待审批版本，并保留业务指标编号
              {revisionTarget['指标编号']}。
            </p>
          ) : (
            <p className="page-desc">草稿版本可就地修订，不会产生新的版本记录。</p>
          )}
          <label className="filter-item block">
            <span>新的计算公式</span>
            <textarea
              rows={4}
              value={revisionFormula}
              onChange={(event) => setRevisionFormula(event.target.value)}
            />
          </label>
          <div className="drawer-foot">
            <button className="btn primary" type="button" onClick={() => void submitRevision()}>
              保存变更
            </button>
          </div>
        </section>
      )}

      {history && (
        <section className="drawer">
          <header className="drawer-head">
            <h3>版本记录 · {history['指标编号']}</h3>
            <button className="btn ghost" type="button" onClick={() => setHistory(null)}>关闭</button>
          </header>
          <table className="data-table">
            <thead>
              <tr><th>版本</th><th>状态</th><th>计算公式</th><th>创建时间</th><th>发布时间</th><th>留档说明</th></tr>
            </thead>
            <tbody>
              {history.versions.map((version) => (
                <tr key={String(version.id)}>
                  <td>{version['口径版本']}</td>
                  <td>{version.status}</td>
                  <td className="formula-cell">{version['计算公式']}</td>
                  <td>{fmt(version.created_at)}</td>
                  <td>{fmt(version.published_at)}</td>
                  <td>{version.is_current ? '当前版本' : version.status === '已发布' ? '按发布时口径留档' : '历史记录'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  )
}
