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



# FastAPI snippet you can drop into your app for /health/upstream
import socket, ssl, json, time
from fastapi import APIRouter, Response
import httpx

router = APIRouter()

UP_HOST = "tms-patt.loadtracking.com"
UP_PORT = 5790
UP_URL_HTTP = f"http://{UP_HOST}:{UP_PORT}/"
UP_URL_HTTPS = f"https://{UP_HOST}:{UP_PORT}/"

def try_dns(host: str):
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, None)
        dur = round((time.time()-t0)*1000)
        return {"ok": True, "answers": [i[4][0] for i in infos], "ms": dur}
    except Exception as e:
        dur = round((time.time()-t0)*1000)
        return {"ok": False, "error": repr(e), "ms": dur}

def try_tcp(host: str, port: int, family=socket.AF_UNSPEC):
    t0 = time.time()
    try:
        for res in socket.getaddrinfo(host, port, family, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            with socket.socket(af, socktype, proto) as s:
                s.settimeout(5)
                s.connect(sa)
                dur = round((time.time()-t0)*1000)
                return {"ok": True, "family": "IPv6" if af==socket.AF_INET6 else "IPv4", "peer": sa, "ms": dur}
        return {"ok": False, "error": "no addrinfo results"}
    except Exception as e:
        dur = round((time.time()-t0)*1000)
        return {"ok": False, "error": repr(e), "ms": dur}

def try_tls(host: str, port: int):
    t0 = time.time()
    ctx = ssl.create_default_context()
    # Ensure SNI is used
    try:
        with socket.create_connection((host, port), timeout=7) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                dur = round((time.time()-t0)*1000)
                return {"ok": True, "cipher": ssock.cipher(), "cert_subject": cert.get('subject'), "ms": dur}
    except Exception as e:
        dur = round((time.time()-t0)*1000)
        return {"ok": False, "error": repr(e), "ms": dur}

async def try_http(url: str):
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=5.0)) as client:
            r = await client.get(url)
            dur = round((time.time()-t0)*1000)
            return {"ok": True, "status": r.status_code, "ms": dur}
    except Exception as e:
        dur = round((time.time()-t0)*1000)
        return {"ok": False, "error": repr(e), "ms": dur}

@router.get("/health/upstream-debug")
async def upstream_debug():
    dns_res = try_dns(UP_HOST)
    tcp_v4 = try_tcp(UP_HOST, UP_PORT, socket.AF_INET)
    tcp_v6 = try_tcp(UP_HOST, UP_PORT, socket.AF_INET6)
    tls_res = try_tls(UP_HOST, UP_PORT)
    http_plain = await try_http(UP_URL_HTTP)
    http_tls = await try_http(UP_URL_HTTPS)
    body = {
        "dns": dns_res,
        "tcp_ipv4": tcp_v4,
        "tcp_ipv6": tcp_v6,
        "tls": tls_res,
        "http_http": http_plain,
        "http_https": http_tls,
    }
    status = 200 if any(x.get("ok") for x in [tcp_v4, tcp_v6, tls_res, http_plain, http_tls]) else 503
    return Response(content=json.dumps(body, indent=2), media_type="application/json", status_code=status)
