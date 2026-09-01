import type { ReactNode } from 'react'
import { useState } from 'react'
import {
  HistoryOutlined,
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  SearchOutlined,
  SettingOutlined,
  SunOutlined,
} from '@ant-design/icons'
import { Alert, Breadcrumb, Button, Drawer, Flex, Grid, Layout, Menu, Switch, theme } from 'antd'

type AppShellProps = {
  page: string
  title: string
  dark: boolean
  apiWarning?: string | null
  children: ReactNode
  onNavigate: (page: string) => void
  onDarkChange: (dark: boolean) => void
}

const navItems = [
  { key: 'collection', icon: <SearchOutlined />, label: 'ICP备案查询' },
  { key: 'tasks', icon: <HistoryOutlined />, label: '历史查询' },
  { key: 'settings', icon: <SettingOutlined />, label: '基础配置' },
]

export function AppShell({
  page,
  title,
  apiWarning,
  children,
  dark,
  onNavigate,
  onDarkChange,
}: AppShellProps) {
  const { token } = theme.useToken()
  const screens = Grid.useBreakpoint()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const isMobile = screens.lg === false
  const selectedKey = page.startsWith('collection') ? 'collection' : page === 'settings' ? 'settings' : 'tasks'

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      items={navItems}
      onClick={({ key }) => {
        onNavigate(key)
        setMobileOpen(false)
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
      }}
    />
  )

  return (
    <Layout className="app-shell">
      <Layout.Sider
        width={220}
        collapsedWidth={isMobile ? 0 : 64}
        collapsed={isMobile ? true : collapsed}
        breakpoint="lg"
        trigger={null}
        collapsible
        theme="light"
        style={{ borderInlineEnd: `1px solid ${token.colorSplit}` }}
        onCollapse={setCollapsed}
      >
        <Flex align="center" gap={10} className="brand">
          <span className="brand-mark" aria-hidden>
            资
          </span>
          {!collapsed || isMobile ? <span>信息收集工作台</span> : null}
        </Flex>
        {menu}
      </Layout.Sider>
      <Layout className="app-body">
        <Layout.Header className="app-header">
          <Flex align="center" gap={12}>
            {isMobile ? (
              <Button type="text" icon={<MenuOutlined />} aria-label="打开导航" onClick={() => setMobileOpen(true)} />
            ) : (
              <Button
                type="text"
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                aria-label={collapsed ? '展开导航' : '收起导航'}
                onClick={() => setCollapsed((value) => !value)}
              />
            )}
            <Breadcrumb items={[{ title: '信息收集工作台' }, { title }]} />
          </Flex>
          <Switch
            checked={dark}
            onChange={onDarkChange}
            checkedChildren={<MoonOutlined />}
            unCheckedChildren={<SunOutlined />}
            aria-label="深色模式"
          />
        </Layout.Header>
        <Layout.Content className="app-main">
          {apiWarning ? <Alert type="warning" showIcon title={apiWarning} style={{ marginBottom: 16 }} /> : null}
          {children}
        </Layout.Content>
      </Layout>
      <Drawer title="信息收集工作台" placement="left" open={mobileOpen} onClose={() => setMobileOpen(false)} size={220} styles={{ body: { padding: 0 } }}>
        {menu}
      </Drawer>
    </Layout>
  )
}
