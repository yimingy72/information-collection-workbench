import { lazy, Suspense } from 'react'
import type { QueryView } from '../types'

let modulePromise: Promise<typeof import('./QueryResultsPanel')> | undefined

export function preloadQueryResultsPanel() {
  modulePromise ??= import('./QueryResultsPanel')
  return modulePromise
}

const LazyQueryResultsPanel = lazy(() =>
  preloadQueryResultsPanel().then((module) => ({ default: module.QueryResultsPanel })),
)

export function DeferredQueryResultsPanel({
  query,
  loading = false,
}: {
  query: QueryView
  loading?: boolean
}) {
  return (
    <Suspense
      fallback={(
        <div className="query-results-loading" role="status" aria-live="polite">
          正在加载结果视图…
        </div>
      )}
    >
      <LazyQueryResultsPanel query={query} loading={loading} />
    </Suspense>
  )
}
