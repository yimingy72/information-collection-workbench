import type { ReactNode } from 'react'
import { Pagination } from 'antd'
import type { PaginationProps } from 'antd'
import { tablePagination } from '../pagination'

export function TableFrame({
  pagination = false,
  children,
}: {
  pagination?: PaginationProps | false
  children: ReactNode
}) {
  return (
    <div className="table-frame">
      <div className="table-host">{children}</div>
      {pagination ? (
        <div className="table-pager">
          <Pagination align="end" {...tablePagination(pagination)} />
        </div>
      ) : null}
    </div>
  )
}
