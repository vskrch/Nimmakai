import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/nimmakai/static/dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/analytics': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/stats': 'http://localhost:8000',
      '/ladder': 'http://localhost:8000',
      '/catalog': 'http://localhost:8000',
      '/preferences': 'http://localhost:8000',
    },
  },
})
