# 🚀 ProcureIQ Deployment Guide (Render + Gemini)

## Objective

Deploy the Procurement AI prototype to Render, obtain a public HTTPS live URL, and satisfy hackathon requirements:

- Cloud deployment ✅  
- Uses at least one Google AI service (Gemini) ✅  
- Public demo link for judges ✅

Estimated deployment time: 10–15 minutes.

---

# Architecture

```text
Judge Browser
   ↓
Render Web Service (FastAPI + Frontend + SQLite demo data)
   ↓
Google Gemini API
```

---

# Prerequisites

Before starting, have these ready:

- GitHub account (to push the repo)
- Render account (free tier available at https://render.com)
- Gemini API key from:

https://aistudio.google.com/app/apikey

---

# Step 1 — Push Code to GitHub

If not already on GitHub:

1. Create a new repository on GitHub.
2. Push this project to the repo:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

---

# Step 2 — Create Render Web Service

1. Go to https://dashboard.render.com and sign in.
2. Click **New +** → **Web Service**.
3. Connect your GitHub repository.
4. Render will auto-detect the `Dockerfile`.

Configure the service:

| Setting | Value |
|---------|-------|
| **Name** | `procurement-ai` (or any name) |
| **Region** | Choose closest to you (e.g., Singapore) |
| **Branch** | `main` |
| **Runtime** | `Docker` |
| **Plan** | Free (or Starter for faster builds) |

---

# Step 3 — Add Environment Variable

In the Render dashboard for your service:

1. Go to **Environment** tab.
2. Click **Add Environment Variable**.
3. Add:

```text
Key:   GEMINI_API_KEY
Value: YOUR_GEMINI_KEY
```

Click **Save**.

---

# Step 4 — Deploy

Click **Create Web Service** (or **Manual Deploy** → **Deploy latest commit** if already created).

Render will build the Docker image and deploy it.

Wait 5–10 minutes for the build to complete.

---

# Step 5 — Copy Live URL

After deployment, Render provides a URL like:

```text
https://procurement-ai.onrender.com
```

Copy this.

This is your hackathon submission link.

---

# Step 6 — Verify Deployment

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

In the Render dashboard:

1. Go to **Settings** → edit `Dockerfile` directly or push an update.
2. Find:

```dockerfile
RUN playwright install chromium || true
```

Replace with:

```dockerfile
RUN python -m playwright install chromium
```

3. Commit the change and trigger a manual deploy.

---

# If Port Error Happens

Ensure Dockerfile contains:

```dockerfile
ENV PORT=8080

CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8080"]
```

Then redeploy.

---

