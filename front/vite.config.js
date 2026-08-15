import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Production subpath example:
//   VITE_BASE=/report-agent/ npm run build
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8989',
        changeOrigin: true,
      },
    },
  },
})
