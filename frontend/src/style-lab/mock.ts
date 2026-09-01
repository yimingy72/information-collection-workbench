export const PROVIDERS = [
  { value: 'tianyancha', label: '天眼查' },
  { value: 'aiqicha', label: '爱企查' },
  { value: 'kuaicha', label: '快查' },
  { value: 'riskbird', label: '风鸟' },
] as const

export const INVEST_ROWS = [
  { key: '1', parent: '小米科技有限责任公司', child: '小米通讯技术有限公司', pct: 100, depth: 1, src: ['天眼查', '爱企查', '快查'] },
  { key: '2', parent: '小米科技有限责任公司', child: '北京小米电子软件有限公司', pct: 100, depth: 1, src: ['天眼查', '爱企查'] },
  { key: '3', parent: '小米科技有限责任公司', child: '小米之家商业有限公司', pct: 70, depth: 1, src: ['天眼查', '快查'] },
  { key: '4', parent: '小米科技有限责任公司', child: '小米金融科技有限公司', pct: 51, depth: 1, src: ['天眼查'] },
  { key: '5', parent: '小米通讯技术有限公司', child: '小米通讯技术有限公司南京分公司', pct: 100, depth: 2, src: ['风鸟'] },
]

export const SHAREHOLDER_ROWS = [
  { key: '1', name: '雷军', company: '小米科技有限责任公司', pct: 77.8, src: ['天眼查', '爱企查'] },
  { key: '2', name: '黎万强', company: '小米科技有限责任公司', pct: 10.1, src: ['天眼查'] },
  { key: '3', name: '洪锋', company: '小米科技有限责任公司', pct: 10.1, src: ['天眼查', '快查'] },
]

export const HISTORY_ROWS = [
  { key: '1', keyword: '小米科技有限责任公司', src: ['天眼查', '爱企查', '快查', '风鸟'], depth: 1, holding: 51, status: 'partial', time: '2026-08-27 15:12:08' },
  { key: '2', keyword: '华为技术有限公司', src: ['天眼查', '爱企查'], depth: 1, holding: 51, status: 'succeeded', time: '2026-08-27 11:04:41' },
  { key: '3', keyword: '宁德时代新能源科技股份有限公司', src: ['天眼查'], depth: 2, holding: 20, status: 'failed', time: '2026-08-26 18:33:02' },
]
