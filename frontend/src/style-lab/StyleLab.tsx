import { useMemo, useState } from 'react'
import {
  HistoryOutlined,
  MoonOutlined,
  SearchOutlined,
  SunOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  ConfigProvider,
  Flex,
  Input,
  Layout,
  Menu,
  Splitter,
  Typography,
} from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { Brand, HistoryTable, QueryFields, ResultLedger } from './PreviewKit'
import { STYLES, themeOf, type LabPage, type StyleId } from './themes'
import './style-lab.css'

function SidePreview({ page, onNavigate }: { page: LabPage; onNavigate: (page: LabPage) => void }) {
  return (
    <Layout className="preview-shell">
      <Layout.Sider width={220} theme="light" className="qing-sider">
        <Brand />
        <Menu
          mode="inline"
          selectedKeys={[page]}
          onClick={({ key }) => onNavigate(key as LabPage)}
          items={[
            { key: 'collection', icon: <SearchOutlined />, label: 'ICP备案查询' },
            { key: 'tasks', icon: <HistoryOutlined />, label: '历史查询' },
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="preview-header">
          <Typography.Title level={4} style={{ margin: 0 }}>
            {page === 'tasks' ? '历史查询' : 'ICP备案查询'}
          </Typography.Title>
          <Flex align="center" gap={8}>
            {page === 'tasks' ? (
              <Button type="primary" onClick={() => onNavigate('collection')}>
                新查询
              </Button>
            ) : null}
            <Button type="text" icon={<MoonOutlined />} />
          </Flex>
        </Layout.Header>
        <Layout.Content className="preview-main">
          {page === 'tasks' ? (
            <Card>
              <HistoryTable onOpen={() => onNavigate('collection')} />
            </Card>
          ) : (
            <Flex vertical gap={16}>
              <Card>
                <QueryFields layout="inline" />
              </Card>
              <Card>
                <ResultLedger compactTitle />
              </Card>
            </Flex>
          )}
        </Layout.Content>
      </Layout>
    </Layout>
  )
}

function MixPreview({ page, onNavigate }: { page: LabPage; onNavigate: (page: LabPage) => void }) {
  return (
    <Layout className="preview-shell mix-shell">
      <Layout.Header className="mix-header">
        <Brand markClass="brand-mark-blue" />
        <Menu
          mode="horizontal"
          selectedKeys={[page]}
          onClick={({ key }) => onNavigate(key as LabPage)}
          style={{ flex: 1, minWidth: 0, border: 0, background: 'transparent' }}
          items={[
            { key: 'collection', label: 'ICP备案查询' },
            { key: 'tasks', label: '历史查询' },
          ]}
        />
        <Button type="text" icon={<SunOutlined />} />
      </Layout.Header>
      <Layout>
        {page === 'collection' ? (
          <Layout.Sider width={320} theme="light" className="mix-form-sider">
            <Typography.Title level={5} style={{ margin: '0 0 16px' }}>
              查询条件
            </Typography.Title>
            <QueryFields layout="stack" />
          </Layout.Sider>
        ) : null}
        <Layout.Content className="preview-main mix-main">
          {page === 'tasks' ? (
            <Card title="历史查询" extra={<Button type="primary" onClick={() => onNavigate('collection')}>新查询</Button>}>
              <HistoryTable onOpen={() => onNavigate('collection')} />
            </Card>
          ) : (
            <Card styles={{ body: { paddingTop: 16 } }}>
              <ResultLedger />
            </Card>
          )}
        </Layout.Content>
      </Layout>
    </Layout>
  )
}

function TopPreview({ page, onNavigate }: { page: LabPage; onNavigate: (page: LabPage) => void }) {
  return (
    <Layout className="preview-shell command-shell">
      <Layout.Header className="command-header">
        <Flex align="center" gap={24} style={{ width: '100%' }}>
          <Brand markClass="brand-mark-blue" />
          <Menu
            mode="horizontal"
            selectedKeys={[page]}
            onClick={({ key }) => onNavigate(key as LabPage)}
            style={{ flex: 1, minWidth: 0, border: 0 }}
            items={[
              { key: 'collection', label: 'ICP备案查询' },
              { key: 'tasks', label: '历史查询' },
            ]}
          />
          <Button type="text" icon={<SunOutlined />} />
        </Flex>
      </Layout.Header>
      <Layout.Content className="command-main">
        {page === 'tasks' ? (
          <>
            <Flex justify="space-between" align="center" style={{ marginBottom: 16 }}>
              <div>
                <Typography.Title level={3} style={{ margin: 0 }}>
                  历史查询
                </Typography.Title>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  点开一条记录，会带回当时的企业和数据源。
                </Typography.Paragraph>
              </div>
              <Button type="primary" onClick={() => onNavigate('collection')}>
                新查询
              </Button>
            </Flex>
            <Card>
              <HistoryTable onOpen={() => onNavigate('collection')} />
            </Card>
          </>
        ) : (
          <>
            <Typography.Title level={2} style={{ margin: '0 0 8px' }}>
              查企业股权
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 20 }}>
              先搜企业，再收紧数据源、深度和持股。四个来源的标签会完整显示。
            </Typography.Paragraph>
            <Input.Search
              size="large"
              defaultValue="小米科技"
              enterButton="查询"
              allowClear
              style={{ maxWidth: 720, marginBottom: 16 }}
            />
            <Card styles={{ body: { padding: 20 } }}>
              <QueryFields layout="inline" size="large" showKeyword={false} showSubmit={false} />
            </Card>
            <Card style={{ marginTop: 16 }} styles={{ body: { paddingTop: 16 } }}>
              <ResultLedger />
            </Card>
          </>
        )}
      </Layout.Content>
    </Layout>
  )
}

function SplitPreview({ page, onNavigate }: { page: LabPage; onNavigate: (page: LabPage) => void }) {
  return (
    <Layout className="preview-shell">
      <Layout.Header className="preview-header">
        <Brand />
        <Menu
          mode="horizontal"
          selectedKeys={[page]}
          onClick={({ key }) => onNavigate(key as LabPage)}
          style={{ flex: 1, minWidth: 0, border: 0, background: 'transparent' }}
          items={[
            { key: 'collection', label: 'ICP备案查询' },
            { key: 'tasks', label: '历史查询' },
          ]}
        />
        <Button type="text" icon={<MoonOutlined />} />
      </Layout.Header>
      <Layout.Content>
        {page === 'tasks' ? (
          <div className="split-ledger">
            <Flex justify="space-between" align="center" style={{ marginBottom: 12 }}>
              <Typography.Title level={4} style={{ margin: 0 }}>
                历史查询
              </Typography.Title>
              <Button type="primary" onClick={() => onNavigate('collection')}>
                新查询
              </Button>
            </Flex>
            <HistoryTable onOpen={() => onNavigate('collection')} />
          </div>
        ) : (
          <Splitter style={{ height: 'calc(100vh - 148px)' }}>
            <Splitter.Panel defaultSize={340} min={280} max={420}>
              <div className="split-form">
                <Typography.Title level={4} style={{ marginTop: 0 }}>
                  查询条件
                </Typography.Title>
                <QueryFields layout="stack" />
              </div>
            </Splitter.Panel>
            <Splitter.Panel>
              <div className="split-ledger">
                <ResultLedger />
              </div>
            </Splitter.Panel>
          </Splitter>
        )}
      </Layout.Content>
    </Layout>
  )
}

export function StyleLab() {
  const [id, setId] = useState<StyleId>('mix')
  const [page, setPage] = useState<LabPage>('collection')
  const [picked, setPicked] = useState<StyleId | null>(null)
  const antdTheme = useMemo(() => themeOf(id), [id])
  const current = STYLES.find((item) => item.id === id)!

  return (
    <ConfigProvider locale={zhCN} theme={antdTheme}>
      <div className={`lab lab-${id}`} style={{ ['--lab-accent' as string]: id === 'top' || id === 'mix' ? '#1677ff' : '#0f766e' }}>
        <header className="lab-bar">
          <div>
            <strong>选一种工作台布局</strong>
            <span className="lab-pitch">{current.origin} · {current.pitch}</span>
          </div>
          <Flex gap={8} wrap="wrap" align="center">
            {STYLES.map((item) => (
              <Button key={item.id} type={item.id === id ? 'primary' : 'default'} onClick={() => setId(item.id)}>
                {item.name}
              </Button>
            ))}
            <Button type="primary" ghost={picked === id} onClick={() => setPicked(id)}>
              {picked === id ? '已选这个' : '使用这个风格'}
            </Button>
          </Flex>
        </header>
        {picked ? (
          <div className="lab-picked">
            记下了「{STYLES.find((item) => item.id === picked)?.name}」。回到对话里说一声，我就按这个改正式工作台。
          </div>
        ) : null}
        <div className="lab-toolbar">
          <span>预览页面</span>
          <Flex gap={8}>
            <Button type={page === 'collection' ? 'primary' : 'default'} onClick={() => setPage('collection')}>
              查询页
            </Button>
            <Button type={page === 'tasks' ? 'primary' : 'default'} onClick={() => setPage('tasks')}>
              历史页
            </Button>
          </Flex>
        </div>
        <div className="lab-stage">
          {id === 'side' ? <SidePreview page={page} onNavigate={setPage} /> : null}
          {id === 'mix' ? <MixPreview page={page} onNavigate={setPage} /> : null}
          {id === 'top' ? <TopPreview page={page} onNavigate={setPage} /> : null}
          {id === 'split' ? <SplitPreview page={page} onNavigate={setPage} /> : null}
        </div>
      </div>
    </ConfigProvider>
  )
}
