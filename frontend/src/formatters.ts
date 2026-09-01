import { PROVIDER_OPTIONS } from './types'

export const formatDate = (value?: string | null) => {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

export const formatPercent = (value?: number | null, maximumFractionDigits = 2) => {
  if (value == null) return '—'
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits }).format(Number(value))}%`
}

export const providerLabel = (id: string) =>
  PROVIDER_OPTIONS.find((item) => item.value === id)?.label ?? (id === 'tianyancha-anonymous' ? '天眼查' : id)

const SOURCE_ORDER = ['天眼查', '爱企查', '风鸟', '快查']

export const sourceTags = (value: string) =>
  [...new Set(value.split(/[、,]/).map((item) => providerLabel(item.trim())).filter(Boolean))]
    .sort((a, b) => {
      const left = SOURCE_ORDER.indexOf(a)
      const right = SOURCE_ORDER.indexOf(b)
      return (left === -1 ? SOURCE_ORDER.length : left) - (right === -1 ? SOURCE_ORDER.length : right)
    })

export const formatDuration = (startedAt?: string | null, finishedAt?: string | null) => {
  if (!startedAt) return '—'
  const started = new Date(startedAt).getTime()
  const finished = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (!Number.isFinite(started) || !Number.isFinite(finished)) return '—'

  const milliseconds = Math.max(0, finished - started)
  if (milliseconds < 1000) return `${milliseconds} 毫秒`
  const seconds = Math.round(milliseconds / 1000)
  if (seconds < 60) return `${seconds} 秒`

  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`
}
