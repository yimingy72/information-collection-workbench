import { useLayoutEffect, useRef, useState } from 'react'
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd'
import type { FormInstance } from 'antd'
import { exportQuery } from '../export'
import { StatusTag } from '../components/StatusTag'
import { formatDuration } from '../formatters'
import { QueryResultsPanel } from '../components/QueryResultsPanel'
import type { CollectionValues, ProviderId, QueryView, Run } from '../types'
import { PROVIDER_OPTIONS } from '../types'


function RecentTag({
  run,
  onPick,
  onForget,
}: {
  run: Run
  onPick: (run: Run) => void
  onForget: (run: Run) => void
}) {
  return (
    <Tag
      className="query-recent-tag"
      variant="outlined"
      closeIcon
      onClose={(event) => {
        event.preventDefault()
        event.stopPropagation()
        onForget(run)
      }}
    >
      <Typography.Link onClick={() => onPick(run)}>{run.keyword}</Typography.Link>
    </Tag>
  )
}

function RecentsBar({
  recents,
  onPick,
  onForget,
}: {
  recents: Run[]
  onPick: (run: Run) => void
  onForget: (run: Run) => void
}) {
  const measureRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(0)

  useLayoutEffect(() => {
    const measure = measureRef.current
    if (!measure) {
      setVisible(0)
      return
    }

    const fit = () => {
      const tags = Array.from(measure.querySelectorAll<HTMLElement>('.query-recent-tag'))
      const budget = measure.clientWidth
      let used = 0
      let count = 0
      for (const tag of tags) {
        const next = count === 0 ? tag.offsetWidth : used + 8 + tag.offsetWidth
        if (next > budget) break
        used = next
        count += 1
      }
      setVisible(count)
    }

    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(measure)
    return () => observer.disconnect()
  }, [recents])

  if (!recents.length) return null

  return (
    <div className="query-recents-wrap">
      <div ref={measureRef} className="query-recents query-recents-measure" aria-hidden>
        {recents.map((run) => (
          <RecentTag key={run.id} run={run} onPick={onPick} onForget={onForget} />
        ))}
      </div>
      <div className="query-recents" aria-label="最近查询">
        {recents.slice(0, visible).map((run) => (
          <RecentTag key={run.id} run={run} onPick={onPick} onForget={onForget} />
        ))}
      </div>
    </div>
  )
}

export function CollectionPage({
  form,
  loading,
  query,
  error,
  recents,
  onFinish,
  onPick,
  onForget,
}: {
  form: FormInstance<CollectionValues>
  loading: boolean
  query: QueryView | null
  error: string | null
  recents: Run[]
  onFinish: (values: CollectionValues) => void
  onPick: (run: Run) => void
  onForget: (run: Run) => void
}) {
  const { message } = App.useApp()

  return (
    <div className="page page-split">
      <Card size="small" className="page-head">
        <Form<CollectionValues>
          form={form}
          layout="horizontal"
          requiredMark={false}
          colon={false}
          className="query-form"
          initialValues={{ keyword: '', providers: ['tianyancha'], depth: 1, holding_percent: 100 }}
          onFinish={onFinish}
          onFinishFailed={(info) => {
            const first = info.errorFields[0]?.errors[0]
            if (first) message.warning(first)
          }}
        >
          <Flex gap={16} wrap={false} align="flex-start" className="query-fields">
            <Form.Item label="企业" className="query-company">
              <Flex vertical gap={6} className="query-company-field">
                <Form.Item
                  name="keyword"
                  noStyle
                  rules={[
                    { required: true, message: '请输入企业名称' },
                    { min: 2, message: '至少 2 个字符' },
                  ]}
                >
                  <Input
                    prefix={<SearchOutlined />}
                    placeholder="例如：小米科技有限责任公司"
                    allowClear
                    autoComplete="off"
                  />
                </Form.Item>
                <RecentsBar recents={recents} onPick={onPick} onForget={onForget} />
              </Flex>
            </Form.Item>
            <Form.Item
              name="providers"
              label="数据源"
              rules={[{ required: true, type: 'array', min: 1, message: '请选择数据源' }]}
            >
              <Select
                className="query-providers"
                mode="multiple"
                options={PROVIDER_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
                maxTagCount={4}
                allowClear={false}
                showSearch={{ optionFilterProp: 'label' }}
                placeholder="选择数据源"
                style={{ width: 360 }}
              />
            </Form.Item>
            <Form.Item name="depth" label="查询深度">
              <Select options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 层` }))} style={{ width: 88 }} />
            </Form.Item>
            <Form.Item label="持股 ≥">
              <Space.Compact>
                <Form.Item name="holding_percent" noStyle>
                  <InputNumber min={0} max={100} precision={0} controls={false} style={{ width: 64 }} />
                </Form.Item>
                <Space.Addon>%</Space.Addon>
              </Space.Compact>
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} icon={<SearchOutlined />}>
                查询
              </Button>
            </Form.Item>
          </Flex>
        </Form>
      </Card>

      <Card
        className="fill-card"
        title={query ? query.run.keyword : undefined}
        extra={
          query ? (
            <Space>
              <StatusTag status={query.run.status} />
              <Typography.Text type="secondary">
                查询用时 {formatDuration(query.run.started_at, query.run.finished_at)}
              </Typography.Text>
              <Button
                icon={<DownloadOutlined />}
                disabled={!query.investments.length && !query.icp_records.length}
                onClick={() => exportQuery(query)}
              >
                导出 Excel
              </Button>
            </Space>
          ) : null
        }
      >
        {error ? <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} /> : null}
        {query?.source_errors?.length ? (
          <Alert type="warning" showIcon title={query.source_errors.join('；')} style={{ marginBottom: 16 }} />
        ) : null}
        {query ? (
          <QueryResultsPanel query={query} loading={loading} />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={loading ? '正在查询' : '输入企业名称并选择数据源后开始查询'}
          />
        )}
      </Card>
    </div>
  )
}

export function valuesFromRun(run: Run): CollectionValues {
  return {
    keyword: run.keyword,
    providers: (run.providers?.length ? run.providers : [run.provider]).filter((id): id is ProviderId =>
      PROVIDER_OPTIONS.some((item) => item.value === id),
    ),
    depth: run.depth,
    holding_percent: Number(run.holding_percent),
  }
}
