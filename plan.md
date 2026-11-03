# PLAN.md - ShelfSense AI

## 1. Project Lock
Name: ShelfSense AI
Track: Indie Developer - Build For Your World
Deadline: Nov 4, 9:00 PM ET
One-liner: Turn one shelf photo into live inventory and a supplier reorder in 60 seconds.
Target User: Small shops, cafes, kiosks, mini-marts

## 2. Judging Optimization
Impact (30%): Prevent stockouts/waste for small businesses
Meta Tooling (25%): SAM 3.1 (count/segment) + Muse Spark (identify/reason) + Muse Voice Transcribe (update)
Demo (25%): Live photo-to-inventory-to-order flow, no slides
Technical (20%): Clean Architecture, SOLID, verifiable agent steps, Azure deployed

## 3. MVP Scope - ONLY THIS IN 8 HOURS
[ ] Upload shelf image (jpg/png)
[ ] Agent pipeline with live status steps
[ ] SAM 3.1 segmentation/count OR fallback bounding detection via Muse Spark if SAM API fails
[ ] Muse Spark identifies products: name, category, count, confidence, stock_level (ok/low/empty)
[ ] Inventory table (editable count/name, persisted in SQLite)
[ ] Low-stock dashboard cards: Total SKUs, Low Stock, Out of Stock
[ ] Reorder Agent: generates supplier order text/list from low items
[ ] Voice Note: upload/record audio -> Muse Voice Transcribe -> Muse Spark updates inventory intent
[ ] Demo Mode: 3 pre-loaded shelf images with cached results (INSURANCE if API is slow live)
[ ] Deployed on Azure + Public GitHub repo

## OUT OF SCOPE - DO NOT BUILD
[ ] Auth / Login, Payments, Real supplier API/email sending, Barcode scanner, Multi-store, Mobile app

## 4. Architecture - Clean Architecture + SOLID
Backend: Python 3.11 + FastAPI
Frontend: React + Vite + Tailwind CSS (no other UI lib)
Hosting: Azure Container Apps (Backend) + Azure Static Web Apps (Frontend)
DB: SQLite (file-based, keep simple)

Backend Structure:
backend/
  app/
    domain/          # Entities: Product, InventoryItem, Scan, Reorder - no framework code
    application/     # UseCases: AnalyzeShelfUseCase, GenerateReorderUseCase, ProcessVoiceNoteUseCase
    infrastructure/  # Meta clients: SamClient, MuseSparkClient, VoiceClient, SQLite repos
    interfaces/      # FastAPI routers, DTOs/schemas
    core/            # config, DI container
  main.py

SOLID Rules:
S: Each Meta client = 1 model only. Each UseCase = 1 job.
O: Add new model via new client, don't modify UseCases.
L/I: UseCases depend on interfaces (IModelClient, IInventoryRepo), not concrete clients.
D: FastAPI routes only call UseCases, never call Meta API directly.

Frontend Structure:
frontend/src/
  features/upload, features/inventory, features/reorder, features/voice
  components/AgentTimeline.tsx, InventoryTable.tsx, StatCard.tsx
  services/api.ts
  App.tsx

## 5. API Contracts
POST /api/scans/analyze {image} -> {scan_id, items:[{name,category,count,confidence,stock_level,bbox?}], summary}
GET  /api/inventory -> [items]
PATCH /api/inventory/{id} {count, name}
POST /api/reorder/generate {supplier_name} -> {order_text, items}
POST /api/voice-note {audio} -> {transcript, applied_updates}
GET  /api/health -> {status: ok}

## 6. Agent Pipeline (Show in UI)
Step 1: SAM 3.1 - Segmenting products...
Step 2: Muse Spark - Identifying products...
Step 3: Muse Spark - Assessing stock risk...
Step 4: Agent - Updating inventory & flagging low stock

## 7. Checkpoints for Muse Code
CHECKPOINT 1: Repo scaffolded, health endpoint works, frontend runs - STOP & VERIFY
CHECKPOINT 2: Meta API test script works for Muse Spark with 1 image - STOP & VERIFY
CHECKPOINT 3: Full analyze flow returns real JSON to frontend table - STOP & VERIFY
CHECKPOINT 4: Reorder + Demo Mode works end-to-end - STOP & VERIFY
CHECKPOINT 5: Azure deployed, env vars set, demo video ready - STOP

## 8. Azure Deployment
Backend: Azure Container Apps, env: META_API_KEY, set CORS to frontend URL
Frontend: Azure Static Web Apps, env: VITE_API_URL = backend URL
Secrets: NEVER in frontend or git. Only backend.

## 9. Demo Script (3 Minutes)
0:00 Pain: Shop owner manually counts shelves
0:20 Upload shelf photo live
0:40 Agent timeline - explain SAM + Muse Spark
1:10 Inventory appears, show low-stock items
1:40 Edit a count, say voice note "sold 3 milks"
2:10 Generate supplier reorder
2:40 Impact + close: Built with Muse Spark, SAM 3.1, Voice on Azure