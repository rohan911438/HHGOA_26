# HHGoa Fraud Investigation — Frontend

Next.js 16 (App Router) + React 19 + TypeScript + Tailwind v4 investigation
console for the backend in `../backend/`. Talks to that backend over
plain HTTP through exactly one module — `src/lib/api.ts` — and never
talks to TigerGraph, GSQL, or MCP directly.

Full architecture, API integration detail, main screens, testing, and
security boundaries: **`../backend/docs/phase-2-frontend.md`**. Project
overview, demo flow, and safety boundaries: **`../README.md`**.

## Quick start

```bash
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_URL - point at a running backend
npm run dev                   # http://localhost:3000
```

The backend must be running separately (`cd ../backend && uvicorn
app.api.app:create_app --factory --reload`).

## Scripts

```bash
npm run dev      # local dev server (Turbopack)
npm run build     # production build
npm run lint       # ESLint
npm test            # Jest + React Testing Library
npx tsc --noEmit      # typecheck
```
