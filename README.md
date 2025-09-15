# FastAPI on Railway

Simple FastAPI service for the TNT McLeod update. Deployable on Railway.

## Local development

Requirements: Python 3.10+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# Run locally
uvicorn main:app --reload
# Visit http://127.0.0.1:8000 and API docs at http://127.0.0.1:8000/docs
```

## Endpoints

- `/` – returns a simple status payload
- `/health` – lightweight healthcheck for uptime probes

## Deploy to Railway

This repo includes a `Procfile` so Railway/Nixpacks knows how to start the web service.

1. Push this project to a GitHub repository.
2. In Railway, create a New Project → Deploy from Repo → pick your repo.
3. Railway will build with Nixpacks and start using the Procfile:
   ```
   web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```
4. After the first deploy, open the service → Networking → Generate Domain.

### Deploy via CLI (optional)

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

No extra config is required; Railway sets the `PORT` env var automatically.


