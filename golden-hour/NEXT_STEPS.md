# Your 48-hour runbook (do these in order)

## 1. Git + GitHub (5 min) — run in PowerShell inside the `golden-hour` folder
```powershell
# if a broken .git folder exists, remove it first:
Remove-Item -Recurse -Force .git -ErrorAction SilentlyContinue
git init
git add .
git commit -m "Golden Hour: earthquake USAR decision intelligence platform"
```
Create an empty **public** repo named `golden-hour` on github.com (no README), then:
```powershell
git remote add origin https://github.com/<your-username>/golden-hour.git
git branch -M main
git push -u origin main
```
⚠️ `.gitignore` already blocks `*.json` keys — never commit `gcp-service-account.json`.

## 2. Run locally with real GCP (15 min)
```powershell
pip install -r requirements.txt
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\...\gcp-service-account.json"
python data/generate_data.py
python scripts/setup_bigquery.py     # should print top-5 priority sites
streamlit run app.py
```
Verify: sidebar shows "Data source: BigQuery ✅" and a Scout submission returns `source: gemini`.
If Gemini errors: enable the Vertex AI API in the console and ensure the service account has role **Vertex AI User**.

## 3. Deploy to Cloud Run (20 min)
```powershell
gcloud auth login
gcloud config set project usar-decision-intel
gcloud services enable run.googleapis.com cloudbuild.googleapis.com aiplatform.googleapis.com
gcloud run deploy golden-hour --source . --region us-central1 --allow-unauthenticated --memory 1Gi
```
The printed URL is your **working prototype link**. Grant the Cloud Run service account (shown in the deploy output / IAM page) roles: BigQuery Data Editor, BigQuery Job User, Vertex AI User.

## 4. Demo video script (3 min video)
1. **Hook (20 s):** "March 2025, Mandalay. Thousands trapped. Every hour, survival odds drop."
2. **Scout (40 s):** submit a report with photo → show Gemini's priority score + reasoning. Toggle network OFF → submit → show offline queue → toggle ON → sync.
3. **Command (60 s):** map with 300 sites, golden-hour countdown, hit RUN OPTIMIZER → deployment arcs appear, "192 expected survivors reached, solved in under a second."
4. **Rescue (40 s):** pick a team missing its crane → generate tactical plan → Gemini substitutes lift bags + cribbing.
5. **Close (20 s):** architecture slide — BigQuery + Vertex AI Gemini + OR-Tools + Cloud Run.

## 5. Deck
Reuse the hackathon template. Map slides to: Problem → Solution → Architecture (README diagram) → Demo screenshots → Impact & scaling.

## Cost check
BigQuery: free tier (data is KB-scale). Cloud Run: free tier for demo traffic. Gemini Flash via Vertex AI: fractions of a cent per triage — your $1000 GenAI credit covers this thousands of times over. Total realistic spend: **< $5**.
