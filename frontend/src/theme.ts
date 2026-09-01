import { theme, type ThemeConfig } from 'antd'

const FONT =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

export function workbenchTheme(dark: boolean): ThemeConfig {
  return {
    algorithm: [dark ? theme.darkAlgorithm : theme.defaultAlgorithm],
    token: {
      colorPrimary: '#0F766E',
      colorInfo: '#0F766E',
      colorLink: '#0F766E',
      colorSuccess: '#15803D',
      colorWarning: '#B45309',
      colorError: '#C2413A',
      colorBgLayout: dark ? '#101414' : '#F5F5F5',
      colorText: dark ? '#E8EEEE' : '#162020',
      colorTextSecondary: dark ? '#9AA8A6' : '#5E6C6B',
      borderRadius: 6,
      fontFamily: FONT,
      fontSize: 14,
      controlHeight: 36,
    },
    components: {
      Layout: {
        siderBg: dark ? '#141818' : '#FFFFFF',
        headerBg: dark ? '#141818' : '#FFFFFF',
        bodyBg: dark ? '#101414' : '#F5F5F5',
        headerHeight: 56,
        headerPadding: '0 16px',
      },
      Menu: {
        itemBg: 'transparent',
        itemHeight: 40,
        itemMarginInline: 8,
        itemBorderRadius: 6,
        itemSelectedBg: dark ? '#1A3F3C' : '#E7F3F1',
        itemSelectedColor: dark ? '#5EEAD4' : '#0F766E',
        itemHoverBg: dark ? '#1C2626' : '#F5F5F5',
      },
      Button: {
        primaryShadow: 'none',
      },
      Card: {
        headerFontSize: 16,
      },
      Table: {
        headerBg: dark ? '#1A2222' : '#FAFAFA',
        cellPaddingBlock: 10,
      },
    },
  }
}
