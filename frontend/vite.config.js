import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// Two dev modes:
//
//   npm run dev          localhost only, API called directly on :8000
//   npm run dev:mobile   HTTPS on the LAN, API proxied through this server
//
// `dev:mobile` exists because of one browser rule: a page may only open a
// camera in a secure context. `http://localhost` counts as one, but
// `http://192.168.1.20:5173` — the address a phone has to use — does not.
// So testing the barcode scanner on a phone needs real HTTPS, which is
// what basicSsl provides with a self-signed certificate.
//
// The proxy solves the second half. With `VITE_API_URL=/api` (set in
// .env.mobile) the app calls its own origin, and this server forwards to
// the backend. That means the phone needs one reachable address instead of
// two, and same-origin requests skip CORS entirely — no CORS_ORIGINS entry
// per device, which would otherwise be a new backend restart per tester.
export default defineConfig(({ mode }) => {
  const mobile = mode === 'mobile'

  return {
    plugins: [react(), tailwindcss(), ...(mobile ? [basicSsl()] : [])],

    server: {
      // `true` binds every interface so the LAN can reach it. Left off by
      // default: exposing a dev server on a shared network — a campus or
      // café — should be something you opt into, not something that
      // happens because you ran the usual command.
      host: mobile,

      proxy: {
        '/api': {
          target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
