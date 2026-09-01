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
}

export type ServerlessProxyEnableResult = {
  settings: SettingsView
  test: ServerlessProxyTest
}

export type ServerlessProxyDeployResult = {
  settings: SettingsView
  test: ServerlessProxyTest
}

export type SettingsView = {
  sessions: ProviderSession[]
  serverless_proxy: ServerlessProxySettings
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
