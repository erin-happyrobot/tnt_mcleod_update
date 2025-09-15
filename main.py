from fastapi import FastAPI
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

@app.get("/get_load_data")
async def get_load_data(order_id: str):
    logger.info(f"Getting load data for order {order_id}")
    logger.info(f"GET_URL: {os.getenv('GET_URL')}")
    logger.info(f"TOKEN: {os.getenv('TOKEN')}")
    logger.info(f"COMPANY_ID: {os.getenv('COMPANY_ID')}")
    url = f"{os.getenv('GET_URL')}/orders/{order_id}" 
    headers = {
        "Authorization": f"Token {os.getenv('TOKEN')}",
        "X-com.mcleodsoftware.CompanyID": os.getenv('COMPANY_ID'),
        "Accept": "application/json"
    }
    body = {
        "mode": "raw",
        "raw": "",
        "options": {
            "raw": {
                "language": "json"
            }
        }
    }
    response = requests.post(url, headers=headers, json=body)

    return {"status": "ok", "message": response.json()}


