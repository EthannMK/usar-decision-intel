# End-to-End Setup Guide

## Your credits
- **GCP Free Credit**: 6,439 THB remaining, expires 2026-08-20. General-purpose — use this for everything (BigQuery, Vertex AI, Cloud Run).
- **GenAI App Builder trial credit**: 32,504 THB, expires 2027-05-28, but scoped to Vertex AI Search/Conversation products only. It will NOT cover BigQuery, Vertex AI Gemini calls, or Cloud Run. Don't count on it for the core stack — see "Optional: use the big credit" below for a way to actually spend it.

## Budget reality check
At hackathon scale (10k+ rows, a few thousand Gemini calls, a demo Cloud Run deployment) this project should cost low tens of dollars at most:
- BigQuery: free tier covers 10GB storage + 1TB queries/month permanently — you likely pay $0.
- Vertex AI Gemini 1.5 Flash: fractions of a cent per call.
- Cloud Run: generous free tier (2M requests/month) — likely $0 for a demo.
- Skip AlloyDB/Cloud SQL and persistent GPU VMs — not needed and they're what actually burns credit fast.
- Skip full Looker (Looker Enterprise) — it's expensive and requires a separate paid instance. Use **Looker Studio** instead: it's free and connects directly to BigQuery.

## Step 1 — You do this (browser, ~20 min)
1. Go to console.cloud.google.com, create a new project (e.g. `usar-decision-intel`).
2. Under Billing, confirm this project is linked to the account holding your Free Credit.
3. Enable these APIs (search each in the top search bar → Enable):
   - BigQuery API
   - Vertex AI API
   - Cloud Run API
   - (Optional, only if doing the RAG search stretch feature) Vertex AI Search and Conversation API
4. Go to IAM & Admin → Service Accounts → Create Service Account. Name it `usar-app`. Grant roles: BigQuery Admin, Vertex AI User, Cloud Run Admin (Editor is fine too if you want to move fast during the hackathon — tighten later).
5. Open the service account → Keys → Add Key → Create new key → JSON. This downloads a `.json` file.
6. Upload that JSON key file into this project folder (name it `gcp-service-account.json`). Once it's here, Claude can run `gcloud`/`bq`/the BigQuery Python client directly — you won't need to touch the console again for day-to-day work.

## Step 2 — Claude does this (once the key is uploaded)
- Authenticate the sandbox with the service account.
- Create the BigQuery dataset + tables (`bigquery/schema.sql`).
- Load the synthetic + real Myanmar geo dataset.
- Wire up Gemini and OR-Tools against real BigQuery data instead of local mocks.

## Step 3 — GPU benchmark (RAPIDS needs a real GPU; this sandbox doesn't have one)
- Easiest free option: Google Colab (free T4 GPU). Claude will hand you a ready-to-run notebook — open it in Colab, run it, and it produces the CPU-vs-GPU timing numbers to show on the dashboard.
- If your hackathon provides an NVIDIA GPU workstation/credits, use that instead — same script.

## Step 4 — Deploy for the demo
- Simplest: Streamlit Community Cloud (free, no GCP cost, public URL in minutes).
- More "on-brand" for the judging criteria: Cloud Run (covered by your free credit, shows you're using the GCP stack end-to-end).

## Optional: actually use the big GenAI App Builder credit
Add a "conversational search over incident history" feature using Vertex AI Search (RAG) — this is literally in the hackathon's tech list ("Conversational analytics and natural language interfaces", "RAG") and is the one thing that credit is scoped for. Worth adding as a stretch feature once the core MVP works.
