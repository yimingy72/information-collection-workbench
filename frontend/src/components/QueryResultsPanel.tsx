import { memo, useEffect, useState } from 'react'
import { Empty, Flex, Segmented, Table } from 'antd'
import type { TableProps } from 'antd'
import { SourceTag } from './SourceTag'
import { formatPercent, sourceTags } from '../formatters'
import { TableFrame } from './TableFrame'
import { usePagedData } from '../pagination'
import type { IcpRow, InvestmentRow, QueryView } from '../types'

const Sources = memo(function Sources({ value }: { value: string }) {
  return (
    <Flex className="source-tags" gap={4} wrap>
      {sourceTags(value).map((name) => (
        <SourceTag key={name} name={name} />
      ))}
    </Flex>
  )
})

const investmentColumns: TableProps<InvestmentRow>['columns'] = [
  { title: '投资方', dataIndex: 'parent_name', ellipsis: true },
  { title: '被投企业', dataIndex: 'child_name', ellipsis: true },
  {
    title: '持股',
    dataIndex: 'holding_percent',
    width: 108,
    align: 'right',
    render: (value?: number | null) => formatPercent(value, 2),
  },
  { title: '层级', dataIndex: 'depth', width: 88, render: (depth: number) => `${depth} 层` },
  {
    title: '来源',
    dataIndex: 'source',
    width: 280,
    render: (value: string) => <Sources value={value} />,
  },
]

const icpColumns: TableProps<IcpRow>['columns'] = [
  { title: '公司', dataIndex: 'unit_name', width: 140, ellipsis: true },
  { title: '主体备案号', dataIndex: 'main_licence', ellipsis: true, width: 190 },
  { title: '服务备案号', dataIndex: 'service_licence', ellipsis: true, width: 210 },
  { title: '域名', dataIndex: 'domain', ellipsis: true, width: 220 },
  { title: '主办单位性质', dataIndex: 'nature_name', width: 120 },
  {
    title: '更新时间',
    dataIndex: 'update_time',
    width: 270,
    onCell: () => ({ style: { whiteSpace: 'nowrap' } }),
    render: (value: string) => <span className="icp-update-value">{value || '—'}</span>,
  },
]

const investmentRowKey = (row: InvestmentRow) =>
  [row.parent_name, row.child_name, row.depth, row.holding_percent ?? ''].join('\u0000')

const icpRowKey = (row: IcpRow) =>
  [row.unit_name, row.main_licence, row.service_licence, row.domain].join('\u0000')

function QueryResultsPanelView({
  query,
  loading = false,
}: {
  query: QueryView
  loading?: boolean
}) {
  const [view, setView] = useState<'invest' | 'icp'>('invest')
  const running = query.run.status === 'queued' || query.run.status === 'running'
  const investments = query.investments ?? []
  const icpRecords = query.icp_records ?? []
  const investPage = usePagedData(investments, query.run.id)
  const icpPage = usePagedData(icpRecords, query.run.id)

  useEffect(() => {
    setView('invest')
  }, [query.run.id])

  return (
    <Flex vertical gap={12} className="query-results-panel">
      <Segmented
        block
        options={[
          { label: `对外投资 ${investments.length}`, value: 'invest' },
          { label: `ICP备案 ${icpRecords.length}`, value: 'icp' },
        ]}
        value={view}
        onChange={(value) => setView(value as 'invest' | 'icp')}
      />
      <TableFrame pagination={view === 'icp' ? icpPage.pagination : investPage.pagination}>
        {view === 'icp' ? (
          <Table
            rowKey={icpRowKey}
            className="table-fill"
            columns={icpColumns}
            dataSource={icpPage.data}
            loading={loading}
            scroll={{ x: 1150, y: 'calc(100vh - 360px)' }}
            pagination={false}
            virtual
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={running ? '正在实时查询 ICP 备案' : '暂无 ICP 备案数据'}
                />
              ),
            }}
          />
        ) : (
          <Table
            rowKey={investmentRowKey}
            className="table-fill"
            columns={investmentColumns}
            dataSource={investPage.data}
            loading={loading}
            scroll={{ x: 720, y: 'calc(100vh - 360px)' }}
            pagination={false}
            virtual
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={running ? '正在实时发现投资关系' : '没有对外投资'}
                />
              ),
            }}
          />
        )}
      </TableFrame>
    </Flex>
  )
}

export const QueryResultsPanel = memo(
  QueryResultsPanelView,
  (previous, next) =>
    previous.loading === next.loading
    && previous.query.run.id === next.query.run.id
    && previous.query.run.status === next.query.run.status
    && previous.query.investments === next.query.investments
    && previous.query.icp_records === next.query.icp_records,
)
