import { useEffect, useRef, useState } from 'react'
import { App as AntdApp, ConfigProvider, Form } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { deleteRuns, getQuery, getSettings, listRuns, runQuery } from './api'
import { AppShell } from './components/AppShell'
import { CollectionPage } from './pages/CollectionPage'
import { SettingsPage } from './pages/SettingsPage'
import { TasksPage } from './pages/TasksPage'
import { workbenchTheme } from './theme'
import type { CollectionValues, ProviderId, QueryView, Run, SettingsView } from './types'
import { PROVIDER_OPTIONS, SESSION_PROVIDERS } from './types'
import { TABLE_PAGE_SIZE } from './pagination'
import './styles.css'

const parseHash = () => {
  const raw = location.hash.replace(/^#/, '') || 'collection'
  if (raw === 'dashboard' || raw === 'history') return 'tasks'
  if (raw === 'proxy-settings') return 'settings'
  if (raw.startsWith('results/')) return `collection/${raw.slice('results/'.length)}`
  return raw
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
    if (!page.startsWith('collection/')) return
    const id = page.slice('collection/'.length)
    if (!id || loadedId.current === id) return
    loadedId.current = id
    setQuerying(true)
    getQuery(id)
      .then((view) => {
        setQuery(view)
        setQueryError(null)
        form.setFieldsValue({
          keyword: view.run.keyword,
          providers: (view.run.providers ?? [view.run.provider]).filter((id): id is ProviderId =>
            PROVIDER_OPTIONS.some((item) => item.value === id)
          ),
          depth: view.run.depth,
          holding_percent: Number(view.run.holding_percent),
        })
      })
      .catch((error) => setQueryError(error instanceof Error ? error.message : '查询记录不存在'))
      .finally(() => setQuerying(false))
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
    setQueryError(null)
    try {
      const view = await runQuery(values)
      loadedId.current = view.run.id
      setQuery(view)
      if (view.source_errors.length) message.warning('部分数据源未返回结果')
      navigate(`collection/${view.run.id}`)
      void refreshRecents()
    } catch (error) {
      setQuery(null)
      setQueryError(error instanceof Error ? error.message : '查询失败')
    } finally {
      setQuerying(false)
    }
  }

  const title = page === 'settings' ? '基础配置' : page === 'proxy-settings' ? '基础配置' : page === 'tasks' ? '历史查询' : 'ICP备案查询'

  const content = page === 'settings' ? (
    <SettingsPage />
  ) : page === 'proxy-settings' ? (
    <SettingsPage />
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
