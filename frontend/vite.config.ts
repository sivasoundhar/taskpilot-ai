import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    proxy: {
      // Backend runs on :8000 in dev; proxy backend routes so the
      // frontend can call relative paths without CORS complications.
      // IMPORTANT: every backend route the frontend calls needs an entry
      // here -- a missing one doesn't error, it silently falls through
      // to Vite's own SPA index.html, which then fails JSON parsing in
      // a way that's easy to misdiagnose as a React bug instead of a
      // proxy config gap (found live testing the History/Settings pages).
      '/run': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/history': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
    },
  },
})
