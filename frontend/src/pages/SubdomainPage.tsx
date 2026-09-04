import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import {
  App,
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Input,
  Progress,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableProps } from 'antd'
import { DeleteOutlined, DownloadOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import {
  cancelSubdomainRun,
  createSubdomainRun,
  deleteSubdomainRun,
  getAllSubdomainResults,
  getSubdomainRun,
  listIcpDomainRuns,
  listSubdomainRuns,
  subdomainEventUrl,
} from '../api'
import { StatusTag } from '../components/StatusTag'
import { TableFrame } from '../components/TableFrame'
import { formatDate, formatDuration } from '../formatters'
import { usePagedData } from '../pagination'
import type { IcpDomainRun, SubdomainOptions, SubdomainResult, SubdomainRun } from '../types'

const terminalStatuses = new Set(['succeeded', 'partial', 'failed', 'cancelled'])
const phaseLabels: Record<string, string> = {
  queued: '等待执行',
  collecting: '被动数据源收集',
  resolving: 'DNS 解析验证',
  probing: 'HTTP 存活探测',
  completed: '查询完成',
  failed: '查询失败',
}

const statusLabels: Record<string, string> = {
  queued: '等待中',
  running: '进行中',
  succeeded: '成功',
  partial: '部分成功',
  failed: '失败',
  cancelled: '已取消',
}

function parseDomains(value: string) {
  return [...new Set(value.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean))]
}


function recentRunLabel(run: SubdomainRun) {
  const first = run.domains[0] || '空任务'
  const suffix = run.domains.length > 1 ? ` 等 ${run.domains.length} 个主域名` : ''
  return `${first}${suffix} · ${formatDate(run.created_at)}`
}

function progressStatus(status: SubdomainRun['status']) {
  if (status === 'failed') return 'exception' as const
  if (status === 'succeeded') return 'success' as const
  if (!terminalStatuses.has(status)) return 'active' as const
  return 'normal' as const
}

function readableWarning(value: string) {
  const source = value.split('：', 1)[0]
  if (/429|Too Many Requests/i.test(value)) return `${source}：请求频率受限（HTTP 429），其他来源已继续查询`
  const withoutUrls = value
    .replace(/For more information check:\s*https?:\/\/\S+/gi, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return withoutUrls.length > 220 ? `${withoutUrls.slice(0, 217)}…` : withoutUrls
}

function exportResults(run: SubdomainRun, rows: SubdomainResult[]) {
  const header = ['主域名', '子域名', 'IP', 'CNAME', 'HTTP状态', '访问地址', '标题', '来源', '发现时间']
  const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`
  const body = rows.map((row) => [
    row.root_domain,
    row.hostname,
    row.ips.join('、'),
    row.canonical_name,
    row.http_status ?? '',
    row.http_url,
    row.title,
    row.sources.join('、'),
    formatDate(row.discovered_at),
  ].map(escape).join(','))
  const blob = new Blob([`\uFEFF${[header.map(escape).join(','), ...body].join('\r\n')}`], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  const url = URL.createObjectURL(blob)
  const filename = run.domains.length > 1
    ? `${run.domains[0]}等${run.domains.length}个主域名`
    : run.domains[0] || '子域名'
  link.href = url
  link.download = `${filename}-查询结果.csv`
  link.hidden = true
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function SubdomainPage({
  runId,
  onOpenRun,
}: {
  runId?: string
  onOpenRun: (runId?: string) => void
}) {
  const { message, modal } = App.useApp()
  const [mode, setMode] = useState<'manual' | 'icp'>('manual')
  const [manualValue, setManualValue] = useState('')
  const [options, setOptions] = useState<SubdomainOptions>({ passive: true, brute_force: true, deep_scan: true, http_probe: true })
  const [icpRuns, setIcpRuns] = useState<IcpDomainRun[]>([])
  const [selectedIcpRunIds, setSelectedIcpRunIds] = useState<string[]>([])
  const [selectedIcpDomains, setSelectedIcpDomains] = useState<string[]>([])
  const [recentRuns, setRecentRuns] = useState<SubdomainRun[]>([])
  const [run, setRun] = useState<SubdomainRun | null>(null)
  const resultsByIdRef = useRef(new Map<number, SubdomainResult>())
  const pendingResultsRef = useRef<SubdomainResult[]>([])
  const resultFlushRef = useRef<number | null>(null)
  const [resultsVersion, setResultsVersion] = useState(0)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [resultKeyword, setResultKeyword] = useState('')
  const deferredResultKeyword = useDeferredValue(resultKeyword)
  const [resultView, setResultView] = useState<'all' | 'web' | 'wildcard'>('all')
  const eventSourceRef = useRef<EventSource | null>(null)
  const autoOpenedRef = useRef(false)

  const results = useMemo(() => [...resultsByIdRef.current.values()], [resultsVersion])
  const replaceResults = (items: SubdomainResult[]) => {
    resultsByIdRef.current = new Map(items.map((item) => [item.id, item]))
    pendingResultsRef.current = []
    if (resultFlushRef.current !== null) {
      window.clearTimeout(resultFlushRef.current)
      resultFlushRef.current = null
    }
    setResultsVersion((value) => value + 1)
  }
  const queueResult = (item: SubdomainResult) => {
    pendingResultsRef.current.push(item)
    if (resultFlushRef.current !== null) return
    resultFlushRef.current = window.setTimeout(() => {
      const pending = pendingResultsRef.current.splice(0)
      resultFlushRef.current = null
      if (!pending.length) return
      for (const next of pending) resultsByIdRef.current.set(next.id, next)
      setResultsVersion((value) => value + 1)
    }, 80)
  }

  const availableIcpDomains = useMemo(() => {
    const selected = new Set(selectedIcpRunIds)
    return [...new Set(icpRuns.filter((item) => selected.has(item.id)).flatMap((item) => item.domains))].sort()
  }, [icpRuns, selectedIcpRunIds])

  const sortedResults = useMemo(() => [...results].sort((a, b) => b.id - a.id), [results])
  const resultCounts = useMemo(() => ({
    all: results.length,
    web: results.filter((row) => Boolean(row.http_status)).length,
    wildcard: results.filter((row) => row.wildcard).length,
  }), [results])
  const filteredResults = useMemo(() => {
    const keyword = deferredResultKeyword.trim().toLowerCase()
    return sortedResults.filter((row) => {
      if (resultView === 'web' && !row.http_status) return false
      if (resultView === 'wildcard' && !row.wildcard) return false
      if (!keyword) return true
      return [row.root_domain, row.hostname, row.canonical_name, row.title, ...row.ips]
        .some((value) => String(value || '').toLowerCase().includes(keyword))
    })
  }, [deferredResultKeyword, resultView, sortedResults])
  const paged = usePagedData(filteredResults, `${run?.id ?? ''}:${deferredResultKeyword}:${resultView}`)
  const percent = run?.total
    ? Math.min(100, Math.round((run.progress / run.total) * 100))
    : run && terminalStatuses.has(run.status) ? 100 : 0

  const refreshRecent = async () => {
    const data = await listSubdomainRuns(1, 30)
    setRecentRuns(data.items)
    if (runId) {
      const current = data.items.find((item) => item.id === runId)
      if (current) setRun(current)
    }
    return data.items
  }

  useEffect(() => {
    let cancelled = false
    void listIcpDomainRuns()
      .then((icp) => {
        if (!cancelled) setIcpRuns(icp.items)
      })
      .catch(() => message.warning('ICP 历史域名暂时无法加载，可继续手动输入域名'))
    void listSubdomainRuns(1, 30)
      .then((recent) => {
        if (cancelled) return
        setRecentRuns(recent.items)
        if (!runId && !autoOpenedRef.current && recent.items.length) {
          autoOpenedRef.current = true
          const active = recent.items.find((item) => !terminalStatuses.has(item.status))
          onOpenRun((active ?? recent.items[0]).id)
        }
      })
      .catch(() => message.error('无法加载子域名历史查询'))
    return () => {
      cancelled = true
    }
  }, [])

  const hasActiveRecent = recentRuns.some((item) => !terminalStatuses.has(item.status))

  useEffect(() => {
    if (!hasActiveRecent) return
    const timer = window.setInterval(() => {
      void refreshRecent().catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [hasActiveRecent, runId])

  useEffect(() => {
    if (!selectedIcpRunIds.length) {
      setSelectedIcpDomains([])
      return
    }
    setSelectedIcpDomains(availableIcpDomains)
  }, [availableIcpDomains.join('|'), selectedIcpRunIds.join('|')])

  useEffect(() => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    setResultKeyword('')
    setResultView('all')
    if (!runId) {
      setRun(null)
      replaceResults([])
      return
    }

    let cancelled = false
    setLoading(true)
    Promise.all([getSubdomainRun(runId), getAllSubdomainResults(runId)])
      .then(([nextRun, response]) => {
        if (cancelled) return
        setRun(nextRun)
        replaceResults(response.items)
        setRecentRuns((current) => [
          nextRun,
          ...current.filter((item) => item.id !== nextRun.id),
        ].slice(0, 30))
        if (terminalStatuses.has(nextRun.status)) return
        const afterSeq = response.items.reduce((value, item) => Math.max(value, item.stream_seq), 0)
        const source = new EventSource(subdomainEventUrl(runId, afterSeq))
        eventSourceRef.current = source
        source.addEventListener('result', (event) => {
          const item = JSON.parse((event as MessageEvent).data) as SubdomainResult
          queueResult(item)
        })
        source.addEventListener('progress', (event) => {
          setRun(JSON.parse((event as MessageEvent).data) as SubdomainRun)
        })
        source.addEventListener('done', (event) => {
          setRun(JSON.parse((event as MessageEvent).data) as SubdomainRun)
          source.close()
          eventSourceRef.current = null
          void Promise.all([getSubdomainRun(runId), getAllSubdomainResults(runId)])
            .then(([finalRun, finalResults]) => {
              setRun(finalRun)
              replaceResults(finalResults.items)
            })
            .catch(() => undefined)
          void refreshRecent()
        })
        source.onerror = () => {
          // EventSource reconnects automatically; refresh the summary while the
          // transport is recovering instead of disabling real-time output.
          void getSubdomainRun(runId).then(setRun).catch(() => undefined)
        }
      })
      .catch((error) => message.error(error instanceof Error ? error.message : '查询记录加载失败'))
      .finally(() => setLoading(false))

    return () => {
      cancelled = true
      eventSourceRef.current?.close()
      eventSourceRef.current = null
    }
  }, [runId, refreshKey])

  useEffect(() => () => {
    if (resultFlushRef.current !== null) window.clearTimeout(resultFlushRef.current)
  }, [])

  const submit = async () => {
    const domains = mode === 'manual' ? parseDomains(manualValue) : selectedIcpDomains
    if (!domains.length) {
      message.warning(mode === 'manual' ? '请输入至少一个域名' : '请选择包含 ICP 域名的查询记录')
      return
    }
    if (!options.passive && !options.brute_force) {
      message.warning('被动数据源和 DNS 字典至少启用一项')
      return
    }
    setSubmitting(true)
    try {
      const created = await createSubdomainRun({
        domains,
        source_run_ids: mode === 'icp' ? selectedIcpRunIds : [],
        options,
      })
      setRun(created)
      replaceResults([])
      onOpenRun(created.id)
      await refreshRecent()
      message.success(`已提交 ${created.domains.length} 个主域名`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const removeCurrent = () => {
    if (!run) return
    modal.confirm({
      title: '删除这条子域名查询记录？',
      content: '查询结果会一并删除，正在执行的任务也会停止写入。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await deleteSubdomainRun(run.id)
        onOpenRun(undefined)
        await refreshRecent()
      },
    })
  }

  const stopCurrent = async () => {
    if (!run || terminalStatuses.has(run.status)) return
    try {
      const cancelled = await cancelSubdomainRun(run.id)
      setRun(cancelled)
      await refreshRecent()
      message.success('查询已停止，现有结果已保留')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '停止查询失败')
    }
  }

  const columns: TableProps<SubdomainResult>['columns'] = [
    { title: '主域名', dataIndex: 'root_domain', width: 160, ellipsis: true },
    {
      title: '子域名',
      dataIndex: 'hostname',
      width: 260,
      ellipsis: true,
      render: (value: string, row) => row.http_url ? <a href={row.http_url} target="_blank" rel="noreferrer">{value}</a> : value,
    },
    {
      title: '解析地址',
      dataIndex: 'ips',
      width: 240,
      render: (values: string[], row) => (
        <Flex gap={4} wrap>
          {values.map((value) => <Tag key={value}>{value}</Tag>)}
          {row.wildcard ? <Tag color="warning">泛解析</Tag> : null}
        </Flex>
      ),
    },
    {
      title: 'HTTP',
      dataIndex: 'http_status',
      width: 90,
      align: 'center',
      render: (value?: number | null) => value ? <Tag color={value < 400 ? 'success' : value < 500 ? 'warning' : 'error'}>{value}</Tag> : '—',
    },
    { title: '页面标题', dataIndex: 'title', minWidth: 180, ellipsis: true, render: (value: string) => value || '—' },
    {
      title: '来源',
      dataIndex: 'sources',
      width: 230,
      render: (values: string[]) => <Flex gap={4} wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Flex>,
    },
    { title: '发现时间', dataIndex: 'discovered_at', width: 190, render: formatDate },
  ]

  return (
    <div className="page page-split subdomain-page">
      <Card className="page-head subdomain-query-card" size="small" styles={{ body: { padding: 12 } }}>
        <Flex vertical gap={10} className="subdomain-query-stack">
          <Flex gap={10} align="flex-start" className="subdomain-source-row">
            <Segmented
              className="subdomain-mode-switch"
              value={mode}
              onChange={(value) => setMode(value as 'manual' | 'icp')}
              options={[{ label: '输入域名', value: 'manual' }, { label: '选择 ICP 结果', value: 'icp' }]}
            />
            {mode === 'manual' ? (
              <Input.TextArea
                className="subdomain-domain-input"
                value={manualValue}
                autoSize={{ minRows: 1, maxRows: 3 }}
                placeholder="输入一个或多个域名，支持换行、空格或逗号分隔"
                onChange={(event) => setManualValue(event.target.value)}
              />
            ) : (
              <Flex className="subdomain-icp-inputs" gap={8} wrap="wrap">
                <Select
                  className="subdomain-icp-run-select"
                  mode="multiple"
                  maxTagCount="responsive"
                  value={selectedIcpRunIds}
                  placeholder="选择 ICP 查询记录"
                  options={icpRuns.map((item) => ({
                    value: item.id,
                    label: `${item.keyword} · ${item.domains.length} 个域名 · ${formatDate(item.created_at)}`,
                  }))}
                  onChange={setSelectedIcpRunIds}
                />
                <Select
                  className="subdomain-icp-domain-select"
                  mode="multiple"
                  maxTagCount="responsive"
                  value={selectedIcpDomains}
                  placeholder="选择要查询的备案域名"
                  options={availableIcpDomains.map((domain) => ({ value: domain, label: domain }))}
                  onChange={setSelectedIcpDomains}
                />
              </Flex>
            )}
          </Flex>
          <Flex justify="space-between" align="center" gap={12} wrap="wrap" className="subdomain-option-row">
            <Checkbox.Group
              className="subdomain-options"
              value={Object.entries(options).filter(([, enabled]) => enabled).map(([key]) => key)}
              options={[
                { label: '被动数据源', value: 'passive' },
                { label: 'DNS 字典', value: 'brute_force' },
                { label: '智能变体', value: 'deep_scan', disabled: !options.brute_force },
                { label: 'HTTP 探测', value: 'http_probe' },
              ]}
              onChange={(values) => setOptions({
                passive: values.includes('passive'),
                brute_force: values.includes('brute_force'),
                deep_scan: values.includes('brute_force') && values.includes('deep_scan'),
                http_probe: values.includes('http_probe'),
              })}
            />
            <Space size={12}>
              <Typography.Text type="secondary">
                {mode === 'manual' ? parseDomains(manualValue).length : selectedIcpDomains.length} 个主域名
              </Typography.Text>
              <Button type="primary" icon={<PlayCircleOutlined />} loading={submitting} onClick={() => void submit()}>
                开始查询
              </Button>
            </Space>
          </Flex>
        </Flex>
      </Card>

      <Card
        className="fill-card subdomain-results-card"
        styles={{ body: { paddingTop: 12 } }}
        title={<Space><span>实时查询结果</span>{run ? <StatusTag status={run.status} /> : null}</Space>}
        extra={(
          <Space className="subdomain-card-actions" wrap size={8}>
            <Select
              className="subdomain-history-select"
              allowClear
              showSearch={{ optionFilterProp: 'label' }}
              placeholder={`查询记录（${recentRuns.length}）`}
              value={run?.id}
              options={recentRuns.map((item) => ({
                value: item.id,
                label: `${statusLabels[item.status] ?? item.status} · ${recentRunLabel(item)}`,
                run: item,
              }))}
              optionRender={(option) => {
                const item = option.data.run as SubdomainRun
                return (
                  <Flex align="center" justify="space-between" gap={12}>
                    <Typography.Text ellipsis>{recentRunLabel(item)}</Typography.Text>
                    <Space size={6}>
                      {!terminalStatuses.has(item.status) ? (
                        <Typography.Text type="secondary">
                          {item.total ? `${item.progress}/${item.total}` : phaseLabels[item.phase] ?? item.phase}
                        </Typography.Text>
                      ) : null}
                      <StatusTag status={item.status} />
                    </Space>
                  </Flex>
                )
              }}
              onChange={(value) => onOpenRun(value)}
            />
            {hasActiveRecent ? <Tag color="processing">进行中 {recentRuns.filter((item) => !terminalStatuses.has(item.status)).length}</Tag> : null}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                setRefreshKey((value) => value + 1)
                void refreshRecent().catch(() => message.error('刷新查询记录失败'))
              }}
              disabled={!runId}
            >
              刷新
            </Button>
            {!run || terminalStatuses.has(run.status) ? null : (
              <Button danger icon={<StopOutlined />} onClick={() => void stopCurrent()}>
                停止
              </Button>
            )}
            <Button icon={<DownloadOutlined />} disabled={!run || !results.length} onClick={() => run && exportResults(run, sortedResults)}>导出 CSV</Button>
            <Button danger icon={<DeleteOutlined />} disabled={!run} onClick={removeCurrent}>删除</Button>
          </Space>
        )}
      >
        {!run ? (
          <div className="subdomain-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入域名或选择 ICP 结果后开始查询" /></div>
        ) : (
          <Flex vertical gap={12} className="fill-body">
            <Flex className="subdomain-summary" align="center" gap={20} wrap="wrap">
              <Statistic style={{ minWidth: 84 }} styles={{ header: { paddingBottom: 0 }, content: { fontSize: 20 } }} title="主域名" value={run.domains.length} />
              <Statistic style={{ minWidth: 84 }} styles={{ header: { paddingBottom: 0 }, content: { fontSize: 20 } }} title="候选" value={run.total ?? 0} />
              <Statistic style={{ minWidth: 84 }} styles={{ header: { paddingBottom: 0 }, content: { fontSize: 20 } }} title="已处理" value={run.progress} />
              <Statistic style={{ minWidth: 84 }} styles={{ header: { paddingBottom: 0 }, content: { fontSize: 20 } }} title="有效子域名" value={run.discovered} />
              <div className="subdomain-progress-wrap">
                <Flex justify="space-between" gap={12}>
                  <Typography.Text>{phaseLabels[run.phase] ?? run.phase}</Typography.Text>
                  <Typography.Text type="secondary">用时 {formatDuration(run.started_at, run.finished_at)}</Typography.Text>
                </Flex>
                <Progress percent={percent} status={progressStatus(run.status)} />
              </div>
            </Flex>
            {run.error ? <Alert type="error" showIcon title={run.error} /> : null}
            {run.warnings.length ? (
              <Alert
                className="subdomain-warning-alert"
                type="warning"
                showIcon
                title={`部分数据源未完成（${run.warnings.length}）`}
                description={(
                  <Flex vertical gap={2}>
                    {run.warnings.slice(0, 4).map((warning) => (
                      <Typography.Text key={warning}>{readableWarning(warning)}</Typography.Text>
                    ))}
                    {run.warnings.length > 4 ? (
                      <Typography.Text type="secondary">另有 {run.warnings.length - 4} 条，可在刷新后重试未命中的来源</Typography.Text>
                    ) : null}
                  </Flex>
                )}
              />
            ) : null}
            <Flex className="subdomain-result-toolbar" align="center" justify="space-between" gap={10} wrap="wrap">
              <Input.Search
                className="subdomain-result-search"
                aria-label="筛选子域名查询结果"
                allowClear
                value={resultKeyword}
                placeholder="筛选子域名、IP、CNAME 或标题"
                onChange={(event) => setResultKeyword(event.target.value)}
              />
              <Space className="subdomain-result-filter-group" size={10}>
                <Segmented
                  size="small"
                  value={resultView}
                  onChange={(value) => setResultView(value as 'all' | 'web' | 'wildcard')}
                  options={[
                    { label: `全部 ${resultCounts.all}`, value: 'all' },
                    { label: `有响应 ${resultCounts.web}`, value: 'web' },
                    { label: `泛解析 ${resultCounts.wildcard}`, value: 'wildcard' },
                  ]}
                />
                <Typography.Text type="secondary">显示 {filteredResults.length} 条</Typography.Text>
              </Space>
            </Flex>
            <TableFrame pagination={paged.pagination}>
              <Table
                rowKey="id"
                className="table-fill"
                columns={columns}
                dataSource={paged.data}
                loading={loading}
                scroll={{ x: 1380 }}
                pagination={false}
                locale={{
                  emptyText: (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={results.length
                        ? '没有匹配结果'
                        : terminalStatuses.has(run.status) ? '没有发现有效子域名' : '正在等待查询结果…'}
                    />
                  ),
                }}
              />
            </TableFrame>
          </Flex>
        )}
      </Card>
    </div>
  )
}
