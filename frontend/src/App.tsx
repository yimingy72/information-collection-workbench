import { useEffect, useRef, useState } from 'react'
import { App as AntdApp, ConfigProvider, Form } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import {
  cancelCollectionRun,
  collectionEventUrl,
  createCollectionRun,
  deleteRuns,
  getQuery,
  getSettings,
  listRuns,
} from './api'
import { AppShell } from './components/AppShell'
import { CollectionPage } from './pages/CollectionPage'
import { SettingsPage } from './pages/SettingsPage'
import { SubdomainPage } from './pages/SubdomainPage'
import { TasksPage } from './pages/TasksPage'
import { sourceTags } from './formatters'
import { workbenchTheme } from './theme'
import type {
  CollectionDelta,
  CollectionValues,
  IcpRow,
  InvestmentRow,
  ProviderId,
  QueryView,
  Run,
  SettingsView,
} from './types'
import { PROVIDER_OPTIONS, SESSION_PROVIDERS } from './types'
import { TABLE_PAGE_SIZE } from './pagination'
import './styles.css'

const terminalStatuses = new Set(['succeeded', 'partial', 'failed', 'cancelled'])

const parseHash = () => {
  const raw = location.hash.replace(/^#/, '') || 'collection'
  if (raw === 'dashboard' || raw === 'history') return 'tasks'
  if (raw === 'proxy-settings') return 'settings'
  if (raw.startsWith('results/')) return `collection/${raw.slice('results/'.length)}`
  return raw
}

const investmentKey = (row: InvestmentRow) =>
  [row.parent_name, row.child_name, row.depth, row.holding_percent ?? ''].join('\u0000')

const mergeInvestments = (current: InvestmentRow[], incoming: InvestmentRow[]) => {
  const rows = new Map(current.map((item) => [investmentKey(item), item]))
  incoming.forEach((item) => {
    const key = investmentKey(item)
    const existing = rows.get(key)
    rows.set(
      key,
      existing
        ? { ...existing, source: sourceTags(`${existing.source}、${item.source}`).join('、') }
        : item,
    )
  })
  return [...rows.values()]
}

const icpKey = (row: IcpRow) =>
  [row.unit_name, row.main_licence, row.service_licence, row.domain].join('\u0000')

const mergeIcpRecords = (current: IcpRow[], incoming: IcpRow[]) => {
  const rows = new Map(current.map((item) => [icpKey(item), item]))
  incoming.forEach((item) => rows.set(icpKey(item), item))
  return [...rows.values()]
}

function Workbench({
  dark,
  onDarkChange,
}: {
  dark: boolean
  onDarkChange: (value: boolean) => void
}) {
  const { message } = AntdApp.useApp()
  const [page, setPage] = useState(parseHash)
  const [runs, setRuns] = useState<Run[]>([])
  const [runTotal, setRunTotal] = useState(0)
  const [runPage, setRunPage] = useState(1)
  const [runPageSize, setRunPageSize] = useState(TABLE_PAGE_SIZE)
  const [runKeyword, setRunKeyword] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [runsLoading, setRunsLoading] = useState(false)
  const [query, setQuery] = useState<QueryView | null>(null)
  const [querying, setQuerying] = useState(false)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [form] = Form.useForm<CollectionValues>()
  const loadedId = useRef<string | null>(null)
  const collectionEventSource = useRef<EventSource | null>(null)
  const [settings, setSettings] = useState<SettingsView | null>(null)

  const refreshSettings = async () => {
    try {
      setSettings(await getSettings())
      setApiError(null)
    } catch {
      setApiError('无法连接工作台 API。')
    }
  }

  const navigate = (next: string) => {
    location.hash = next
    setPage(next)
  }

  const go = (next: string) => {
    if (next === 'collection') {
      setQuery(null)
      setQueryError(null)
      setQuerying(false)
      loadedId.current = null
      void refreshSettings()
    }
    navigate(next)
  }

  useEffect(() => {
    const onHash = () => setPage(parseHash())
    addEventListener('hashchange', onHash)
    return () => removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }, [dark])

  const [recents, setRecents] = useState<Run[]>([])

  const refreshRuns = async () => {
    setRunsLoading(true)
    try {
      const data = await listRuns(runPage, runPageSize, runKeyword, runStatus)
      setRuns(data.items)
      setRunTotal(data.total)
      setApiError(null)
    } catch {
      setApiError('无法连接工作台 API。')
    } finally {
      setRunsLoading(false)
    }
  }

  const refreshRecents = async () => {
    try {
      const data = await listRuns(1, 12)
      const seen = new Set<string>()
      setRecents(
        data.items.filter((run) => {
          const key = run.keyword.trim()
          if (!key || seen.has(key)) return false
          seen.add(key)
          return true
        }),
      )
      setApiError(null)
    } catch {
      setApiError('无法连接工作台 API。')
    }
  }

  useEffect(() => {
    void refreshRecents()
    void refreshSettings()
  }, [])

  useEffect(() => {
    if (page !== 'tasks') return
    void refreshRuns()
  }, [page, runPage, runPageSize, runKeyword, runStatus])

  useEffect(() => {
    collectionEventSource.current?.close()
    collectionEventSource.current = null
    if (!page.startsWith('collection/')) return
    const id = page.slice('collection/'.length)
    if (!id) return

    let cancelled = false
    setQuerying(true)
    setQueryError(null)

    const load = async () => {
      const view = await getQuery(id)
      if (cancelled) return
      setQuery(view)
      if (loadedId.current !== id) {
        loadedId.current = id
        form.setFieldsValue({
          keyword: view.run.keyword,
          providers: (view.run.providers ?? [view.run.provider]).filter((providerId): providerId is ProviderId =>
            PROVIDER_OPTIONS.some((item) => item.value === providerId)
          ),
          depth: view.run.depth,
          holding_percent: Number(view.run.holding_percent),
        })
      }
      if (terminalStatuses.has(view.run.status)) {
        setQuerying(false)
        return
      }

      const source = new EventSource(
        collectionEventUrl(id, view.relationship_cursor, view.result_cursor),
      )
      collectionEventSource.current = source
      source.addEventListener('delta', (event) => {
        const delta = JSON.parse((event as MessageEvent).data) as CollectionDelta
        setQuery((current) => {
          if (!current || current.run.id !== id) return current
          return {
            ...current,
            investments: mergeInvestments(current.investments, delta.investments),
            icp_records: mergeIcpRecords(current.icp_records, delta.icp_records),
            relationship_cursor: Math.max(current.relationship_cursor, delta.relationship_cursor),
            result_cursor: Math.max(current.result_cursor, delta.result_cursor),
          }
        })
      })
      source.addEventListener('progress', (event) => {
        const run = JSON.parse((event as MessageEvent).data) as Run
        setQuery((current) => current?.run.id === id ? { ...current, run } : current)
        setQuerying(!terminalStatuses.has(run.status))
      })
      source.addEventListener('done', (event) => {
        const run = JSON.parse((event as MessageEvent).data) as Run
        setQuery((current) => current?.run.id === id ? { ...current, run } : current)
        setQuerying(false)
        source.close()
        collectionEventSource.current = null
        void getQuery(id)
          .then((finalView) => {
            if (cancelled) return
            setQuery(finalView)
            if (finalView.source_errors.length && finalView.run.status !== 'cancelled') {
              message.warning('部分数据源未返回结果')
            }
          })
          .catch(() => undefined)
        void refreshRecents()
      })
      source.onerror = () => {
        // EventSource reconnects automatically. Refresh only the lightweight
        // run/snapshot on transport recovery rather than polling all rows.
        void getQuery(id)
          .then((next) => {
            if (!cancelled) setQuery(next)
          })
          .catch(() => undefined)
      }
    }

    void load().catch((error) => {
      if (cancelled) return
      setQuerying(false)
      setQueryError(error instanceof Error ? error.message : '查询记录不存在')
    })

    return () => {
      cancelled = true
      collectionEventSource.current?.close()
      collectionEventSource.current = null
    }
  }, [page, form])

  const loggedIn = new Set(
    (settings?.sessions ?? []).filter((item) => item.status === 'logged_in').map((item) => item.provider),
  )

  const submit = async (values: CollectionValues) => {
    const missing = SESSION_PROVIDERS.filter((item) => values.providers.includes(item.value) && !loggedIn.has(item.value)).map((item) => item.label)
    if (missing.length) {
      message.warning(`请先登录${missing.join('、')}`)
      return
    }
    setQuerying(true)
    setQuery(null)
    setQueryError(null)
    loadedId.current = null
    try {
      const created = await createCollectionRun(values)
      navigate(`collection/${created.id}`)
      void refreshRecents()
    } catch (error) {
      setQuerying(false)
      setQueryError(error instanceof Error ? error.message : '查询失败')
    }
  }

  const cancelCurrentQuery = async () => {
    if (!query || terminalStatuses.has(query.run.status)) return
    try {
      const run = await cancelCollectionRun(query.run.id)
      setQuery((current) => current?.run.id === run.id ? { ...current, run } : current)
      setQuerying(false)
      message.success('查询已停止，现有结果已保留')
      void refreshRecents()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '停止查询失败')
    }
  }

  const title = page.startsWith('subdomains') ? '子域名查询' : page === 'settings' ? '基础配置' : page === 'proxy-settings' ? '基础配置' : page === 'tasks' ? '历史查询' : 'ICP备案查询'

  const content = page === 'settings' ? (
    <SettingsPage />
  ) : page === 'proxy-settings' ? (
    <SettingsPage />
  ) : page.startsWith('subdomains') ? (
    <SubdomainPage
      runId={page.startsWith('subdomains/') ? page.slice('subdomains/'.length) : undefined}
      onOpenRun={(id) => navigate(id ? `subdomains/${id}` : 'subdomains')}
    />
  ) : page === 'tasks' ? (
    <TasksPage
      runs={runs}
      total={runTotal}
      page={runPage}
      pageSize={runPageSize}
      loading={runsLoading}
      keyword={runKeyword}
      status={runStatus}
      onKeywordChange={(value) => {
        setRunPage(1)
        setRunKeyword(value)
      }}
      onStatusChange={(value) => {
        setRunPage(1)
        setRunStatus(value)
      }}
      onPageChange={(nextPage, nextSize) => {
        if (nextSize !== runPageSize) {
          setRunPage(1)
          setRunPageSize(nextSize)
          return
        }
        setRunPage(nextPage)
      }}
      onDelete={async (ids) => {
        await deleteRuns(ids)
        await refreshRuns()
        await refreshRecents()
      }}
    />
  ) : (
    <CollectionPage
      form={form}
      loading={querying}
      query={query}
      error={queryError}
      recents={recents}
      onFinish={submit}
      onPick={(run) => {
        form.setFieldsValue({ keyword: run.keyword })
      }}
      onForget={async (run) => {
        try {
          await deleteRuns([run.id])
          await refreshRecents()
          if (page === 'tasks') await refreshRuns()
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除失败')
        }
      }}
      onCancel={cancelCurrentQuery}
      settings={settings}
    />
  )

  return (
    <AppShell
      page={page.startsWith('collection') ? 'collection' : page}
      dark={dark}
      title={title}
      apiWarning={apiError}
      onNavigate={go}
      onDarkChange={onDarkChange}
    >
      {content}
    </AppShell>
  )
}

export default function App() {
  const [dark, setDark] = useState(localStorage.getItem('theme') === 'dark')

  return (
    <ConfigProvider locale={zhCN} theme={workbenchTheme(dark)}>
      <AntdApp>
        <Workbench dark={dark} onDarkChange={setDark} />
      </AntdApp>
    </ConfigProvider>
  )
}
