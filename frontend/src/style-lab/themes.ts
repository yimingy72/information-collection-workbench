import { theme, type ThemeConfig } from 'antd'

export type StyleId = 'side' | 'mix' | 'top' | 'split'
export type LabPage = 'collection' | 'tasks'

const FONT =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

export const STYLES: { id: StyleId; name: string; origin: string; pitch: string }[] = [
  {
    id: 'side',
    name: 'Side 浅色侧栏',
    origin: 'Ant Design Pro · layout: side',
    pitch: '左侧菜单 + 右侧作业区。最接近官方后台，但不用深色侧栏。',
  },
  {
    id: 'mix',
    name: 'Mix 顶栏加条件',
    origin: 'Ant Design Pro · layout: mix',
    pitch: '顶栏切换页面，左侧放查询条件，右侧始终是台账。',
  },
  {
    id: 'top',
    name: 'Top 检索台',
    origin: 'Ant Design Pro · layout: top',
    pitch: '取消侧栏。先搜企业，再收紧条件，结果铺在正中。',
  },
  {
    id: 'split',
    name: 'Split 左右分栏',
    origin: '工作台结构，不是 Pro 默认模板',
    pitch: '条件固定在左，结果占满右侧，适合连续核对股权。',
  },
]

export function themeOf(id: StyleId): ThemeConfig {
  const dark = false
  const blue = id === 'top' || id === 'mix'
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      fontFamily: FONT,
      colorPrimary: blue ? '#1677ff' : '#0f766e',
      borderRadius: id === 'top' ? 10 : id === 'mix' ? 6 : 8,
      controlHeight: id === 'top' ? 40 : 36,
      colorBgLayout: id === 'top' ? '#f7f8fa' : id === 'side' ? '#f4f6f6' : '#f5f7f7',
      colorText: '#162020',
      colorTextSecondary: '#5e6c6b',
    },
    components: {
      Layout: {
        siderBg: '#ffffff',
        headerBg: '#ffffff',
        bodyBg: id === 'top' ? '#f7f8fa' : id === 'side' ? '#f4f6f6' : '#f5f7f7',
        headerHeight: id === 'top' ? 64 : 56,
        headerPadding: '0 20px',
      },
      Menu: {
        itemBg: 'transparent',
        itemSelectedBg: blue ? '#e6f4ff' : '#e7f3f1',
        itemSelectedColor: blue ? '#1677ff' : '#0f766e',
        activeBarBorderWidth: 0,
      },
      Table: {
        headerBg: '#fafbfc',
        cellPaddingBlock: 10,
      },
      Card: {
        paddingLG: 20,
      },
    },
  }
}
