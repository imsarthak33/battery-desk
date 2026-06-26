# 🔋 BatteryDesk — Live Battery Scrap Intelligence

AI-powered battery scrap price analysis using multi-agent CrewAI pipeline with live metal price scraping.
I built this in caffeine-drenched spurts to solve one thing: stop guessing what those busted EV packs are actually worth. This is a scrappy pipeline that mashes live metal prices, a few overly ambitious agents, and a no-frills dashboard to tell you whether to buy that pallet of shredded cells or run.

No marketing fluff. Just the guts.

---

TL;DR
- Drop in API keys, pip install, run the FastAPI app, open localhost:8000.
- It fetches live metal prices, runs a multi-agent margin calculator, and shows progress in real time.
- Works for: NMC, NCA, LCO, LFP, Lead Acid.

Why this exists (short version)
- People trade battery scrap on gut feeling. I wanted something fast and dirty that gives an honest number — with a margin stack, cost breakdown, and live market pulls. I call it BatteryDesk because “Battery Prophet” felt arrogant.

What it does (plain)
- Scrapes live metal prices (Google via Serper + optional metal price APIs).
- Runs a CrewAI multi-agent pipeline for fetching data, computing margins, and short-term forecasting.
- Exposes a simple SSE-powered UI that shows progress like a terminal because I like dark themes.
- Outputs buy/sell signals and full cost breakdowns so you can actually reconcile P&L.

Features
- Live metal price scraping (via Serper API; optional metalpriceapi)
- Multi-agent pipeline (CrewAI agents orchestrating fetch → compute → forecast)
- Margin Stack Calculator: see buy price, processing, transport, margin, tax, ROI
- Supports common chemistries: NMC, NCA, LCO, LFP, Lead Acid
- Lightweight frontend: vanilla HTML/CSS/JS (dark terminal vibes)
- CLI mode for quick one-off runs or automation

Tech stack (what I actually used)
- AI Agents: CrewAI (orchestrator) + NVIDIA NIM (Llama 3.3 70B if you have the GPU ego)
- Backend: FastAPI + Uvicorn
- Frontend: Vanilla HTML/CSS/JS (keeps things simple and debuggable)
- Price data: Serper (Google scraping), Trading Economics, optional metalpriceapi
- Forex: Live USD/INR from free endpoints

Quickstart — get it running locally
1) Clone and install
```bash
git clone https://github.com/imsarthak33/battery-desk.git
cd battery-desk
pip install -r requirements.txt
## Features

- **Live Metal Prices** — fetches real-time prices from Google Search (via Serper API)
- **Multi-Agent AI Pipeline** — CrewAI agents for data fetching, margin calculation, and market forecasting
- **Web Dashboard** — real-time SSE-powered UI with live progress tracking
- **Battery Chemistries** — NMC, NCA, LCO, LFP, Lead Acid
- **Margin Stack Calculator** — automatic buy/sell decision with full cost breakdown

## Tech Stack

| Component | Technology |
|-----------|-----------|
| AI Agents | CrewAI + NVIDIA NIM (Llama 3.3 70B) |
| Backend | FastAPI + Uvicorn |
| Frontend | Vanilla HTML/CSS/JS (dark terminal theme) |
| Price Data | Serper API (Google Search), Trading Economics |
| Forex | Live USD/INR from free APIs |

## Setup

1. **Clone and install:**
```bash
pip install -r requirements.txt
```

2. **Configure API keys** — copy `.env.example` to `.env` and fill in:
```bash
cp .env.example .env
```
- `NVIDIA_API_KEY` — get from [build.nvidia.com](https://build.nvidia.com/)
- `SERPER_API_KEY` — get from [serper.dev](https://serper.dev/)
- `METAL_PRICE_API_KEY` — optional, from [metalpriceapi.com](https://metalpriceapi.com/)

3. **Run the web app:**
```bash
uvicorn app:app --reload
```
Open http://localhost:8000

4. **Or run CLI:**
```bash
python main_v2.py --chemistry NMC --ask 300
```

## Deployment (Railway)

1. Push to GitHub
2. Connect repo on [railway.app](https://railway.app/)
3. Add environment variables (NVIDIA_API_KEY, SERPER_API_KEY)
4. Deploy — Railway auto-detects the Procfile

## License

MIT
