import { useEffect, useMemo, useState } from 'react'
import type { PaginationProps } from 'antd'

export const TABLE_PAGE_SIZE = 20
export const TABLE_PAGE_SIZE_OPTIONS = [10, 20, 50]

export const tablePagination = (overrides: PaginationProps = {}): PaginationProps => ({
  defaultPageSize: TABLE_PAGE_SIZE,
  pageSize: TABLE_PAGE_SIZE,
  pageSizeOptions: TABLE_PAGE_SIZE_OPTIONS,
  showSizeChanger: true,
  hideOnSinglePage: false,
  showTotal: (total, range) => `${range[0]}-${range[1]} / ${total}`,
  ...overrides,
})

export function usePagedData<T>(items: T[], resetKey = '') {
  const [current, setCurrent] = useState(1)
  const [pageSize, setPageSize] = useState(TABLE_PAGE_SIZE)

  useEffect(() => {
    setCurrent(1)
  }, [resetKey])

  const total = items.length
  const page = Math.min(current, Math.max(1, Math.ceil(total / pageSize) || 1))

  useEffect(() => {
    if (page !== current) setCurrent(page)
  }, [current, page])

  const data = useMemo(
    () => items.slice((page - 1) * pageSize, page * pageSize),
    [items, page, pageSize],
  )

  return {
    data,
    pagination: {
      current: page,
      pageSize,
      total,
      onChange: (nextPage: number, nextSize: number) => {
        if (nextSize !== pageSize) {
          setPageSize(nextSize)
          setCurrent(1)
          return
        }
        setCurrent(nextPage)
      },
    } satisfies PaginationProps,
  }
}
