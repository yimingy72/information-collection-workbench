import { Tag } from 'antd'
import type { RunStatus } from '../types'

const statusConfig: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '等待中' },
  running: { color: 'processing', label: '查询中' },
  succeeded: { color: 'success', label: '成功' },
  failed: { color: 'error', label: '失败' },
  partial: { color: 'warning', label: '部分成功' },
  cancelled: { color: 'default', label: '已取消' },
}

export function StatusTag({ status }: { status: RunStatus }) {
  const config = statusConfig[status] ?? { color: 'default', label: status }
  return <Tag color={config.color}>{config.label}</Tag>
}
