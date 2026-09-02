import type {
  CollectionValues,
  QrPoll,
  QrStart,
  QueryView,
  Run,
  RunList,
  ServerlessProxyDeployResult,
  ServerlessProxyEnableResult,
  ManualProxy,
  ManualProxyTest,
  ServerlessProxyTest,
  ServerlessProxyValues,
  SessionProviderId,
  SettingsView,
} from './types'

const readError = async (response: Response) => {
  const text = await response.text()
  try {
    const parsed = JSON.parse(text) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) {
      return parsed.detail.map((item) => typeof item === 'object' && item && 'msg' in item ? String((item as { msg: string }).msg) : JSON.stringify(item)).join('；')
    }
    if (parsed.detail) return JSON.stringify(parsed.detail)
  } catch {
    // Keep the original response body when it is not JSON.
  }
  return text || `请求失败（${response.status}）`
}

export const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    credentials: 'omit',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) throw new Error(await readError(response))
  if (response.status === 204) return undefined as T
  const body = await response.text()
  return (body ? JSON.parse(body) : undefined) as T
}

export const listRuns = (page: number, pageSize: number, keyword = '', status = '') => {
  const query = new URLSearchParams({
    limit: String(pageSize),
    offset: String((page - 1) * pageSize),
  })
  if (keyword.trim()) query.set('keyword', keyword.trim())
  if (status.trim()) query.set('status', status.trim())
  return api<RunList>(`/api/v1/collection-runs?${query.toString()}`)
}

export const getRun = (runId: string) =>
  api<Run>(`/api/v1/collection-runs/${runId}`)

export const runQuery = (values: CollectionValues) =>
  api<QueryView>('/api/v1/queries', {
    method: 'POST',
    body: JSON.stringify({
      keyword: values.keyword,
      providers: values.providers,
      depth: values.depth,
      holding_percent: values.holding_percent,
      fields: ['invest'],
      include_branches: false,
    }),
  })

export const getQuery = (runId: string) =>
  api<QueryView>(`/api/v1/queries/${runId}`)

export const deleteRuns = (ids: string[]) =>
  api<{ deleted: number }>('/api/v1/collection-runs/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })


export const getSettings = () => api<SettingsView>('/api/v1/settings')

export const saveServerlessProxy = (values: ServerlessProxyValues) =>
  api<SettingsView>('/api/v1/settings/serverless-proxy', {
    method: 'PUT',
    body: JSON.stringify(values),
  })

export const deployServerlessProxy = (values: ServerlessProxyValues) =>
  api<ServerlessProxyDeployResult>('/api/v1/settings/serverless-proxy/deploy', {
    method: 'POST',
    body: JSON.stringify(values),
  })

export const testServerlessProxy = () =>
  api<ServerlessProxyTest>('/api/v1/settings/serverless-proxy/test', { method: 'POST' })

export const enableServerlessProxy = () =>
  api<ServerlessProxyEnableResult>('/api/v1/settings/serverless-proxy/enable', { method: 'POST' })

export const disableServerlessProxy = () =>
  api<SettingsView>('/api/v1/settings/serverless-proxy/disable', { method: 'POST' })

export const deleteServerlessProxyDeployment = () =>
  api<SettingsView>('/api/v1/settings/serverless-proxy/deployment', { method: 'DELETE' })

export const deleteServerlessProxyNode = (nodeId: string) =>
  api<SettingsView>(`/api/v1/settings/serverless-proxy/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' })

export const createManualProxy = (proxyUrl: string, enabled = true) =>
  api<ManualProxy>('/api/v1/settings/manual-proxies', {
    method: 'POST',
    body: JSON.stringify({ proxy_url: proxyUrl, enabled }),
  })

export const updateManualProxy = (proxyId: string, proxyUrl: string, enabled = true) =>
  api<ManualProxy>(`/api/v1/settings/manual-proxies/${proxyId}`, {
    method: 'PUT',
    body: JSON.stringify({ proxy_url: proxyUrl, enabled }),
  })

export const deleteManualProxy = (proxyId: string) =>
  api<{ deleted: number }>(`/api/v1/settings/manual-proxies/${proxyId}`, { method: 'DELETE' })

export const toggleManualProxy = (proxyId: string) =>
  api<ManualProxy>(`/api/v1/settings/manual-proxies/${proxyId}/toggle`, { method: 'POST' })

export const testManualProxy = (proxyId: string) =>
  api<ManualProxyTest>(`/api/v1/settings/manual-proxies/${proxyId}/test`, { method: 'POST' })

export const clearSession = (provider: SessionProviderId) =>
  api<SettingsView>(`/api/v1/settings/sessions/${provider}`, { method: 'DELETE' })

export const startQrLogin = (provider: SessionProviderId) =>
  api<QrStart>(`/api/v1/settings/sessions/${provider}/qr/start`, { method: 'POST' })

export const pollQrLogin = (provider: SessionProviderId, sessionId: string) =>
  api<QrPoll>(`/api/v1/settings/sessions/${provider}/qr/${sessionId}`)

export const cancelQrLogin = (provider: SessionProviderId, sessionId: string) =>
  api<{ status: string }>(`/api/v1/settings/sessions/${provider}/qr/${sessionId}`, { method: 'DELETE' })
