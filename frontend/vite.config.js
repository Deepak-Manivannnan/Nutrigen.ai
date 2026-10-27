import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Read VITE_* values from the project-root .env (one .env for the whole
  // repo locally). Only VITE_-prefixed keys reach the browser; on Render the
  // values come from the service's environment instead.
  envDir: '..',
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
