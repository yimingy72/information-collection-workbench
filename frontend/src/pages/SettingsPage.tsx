import { useEffect, useRef, useState } from 'react'
import {
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  LogoutOutlined,
  PoweroffOutlined,
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
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  cancelQrLogin,
  clearSession,
  deleteServerlessProxyDeployment,
  deleteServerlessProxyNode,
  deployServerlessProxy,
  disableServerlessProxy,
  getSettings,
  pollQrLogin,
  saveServerlessProxy,
  testServerlessProxy,
  startQrLogin,
} from '../api'
import { formatDate } from '../formatters'
import type { CloudProvider, ServerlessProxyValues, SessionProviderId, SettingsView } from '../types'
import { ProxySettingsPage } from './ProxySettingsPage'

type SettingsSection = 'sources' | 'cloud' | 'manual'

const ALIYUN_REGIONS = [
  { value: 'cn-hangzhou', label: '华东 1（杭州）' },
  { value: 'cn-shanghai', label: '华东 2（上海）' },
  { value: 'cn-qingdao', label: '华北 1（青岛）' },
  { value: 'cn-beijing', label: '华北 2（北京）' },
  { value: 'cn-zhangjiakou', label: '华北 3（张家口）' },
  { value: 'cn-huhehaote', label: '华北 5（呼和浩特）' },
  { value: 'cn-shenzhen', label: '华南 1（深圳）' },
  { value: 'cn-chengdu', label: '西南 1（成都）' },
  { value: 'cn-hongkong', label: '中国香港' },
] as const

const TENCENT_REGIONS = [
  { value: 'ap-guangzhou', label: '华南地区（广州）' },
  { value: 'ap-shanghai', label: '华东地区（上海）' },
  { value: 'ap-beijing', label: '华北地区（北京）' },
  { value: 'ap-chengdu', label: '西南地区（成都）' },
  { value: 'ap-nanjing', label: '华东地区（南京）' },
  { value: 'ap-hongkong', label: '中国香港' },
] as const

const sessionTag = (status: SettingsView['sessions'][number]['status']) => {
  if (status === 'logged_in') return <Tag color="success">已登录</Tag>
  if (status === 'expired') return <Tag color="warning">已过期</Tag>
  return <Tag>未登录</Tag>
}

const proxyTag = (status?: string, enabled = false) => {
  if (status === 'ready') return <Tag color={enabled ? 'success' : 'processing'}>{enabled ? '运行中' : '已验证'}</Tag>
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
  const provider: CloudProvider = settings.serverless_proxy.provider === 'tencent' ? 'tencent' : 'aliyun'
  const regions = provider === 'tencent' ? TENCENT_REGIONS : ALIYUN_REGIONS
  const region = regions.some((item) => item.value === settings.serverless_proxy.region)
    ? settings.serverless_proxy.region
    : regions[0].value

  return {
    enabled: settings.serverless_proxy.enabled,
    provider,
    endpoint: settings.serverless_proxy.endpoint,
    region,
    function_name: settings.serverless_proxy.function_name,
    image_uri: settings.serverless_proxy.image_uri,
    access_key_id: settings.serverless_proxy.access_key_id,
    access_key_secret: undefined,
    insecure_skip_verify: settings.serverless_proxy.insecure_skip_verify,
  }
}

const managedFields = (provider: CloudProvider) => {
  if (provider === 'tencent') {
    return {
      keyLabel: 'SecretId',
      secretLabel: 'SecretKey',
      regions: TENCENT_REGIONS,
    }
  }
  return {
    keyLabel: 'AccessKey ID',
    secretLabel: 'AccessKey Secret',
    regions: ALIYUN_REGIONS,
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
  const [disablingProxy, setDisablingProxy] = useState(false)
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

  const saveProxy = async (notify = true) => {
    setSavingProxy(true)
    try {
      const values = { ...await proxyForm.validateFields(), image_uri: '' }
      const next = await saveServerlessProxy(values)
      applySettings(next)
      if (notify) message.success('配置已保存，当前查询路由未改变')
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
      const values = { ...await proxyForm.validateFields(), enabled: false, image_uri: '' }
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

  const onDisableProxy = async () => {
    setDisablingProxy(true)
    try {
      applySettings(await disableServerlessProxy())
      message.success('云函数代理已停用，企业查询恢复直连')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '停用代理失败')
    } finally {
      setDisablingProxy(false)
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

  const onDeleteProxyNode = async (nodeId: string) => {
    setDeletingProxy(true)
    try {
      applySettings(await deleteServerlessProxyNode(nodeId))
      message.success('云函数节点已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除云函数节点失败')
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
  const hasReadyManualProxy = Boolean(
    settings?.manual_proxies.some((item) => item.enabled && item.status === 'ready'),
  )
  const routeLabel = hasReadyManualProxy ? 'HTTP代理' : proxy?.enabled ? '云函数代理' : '直连'
  const routeColor = hasReadyManualProxy ? 'processing' : proxy?.enabled ? 'success' : 'default'
  const managed = managedFields(provider)

  const cloudNodeColumns: TableProps<NonNullable<SettingsView['serverless_proxy']['nodes']>[number]>['columns'] = [
    { title: '地域', dataIndex: 'region', width: 150 },
    { title: '函数', dataIndex: 'function_name', ellipsis: true },
    {
      title: '状态',
      key: 'status',
      width: 90,
      render: (_: unknown, row) => proxyTag(row.status, row.enabled),
    },
    {
      title: '延迟',
      dataIndex: 'latency_ms',
      width: 90,
      render: (value: number | null | undefined) => value == null ? '-' : `${value} ms`,
    },
    {
      title: '操作',
      key: 'actions',
      width: 76,
      render: (_: unknown, row) => (
        row.deployment_id ? (
          <Popconfirm
            title="删除这个云函数节点？"
            description="只删除当前节点，其他区域节点继续工作。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => void onDeleteProxyNode(row.id)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />} loading={deletingProxy} />
          </Popconfirm>
        ) : null
      ),
    },
  ]

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

  const cloudProxyTab = (
    <Card
      className="serverless-shell"
      title={(
        <Space>
          <CloudServerOutlined />
          <span>SeaMoon 云函数代理（ICP / 爱企查）</span>
          {proxyTag(proxy?.status, proxy?.enabled)}
        </Space>
      )}
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
          region: 'cn-hangzhou',
          function_name: 'asset-workbench-seamoon',
          insecure_skip_verify: false,
        }}
      >
        <div className="serverless-action-bar serverless-action-bar-top" role="toolbar" aria-label="云函数操作">
          <div className="serverless-action-meta">
            <Space size={6} wrap>
              <Typography.Text type="secondary">ICP / 爱企查查询路由</Typography.Text>
              <Tag color={routeColor}>{routeLabel}</Tag>
              <Typography.Text type="secondary" className="serverless-action-hint">
                手动代理优先；无可用手动代理时才使用云函数
              </Typography.Text>
            </Space>
          </div>
          <Space size={4} wrap className="serverless-action-buttons">
            <Button size="small" icon={<SaveOutlined />} loading={savingProxy} onClick={() => void saveProxy()}>
              保存
            </Button>
            <Button
              size="small"
              type='primary'
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
            {proxy?.enabled && (
              <Button
                size="small"
                icon={<PoweroffOutlined />}
                loading={disablingProxy}
                onClick={() => void onDisableProxy()}
              >
                停止
              </Button>
            )}
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
        </div>

        <Card type="inner" size="small" title="1. 选择云平台" className="serverless-section-card serverless-setup-card">
          <Form.Item name="provider" label="云平台" rules={[{ required: true }]}>
            <Select
              onChange={(value: CloudProvider) => {
                if (value === 'aliyun') proxyForm.setFieldValue('region', 'cn-hangzhou')
                if (value === 'tencent') proxyForm.setFieldValue('region', 'ap-guangzhou')
              }}
              options={[
                { value: 'aliyun', label: '阿里云函数计算 FC' },
                { value: 'tencent', label: '腾讯云函数 SCF' },
              ]}
            />
          </Form.Item>
        </Card>

        <Card type="inner" size="small" title="2. 填写云账户和函数参数" className="serverless-section-card">
          <Typography.Text className="serverless-managed-note" type="secondary">
            平台会自动创建并管理 SeaMoon 函数，无需填写函数地址或镜像；托管规格：0.1 vCPU / 128 MB / 512 MB / 单实例并发 6 / 最小实例 0。
          </Typography.Text>
          <div className="serverless-proxy-grid">
            <Form.Item name="region" label="函数地域" rules={[{ required: true, message: '请输入地域' }]}>
              <Select options={[...managed.regions]} />
            </Form.Item>
            <Form.Item name="function_name" label="函数名称" rules={[{ required: true, message: '请输入函数名称' }]}>
              <Input placeholder="asset-workbench-seamoon" />
            </Form.Item>
            <Form.Item name="access_key_id" label={managed.keyLabel} extra="只用于调用云平台 API 创建或删除函数。">
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item
              name="access_key_secret"
              label={managed.secretLabel}
              extra={proxy?.has_access_key_secret ? '已保存密钥；留空表示保持不变。' : '首次自动部署时需要填写。'}
            >
              <Input.Password autoComplete="new-password" placeholder={proxy?.has_access_key_secret ? '已保存，留空不修改' : ''} />
            </Form.Item>
          </div>
        </Card>

        <Card type="inner" size="small" title={`3. 节点池（${proxy?.nodes?.length ?? 0}）`} className="serverless-section-card">
          <Table
            className="serverless-node-table"
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={proxy?.nodes ?? []}
            columns={cloudNodeColumns}
            locale={{ emptyText: '尚未部署云函数节点' }}
          />
        </Card>

        <Card type="inner" size="small" title="4. 验证状态" className="serverless-section-card">
          <div className="serverless-route-summary">
            <Typography.Text type="secondary">部署会自动验证并启用代理；测试只检查链路，不切换当前路由。</Typography.Text>
            <Form.Item name="insecure_skip_verify" label="TLS 证书校验" valuePropName="checked" className="serverless-tls-item">
              <Switch size="small" checkedChildren="跳过" unCheckedChildren="校验" />
            </Form.Item>
          </div>
          {proxy?.last_error && <Alert className="proxy-error" type="error" showIcon title={proxy.last_error} />}
        </Card>
      </Form>
    </Card>
  )

  const manualProxyTab = <ProxySettingsPage embedded />

  return (
    <div className="page settings-page">
      <div className="settings-section-switch">
        <Segmented
          size="small"
          value={settingsSection}
          options={[
            { value: 'sources', label: '数据源配置', icon: <DatabaseOutlined /> },
            { value: 'cloud', label: '云函数配置', icon: <CloudServerOutlined /> },
            { value: 'manual', label: '代理设置', icon: <ApiOutlined /> },
          ]}
          onChange={(value) => setSettingsSection(value as SettingsSection)}
        />
      </div>
      <div className="settings-section-content">
        {settingsSection === 'sources' ? dataSourceTab : settingsSection === 'cloud' ? cloudProxyTab : manualProxyTab}
      </div>
    </div>
  )
}
