import {
  Alert,
  Badge,
  Button,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { HISTORY_ROWS, INVEST_ROWS, PROVIDERS, SHAREHOLDER_ROWS } from './mock'

export function Brand({ markClass = '' }: { markClass?: string }) {
  return (
    <div className="brand">
      <span className={`brand-mark ${markClass}`.trim()}>资</span>
      <span>信息收集工作台</span>
    </div>
  )
}

export function QueryFields({
  layout,
  size = 'middle',
  showKeyword = true,
  showSubmit = true,
}: {
  layout: 'inline' | 'stack'
  size?: 'middle' | 'large'
  showKeyword?: boolean
  showSubmit?: boolean
}) {
  const select = (
    <Select
      mode="multiple"
      options={[...PROVIDERS]}
      defaultValue={PROVIDERS.map((item) => item.value)}
      maxTagCount={4}
      allowClear={false}
      optionFilterProp="label"
      placeholder="选择数据源"
      style={{ width: layout === 'inline' ? 280 : '100%' }}
    />
  )
  const depth = (
    <Segmented options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 层` }))} defaultValue={1} />
  )
  const holding = <InputNumber min={0} max={100} defaultValue={51} addonAfter="%" style={{ width: 128 }} />

  if (layout === 'stack') {
    return (
      <Form layout="vertical" requiredMark={false} size={size}>
        {showKeyword ? (
          <Form.Item label="企业" required>
            <Input defaultValue="小米科技" prefix={<SearchOutlined />} allowClear />
          </Form.Item>
        ) : null}
        <Form.Item label="数据源" required>
          {select}
        </Form.Item>
        <Form.Item label="深度">{depth}</Form.Item>
        <Form.Item label="持股 ≥">{holding}</Form.Item>
        {showSubmit ? (
          <Button type="primary" icon={<SearchOutlined />} block>
            查询
          </Button>
        ) : null}
      </Form>
    )
  }

  return (
    <Form layout="vertical" requiredMark={false} size={size}>
      <Flex gap={12} wrap="wrap" align="flex-end">
        {showKeyword ? (
          <Form.Item label="企业" required style={{ flex: '1 1 280px', marginBottom: 0 }}>
            <Input defaultValue="小米科技" prefix={<SearchOutlined />} allowClear />
          </Form.Item>
        ) : null}
        <Form.Item label="数据源" required style={{ marginBottom: 0 }}>
          {select}
        </Form.Item>
        <Form.Item label="深度" style={{ marginBottom: 0 }}>
          {depth}
        </Form.Item>
        <Form.Item label="持股 ≥" style={{ marginBottom: 0 }}>
          {holding}
        </Form.Item>
        {showSubmit ? (
          <Form.Item label=" " style={{ marginBottom: 0 }}>
            <Button type="primary" icon={<SearchOutlined />}>
              查询
            </Button>
          </Form.Item>
        ) : null}
      </Flex>
    </Form>
  )
}

const investColumns = [
  { title: '投资方', dataIndex: 'parent' },
  { title: '被投企业', dataIndex: 'child' },
  {
    title: '持股',
    dataIndex: 'pct',
    width: 88,
    align: 'right' as const,
    render: (value: number) => `${value}%`,
  },
  { title: '层级', dataIndex: 'depth', width: 80, render: (depth: number) => `${depth} 层` },
  {
    title: '来源',
    dataIndex: 'src',
    width: 240,
    render: (src: string[]) => (
      <Space size={4} wrap>
        {src.map((name) => (
          <Tag key={name}>{name}</Tag>
        ))}
      </Space>
    ),
  },
]

const shareholderColumns = [
  { title: '股东', dataIndex: 'name' },
  { title: '持股企业', dataIndex: 'company' },
  {
    title: '持股',
    dataIndex: 'pct',
    width: 88,
    align: 'right' as const,
    render: (value: number) => `${value}%`,
  },
  {
    title: '来源',
    dataIndex: 'src',
    width: 220,
    render: (src: string[]) => (
      <Space size={4} wrap>
        {src.map((name) => (
          <Tag key={name}>{name}</Tag>
        ))}
      </Space>
    ),
  },
]

export function ResultLedger({ compactTitle = false }: { compactTitle?: boolean }) {
  return (
    <div>
      <Flex justify="space-between" align="flex-start" gap={16} wrap="wrap" style={{ marginBottom: 12 }}>
        <div>
          <Typography.Title level={compactTitle ? 4 : 3} style={{ margin: 0 }}>
            小米科技有限责任公司
          </Typography.Title>
          <Typography.Text type="secondary">天眼查、爱企查、快查、风鸟 · 1 层 · 持股 ≥ 100%</Typography.Text>
        </div>
        <Space>
          <Tag color="warning">部分成功</Tag>
          <Typography.Text type="secondary">15:12:08</Typography.Text>
        </Space>
      </Flex>
      <Alert
        type="warning"
        showIcon
        message="风鸟未返回股东数据；快查深度 2 被限流。"
        style={{ marginBottom: 12 }}
      />
      <Tabs
        items={[
          {
            key: 'invest',
            label: (
              <Space size={6}>
                对外投资
                <Badge count={INVEST_ROWS.length} showZero color="var(--lab-accent, #0f766e)" />
              </Space>
            ),
            children: (
              <Table
                size="middle"
                rowKey="key"
                columns={investColumns}
                dataSource={INVEST_ROWS}
                pagination={false}
                scroll={{ x: 720 }}
              />
            ),
          },
          {
            key: 'partner',
            label: (
              <Space size={6}>
                股东
                <Badge count={SHAREHOLDER_ROWS.length} showZero />
              </Space>
            ),
            children: (
              <Table
                size="middle"
                rowKey="key"
                columns={shareholderColumns}
                dataSource={SHAREHOLDER_ROWS}
                pagination={false}
                scroll={{ x: 640 }}
              />
            ),
          },
        ]}
      />
    </div>
  )
}

export function HistoryTable({ onOpen }: { onOpen?: () => void }) {
  return (
    <Table
      rowKey="key"
      dataSource={HISTORY_ROWS}
      pagination={false}
      scroll={{ x: 880 }}
      onRow={() => ({ onClick: onOpen, style: { cursor: 'pointer' } })}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有查询记录" /> }}
      columns={[
        {
          title: '企业',
          dataIndex: 'keyword',
          render: (keyword: string) => (
            <Button type="link" onClick={onOpen}>
              {keyword}
            </Button>
          ),
        },
        {
          title: '数据源',
          dataIndex: 'src',
          width: 280,
          render: (src: string[]) => (
            <Space size={4} wrap>
              {src.map((name) => (
                <Tag key={name}>{name}</Tag>
              ))}
            </Space>
          ),
        },
        { title: '深度', dataIndex: 'depth', width: 80, render: (depth: number) => `${depth} 层` },
        { title: '持股 ≥', dataIndex: 'holding', width: 88, render: (value: number) => `${value}%` },
        {
          title: '状态',
          dataIndex: 'status',
          width: 112,
          render: (status: string) => {
            const map: Record<string, { color: string; label: string }> = {
              succeeded: { color: 'success', label: '成功' },
              failed: { color: 'error', label: '失败' },
              partial: { color: 'warning', label: '部分成功' },
            }
            const item = map[status] ?? { color: 'default', label: status }
            return <Tag color={item.color}>{item.label}</Tag>
          },
        },
        { title: '查询时间', dataIndex: 'time', width: 176 },
      ]}
    />
  )
}
