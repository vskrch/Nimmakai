import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'

export default defineConfig({
  plugins: [
    react(),
    // ponytail: only emit bundle analysis in ANALYZE=1 builds. Production builds
    // leave this file absent unless explicitly requested.
    process.env.ANALYZE === '1' && visualizer({ open: false, filename: '../bundle-stats.html' }),
  ].filter(Boolean),
  build: {
    outDir: '../src/potato/static/dist',
    emptyOutDir: true,
    // ponytail: chunk admin-heavy pages separately so the dashboard shell loads fast.
    rollupOptions: {
      output: {
        manualChunks: {
          chat: ['./src/pages/ChatPage.tsx'],
          admin: [
            './src/pages/ModelsPage.tsx',
            './src/pages/ProvidersPage.tsx',
            './src/pages/HealthPage.tsx',
            './src/pages/RoutingPage.tsx',
            './src/pages/ModelLaddersPage.tsx',
            './src/pages/ModelPoolGatingPage.tsx',
            './src/pages/RLPage.tsx',
            './src/pages/UsersPage.tsx',
          ],
          analytics: [
            './src/pages/AnalyticsOverviewPage.tsx',
            './src/pages/RequestsPage.tsx',
            './src/pages/CostPage.tsx',
            './src/pages/IntentsPage.tsx',
            './src/pages/LiveFeedPage.tsx',
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/analytics': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/accounts': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/stats': 'http://localhost:8000',
      '/ladder': 'http://localhost:8000',
      '/catalog': 'http://localhost:8000',
      '/preferences': 'http://localhost:8000',
    },
  },
})
