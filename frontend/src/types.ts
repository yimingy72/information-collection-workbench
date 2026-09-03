export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'partial' | 'cancelled' | string

export const PROVIDER_OPTIONS = [
  { value: 'tianyancha', label: '天眼查' },
  { value: 'aiqicha', label: '爱企查' },
  { value: 'kuaicha', label: '快查' },
  { value: 'riskbird', label: '风鸟' },
] as const

export type ProviderId = typeof PROVIDER_OPTIONS[number]['value']

export type CollectionValues = {
  keyword: string
  providers: ProviderId[]
  depth: number
  holding_percent: number
}

export type Run = {
  id: string
  keyword: string
  provider: string
  providers: string[]
  depth: number
  holding_percent: number
  include_branches: boolean
  fields: string[]
  status: RunStatus
  attempts: number
  progress: number
  total?: number | null
  icp_cache_hits: number
  icp_live_queries: number
  error?: string | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

export type RunList = {
  items: Run[]
  total: number
}

export type InvestmentRow = {
  parent_name: string
  child_name: string
  holding_percent?: number | null
  depth: number
  source: string
}

export type IcpRow = {
  unit_name: string
  main_licence: string
  service_licence: string
  domain: string
  nature_name: string
  update_time: string
  source: string
}

export type ShareholderRow = {
  name: string
  company: string
  holding_percent?: number | null
  source: string
}

export type QueryView = {
  run: Run
  investments: InvestmentRow[]
  shareholders: ShareholderRow[]
  icp_records: IcpRow[]
  source_errors: string[]
}


export const SESSION_PROVIDERS = [
  { value: 'aiqicha', label: '爱企查' },
  { value: 'kuaicha', label: '快查' },
  { value: 'riskbird', label: '风鸟' },
] as const

export type SessionProviderId = typeof SESSION_PROVIDERS[number]['value']
export type SessionStatus = 'logged_out' | 'logged_in' | 'expired'

export type ProviderSession = {
  provider: SessionProviderId
  label: string
  status: SessionStatus
  expires_at?: string | null
  updated_at?: string | null
}

export type CloudProvider = 'aliyun' | 'tencent' | 'custom'

export type ServerlessProxyNode = {
  id: string
  enabled: boolean
  provider: CloudProvider
  endpoint: string
  region: string
  function_name: string
  image_uri: string
  access_key_id: string
  has_access_key_secret: boolean
  insecure_skip_verify: boolean
  deployment_id: string
  status: string
  last_error: string
  latency_ms?: number | null
  failure_count: number
  updated_at?: string | null
}

export type ServerlessProxySettings = {
  enabled: boolean
  provider: CloudProvider
  endpoint: string
  region: string
  function_name: string
  image_uri: string
  access_key_id: string
  has_access_key_secret: boolean
  insecure_skip_verify: boolean
  deployment_id: string
  status: string
  last_error: string
  local_proxy_url: string
  updated_at?: string | null
  nodes: ServerlessProxyNode[]
}

export type ServerlessProxyValues = {
  enabled: boolean
  provider: CloudProvider
  endpoint: string
  region: string
  function_name: string
  image_uri: string
  access_key_id: string
  access_key_secret?: string
  insecure_skip_verify: boolean
}

export type ServerlessProxyTest = {
  status: 'ok'
  latency_ms: number
  endpoint: string
  target: string
  tested_nodes?: number
  successful_nodes?: number
}

export type ServerlessProxyEnableResult = {
  settings: SettingsView
  test: ServerlessProxyTest
}

export type ServerlessProxyDeployResult = {
  settings: SettingsView
  test: ServerlessProxyTest
}

export type ManualProxy = {
  id: string
  scheme: 'http' | 'https'
  host: string
  port: number
  username: string
  has_password: boolean
  enabled: boolean
  status: string
  latency_ms?: number | null
  failure_count: number
  last_error: string
  last_tested_at?: string | null
  created_at: string
  updated_at: string
}

export type ManualProxyTest = {
  status: 'ok'
  proxy_id: string
  latency_ms: number
  target: string
}

export type SettingsView = {
  sessions: ProviderSession[]
  serverless_proxy: ServerlessProxySettings
  manual_proxies: ManualProxy[]
}

export type LoginValues = {
  cookie: string
  expires_at?: string | null
}

export type QrStart = {
  session_id: string
  image_base64: string
  expires_in: number
}

export type QrPollStatus = 'pending' | 'scanned' | 'success' | 'failed' | 'expired'

export type QrPoll = {
  status: QrPollStatus
}

export type SubdomainOptions = {
  passive: boolean
  brute_force: boolean
  deep_scan: boolean
  http_probe: boolean
}

export type SubdomainRun = {
  id: string
  domains: string[]
  source_run_ids: string[]
  options: SubdomainOptions
  status: RunStatus
  phase: string
  attempts: number
  progress: number
  total?: number | null
  discovered: number
  warnings: string[]
  error?: string | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

export type SubdomainRunList = {
  items: SubdomainRun[]
  total: number
}

export type SubdomainResult = {
  id: number
  run_id: string
  root_domain: string
  hostname: string
  ips: string[]
  canonical_name: string
  dns_status: string
  wildcard: boolean
  http_url: string
  http_status?: number | null
  title: string
  sources: string[]
  discovered_at: string
}

export type SubdomainResults = {
  run_id: string
  items: SubdomainResult[]
  total: number
}

export type IcpDomainRun = {
  id: string
  keyword: string
  created_at: string
  domains: string[]
}
