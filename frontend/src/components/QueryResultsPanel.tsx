import { useEffect, useState } from 'react'
import { Empty, Flex, Segmented, Table } from 'antd'
import type { TableProps } from 'antd'
import { SourceTag } from './SourceTag'
import { formatPercent, sourceTags } from '../formatters'
import { TableFrame } from './TableFrame'
import { usePagedData } from '../pagination'
import type { IcpRow, InvestmentRow, QueryView } from '../types'

function Sources({ value }: { value: string }) {
  return (
    <Flex className="source-tags" gap={4} wrap>
      {sourceTags(value).map((name) => (
        <SourceTag key={name} name={name} />
      ))}
    </Flex>
  )
}

export function QueryResultsPanel({ query, loading = false }: { query: QueryView; loading?: boolean }) {
  const [view, setView] = useState<'invest' | 'icp'>('invest')
  const investments = query.investments ?? []
  const icpRecords = query.icp_records ?? []
  const investPage = usePagedData(investments, query.run.id)
  const icpPage = usePagedData(icpRecords, query.run.id)

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
    { title: '来源', dataIndex: 'source', width: 280, render: (value: string) => <Sources value={value} /> },
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
            rowKey={(row, index) => `${row.unit_name}-${row.service_licence}-${row.domain}-${index}`}
            className="table-fill"
            columns={icpColumns}
            dataSource={icpPage.data}
            loading={loading}
            scroll={{ x: 1150 }}
            pagination={false}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 ICP 备案数据" /> }}
          />
        ) : (
          <Table
            rowKey={(row, index) => `${row.parent_name}-${row.child_name}-${row.source}-${index}`}
            className="table-fill"
            columns={investmentColumns}
            dataSource={investPage.data}
            loading={loading}
            scroll={{ x: 720 }}
            pagination={false}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有对外投资" /> }}
          />
        )}
      </TableFrame>
    </Flex>
  )
}
