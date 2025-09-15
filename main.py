from fastapi import FastAPI
from fastapi import HTTPException
import socket
import time
from urllib.parse import urlparse
import os
import requests
import logging

logger = logging.getLogger(__name__)


app = FastAPI(title="TNT McLeod API", version="0.1.0")


@app.get("/")
async def read_root() -> dict:

    return {"status": "ok", "message": "TNT McLeod API"}


@app.get("/health")
async def health() -> dict:

    return {"status": "healthy"}

def _parse_bool_env(var_name: str, default: bool = True) -> bool:
    value = os.getenv(var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_order_url(base_url: str, order_id: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/orders"):
        return f"{base}/{order_id}"
    return f"{base}/orders/{order_id}"


def _fetch_order_data(order_id: str) -> dict:
    base_url = os.getenv('GET_URL')
    token = os.getenv('TOKEN')
    company_id = os.getenv('COMPANY_ID')

    missing = [name for name, value in [("GET_URL", base_url), ("TOKEN", token), ("COMPANY_ID", company_id)] if not value]
    if missing:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": missing})

    url = _build_order_url(base_url, order_id)
    headers = {
        "Authorization": f"Token {token}",
        "X-com.mcleodsoftware.CompanyID": company_id,
        "Accept": "application/json"
    }

    method = (os.getenv("REQUEST_METHOD") or "GET").strip().upper()
    timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS") or 15)
    verify_tls = _parse_bool_env("REQUESTS_VERIFY", True)

    try:
        if method == "POST":
            body = {"mode": "raw", "raw": "", "options": {"raw": {"language": "json"}}}
            response = requests.post(url, headers=headers, json=body, timeout=timeout_seconds, verify=verify_tls)
        else:
            response = requests.get(url, headers=headers, timeout=timeout_seconds, verify=verify_tls)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail={"error": "Upstream HTTP error", "detail": detail})
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail={"error": "Upstream connection error", "detail": str(exc)})


@app.get("/get_load_data")
async def get_load_data(order_id: str):
    logger.info(f"Getting load data for order {order_id}")
    data = _fetch_order_data(order_id)
    return {"status": "ok", "message": data}



@app.get("/get_load_data/{order_id}")
async def get_load_data_path(order_id: str):
    logger.info(f"Getting load data for order {order_id}")
    data = _fetch_order_data(order_id)
    return {"status": "ok", "message": data}


@app.get("/health/upstream")
async def health_upstream() -> dict:
    base_url = os.getenv('GET_URL')
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": ["GET_URL"]})
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    host = parsed.hostname or base_url
    port = parsed.port or (443 if (parsed.scheme or "https").lower() == "https" else 80)
    try:
        start = time.time()
        with socket.create_connection((host, port), timeout=5):
            pass
        connect_ms = int((time.time() - start) * 1000)
        return {"status": "ok", "host": host, "port": port, "connect_ms": connect_ms}
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": "Upstream TCP connect failed", "host": host, "port": port, "detail": str(exc)})

