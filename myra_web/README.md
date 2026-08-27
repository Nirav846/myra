# MYRA Frontend (`myra_web/`)

React 19 + TypeScript + Vite 6 frontend for MYRA, plus the FastAPI API bridge.

## What's here

- `myra_fastapi_server.py` — the FastAPI app (**API bridge**) wiring **19 routers** (port 8000).
- `routes/` — 19 router modules (scanners, rrg, fund-traction, cross-buy, finstack, pipeline, etc.).
- `src/` — the React application (42 routes across scanners and analysis views).

## Development

Start the backend (port 8000) from the repo root:

```bash
python run_fastapi.py
```

Start the frontend (port 3000):

```bash
npm run dev          # vite --port 3000
```

Then open **http://localhost:3000**.

## Build & type-check

```bash
npm run build        # production build → dist/
npm run lint         # tsc --noEmit
```

CI (`../.github/workflows/ci.yml`) builds this project with **Node 22**.

## Environment

Copy secrets into a `.env` file if needed (e.g. Gemini for AI opinions). Backend configuration lives in the repo-root `.env` (see the main [README](../README.md)).
