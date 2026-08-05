import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, proxy API calls to the backend so the console can run on :5173 while
// FastAPI runs on :8000. In production the built assets are served by FastAPI
// from the same origin, so relative URLs (see api.js) just work.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/scheduled-outages': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1500 },
})
