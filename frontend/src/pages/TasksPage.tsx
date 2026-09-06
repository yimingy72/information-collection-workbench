import { useEffect, useRef, useState } from 'react'
import { ArrowLeftOutlined, DeleteOutlined, DownloadOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Empty, Flex, Input, Popconfirm, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import {
  deleteHistory,
  getAllSubdomainResults,
  getQuery,
  getSubdomainRun,
  listHistory,
} from '../api'
import { SourceTag } from '../components/SourceTag'
import { StatusTag } from '../components/StatusTag'
import { QueryResultsPanel } from '../components/QueryResultsPanel'
import { TableFrame } from '../components/TableFrame'
import { exportQuery, exportSubdomains } from '../export'
import { formatDate, formatDuration, providerLabel } from '../formatters'
import { TABLE_PAGE_SIZE, usePagedData } from '../pagination'
import type { HistoryItem, HistoryKind, QueryView, SubdomainResult, SubdomainRun } from '../types'

const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '成功', value: 'succeeded' },
  { label: '部分成功', value: 'partial' },
  { label: '失败', value: 'failed' },
  { label: '查询中', value: 'running' },
  { label: '等待中', value: 'queued' },
]

const KIND_OPTIONS = [
  { label: '全部类型', value: '' },
  { label: 'ICP备案', value: 'collection' },
  { label: '子域名', value: 'subdomain' },
]

function historyKey(item: Pick<HistoryItem, 'kind' | 'id'>) {
  return `${item.kind}:${item.id}`
}

function historyTitle(item: HistoryItem) {
  return item.title || (item.kind === 'subdomain' ? '子域名查询' : '查询记录')
}

function SubdomainHistoryResults({
  run,
  results,
  loading,
}: {
  run: SubdomainRun
  results: SubdomainResult[]
  loading: boolean
}) {
  const paged = usePagedData(results, run.id)

  const columns: TableProps<SubdomainResult>['columns'] = [
    { title: '主域名', dataIndex: 'root_domain', width: 160, ellipsis: true },
    {
      title: '子域名',
      dataIndex: 'hostname',
      ellipsis: true,
      render: (value: string, row) => row.http_url ? <a href={row.http_url} target="_blank" rel="noreferrer">{value}</a> : value,
    },
    {
      title: '解析地址',
      dataIndex: 'ips',
      width: 220,
      render: (values: string[] = []) => values.join('、') || '—',
    },
    {
      title: 'HTTP',
      dataIndex: 'http_status',
      width: 80,
      align: 'center',
      render: (value?: number | null) => value ?? '—',
    },
    { title: '页面标题', dataIndex: 'title', ellipsis: true, render: (value: string) => value || '—' },
    {
      title: '来源',
      dataIndex: 'sources',
      width: 220,
      render: (values: string[] = []) => (
        <Flex gap={4} wrap>
          {values.map((value) => <Tag key={value}>{value}</Tag>)}
        </Flex>
      ),
    },
  ]

  return (
    <TableFrame pagination={paged.pagination}>
      <Table
        rowKey="id"
        className="table-fill"
        columns={columns}
        dataSource={paged.data}
        loading={loading}
        scroll={{ x: 1100 }}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={loading ? '正在加载子域名结果…' : '没有发现有效子域名'}
            />
          ),
        }}
      />
    </TableFrame>
  )
}

export function TasksPage() {
  const { message } = App.useApp()
  const [items, setItems] = useState<HistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(TABLE_PAGE_SIZE)
  const [keyword, setKeyword] = useState('')
  const [keywordDraft, setKeywordDraft] = useState('')
  const [status, setStatus] = useState('')
  const [kind, setKind] = useState<HistoryKind | ''>('')
  const [loading, setLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportingId, setExportingId] = useState<string | null>(null)
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [openItem, setOpenItem] = useState<HistoryItem | null>(null)
  const [openQuery, setOpenQuery] = useState<QueryView | null>(null)
  const [openSubdomain, setOpenSubdomain] = useState<SubdomainRun | null>(null)
  const [openSubdomainResults, setOpenSubdomainResults] = useState<SubdomainResult[]>([])
  const [openLoading, setOpenLoading] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)
  const detailRequest = useRef(0)

  const refresh = async () => {
    setLoading(true)
    try {
      const data = await listHistory(page, pageSize, keyword, status, kind)
      setItems(data.items)
      setTotal(data.total)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '无法加载历史查询')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (keywordDraft !== keyword) {
        setPage(1)
        setKeyword(keywordDraft)
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword, keywordDraft])

  useEffect(() => {
    void refresh()
  }, [page, pageSize, keyword, status, kind])

  useEffect(() => {
    const valid = new Set(items.map(historyKey))
    setSelectedKeys((current) => current.filter((key) => valid.has(key)))
    if (openItem && !valid.has(historyKey(openItem))) {
      detailRequest.current += 1
      setOpenItem(null)
      setOpenQuery(null)
      setOpenSubdomain(null)
      setOpenSubdomainResults([])
      setOpenError(null)
    }
  }, [items, openItem])

  const closeDetail = () => {
    detailRequest.current += 1
    setOpenItem(null)
    setOpenQuery(null)
    setOpenSubdomain(null)
    setOpenSubdomainResults([])
    setOpenError(null)
    setOpenLoading(false)
  }

  const openHistory = async (item: HistoryItem) => {
    if (openItem && historyKey(openItem) === historyKey(item)) {
      closeDetail()
      return
    }
    const requestId = ++detailRequest.current
    setOpenItem(item)
    setOpenQuery(null)
    setOpenSubdomain(null)
    setOpenSubdomainResults([])
    setOpenError(null)
    setOpenLoading(true)
    try {
      if (item.kind === 'collection') {
        const view = await getQuery(item.id)
        if (detailRequest.current !== requestId) return
        setOpenQuery(view)
      } else {
        const [run, results] = await Promise.all([
          getSubdomainRun(item.id),
          getAllSubdomainResults(item.id),
        ])
        if (detailRequest.current !== requestId) return
        setOpenSubdomain(run)
        setOpenSubdomainResults(results.items)
      }
    } catch (error) {
      if (detailRequest.current !== requestId) return
      setOpenError(error instanceof Error ? error.message : '无法读取查询结果')
    } finally {
      if (detailRequest.current === requestId) setOpenLoading(false)
    }
  }

  const remove = async (targets: HistoryItem[]) => {
    if (!targets.length) return
    setDeleting(true)
    try {
      await deleteHistory(targets.map((item) => ({ id: item.id, kind: item.kind })))
      setSelectedKeys((current) => current.filter((key) => !targets.some((item) => historyKey(item) === key)))
      if (openItem && targets.some((item) => historyKey(item) === historyKey(openItem))) closeDetail()
      message.success(targets.length > 1 ? `已删除 ${targets.length} 条记录` : '已删除')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const exportCollection = async (id: string) => {
    const view = await getQuery(id)
    if (!view.investments.length && !view.icp_records.length) {
      throw new Error('这条记录没有可导出的查询数据')
    }
    exportQuery(view)
  }

  const exportSubdomain = async (id: string) => {
    const [run, results] = await Promise.all([getSubdomainRun(id), getAllSubdomainResults(id)])
    if (!results.items.length) throw new Error('这条记录没有可导出的查询数据')
    exportSubdomains(run, results.items)
  }

  const exportOne = async (item: HistoryItem) => {
    setExportingId(historyKey(item))
    try {
      if (item.kind === 'collection') await exportCollection(item.id)
      else await exportSubdomain(item.id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '导出失败')
    } finally {
      setExportingId(null)
    }
  }

  const exportSelected = async () => {
    const selected = items.filter((item) => selectedKeys.includes(historyKey(item)))
    if (!selected.length) return
    setExporting(true)
    try {
      for (const item of selected) {
        if (item.kind === 'collection') await exportCollection(item.id)
        else await exportSubdomain(item.id)
      }
      message.success(`已导出 ${selected.length} 条记录`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '导出失败')
    } finally {
      setExporting(false)
    }
  }

  const columns: TableProps<HistoryItem>['columns'] = [
    {
      title: '查询对象',
      dataIndex: 'title',
      ellipsis: true,
      render: (_value: string, item) => (
        <Typography.Link
          ellipsis
          onClick={(event) => {
            event.preventDefault()
            void openHistory(item)
          }}
          title={historyTitle(item)}
        >
          {historyTitle(item)}
        </Typography.Link>
      ),
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (value: HistoryKind) => (
        <Tag color={value === 'subdomain' ? 'processing' : 'default'}>
          {value === 'subdomain' ? '子域名' : 'ICP备案'}
        </Tag>
      ),
    },
    {
      title: '摘要',
      key: 'summary',
      width: 280,
      render: (_, item) => {
        if (item.kind === 'collection') {
          const providers = item.summary.providers?.length ? item.summary.providers : []
          return (
            <Space className="source-tags" size={4} wrap>
              {providers.map((provider) => (
                <SourceTag key={provider} name={providerLabel(provider)} />
              ))}
              {item.summary.depth != null ? <Typography.Text type="secondary">{item.summary.depth} 层</Typography.Text> : null}
              {item.summary.holding_percent != null ? (
                <Typography.Text type="secondary">≥{Number(item.summary.holding_percent)}%</Typography.Text>
              ) : null}
            </Space>
          )
        }
        const domainCount = item.summary.domains?.length ?? 0
        return (
          <Typography.Text type="secondary">
            {domainCount} 个主域名 · {item.summary.discovered ?? 0} 条结果
          </Typography.Text>
        )
      },
    },
    { title: '状态', dataIndex: 'status', width: 100, render: (value) => <StatusTag status={value} /> },
    { title: '查询时间', dataIndex: 'created_at', width: 176, render: formatDate },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, item) => (
        <Space size={12}>
          <Typography.Link disabled={exportingId === historyKey(item) || deleting || exporting} onClick={() => void exportOne(item)}>
            导出
          </Typography.Link>
          <Popconfirm title="删除这条查询记录？" okText="删除" cancelText="取消" onConfirm={() => void remove([item])}>
            <Typography.Link type="danger">删除</Typography.Link>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const openTitle = openItem
    ? `${openItem.kind === 'subdomain' ? '子域名结果' : '查询结果'} · ${historyTitle(openItem)}`
    : '查询结果'
  const selectedItems = items.filter((item) => selectedKeys.includes(historyKey(item)))

  return (
    <Card className="page fill-card">
      <Flex vertical gap={12} className="fill-body">
        {openItem ? (
          <Card
            size="small"
            className="history-query-detail"
            title={
              <Space size={8}>
                <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={closeDetail}>
                  返回列表
                </Button>
                <Typography.Text strong>{openTitle}</Typography.Text>
              </Space>
            }
            extra={
              openQuery || openSubdomain ? (
                <Space size={8}>
                  <StatusTag status={(openQuery?.run.status ?? openSubdomain?.status) || openItem.status} />
                  <Typography.Text type="secondary">
                    查询用时 {formatDuration(
                      openQuery?.run.started_at ?? openSubdomain?.started_at ?? openItem.started_at,
                      openQuery?.run.finished_at ?? openSubdomain?.finished_at ?? openItem.finished_at,
                    )}
                  </Typography.Text>
                  <Button
                    size="small"
                    icon={<DownloadOutlined />}
                    disabled={openQuery
                      ? !openQuery.investments.length && !openQuery.icp_records.length
                      : !openSubdomainResults.length}
                    onClick={() => {
                      if (openQuery) exportQuery(openQuery)
                      else if (openSubdomain) exportSubdomains(openSubdomain, openSubdomainResults)
                    }}
                  >
                    导出 Excel
                  </Button>
                </Space>
              ) : null
            }
          >
            {openLoading ? (
              <div className="history-query-loading">
                <Typography.Text type="secondary">正在加载查询结果…</Typography.Text>
              </div>
            ) : openError ? (
              <Alert type="error" showIcon title={openError} />
            ) : openQuery ? (
              <>
                {openQuery.source_errors.length ? (
                  <Alert type="warning" showIcon title={openQuery.source_errors.join('；')} style={{ marginBottom: 12 }} />
                ) : null}
                <QueryResultsPanel query={openQuery} />
              </>
            ) : openSubdomain ? (
              <>
                <SubdomainHistoryResults run={openSubdomain} results={openSubdomainResults} loading={false} />
              </>
            ) : null}
          </Card>
        ) : (
          <>
            <Flex className="task-toolbar" align="center" gap={8} wrap="nowrap">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                value={keywordDraft}
                placeholder="搜索企业或域名"
                style={{ width: 240 }}
                onChange={(event) => setKeywordDraft(event.target.value)}
              />
              <Segmented
                value={kind}
                options={KIND_OPTIONS}
                onChange={(value) => {
                  setPage(1)
                  setKind(value as HistoryKind | '')
                }}
              />
              <Select
                value={status}
                options={STATUS_OPTIONS}
                style={{ width: 120 }}
                onChange={(value) => {
                  setPage(1)
                  setStatus(value)
                }}
              />
              <Button
                icon={<DownloadOutlined />}
                disabled={!selectedItems.length}
                loading={exporting}
                onClick={() => void exportSelected()}
              >
                导出所选
              </Button>
              <Popconfirm
                title={`删除所选 ${selectedItems.length} 条记录？`}
                okText="删除"
                cancelText="取消"
                disabled={!selectedItems.length}
                onConfirm={() => void remove(selectedItems)}
              >
                <Button danger icon={<DeleteOutlined />} disabled={!selectedItems.length} loading={deleting}>
                  删除所选
                </Button>
              </Popconfirm>
            </Flex>
            <TableFrame
              pagination={{
                current: page,
                pageSize,
                total,
                onChange: (nextPage, nextSize) => {
                  if (nextSize !== pageSize) {
                    setPage(1)
                    setPageSize(nextSize)
                    return
                  }
                  setPage(nextPage)
                },
              }}
            >
              <Table
                rowKey={historyKey}
                className="table-fill"
                columns={columns}
                dataSource={items}
                loading={loading}
                scroll={{ x: 1080 }}
                pagination={false}
                rowSelection={{
                  selectedRowKeys: selectedKeys,
                  onChange: (keys) => setSelectedKeys(keys.map(String)),
                }}
                locale={{
                  emptyText: (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={keyword || status || kind ? '没有符合筛选的记录' : '还没有查询记录'}
                    />
                  ),
                }}
              />
            </TableFrame>
          </>
        )}
      </Flex>
    </Card>
  )
}
