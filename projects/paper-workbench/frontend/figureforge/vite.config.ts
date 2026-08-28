import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  base: '/figureforge/',
  build: {
    outDir: '../../web/static/figureforge',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'figureforge.js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: (assetInfo) => assetInfo.name?.endsWith('.css') ? 'figureforge.css' : 'assets/[name]-[hash][extname]',
      },
    },
  },
})
