"""
order-service: accepts orders, checks them against menu-service, stores them in Redis.

Endpoints:
  GET  /health             -> simple "I'm alive" check
  POST /orders             -> place an order: {"dish_id": 1, "quantity": 2}
  GET  /orders             -> all orders
  GET  /orders/{order_id}  -> one order, or 404
"""

import json
import os
from datetime import datetime, timezone

import httpx
import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# --- Configuration -----------------------------------------------------------
# All settings come from environment variables instead of being written into
# the code. The same image can then run anywhere (your laptop, Docker,
# Kubernetes) and only the settings change, not the code. In Kubernetes we'll
# set these values in the deployment file, so there's no need to rebuild the
# image for each environment. The defaults match the container names we use
# on the Docker network.
MENU_SERVICE_URL = os.getenv("MENU_SERVICE_URL", "http://menu-service:8000")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# decode_responses=True makes Redis return normal Python strings instead of bytes.
# Creating the client doesn't connect yet. It connects on the first command.
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Redis key that holds the last order id we handed out.
ORDER_ID_COUNTER = "order_id_counter"

app = FastAPI(title="Order Service")


class OrderRequest(BaseModel):
    dish_id: int
    # Field(ge=1) means "greater than or equal to 1". FastAPI automatically
    # rejects quantity 0 or negative numbers with a 422 error.
    quantity: int = Field(ge=1)


# If Redis is down, any Redis command raises ConnectionError. Instead of a
# confusing "500 Internal Server Error", reply with a clear 503 message.
@app.exception_handler(redis.exceptions.ConnectionError)
def redis_unavailable(request: Request, exc: redis.exceptions.ConnectionError):
    return JSONResponse(status_code=503, content={"detail": "Order storage (Redis) is unavailable"})


@app.get("/health")
def health():
    return {"status": "ok"}


def fetch_dish(dish_id: int) -> dict:
    """Ask menu-service for a dish. Raises the right HTTP error if something goes wrong."""
    try:
        # Always set a timeout so a slow menu-service can't make us hang forever.
        resp = httpx.get(f"{MENU_SERVICE_URL}/menu/{dish_id}", timeout=3.0)
    except httpx.RequestError:
        # Covers "can't connect", "name not found" and "timed out".
        raise HTTPException(status_code=503, detail="Menu service is unavailable, please try again later")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Dish with id {dish_id} not found on the menu")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Menu service returned an unexpected error")
    return resp.json()


@app.post("/orders", status_code=201)
def create_order(order: OrderRequest):
    dish = fetch_dish(order.dish_id)

    # INCR adds 1 to the counter and returns the new value, all in one step.
    # Even if two orders arrive at the same moment, each one gets a unique id.
    order_id = r.incr(ORDER_ID_COUNTER)

    new_order = {
        "id": order_id,
        "dish_id": order.dish_id,
        "dish_name": dish["name"],
        "quantity": order.quantity,
        "total_price": dish["price"] * order.quantity,
        "status": "placed",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Redis stores plain strings, so we turn the order into JSON text.
    r.set(f"order:{order_id}", json.dumps(new_order))
    return new_order


@app.get("/orders")
def list_orders():
    # Ids run from 1 up to the counter's value, so we can build every key and
    # fetch them all in one round trip with MGET.
    last_id = int(r.get(ORDER_ID_COUNTER) or 0)
    if last_id == 0:
        return []
    keys = [f"order:{i}" for i in range(1, last_id + 1)]
    return [json.loads(value) for value in r.mget(keys) if value is not None]


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    value = r.get(f"order:{order_id}")
    if value is None:
        raise HTTPException(status_code=404, detail=f"Order with id {order_id} not found")
    return json.loads(value)
