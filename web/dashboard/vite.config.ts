/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'

// MapLibre 6 starts its tile worker from `maplibre-gl-worker.mjs` (which imports `maplibre-gl-shared.mjs`), resolved next to
// the main script at run time. The bundler does not see that, so a production build had no working map ("Worker failed to
// load"). Emit both files, unhashed, into assets/ where the main script looks for them.
function maplibreWorker(): Plugin {
  const files = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']
  return {
    name: 'maplibre-worker',
    apply: 'build',
    generateBundle() {
      for (const f of files) {
        this.emitFile({
          type: 'asset',
          fileName: `assets/${f}`,
          source: readFileSync(new URL(`./node_modules/maplibre-gl/dist/${f}`, import.meta.url)),
        })
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), maplibreWorker()],
  server: { port: 5173, strictPort: true },
  // MapLibre ships its tile worker as a separate file; pre-bundling breaks its URL in dev.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
