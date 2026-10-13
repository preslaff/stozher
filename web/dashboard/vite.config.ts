/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  // MapLibre ships its tile worker as a separate file; pre-bundling breaks its URL in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
