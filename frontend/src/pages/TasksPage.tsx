import { useEffect, useRef, useState } from 'react'
import { ArrowLeftOutlined, DeleteOutlined, DownloadOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Empty, Flex, Input, Popconfirm, Select, Space, Table, Typography } from 'antd'
import type { TableProps } from 'antd'
import { getQuery } from '../api'
import { SourceTag } from '../components/SourceTag'
import { StatusTag } from '../components/StatusTag'
import { exportQuery } from '../export'
import { formatDate, formatDuration, providerLabel } from '../formatters'
import { QueryResultsPanel } from '../components/QueryResultsPanel'
import { TableFrame } from '../components/TableFrame'
import type { QueryView, Run } from '../types'

const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '成功', value: 'succeeded' },
  { label: '部分成功', value: 'partial' },
  { label: '失败', value: 'failed' },
  { label: '查询中', value: 'running' },
  { label: '等待中', value: 'queued' },
]

export function TasksPage({
  runs,
  total,
  page,
  pageSize,
  loading,
  keyword,
  status,
  onKeywordChange,
  onStatusChange,
  onPageChange,
  onDelete,
}: {
  runs: Run[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  keyword: string
  status: string
  onKeywordChange: (value: string) => void
  onStatusChange: (value: string) => void
  onPageChange: (page: number, pageSize: number) => void
  onDelete: (ids: string[]) => Promise<void>
}) {
  const { message } = App.useApp()
  const [deleting, setDeleting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportingId, setExportingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [keywordDraft, setKeywordDraft] = useState(keyword)
  const [openRunId, setOpenRunId] = useState<string | null>(null)
  const [openQuery, setOpenQuery] = useState<QueryView | null>(null)
  const [openLoading, setOpenLoading] = useState(false)
  const [openError, setOpenError] = useState<string | null>(null)
  const detailRequest = useRef(0)

  useEffect(() => {
    setKeywordDraft(keyword)
  }, [keyword])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (keywordDraft !== keyword) onKeywordChange(keywordDraft)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword, keywordDraft, onKeywordChange])

  useEffect(() => {
    const valid = new Set(runs.map((item) => item.id))
    setSelectedIds((current) => current.filter((id) => valid.has(id)))
    if (openRunId && !valid.has(openRunId)) {
      detailRequest.current += 1
      setOpenRunId(null)
      setOpenQuery(null)
      setOpenError(null)
    }
  }, [runs, openRunId])

  const closeDetail = () => {
    detailRequest.current += 1
    setOpenRunId(null)
    setOpenQuery(null)
    setOpenError(null)
    setOpenLoading(false)
  }

  const openRun = async (run: Run) => {
    if (openRunId === run.id) {
      closeDetail()
      return
    }

    const requestId = ++detailRequest.current
    setOpenRunId(run.id)
    setOpenQuery(null)
    setOpenError(null)
    setOpenLoading(true)
    try {
      const view = await getQuery(run.id)
      if (detailRequest.current !== requestId) return
      setOpenQuery(view)
    } catch (error) {
      if (detailRequest.current !== requestId) return
      setOpenError(error instanceof Error ? error.message : '无法读取查询结果')
    } finally {
      if (detailRequest.current === requestId) setOpenLoading(false)
    }
  }

  const remove = async (ids: string[]) => {
    if (!ids.length) return
    setDeleting(true)
    try {
      await onDelete(ids)
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)))
      message.success(ids.length > 1 ? `已删除 ${ids.length} 条记录` : '已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const exportRun = async (run: Run) => {
    const view = await getQuery(run.id)
    if (!view.investments.length && !view.icp_records.length) {
      throw new Error('这条记录没有可导出的查询数据')
    }
    exportQuery(view)
  }

  const exportOne = async (run: Run) => {
    setExportingId(run.id)
    try {
      await exportRun(run)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '导出失败')
    } finally {
      setExportingId(null)
    }
  }

  const exportSelected = async () => {
    const selected = runs.filter((item) => selectedIds.includes(item.id))
    if (!selected.length) return
    setExporting(true)
    try {
      for (const run of selected) {
        await exportRun(run)
      }
      message.success(`已导出 ${selected.length} 条记录`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '导出失败')
    } finally {
      setExporting(false)
    }
  }

  const columns: TableProps<Run>['columns'] = [
    {
      title: '企业',
      dataIndex: 'keyword',
      ellipsis: true,
      render: (value: string, run) => (
        <Typography.Link
          ellipsis
          onClick={(event) => {
            event.preventDefault()
            void openRun(run)
          }}
          title={value}
        >
          {value}
        </Typography.Link>
      ),
    },
    {
      title: '数据源',
      dataIndex: 'providers',
      width: 280,
      render: (providers: string[] | undefined, run) => (
        <Space className="source-tags" size={4}>
          {(providers?.length ? providers : [run.provider]).map((item) => (
            <SourceTag key={item} name={providerLabel(item)} />
          ))}
        </Space>
      ),
    },
    { title: '查询深度', dataIndex: 'depth', width: 88, render: (depth: number) => `${depth} 层` },
    {
      title: '持股 ≥',
      dataIndex: 'holding_percent',
      width: 88,
      render: (value: number) => `${Number(value)}%`,
    },
    { title: '状态', dataIndex: 'status', width: 100, render: (value) => <StatusTag status={value} /> },
    { title: '查询时间', dataIndex: 'created_at', width: 176, render: formatDate },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, run) => (
        <Space size={12}>
          <Typography.Link disabled={exportingId === run.id || deleting || exporting} onClick={() => void exportOne(run)}>
            导出
          </Typography.Link>
          <Popconfirm title="删除这条查询记录？" okText="删除" cancelText="取消" onConfirm={() => void remove([run.id])}>
            <Typography.Link type="danger">删除</Typography.Link>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card className="page fill-card">
      <Flex vertical gap={12} className="fill-body">
        {openRunId ? (
          <Card
            size="small"
            className="history-query-detail"
            title={
              <Space size={8}>
                <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={closeDetail}>
                  返回列表
                </Button>
                <Typography.Text strong>
                  {openQuery ? `查询结果 · ${openQuery.run.keyword}` : '查询结果'}
                </Typography.Text>
              </Space>
            }
            extra={
              openQuery ? (
                <Space size={8}>
                  <StatusTag status={openQuery.run.status} />
                  <Typography.Text type="secondary">
                    查询用时 {formatDuration(openQuery.run.started_at, openQuery.run.finished_at)}
                  </Typography.Text>
                  <Button
                    size="small"
                    icon={<DownloadOutlined />}
                    disabled={!openQuery.investments.length && !openQuery.icp_records.length}
                    onClick={() => exportQuery(openQuery)}
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
            ) : null}
          </Card>
        ) : (
          <>
            <Flex className="task-toolbar" align="center" gap={8} wrap="nowrap">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                value={keywordDraft}
                placeholder="搜索企业"
                style={{ width: 240 }}
                onChange={(event) => setKeywordDraft(event.target.value)}
              />
              <Select value={status} options={STATUS_OPTIONS} style={{ width: 120 }} onChange={onStatusChange} />
              <Button
                icon={<DownloadOutlined />}
                disabled={!selectedIds.length}
                loading={exporting}
                onClick={() => void exportSelected()}
              >
                导出所选
              </Button>
              <Popconfirm
                title={`删除所选 ${selectedIds.length} 条记录？`}
                okText="删除"
                cancelText="取消"
                disabled={!selectedIds.length}
                onConfirm={() => void remove(selectedIds)}
              >
                <Button danger icon={<DeleteOutlined />} disabled={!selectedIds.length} loading={deleting}>
                  删除所选
                </Button>
              </Popconfirm>
            </Flex>
            <TableFrame
              pagination={{
                current: page,
                pageSize,
                total,
                onChange: (nextPage, nextSize) => onPageChange(nextPage, nextSize),
              }}
            >
              <Table
                rowKey="id"
                className="table-fill"
                columns={columns}
                dataSource={runs}
                loading={loading}
                scroll={{ x: 1080 }}
                pagination={false}
                rowSelection={{
                  selectedRowKeys: selectedIds,
                  onChange: (keys) => setSelectedIds(keys.map(String)),
                }}
                locale={{
                  emptyText: (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={keyword || status ? '没有符合筛选的记录' : '还没有查询记录'}
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
