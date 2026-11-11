# ClipForge — frontend

Interface React + TypeScript + Vite + Tailwind CSS.

## Développement

`bun run dev` lance le serveur Vite avec HMR. Les requêtes `/api/*` sont
proxifiées vers le backend FastAPI sur `http://127.0.0.1:8765`.

## Build

`bun run build` compile TypeScript (`tsc -b`) puis génère le bundle de
production dans `dist/`, servi ensuite par le backend (`app.frontend()`).

## Lint

`bun run lint` exécute Biome (`biome check .`) sur l'ensemble du projet.
