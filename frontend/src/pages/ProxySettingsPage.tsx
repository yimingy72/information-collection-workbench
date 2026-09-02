import { useEffect, useState } from 'react'
import {
  ApiOutlined,
  DeleteOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import {
  createManualProxy,
  deleteManualProxy,
  getSettings,
  testManualProxy,
  toggleManualProxy,
} from '../api'
import { formatDate } from '../formatters'
import type { ManualProxy } from '../types'

type ProxyFormValues = { proxy_url: string }

const statusTag = (status: string, enabled: boolean) => {
  if (status === 'ready' && enabled) return <Tag color="success">可用</Tag>
  if (status === 'testing') return <Tag color="processing">检测中</Tag>
  if (status === 'error') return <Tag color="error">异常</Tag>
  if (status === 'disabled' || !enabled) return <Tag>已停用</Tag>
  return <Tag color="warning">待检测</Tag>
}

const displayAddress = (proxy: ManualProxy) => {
  const auth = proxy.username ? `${proxy.username}:****@` : ''
  return `${proxy.scheme}://${auth}${proxy.host}:${proxy.port}`
}

export function ProxySettingsPage({ embedded = false }: { embedded?: boolean }) {
  const { message } = App.useApp()
  const [form] = Form.useForm<ProxyFormValues>()
  const [proxies, setProxies] = useState<ManualProxy[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    try {
      const settings = await getSettings()
      setProxies(settings.manual_proxies ?? [])
      setError(null)
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : '无法读取代理设置'
      setError(detail)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const addProxy = async (values: ProxyFormValues) => {
    setSaving(true)
    try {
      await createManualProxy(values.proxy_url, true)
      form.resetFields()
      await refresh()
      message.success('代理已添加，请点击检测确认可用')
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : '添加代理失败')
    } finally {
      setSaving(false)
    }
  }

  const testProxy = async (proxy: ManualProxy) => {
    setTestingId(proxy.id)
    try {
      const result = await testManualProxy(proxy.id)
      await refresh()
      message.success(`代理检测成功，百度延迟 ${result.latency_ms} ms`)
    } catch (reason) {
      await refresh()
      message.error(reason instanceof Error ? reason.message : '代理检测失败')
    } finally {
      setTestingId(null)
    }
  }

  const toggleProxy = async (proxy: ManualProxy) => {
    setTogglingId(proxy.id)
    try {
      await toggleManualProxy(proxy.id)
      await refresh()
      message.success(proxy.enabled ? '代理已停用' : '代理已启用')
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : '切换代理状态失败')
    } finally {
      setTogglingId(null)
    }
  }

  const removeProxy = async (proxy: ManualProxy) => {
    setDeletingId(proxy.id)
    try {
      await deleteManualProxy(proxy.id)
      await refresh()
      message.success('代理已删除')
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : '删除代理失败')
    } finally {
      setDeletingId(null)
    }
  }

  const columns: TableProps<ManualProxy>['columns'] = [
    {
      title: '代理地址',
      key: 'address',
      ellipsis: true,
      render: (_: unknown, proxy) => <Typography.Text code title={displayAddress(proxy)}>{displayAddress(proxy)}</Typography.Text>,
    },
    {
      title: '状态',
      key: 'status',
      width: 92,
      render: (_: unknown, proxy) => statusTag(proxy.status, proxy.enabled),
    },
    {
      title: '延迟',
      dataIndex: 'latency_ms',
      width: 90,
      render: (value: number | null | undefined) => value == null ? '-' : `${value} ms`,
    },
    {
      title: '失败次数',
      dataIndex: 'failure_count',
      width: 86,
    },
    {
      title: '最近检测',
      dataIndex: 'last_tested_at',
      width: 168,
      render: (value: string | null | undefined) => value ? formatDate(value) : '未检测',
    },
    {
      title: '错误信息',
      dataIndex: 'last_error',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    {
      title: '启用',
      key: 'enabled',
      width: 70,
      render: (_: unknown, proxy) => (
        <Button
          type="link"
          size="small"
          loading={togglingId === proxy.id}
          disabled={proxy.status === 'testing'}
          onClick={() => void toggleProxy(proxy)}
        >
          {proxy.enabled ? '停用' : '启用'}
        </Button>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: unknown, proxy) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<ApiOutlined />}
            loading={testingId === proxy.id}
            onClick={() => void testProxy(proxy)}
          >
            检测
          </Button>
          <Popconfirm
            title="删除这个代理？"
            description="删除后不会再用于代理轮换。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => void removeProxy(proxy)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />} loading={deletingId === proxy.id}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className={embedded ? 'proxy-settings-page' : 'page proxy-settings-page'}>
      <Card
        size="small"
        title={(
          <Space>
            <ApiOutlined />
            <span>代理设置</span>
          </Space>
        )}
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button>}
      >
        <Alert
          type="info"
          showIcon
          title="所有已启用且检测成功的手动代理会组成轮询池。手动代理池优先于云函数；没有可用手动代理时才使用已启用的云函数。"
          description="查询页面不单独选择代理，代理路由在这里统一配置；同一查询不会混用手动代理和云函数。"
          className="proxy-settings-note"
        />
        <Form
          form={form}
          layout="inline"
          size="small"
          className="manual-proxy-form"
          onFinish={(values) => void addProxy(values)}
        >
          <Form.Item
            name="proxy_url"
            label="代理地址"
            rules={[{ required: true, message: '请输入代理地址' }]}
            className="manual-proxy-input"
          >
            <Input placeholder="t18831534475414:密码@h515.kdltps.com:15818" allowClear />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={saving}>添加代理</Button>
          </Form.Item>
        </Form>
        {error && <Alert type="error" showIcon title={error} className="proxy-settings-error" />}
        <Table
          className="manual-proxy-table"
          rowKey="id"
          size="small"
          loading={loading}
          pagination={false}
          dataSource={proxies}
          columns={columns}
          locale={{ emptyText: '暂无手动代理' }}
        />
      </Card>
    </div>
  )
}
