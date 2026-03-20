# 🔋 BatteryDesk — Live Battery Scrap Intelligence

AI-powered battery scrap price analysis using multi-agent CrewAI pipeline with live metal price scraping.

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
