import { formatPercent, sourceTags } from './formatters'
import type { QueryView } from './types'

type ExportValue = string | number

type ExportSheet = {
  name: string
  headers: string[]
  rows: ExportValue[][]
  widths?: number[]
}

function escapeCell(value: string) {
  return String(value)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function cell(value: ExportValue, style?: string) {
  const type = typeof value === 'number' && Number.isFinite(value) ? 'Number' : 'String'
  const styleAttribute = style ? ` ss:StyleID="${style}"` : ''
  return `<ss:Cell${styleAttribute}><ss:Data ss:Type="${type}">${escapeCell(String(value ?? ''))}</ss:Data></ss:Cell>`
}

function renderSheet(sheet: ExportSheet) {
  const columns = (sheet.widths ?? []).map((width) => `<ss:Column ss:Width="${width}"/>`).join('')
  const header = `<ss:Row>${sheet.headers.map((value) => cell(value, 'Header')).join('')}</ss:Row>`
  const body = sheet.rows
    .map((row) => `<ss:Row>${row.map((value) => cell(value)).join('')}</ss:Row>`)
    .join('')
  return `<Worksheet ss:Name="${escapeCell(sheet.name)}"><ss:Table>${columns}${header}${body}</ss:Table></Worksheet>`
}

function safeFilename(value: string) {
  return value.replace(/[\\/:*?"<>|]/g, '_').trim() || '查询结果'
}

export function exportExcel(filename: string, sheets: ExportSheet[]) {
  const xml = `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
  <Styles>
    <Style ss:ID="Header">
      <Font ss:Bold="1" ss:Color="#162020"/>
      <Interior ss:Color="#E7F3F1" ss:Pattern="Solid"/>
      <Alignment ss:Horizontal="Center" ss:Vertical="Center"/>
    </Style>
  </Styles>
  ${sheets.map(renderSheet).join('')}
</Workbook>`
  const blob = new Blob([xml], { type: 'application/vnd.ms-excel' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeFilename(filename)}.xls`
  link.click()
  URL.revokeObjectURL(url)
}

export function exportQuery(query: QueryView) {
  exportExcel(`${query.run.keyword}-查询结果`, [
    {
      name: '对外投资',
      headers: ['投资方', '被投企业', '持股', '层级', '来源'],
      widths: [220, 220, 80, 80, 150],
      rows: query.investments.map((row) => [
        row.parent_name,
        row.child_name,
        formatPercent(row.holding_percent, 2),
        `${row.depth} 层`,
        sourceTags(row.source).join('、'),
      ]),
    },
    {
      name: 'ICP备案',
      headers: ['公司', '主体备案号', '服务备案号', '域名', '主办单位性质', '更新时间'],
      widths: [180, 150, 180, 180, 120, 180],
      rows: query.icp_records.map((row) => [
        row.unit_name,
        row.main_licence,
        row.service_licence,
        row.domain,
        row.nature_name,
        row.update_time,
      ]),
    },
  ])
}

// Keep the old named exports for any external callers; both now generate one
// workbook containing the two result sheets.
export function exportInvestments(query: QueryView) {
  exportQuery(query)
}

export function exportIcp(query: QueryView) {
  exportQuery(query)
}
