import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const reactRuntimePattern = /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/
const rcComponentPattern = /[\\/]node_modules[\\/]@rc-component[\\/]([^\\/]+)[\\/]/
const sharedAntdRuntime = [
  'async-validator',
  'dialog',
  'drawer',
  'form',
  'input',
  'input-number',
  'menu',
  'motion',
  'notification',
  'overflow',
  'select',
  'trigger',
  'util',
  'virtual-list',
]

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (reactRuntimePattern.test(id)) return 'react-vendor'
          const rcComponent = id.match(rcComponentPattern)?.[1]
          if (rcComponent && sharedAntdRuntime.some((name) => name === rcComponent)) return 'antd-runtime'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
