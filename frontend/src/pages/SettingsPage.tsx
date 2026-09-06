import { useEffect, useRef, useState } from 'react'
import {
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  LogoutOutlined,
  QrcodeOutlined,
  RocketOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Form,
  Input,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  cancelQrLogin,
  clearSession,
  deleteServerlessProxyDeployment,
  deployServerlessProxy,
  getSettings,
  pollQrLogin,
  saveServerlessProxy,
  testServerlessProxy,
  startQrLogin,
} from '../api'
import { formatDate } from '../formatters'
import type { CloudProvider, ServerlessProxyValues, SessionProviderId, SettingsView } from '../types'
import { ProxySettingsPage } from './ProxySettingsPage'

type SettingsSection = 'sources' | 'proxy'

const DEFAULT_FUNCTION_NAME = 'asset-workbench-seamoon'
const ALIYUN_DEFAULT_REGION = 'cn-hangzhou'
const TENCENT_DEFAULT_REGION = 'ap-guangzhou'
const ALIYUN_REGIONS = [
  ALIYUN_DEFAULT_REGION,
  'cn-shanghai',
  'cn-qingdao',
  'cn-beijing',
  'cn-zhangjiakou',
  'cn-huhehaote',
  'cn-shenzhen',
  'cn-chengdu',
  'cn-hongkong',
] as const
const TENCENT_REGIONS = [
  TENCENT_DEFAULT_REGION,
  'ap-shanghai',
  'ap-beijing',
  'ap-chengdu',
  'ap-nanjing',
  'ap-hongkong',
] as const

const managedCloudTarget = (provider: CloudProvider, region?: string, functionName?: string) => {
  const nextProvider: CloudProvider = provider === 'tencent' ? 'tencent' : 'aliyun'
  const allowed = nextProvider === 'tencent' ? TENCENT_REGIONS : ALIYUN_REGIONS
  const fallback = nextProvider === 'tencent' ? TENCENT_DEFAULT_REGION : ALIYUN_DEFAULT_REGION
  const nextRegion = allowed.find((item) => item === region) ?? fallback
  return {
    provider: nextProvider,
    region: nextRegion,
    function_name: (functionName || '').trim() || DEFAULT_FUNCTION_NAME,
  }
}

const sessionTag = (status: SettingsView['sessions'][number]['status']) => {
  if (status === 'logged_in') return <Tag color="success">已登录</Tag>
  if (status === 'expired') return <Tag color="warning">已过期</Tag>
  return <Tag>未登录</Tag>
}

const proxyTag = (status?: string) => {
  if (status === 'ready') return <Tag color="success">已就绪</Tag>
  if (status === 'deployed') return <Tag color="processing">已部署</Tag>
  if (status === 'deploying' || status === 'testing') return <Tag color="processing">处理中</Tag>
  if (status === 'error') return <Tag color="error">异常</Tag>
  if (status === 'configured') return <Tag color="warning">已配置</Tag>
  return <Tag>未配置</Tag>
}

function QrLogin({ provider, onSuccess }: { provider: SessionProviderId; onSuccess: () => void }) {
  const { message } = App.useApp()
  const [starting, setStarting] = useState(false)
  const [qr, setQr] = useState<{ sessionId: string; image: string } | null>(null)
  const [scanned, setScanned] = useState(false)
  const timer = useRef<number | null>(null)
  const cancelled = useRef(false)

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }

  useEffect(
    () => () => {
      cancelled.current = true
      clearTimer()
    },
    [],
  )

  const poll = async (sessionId: string) => {
    try {
      const result = await pollQrLogin(provider, sessionId)
      if (cancelled.current) return
      if (result.status === 'scanned') {
        setScanned(true)
        timer.current = window.setTimeout(() => void poll(sessionId), 1200)
        return
      }
      if (result.status === 'success') {
        clearTimer()
        setQr(null)
        setScanned(false)
        onSuccess()
        message.success('扫码登录成功')
        return
      }
      if (result.status === 'failed' || result.status === 'expired') {
        clearTimer()
        setQr(null)
        setScanned(false)
        message.warning(result.status === 'expired' ? '二维码已过期，请重新获取' : '扫码登录失败，请重试')
        return
      }
      timer.current = window.setTimeout(() => void poll(sessionId), 1200)
    } catch {
      if (cancelled.current) return
      timer.current = window.setTimeout(() => void poll(sessionId), 2500)
    }
  }

  const start = async () => {
    setStarting(true)
    try {
      const data = await startQrLogin(provider)
      cancelled.current = false
      setScanned(false)
      setQr({ sessionId: data.session_id, image: data.image_base64 })
      void poll(data.session_id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '二维码获取失败')
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    cancelled.current = true
    clearTimer()
    const current = qr
    setQr(null)
    setScanned(false)
    if (current) {
      try {
        await cancelQrLogin(provider, current.sessionId)
      } catch {
        // The server-side QR session expires automatically.
      }
    }
  }

  if (qr) {
    return (
      <Flex vertical gap={8} align="center">
        <img className="qr-image" src={`data:image/png;base64,${qr.image}`} alt="扫码登录" />
        <Typography.Text type="secondary">{scanned ? '已扫码，请在手机上确认' : '手机扫码确认登录'}</Typography.Text>
        <Button size="small" onClick={() => void cancel()}>取消</Button>
      </Flex>
    )
  }

  return (
    <Button size="small" icon={<QrcodeOutlined />} loading={starting} onClick={() => void start()}>
      扫码登录
    </Button>
  )
}

const proxyFormValues = (settings: SettingsView): ServerlessProxyValues => {
  const target = managedCloudTarget(
    settings.serverless_proxy.provider === 'tencent' ? 'tencent' : 'aliyun',
    settings.serverless_proxy.region,
    settings.serverless_proxy.function_name,
  )
  return {
    enabled: settings.serverless_proxy.enabled,
    provider: target.provider,
    endpoint: settings.serverless_proxy.endpoint,
    region: target.region,
    function_name: target.function_name,
    image_uri: settings.serverless_proxy.image_uri,
    access_key_id: settings.serverless_proxy.access_key_id,
    access_key_secret: undefined,
    insecure_skip_verify: settings.serverless_proxy.insecure_skip_verify,
  }
}

export function SettingsPage() {
  const { message } = App.useApp()
  const [proxyForm] = Form.useForm<ServerlessProxyValues>()
  const provider = Form.useWatch('provider', proxyForm) ?? 'aliyun'
  const [settings, setSettings] = useState<SettingsView | null>(null)
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('sources')
  const [loading, setLoading] = useState(true)
  const [loggingOut, setLoggingOut] = useState<SessionProviderId | null>(null)
  const [savingProxy, setSavingProxy] = useState(false)
  const [deployingProxy, setDeployingProxy] = useState(false)
  const [testingProxy, setTestingProxy] = useState(false)
  const [deletingProxy, setDeletingProxy] = useState(false)

  const applySettings = (next: SettingsView) => {
    setSettings(next)
    proxyForm.setFieldsValue(proxyFormValues(next))
  }

  const refresh = async () => {
    try {
      applySettings(await getSettings())
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法读取基础配置')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const cloudFormValues = async (overrides: Partial<ServerlessProxyValues> = {}) => {
    const values = await proxyForm.validateFields()
    const target = managedCloudTarget(values.provider, values.region, values.function_name)
    proxyForm.setFieldsValue(target)
    return {
      ...values,
      ...target,
      ...overrides,
      image_uri: '',
    }
  }

  const saveProxy = async (notify = true) => {
    setSavingProxy(true)
    try {
      const values = await cloudFormValues()
      const next = await saveServerlessProxy(values)
      applySettings(next)
      if (notify) message.success('云函数配置已保存')
      return true
    } catch (error) {
      if (error instanceof Error) message.error(error.message)
      return false
    } finally {
      setSavingProxy(false)
    }
  }

  const onDeployProxy = async () => {
    setDeployingProxy(true)
    try {
      const values = await cloudFormValues({ enabled: false })
      const result = await deployServerlessProxy(values)
      applySettings(result.settings)
      message.success(`云函数已部署、验证并启用，百度实测 ${result.test.latency_ms} ms`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '云函数部署或验证失败')
      void refresh()
    } finally {
      setDeployingProxy(false)
    }
  }

  const onTestProxy = async () => {
    setTestingProxy(true)
    try {
      const result = await testServerlessProxy()
      await refresh()
      message.success(`云函数测试成功，百度实测 ${result.latency_ms} ms`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '云函数测试失败')
      void refresh()
    } finally {
      setTestingProxy(false)
    }
  }

  const onDeleteProxy = async () => {
    setDeletingProxy(true)
    try {
      applySettings(await deleteServerlessProxyDeployment())
      message.success('云函数已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '云函数删除失败')
    } finally {
      setDeletingProxy(false)
    }
  }

  const onLogout = async (sessionProvider: SessionProviderId) => {
    setLoggingOut(sessionProvider)
    try {
      applySettings(await clearSession(sessionProvider))
      message.success('已退出')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '退出失败')
    } finally {
      setLoggingOut(null)
    }
  }

  const sessionColumns: TableProps<SettingsView['sessions'][number]>['columns'] = [
    { title: '数据源', dataIndex: 'label', width: 160 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: SettingsView['sessions'][number]['status']) => sessionTag(status),
    },
    {
      title: '过期时间',
      dataIndex: 'expires_at',
      width: 220,
      render: (value: string | null | undefined, row) => {
        if (row.status === 'logged_out') return '-'
        return value ? formatDate(value) : '未记录'
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_: unknown, row) => (
        row.status === 'logged_in' ? (
          <Button
            type="link"
            size="small"
            icon={<LogoutOutlined />}
            loading={loggingOut === row.provider}
            onClick={() => void onLogout(row.provider)}
          >
            退出
          </Button>
        ) : (
          <QrLogin provider={row.provider} onSuccess={() => void refresh()} />
        )
      ),
    },
  ]

  const proxy = settings?.serverless_proxy

  const dataSourceTab = (
    <Card title="数据源登录" size="small" loading={loading}>
      <Table
        className="settings-source-table"
        rowKey="provider"
        size="small"
        pagination={false}
        dataSource={settings?.sessions ?? []}
        columns={sessionColumns}
      />
    </Card>
  )

  const cloudActions = (
    <Space size={8} wrap className="serverless-action-buttons">
      <Button size="small" icon={<SaveOutlined />} loading={savingProxy} onClick={() => void saveProxy()}>
        保存
      </Button>
      <Button
        size="small"
        type="primary"
        icon={<RocketOutlined />}
        loading={deployingProxy}
        onClick={() => void onDeployProxy()}
      >
        一键部署
      </Button>
      <Button
        size="small"
        icon={<ApiOutlined />}
        disabled={!proxy?.nodes?.length}
        loading={testingProxy}
        onClick={() => void onTestProxy()}
      >
        测试
      </Button>
      {proxy?.deployment_id && (
        <Popconfirm
          title="删除云函数？"
          description="将从云平台删除该函数，平台同时关闭代理。"
          okText="删除"
          cancelText="取消"
          onConfirm={() => void onDeleteProxy()}
        >
          <Button size="small" danger icon={<DeleteOutlined />} loading={deletingProxy}>删除</Button>
        </Popconfirm>
      )}
    </Space>
  )

  const cloudProxyTab = (
    <Card
      className="serverless-shell"
      title={(
        <Space>
          <CloudServerOutlined />
          <span>云函数代理</span>
          {proxyTag(proxy?.status)}
        </Space>
      )}
      extra={cloudActions}
      size="small"
      loading={loading}
    >
      <Form
        form={proxyForm}
        size="small"
        layout="vertical"
        className="serverless-proxy-form serverless-compact-form"
        initialValues={{
          enabled: false,
          provider: 'aliyun',
          region: ALIYUN_DEFAULT_REGION,
          function_name: DEFAULT_FUNCTION_NAME,
          insecure_skip_verify: false,
        }}
      >
        <Form.Item name="region" hidden>
          <Input />
        </Form.Item>
        <Form.Item name="function_name" hidden>
          <Input />
        </Form.Item>
        <div className="serverless-proxy-grid">
          <Form.Item name="provider" label="云平台" rules={[{ required: true }]}>
            <Select
              onChange={(value: CloudProvider) => {
                proxyForm.setFieldsValue(managedCloudTarget(value, undefined, proxyForm.getFieldValue('function_name')))
              }}
              options={[
                { value: 'aliyun', label: '阿里云函数计算 FC' },
                { value: 'tencent', label: '腾讯云函数 SCF' },
              ]}
            />
          </Form.Item>
          <Form.Item name="access_key_id" label="AccessKey ID" rules={[{ required: true, message: '请输入 AccessKey ID' }]}>
            <Input autoComplete="off" placeholder={provider === 'tencent' ? '腾讯云 SecretId' : '阿里云 AccessKey ID'} />
          </Form.Item>
          <Form.Item name="access_key_secret" label="AccessKey Secret">
            <Input.Password autoComplete="new-password" placeholder={proxy?.has_access_key_secret ? '已保存，留空不修改' : (provider === 'tencent' ? '腾讯云 SecretKey' : '阿里云 AccessKey Secret')} />
          </Form.Item>
        </div>
        {proxy?.last_error && <Alert className="proxy-error" type="error" showIcon title={proxy.last_error} />}
      </Form>
    </Card>
  )

  const proxyPoolTab = (
    <div className="proxy-pool-page">
      {cloudProxyTab}
      <ProxySettingsPage embedded />
    </div>
  )

  return (
    <div className="page settings-page">
      <div className="settings-section-switch">
        <Segmented
          size="small"
          value={settingsSection}
          options={[
            { value: 'sources', label: '数据源配置', icon: <DatabaseOutlined /> },
            { value: 'proxy', label: '代理池配置', icon: <CloudServerOutlined /> },
          ]}
          onChange={(value) => setSettingsSection(value as SettingsSection)}
        />
      </div>
      <div className="settings-section-content">
        {settingsSection === 'sources' ? dataSourceTab : proxyPoolTab}
      </div>
    </div>
  )
}
