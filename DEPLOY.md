# 🚀 ProcureIQ Deployment Guide (Google Cloud Run + Gemini)

## Objective

Deploy the Procurement AI prototype to Google Cloud Run, obtain a public HTTPS live URL, and satisfy hackathon requirements:

- Cloud deployment ✅  
- Uses at least one Google AI service (Gemini) ✅  
- Public demo link for judges ✅

Estimated deployment time: 20–30 minutes.

---

# Architecture

```text
Judge Browser
   ↓
Google Cloud Run (FastAPI + Frontend + SQLite demo data)
   ↓
Google Gemini API
```

---

# Prerequisites

Before starting, have these ready:

- Google account  
- Credit/debit card (for enabling Google Cloud billing; free tier should cover demo)
- Project zip file:
  
```text
env_procurememt_system.zip
```

- Gemini API key from:

https://aistudio.google.com/app/apikey

---

# Step 1 — Create Google Cloud Project

Open:

https://console.cloud.google.com

1. Click project selector (top bar)
2. Click **New Project**

Example:

```text
procurement-ai-hackathon
```

Click Create.

Wait until project becomes active.

---

# Step 2 — Enable Billing

Go to:

```text
Billing → Link Billing Account
```

Enable billing for the project.

(Cloud Run free tier should keep this at $0 for hackathon usage.)

---

# Step 3 — Enable Required APIs

Go to:

```text
Navigation Menu → APIs & Services → Library
```

Enable these APIs one by one:

### Required

- Cloud Run Admin API
- Cloud Build API
- Artifact Registry API

### For AI Requirement

- Generative Language API

---

# Step 4 — Open Cloud Shell

In top-right corner of Google Cloud Console:

Click:

```text
Activate Cloud Shell
```

Wait for terminal to initialize.

---

# Step 5 — Upload Project Zip

Inside Cloud Shell:

Click:

```text
⋮ → Upload
```

Upload:

```text
env_procurememt_system.zip
```

---

# Step 6 — Unzip Project

Run:

```bash
unzip env_procurememt_system.zip
```

Check folder name:

```bash
ls
```

Enter project:

```bash
cd env_procurememt_system_v2
```

(Adjust folder name if yours differs.)

---

# Step 7 — Create .dockerignore

Very important.

Run:

```bash
nano .dockerignore
```

Paste:

```dockerignore
.venv
__pycache__
*.pyc
.git
.env
tests
```

Save:

- CTRL+O
- Enter
- CTRL+X

---

# Step 8 — Verify Files

Run:

```bash
ls
```

You should see:

```text
Dockerfile
main.py
requirements.txt
```

If yes, proceed.

---

# Step 9 — Add Gemini API Key

Generate API key if not already done:

https://aistudio.google.com/app/apikey

Edit environment file:

```bash
nano .env
```

Add or verify:

```env
GEMINI_API_KEY=YOUR_GEMINI_KEY
```

Save.

---

# Step 10 — Set Active GCP Project

List projects:

```bash
gcloud projects list
```

Set correct one:

```bash
gcloud config set project YOUR_PROJECT_ID
```

Replace:

```text
YOUR_PROJECT_ID
```

with actual project ID.

---

# Step 11 — Deploy to Cloud Run

Run:

```bash
gcloud run deploy procurement-ai \
--source . \
--region asia-south1 \
--allow-unauthenticated
```

When prompted:

### Region

Choose:

```text
asia-south1
```

### Public Access

Choose:

```text
Y
```

Wait 5–10 minutes for build + deployment.

Cloud Run will use your Dockerfile automatically.

---

# Step 12 — Copy Live URL

After deployment you should see:

```text
Service URL:
https://procurement-ai-xxxxx.run.app
```

Copy this.

This is your hackathon submission link.

---

# Step 13 — Verify Deployment

## Health Check

Run:

```bash
curl https://YOUR-URL/health
```

Expected:

```json
{
  "status":"ok"
}
```

---

## Open App

Visit:

```text
https://YOUR-URL
```

Confirm:

- Frontend loads
- Dashboard works
- Gemini functionality works

---

# If Build Fails Due To Playwright

Edit Dockerfile:

```bash
nano Dockerfile
```

Find:

```dockerfile
RUN playwright install chromium || true
```

Replace with:

```dockerfile
RUN python -m playwright install chromium
```

Save.

Redeploy:

```bash
gcloud run deploy procurement-ai --source . --region asia-south1 --allow-unauthenticated
```

---

# If Port Error Happens

Ensure Dockerfile contains:

```dockerfile
ENV PORT=8080

CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8080"]
```

Then redeploy.

---

