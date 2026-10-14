import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const server = 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/aa_recipe_manager/server/static',
    emptyOutDir: true,
    // The app chunk is about 0.9 MB; revisit with Monaco (M5) and EchogramGL (M6).
    chunkSizeWarningLimit: 1200,
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
