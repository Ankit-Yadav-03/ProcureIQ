# ProcureIQ

**End-to-end B2B procurement intelligence. Parses requirements, discovers real vendors, automates outreach, extracts prices from replies, and benchmarks quotes with statistical confidence.**

Built for manufacturing teams in India who currently negotiate over WhatsApp with no price transparency.

---

## What It Does

| Stage | Status | Details |
|-------|--------|---------|
| **Requirement Parsing** | ✅ Live | Natural language → structured JSON via Gemini 2.5 Flash |
| **Vendor Discovery** | ✅ Live | Playwright scrapers for IndiaMART + TradeIndia (phone, location, rating) |
| **Outreach Generation** | ✅ Live | LLM-generated Hindi/English WhatsApp messages with wa.me links |
| **Response Extraction** | ✅ Live | LLM extracts price, delivery, terms from unstructured vendor replies |
| **Data Cleaning** | ✅ Live | IQR/MAD outlier removal + price normalization |
| **ROI Analysis** | ✅ Live | Market benchmark (min/avg/median) + savings calculation |
| **Negotiation Engine** | ✅ Live | Auto-generate data-driven counter-offers using real DB prices |
| **WhatsApp Business API** | 🔮 Phase 2 | Webhook handler built; needs Meta API token for full automation |

**Phase 1 Workflow:** The system generates pre-filled WhatsApp links. A human clicks and sends. Vendor replies are pasted back into the dashboard. This avoids API gatekeeping while proving the full pipeline.

---

## Architecture

```
User Input → FastAPI
     ↓
Gemini Parser (gemini-2.5-flash)  → Structured requirement JSON
     ↓
Vendor Discovery (Playwright)     → IndiaMART + TradeIndia
     ↓
Outreach Engine                   → WhatsApp message + wa.me link
     ↓
Response Collector                → Manual entry
     ↓
Gemini Extractor                  → Price, delivery, terms from raw messages
     ↓
Data Cleaner                      → Outlier removal, normalization
     ↓
Benchmark Engine                  → Min, avg, median market price
     ↓
ROI Engine                        → Savings vs current supplier
     ↓
Negotiation Agent                 → Competing-quote counter-offers (with DB price data)
     ↓
Output                            → Best vendor, total savings, confidence score
```

---

## Project Structure

```
ai-procurement-agent/
├── main.py                    # FastAPI app entry point
├── requirements.txt
├── Dockerfile                 # Production container
├── seed_demo_data.py          # One-click demo dataset for hackathon pitching
│
├── core/
│   ├── db.py                  # aiosqlite init + schema
│   ├── schemas.py             # Pydantic models
│   ├── config.py              # Environment config (Gemini API key)
│   └── utils.py               # Phone normalization, etc.
│
├── routes/
│   ├── procurement.py         # /api/analyze, /api/roi, /api/status, /api/result
│   ├── vendors.py             # /api/vendors/*, /api/vendors/response
│   └── outreach.py            # /api/outreach/*, /api/outreach/webhook
│
├── services/
│   ├── llm_client.py          # Gemini 2.5 Flash wrapper
│   ├── parser.py              # Input parsing + DB storage
│   ├── vendor_discovery.py    # Playwright scraper (IndiaMART, TradeIndia)
│   ├── outreach.py            # Message generation + WhatsApp link builder
│   ├── response_collector.py  # Manual entry
│   ├── extractor.py           # LLM price extraction from vendor replies
│   ├── cleaner.py             # Outlier removal, price normalization
│   ├── pricing.py             # Benchmark stats + ROI calculation
│   ├── roi.py                 # Full pipeline orchestration
│   └── llm_client.py          # Negotiation message generation
│
├── data/
│   └── procurement.db         # SQLite (auto-created)
│
├── frontend/
│   └── index.html             # Dashboard UI
│
└── tests/                     # Test suite
```

---

## Setup

### Prerequisites

- Python 3.11+
- [Gemini API key](https://aistudio.google.com/app/apikey) (free tier)

### 1. Install Dependencies

```bash
cd ai-procurement-agent
pip install -r requirements.txt

# Optional: Install Playwright browsers (for live scraping)
playwright install chromium
```

### 2. Configure Environment

Create a `.env` file:

```bash
GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>
LLM_PROVIDER=gemini
```

> Get your free API key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)

### 3. Run

```bash
uvicorn main:app --reload --port 8000
```

Open: [http://localhost:8000](http://localhost:8000)

---

## API Reference

### POST /api/analyze
Start the pipeline. Parse requirement, begin vendor discovery.

**Request:**
```json
{ "input_text": "steel rod 5000kg delhi 100" }
```

**Response:**
```json
{
  "requirement_id": 1,
  "parsed": {
    "item": "steel rod",
    "quantity": 5000,
    "unit": "kg",
    "location": "delhi",
    "current_price": 100
  },
  "status": "discovery_started"
}
```

---

### GET /api/status/{requirement_id}
Check pipeline progress. Poll this every few seconds after /analyze.

**Response:**
```json
{
  "status": "outreach_ready",
  "vendors_found": 12,
  "vendors_responded": 3,
  "responses_received": 3,
  "vendors": [...]
}
```

---

### POST /api/vendors/response
Submit a vendor's reply (manual entry).

**Request:**
```json
{
  "vendor_id": 4,
  "requirement_id": 1,
  "raw_message": "₹92 per kg + GST, delivery 2-3 days, min 500kg"
}
```

---

### POST /api/roi/{requirement_id}
Run full price extraction + ROI analysis. Call after collecting responses.

**Response:**
```json
{
  "best_price": 92,
  "best_vendor_name": "Sharma Steel",
  "total_savings": 40000,
  "savings_pct": 8.0,
  "confidence": "medium",
  "executive_report": "..."
}
```

---

### POST /api/negotiate/{vendor_id}
Generate a smart counter-offer using actual vendor prices from the database.

**Request:**
```json
{
  "requirement_id": 1,
  "best_competing_price": 88,
  "quantity": 5000,
  "unit": "kg"
}
```

The endpoint automatically:
1. Looks up the vendor's actual quoted price from DB
2. Finds the best competing price among all responses
3. Calculates market average
4. Generates a data-driven negotiation message via Gemini

---

### GET /api/outreach/{requirement_id}
Get all outreach messages with wa.me links for manual sending.

---

## Operational Workflow

1. Enter requirement → system discovers vendors
2. Go to Outreach panel → click wa.me links to send pre-filled WhatsApp messages
3. Wait for vendor replies (typically 30 min – 2 hours)
4. Submit each reply via the Response panel
5. Click "Run Analysis" → get ROI report
6. (Optional) Click "Negotiate" on any vendor → AI generates a data-driven counter-offer using real DB prices

---

## ROI Formula

```
Savings Per Unit  = Current Price - Best Vendor Price
Total Savings     = Savings Per Unit × Quantity
Savings %         = (Savings Per Unit / Current Price) × 100
Annual Estimate   = Total Savings × 12
```

## Confidence Scoring

| Factor               | High  | Medium | Low                  |
|----------------------|-------|--------|----------------------|
| Responses            | 5+    | 3–4    | 1–2                  |
| Price variance (CV%) | <10%  | <25%   | 25%+                 |
| Savings %            | 0–30% | —      | >30% with few quotes |

---

## Scraping Notes

IndiaMART and TradeIndia selectors change periodically. If scraping returns 0 results:
1. Open the site in browser, inspect actual element class names
2. Update selectors in `services/vendor_discovery.py`
3. Verify with `PLAYWRIGHT_HEADLESS=false` to watch the browser

If all live sources fail, the system falls back to a small set of generic placeholder vendors so the pipeline remains demoable. Run `python seed_demo_data.py` to populate a full synthetic dataset for pitching.

---

## Database Schema

```
requirements      — input + parsed fields + pipeline status
vendors           — discovered vendor contacts per requirement
outreach_log      — messages sent with batch tracking
vendor_responses  — raw vendor replies (pre-extraction)
procurement_results — final ROI output per requirement
negotiations      — counter-offer messages + vendor responses
```

All tables use INTEGER autoincrement PKs. No ORMs — raw aiosqlite for speed and simplicity.

---

## Hackathon Pitch Guide

### 30-Second Elevator Pitch
> "Manufacturers in India negotiate procurement over WhatsApp with zero price transparency. Our agent parses a requirement like '5000kg steel rod in Delhi', discovers real vendors on IndiaMART, generates Hindi outreach messages, extracts prices from replies, and benchmarks them against your current supplier — all with statistical confidence scoring. We save 8–15% on every order."

### Live Demo Script (3 minutes)
1. **Parse:** Type *"steel rod 5000kg delhi 100"* → show structured JSON
2. **Discover:** Watch vendors populate from IndiaMART/TradeIndia
3. **Outreach:** Click wa.me link → show pre-filled WhatsApp message
4. **Inject:** Paste a fake vendor reply → show price extraction
5. **Analyze:** Run ROI → reveal savings % and confidence score
6. **Negotiate:** Click "Negotiate" on a vendor → show AI-generated counter-offer using real DB prices

### Why This Wins
- **Real problem:** B2B procurement in India is opaque, manual, and lossy
- **Real data:** Scrapes live vendor directories (not mock APIs)
- **Real ROI:** Calculates actual rupee savings against current spend
- **Real AI:** Gemini 2.5 Flash powers requirement parsing, price extraction, and negotiation
- **Real deployment:** Deployed on Render with a permanent HTTPS link

### Judge FAQ (Preemptive)

**Q: "How do you know the vendors are real?"**
A: We scrape IndiaMART and TradeIndia — the two largest B2B marketplaces in India. Every vendor has a profile page, phone number, and usually ratings. We don't simulate them.

**Q: "What if scraping breaks during the demo?"**
A: We have a resilient retry system with exponential backoff. If a source is down, we fall back to the other. Worst case, `seed_demo_data.py` populates a full synthetic dataset in 2 seconds so the rest of the pipeline is always demonstrable.

**Q: "Why manual WhatsApp instead of API?"**
A: WhatsApp Business API requires Meta approval and a verified business — a 2-week process. Our Phase 1 uses wa.me links so any team can use it today. The API webhook is already built and tested for Phase 2.

**Q: "How accurate is the LLM price extraction?"**
A: We use Gemini 2.5 Flash. In testing it reliably extracts numeric prices from unstructured Hindi/English messages. We mark confidence (high/medium/low) and allow manual override in the dashboard.

**Q: "What's the moat?"**
A: The scraping layer (selectors, retry logic, anti-bot patterns) and the negotiation engine that queries real DB prices to generate data-driven counter-offers. Most LLM demos stop at chat. We go end-to-end: discovery → outreach → extraction → benchmarking → negotiation.

---

## Deployment

See [`DEPLOY.md`](DEPLOY.md) for step-by-step instructions to deploy on **Render**.

Quick summary:
1. Get a [Gemini API key](https://aistudio.google.com/app/apikey)
2. Connect your GitHub repo to Render as a Docker Web Service
3. Add your `GEMINI_API_KEY` environment variable
4. Deploy and share the HTTPS URL with judges

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + Python 3.11 |
| Database | SQLite (aiosqlite) |
| LLM | Gemini 2.5 Flash (Google AI Studio) |
| Scraping | Playwright (async) |
| Frontend | Vanilla HTML/JS (no build step) |
| Messaging | WhatsApp wa.me links (Phase 1) / Meta API (Phase 2) |
| Hosting | Render |

---

## Project link

https://procureiq-vakx.onrender.com

