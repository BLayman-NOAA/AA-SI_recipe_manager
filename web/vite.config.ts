import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const server = 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/aa_recipe_manager/server/static',
    emptyOutDir: true,
    // The app chunk is about 1.3 MB with EchogramGL and zarrita (M6); revisit with Monaco (M5).
    // The decode worker and zarrita's codecs are separate assets, loaded on demand.
    chunkSizeWarningLimit: 1500,
    // popout.html is the empty page a pane opened in a new window loads.
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL('index.html', import.meta.url)),
        popout: fileURLToPath(new URL('popout.html', import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      '/api': { target: server, changeOrigin: true },
      '/echogram': { target: server, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['test/setup.ts'],
    include: ['test/**/*.test.{ts,tsx}'],
  },
});
