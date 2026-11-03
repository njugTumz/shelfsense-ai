# PLAN.md - ShelfSense AI

> FOR MUSE CODE: Read this file before EVERY task. Do NOT build anything in OUT OF SCOPE. After a task, update checkboxes [ ] to [x] and STOP at each CHECKPOINT.

## 1. Project Lock
Name: ShelfSense AI
Track: Indie Developer (Solo) - Build For Your World
Deadline: Nov 4, 9:00 PM ET
One-liner: Turn one shelf photo into live inventory and a supplier reorder in 60 seconds.
Target User: Small shops, cafes, kiosks, mini-marts, pharmacies
Lead Developer: njugTumz (https://github.com/njugTumz)
AI Coding Agent: Muse Code by Meta

## 1A. Deployment-First Rule
Deploy on Azure from Checkpoint 1. Every checkpoint must work on LIVE Azure URL, not just localhost.
Frontend: Azure Static Web Apps
Backend: Azure Container Apps (Docker, Port 8000)
Resource Group: shelfsense-rg (East US)
Live Frontend URL: TO_BE_FILLED
Live Backend URL: TO_BE_FILLED
Secrets (META_API_KEY) ONLY in Azure + local backend/.env. NEVER in frontend/git.

## 1B. Mobile-First + PWA
Phone-first for shopkeepers. Design for 375px first.
- Min touch target 44x44px, bottom CTA bar on mobile
- Camera input MUST be: <input type="file" accept="image/*" capture="environment">
- Inventory shows as cards on mobile, table on desktop
- PWA via vite-plugin-pwa: Installable, manifest name "ShelfSense AI", short_name "ShelfSense", theme_color #0f172a, display standalone, icons 192x192 + 512x512, service worker caches app shell + demo images for offline Demo Mode

## 1C. Judging Optimization
Impact 30%: Prevent stockouts/waste for small businesses
Meta Tooling 25%: SAM 3.1 (segment/count) + Muse Spark (identify/reason) + Muse Voice Transcribe (hands-free updates)
Demo 25%: Live photo -> inventory -> reorder on Azure, works on phone
Technical 20%: Clean Architecture, SOLID, Docker, Azure deployed

## 2. MVP Scope - ONLY THIS
[ ] Upload/take shelf photo
[ ] Agent timeline: SAM -> Muse Spark -> Stock Assessment
[ ] Inventory list extracted: name, category, count, confidence, stock_level (ok/low/out)
[ ] Stats: Total SKUs, Low Stock, Out of Stock
[ ] Editable inventory (change count/name)
[ ] Reorder Agent: generates supplier order text from low/out items
[ ] Voice Note: record/upload audio -> transcribe -> update inventory
[ ] Demo Mode: 3 pre-loaded shelves with cached results - WORKS WITHOUT API KEY
[ ] Public GitHub + 3-min demo video + Live Azure links

## OUT OF SCOPE - DO NOT BUILD
Auth/Login, Payments, Real email/supplier API, Barcode scanner, Multi-store, Native mobile app

## 3. Architecture - Clean Architecture + SOLID
Backend: Python 3.11, FastAPI. Frontend: React + Vite + Tailwind. DB: SQLite
backend/app/domain/ (entities, no framework), application/ (use cases), infrastructure/ (Meta clients, DB), interfaces/ (FastAPI routes), core/ (config, DI)
frontend/src/features/, components/, services/api.ts
SOLID: 1 client per Meta model, routes only call UseCases, UseCases depend on interfaces not concrete clients

## 4. APIs
POST /api/scans/analyze -> {scan_id, items[], summary}
GET /api/inventory, PATCH /api/inventory/{id}
POST /api/reorder/generate -> {order_text, items}
POST /api/voice-note -> {transcript, applied_updates}
GET /api/health -> {status:ok, demo_mode:bool}
If META_API_KEY missing, backend still starts in demo_mode.

## 5. Checkpoints
CHECKPOINT 1: Scaffold + Mobile PWA + Docker + LIVE Azure Deploy of health check
CHECKPOINT 2: Python script tests Muse Spark on 1 shelf image, returns clean JSON
CHECKPOINT 3: Full analyze flow photo->table works live
CHECKPOINT 4: Reorder + Voice + Demo Mode works live
CHECKPOINT 5: Final polish, README, video, submit

## 6. Local Setup Goal
Judge clones and runs in <5 mins with Docker or manual. No Azure needed, no API key needed for Demo Mode.