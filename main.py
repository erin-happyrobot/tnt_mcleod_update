from fastapi import FastAPI
from fastapi import HTTPException
import socket
import time
from urllib.parse import urlparse
import os
import requests
import logging
import ssl
import json
from fastapi import Response
from pydantic import BaseModel
import httpx
from copy import deepcopy
from typing import Any, Dict, Optional

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

    missing = [n for n,v in [("GET_URL", base_url), ("TOKEN", token), ("COMPANY_ID", company_id)] if not v]
    if missing:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": missing})

    # Build URL safely: if GET_URL already ends with /orders, avoid duplicating
    url = _build_order_url(base_url, order_id)

    # If DNS is flaky, optionally route to a fixed IP while keeping Host header
    url_for_connect, host_override = _prepare_target(url)

    # Build headers with flexibility for proxy auth
    headers = {
        "X-com.mcleodsoftware.CompanyID": company_id,
        "Accept": "application/json",
        "Authorization": f"Token {token}"
    }
    if host_override:
        headers.update(host_override)

    method = (os.getenv("REQUEST_METHOD") or "GET").strip().upper()
    timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS") or 15)
    verify_tls = _parse_bool_env("REQUESTS_VERIFY", True)
    # If forcing connect to a specific IP over HTTPS, TLS verification will likely fail
    # because SNI/cert do not match the IP. Default to disabling verification in that case
    # unless the user explicitly set REQUESTS_VERIFY.
    if os.getenv("UPSTREAM_CONNECT_IP") and url.lower().startswith("https://") and os.getenv("REQUESTS_VERIFY") is None:
        verify_tls = False

    try:
        if method == "POST":
            payload = {}
            r = requests.post(url_for_connect, headers=headers, json=payload, timeout=timeout_seconds, verify=verify_tls)
        else:
            r = requests.get(url_for_connect, headers=headers, timeout=timeout_seconds, verify=verify_tls)
        r.raise_for_status()
        return r.json()

    except requests.exceptions.HTTPError as exc:
        # Surface upstream status and body to the client for clarity (e.g., 403 Forbidden)
        status = getattr(exc.response, "status_code", 502) if hasattr(exc, "response") else 502
        try:
            detail = exc.response.json() if exc.response is not None else str(exc)
        except Exception:
            detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status, detail={"error": "Upstream HTTP error", "detail": detail})
    except requests.exceptions.SSLError as exc:
        # Likely cert name/SNI mismatch when connecting by IP
        raise HTTPException(
            status_code=502,
            detail={
                "error": "TLS error to upstream",
                "detail": str(exc),
                "hint": "If you must connect by IP over HTTPS, prefer an /etc/hosts entry so SNI & certs match."
            }
        )
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


@app.get("/health/upstream-ip")
async def health_upstream_ip() -> dict:
    base_url = os.getenv('GET_URL')
    ip = os.getenv('UPSTREAM_CONNECT_IP')
    if not base_url:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": ["GET_URL"]})
    if not ip:
        raise HTTPException(status_code=400, detail={"error": "UPSTREAM_CONNECT_IP not set"})
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    port = parsed.port or (443 if (parsed.scheme or "https").lower() == "https" else 80)
    try:
        start = time.time()
        with socket.create_connection((ip, port), timeout=5):
            pass
        connect_ms = int((time.time() - start) * 1000)
        return {"status": "ok", "ip": ip, "port": port, "connect_ms": connect_ms}
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": "Upstream IP TCP connect failed", "ip": ip, "port": port, "detail": str(exc)})


@app.get("/health/egress-ip")
async def health_egress_ip() -> dict:
    endpoints = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
    ]
    last_error = None
    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=5.0)) as client:
                r = await client.get(url)
                r.raise_for_status()
                try:
                    data = r.json()
                    ip = data.get("ip")
                except Exception:
                    ip = r.text.strip()
                if ip:
                    return {"status": "ok", "ip": ip, "source": url}
        except Exception as e:
            last_error = str(e)
            continue
    raise HTTPException(status_code=502, detail={"error": "Unable to determine egress IP", "detail": last_error})

UP_HOST = "tms-patt.loadtracking.com"
UP_PORT = 5790
UP_URL_HTTP = f"http://{UP_HOST}:{UP_PORT}/"
UP_URL_HTTPS = f"https://{UP_HOST}:{UP_PORT}/"

def try_dns(host: str):
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, None)
        dur = round((time.time()-t0)*1000)
        return {"ok": True, "answers": sorted({i[4][0] for i in infos}), "ms": dur}
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



@app.get("/health/upstream-debug")
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


def _prepare_target(url: str):
    """
    If UPSTREAM_CONNECT_IP is set, route the TCP connection to that IP
    while preserving the Host header (virtual hosting).
    NOTE: For HTTPS, SNI will still use the URL host (the IP in this case),
    which may break TLS. Prefer /etc/hosts over this for HTTPS.
    """
    ip = os.getenv("UPSTREAM_CONNECT_IP")
    if not ip:
        return url, None  # no override

    parsed = urlparse(url if "://" in url else f"https://{url}")
    # Rebuild URL but swap hostname with IP (keep scheme/port/path/query)
    netloc = f"{ip}:{parsed.port}" if parsed.port else ip
    new_url = parsed._replace(netloc=netloc).geturl()

    host_header = os.getenv("HOST_HEADER") or parsed.hostname
    extra_headers = {"Host": host_header} if host_header else None
    return new_url, extra_headers

def transform_payload(
    payload: Dict[str, Any],
    extracted_actual_arrival: Optional[str] = None,
    extracted_actual_departure: Optional[str] = None,
) -> Dict[str, Any]:
    """
    - Remove all instances of keys in FIELDS_TO_REMOVE anywhere in the structure.
    - Apply status rules based on message.movements[0].brokerage_status:
        * ARVDSHPPER -> status=P; mov[0].brokerage_status=ARVDSHPR; stops[0].status=A; stops[0].actual_arrival=extracted_actual_arrival; mov[0].status=P
        * ENROUTE    -> status=P; mov[0].brokerage_status=ENROUTE;   stops[0].status=D; stops[0].actual_departure=extracted_actual_departure; mov[0].status=P
        * ARVDCNSG   -> status=P; mov[0].brokerage_status=ARVDCNSG;  stops[-1].status=A; stops[-1].actual_arrival=extracted_actual_arrival; mov[0].status=P
        * DELIVER    -> status=D; mov[0].brokerage_status=DELIVER;   stops[-1].status=D; stops[-1].actual_departure=extracted_actual_departure; mov[0].status=D
        * BREAKDWN   -> (no changes; placeholder branch)
    - If "message" doesn't exist, will fall back to top-level "status" only where applicable.
    """
    data = deepcopy(payload)

    msg = data.get("message")
    if not isinstance(msg, dict):
        # Still strip fields even if structure isn't what we expect.
        return _remove_fields(data)

    mov0 = _get_first_movement(msg)
    current_brokerage = (mov0.get("brokerage_status") if isinstance(mov0, dict) else None)
    current_brokerage_norm = str(current_brokerage).upper() if current_brokerage is not None else None

    # Add debugging
    print(f"DEBUG: Current brokerage status: {current_brokerage}")
    print(f"DEBUG: Normalized brokerage status: {current_brokerage_norm}")
    print(f"DEBUG: Extracted arrival: {extracted_actual_arrival}")
    print(f"DEBUG: Extracted departure: {extracted_actual_departure}")
    logger.info(f"Current brokerage status: {current_brokerage}")
    logger.info(f"Normalized brokerage status: {current_brokerage_norm}")
    logger.info(f"Extracted arrival: {extracted_actual_arrival}")
    logger.info(f"Extracted departure: {extracted_actual_departure}")

    # ----- Rules -----
    # Only apply transformations for specific statuses
    if current_brokerage_norm in ["ARVDSHPPER", "ARVDSHPR", "ENROUTE", "ARVDCNSG", "DELIVER", "BREAKDWN"]:
        if current_brokerage_norm in ["ARVDSHPPER", "ARVDSHPR"]:
            # status = P
            logger.info("Applying ARVDSHPPER/ARVDSHPR transformation")
            msg["status"] = "P"
            if mov0 is not None:
                mov0["brokerage_status"] = "ARVDSHPR"
                mov0["status"] = "P"
            st0 = _get_stop(msg, 0)
            if st0 is not None:
                st0["status"] = "A"
                if extracted_actual_arrival is not None:
                    st0["actual_arrival"] = extracted_actual_arrival
                    logger.info(f"Set actual_arrival to: {extracted_actual_arrival}")
                else:
                    logger.info("No extracted_actual_arrival provided")

        elif current_brokerage_norm == "ENROUTE":
            # status = P
            msg["status"] = "P"
            if mov0 is not None:
                mov0["brokerage_status"] = "ENROUTE"
                mov0["status"] = "P"
            st0 = _get_stop(msg, 0)
            if st0 is not None:
                st0["status"] = "D"
                if extracted_actual_departure is not None:
                    st0["actual_departure"] = extracted_actual_departure

        elif current_brokerage_norm == "ARVDCNSG":
            # status = P
            msg["status"] = "P"
            if mov0 is not None:
                mov0["brokerage_status"] = "ARVDCNSG"
                mov0["status"] = "P"
            st_last = _get_stop(msg, -1)
            if st_last is not None:
                st_last["status"] = "A"
                if extracted_actual_arrival is not None:
                    st_last["actual_arrival"] = extracted_actual_arrival

        elif current_brokerage_norm == "DELIVER":
            # status = D
            msg["status"] = "D"
            if mov0 is not None:
                mov0["brokerage_status"] = "DELIVER"
                mov0["status"] = "D"
            st_last = _get_stop(msg, -1)
            if st_last is not None:
                st_last["status"] = "D"
                if extracted_actual_departure is not None:
                    st_last["actual_departure"] = extracted_actual_departure

        elif current_brokerage_norm == "BREAKDWN":
            if mov0 is not None:
                mov0["brokerage_status"] = "BREAKDWN"
            pass
    # If status is not one of the above, no changes are made

    # Strip unwanted fields last so we don't accidentally reintroduce them.
    return _remove_fields(data)

FIELDS_TO_REMOVE = {"planning", "order_planning4", "order_planning3", "order_planning2"}


def _remove_fields(obj: Any) -> Any:
    """Recursively remove unwanted keys from any JSON-like Python object."""
    if isinstance(obj, dict):
        return {k: _remove_fields(v) for k, v in obj.items() if k not in FIELDS_TO_REMOVE}
    if isinstance(obj, list):
        return [_remove_fields(v) for v in obj]
    return obj  # primitives


def _get_stop(msg: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    stops = msg.get("stops")
    if isinstance(stops, list) and stops:
        if index == -1:
            return stops[-1] if isinstance(stops[-1], dict) else None
        if 0 <= index < len(stops):
            return stops[index] if isinstance(stops[index], dict) else None
    return None



def _get_first_movement(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    movs = msg.get("movements")
    if isinstance(movs, list) and movs and isinstance(movs[0], dict):
        return movs[0]
    return None


class UpdateLoadDataRequest(BaseModel):
    order_id: str
    extracted_arrival: Optional[str] = None
    extracted_departure: Optional[str] = None


class UpdateBrokerageStatusRequest(BaseModel):
    order_id: str
    brokerage_status: str


@app.post("/update_load_data")
async def update_load_data(body: UpdateLoadDataRequest):
    order_id = body.order_id
    logger.info(f"Updating load data for order {order_id}")
    logger.info(f"Request body: order_id={body.order_id}, arrival={body.extracted_arrival}, departure={body.extracted_departure}")

    # Fetch current order payload and transform with extracted times
    current = _fetch_order_data(order_id)
    logger.info(f"Fetched order data, keys: {list(current.keys()) if isinstance(current, dict) else 'Not a dict'}")
    
    data_cleaned = transform_payload(
        current,
        extracted_actual_arrival=body.extracted_arrival,
        extracted_actual_departure=body.extracted_departure,
    )
    logger.info(f"After transformation, data_cleaned keys: {list(data_cleaned.keys()) if isinstance(data_cleaned, dict) else 'Not a dict'}")

    base_url = os.getenv('GET_URL')
    token = os.getenv('TOKEN')
    company_id = os.getenv('COMPANY_ID')
    missing = [n for n,v in [("GET_URL", base_url), ("TOKEN", token), ("COMPANY_ID", company_id)] if not v]
    if missing:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": missing})

    # Target URL: .../orders/{order_id}
    url_for_connect = base_url + "/orders/update"

    headers = {
        "Authorization": f"Token {token}",
        "X-com.mcleodsoftware.CompanyID": company_id,
        "Accept": "application/json",
    }


    verify_tls = _parse_bool_env("REQUESTS_VERIFY", True)
    if os.getenv("UPSTREAM_CONNECT_IP") and url_for_connect.lower().startswith("https://") and os.getenv("REQUESTS_VERIFY") is None:
        verify_tls = False

    timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS") or 15)
    update_method = (os.getenv("UPDATE_METHOD") or "PUT").strip().upper()

    # Add debugging
    logger.info(f"Attempting {update_method} request to: {url_for_connect}")
    logger.info(f"Headers: {headers}")
    logger.info(f"Payload size: {len(str(data_cleaned))} characters")

    try:
        if update_method == "POST":
            r = requests.post(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        elif update_method == "PATCH":
            r = requests.patch(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        else:
            r = requests.put(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        r.raise_for_status()
        return {"status": "ok", "message": r.json()}
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", 502) if hasattr(exc, "response") else 502
        try:
            detail = exc.response.json() if exc.response is not None else str(exc)
        except Exception:
            detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status, detail={"error": "Upstream HTTP error", "detail": detail})
    except requests.exceptions.SSLError as exc:
        raise HTTPException(status_code=502, detail={"error": "TLS error to upstream", "detail": str(exc)})
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail={"error": "Upstream connection error", "detail": str(exc)})


@app.post("/update_brokerage_status")
async def update_brokerage_status(body: UpdateBrokerageStatusRequest):
    order_id = body.order_id
    new_brokerage_status = body.brokerage_status
    logger.info(f"Updating brokerage status for order {order_id} to {new_brokerage_status}")

    # Fetch current order payload
    current = _fetch_order_data(order_id)
    
    # Add debugging to see the actual structure
    logger.info(f"Order data structure: {list(current.keys()) if isinstance(current, dict) else type(current)}")
    logger.info(f"Order data sample: {str(current)[:500]}...")
    
    # Create a deep copy to avoid modifying the original
    data_cleaned = deepcopy(current)
    
    # Update only the movements[0].brokerage_status field
    # Try to find movements in different possible locations
    movements = None
    if isinstance(data_cleaned, dict):
        # Check if movements is at the top level
        if "movements" in data_cleaned and isinstance(data_cleaned["movements"], list):
            movements = data_cleaned["movements"]
        # Check if movements is inside a "message" object
        elif "message" in data_cleaned and isinstance(data_cleaned["message"], dict):
            msg = data_cleaned["message"]
            if "movements" in msg and isinstance(msg["movements"], list):
                movements = msg["movements"]
    
    if movements and len(movements) > 0 and isinstance(movements[0], dict):
        movements[0]["brokerage_status"] = new_brokerage_status
        logger.info(f"Updated movements[0].brokerage_status to: {new_brokerage_status}")
    else:
        raise HTTPException(status_code=400, detail={
            "error": "No movements found in order data", 
            "available_keys": list(data_cleaned.keys()) if isinstance(data_cleaned, dict) else "Not a dict",
            "data_structure": str(data_cleaned)[:200]
        })

    # Remove unwanted fields
    data_cleaned = _remove_fields(data_cleaned)

    # Send update to upstream API
    base_url = os.getenv('GET_URL')
    token = os.getenv('TOKEN')
    company_id = os.getenv('COMPANY_ID')
    missing = [n for n,v in [("GET_URL", base_url), ("TOKEN", token), ("COMPANY_ID", company_id)] if not v]
    if missing:
        raise HTTPException(status_code=500, detail={"error": "Missing required environment variables", "missing": missing})

    # Target URL: .../orders/update
    url_for_connect = base_url + "/orders/update"

    headers = {
        "Authorization": f"Token {token}",
        "X-com.mcleodsoftware.CompanyID": company_id,
        "Accept": "application/json",
    }

    verify_tls = _parse_bool_env("REQUESTS_VERIFY", True)
    if os.getenv("UPSTREAM_CONNECT_IP") and url_for_connect.lower().startswith("https://") and os.getenv("REQUESTS_VERIFY") is None:
        verify_tls = False

    timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS") or 15)
    update_method = (os.getenv("UPDATE_METHOD") or "PUT").strip().upper()

    # Add debugging
    logger.info(f"Attempting {update_method} request to: {url_for_connect}")
    logger.info(f"Headers: {headers}")
    logger.info(f"Payload size: {len(str(data_cleaned))} characters")

    try:
        if update_method == "POST":
            r = requests.post(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        elif update_method == "PATCH":
            r = requests.patch(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        else:
            r = requests.put(url_for_connect, headers=headers, json=data_cleaned, timeout=timeout_seconds, verify=verify_tls)
        r.raise_for_status()
        return {"status": "ok", "message": r.json()}
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", 502) if hasattr(exc, "response") else 502
        try:
            detail = exc.response.json() if exc.response is not None else str(exc)
        except Exception:
            detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status, detail={"error": "Upstream HTTP error", "detail": detail})
    except requests.exceptions.SSLError as exc:
        raise HTTPException(status_code=502, detail={"error": "TLS error to upstream", "detail": str(exc)})
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail={"error": "Upstream connection error", "detail": str(exc)})
