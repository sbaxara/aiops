// Copyright (c) 2026 Axara Pty Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Vite build settings for the dashboard (React and Tailwind plugins, edition module, version define,
// dev proxy).
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
import path from 'node:path'

// @edition is the Enterprise module when src/enterprise exists (Enterprise source tree), else the
// empty Community module. The Community repository has no src/enterprise folder.
const enterpriseModule = path.resolve(__dirname, 'src/enterprise/index.jsx')
const editionModule = fs.existsSync(enterpriseModule)
  ? enterpriseModule
  : path.resolve(__dirname, 'src/edition/community.js')

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@edition': editionModule } },
  // Release version shown in the UI: AXARA_VERSION (image build arg), else package.json version
  define: {
    __APP_VERSION__: JSON.stringify(process.env.AXARA_VERSION || process.env.npm_package_version || 'dev'),
  },
  server: {
    port: 3000,
    proxy: {
      // Proxy API calls to monitoring server during development
      '/api': {
        target: 'http://localhost:9090',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  }
})