// The docs site viewer: one module script, one stylesheet and the ELK worker,
// with stable names, written to docs/assets/viewer (gitignored, built in CI).
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // Assets resolve against the script's own URL, so the site may live at any path.
  base: './',
  build: {
    outDir: '../docs/assets/viewer',
    emptyOutDir: true,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 800,
    // One entry and no dynamic imports: nothing to preload.
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: { viewer: 'src/viewer/main.tsx' },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: '[name].js',
        assetFileNames: (info) => ((info.names?.[0] ?? '').endsWith('.css') ? 'viewer.css' : '[name][extname]'),
      },
    },
  },
});
