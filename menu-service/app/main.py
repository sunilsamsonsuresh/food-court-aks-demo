"""
menu-service: a tiny FastAPI app that returns the food court's menu.

Endpoints:
  GET /health          -> simple "I'm alive" check (Kubernetes will use this later)
  GET /menu            -> all dishes
  GET /menu/{dish_id}  -> one dish, or 404 if it doesn't exist
"""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Menu Service")

# The menu lives in memory as a plain Python list. No database needed for this
# service, because the menu never changes while the app is running.
DISHES = [
    {"id": 1, "name": "Masala Dosa", "price": 120, "category": "South Indian"},
    {"id": 2, "name": "Paneer Butter Masala", "price": 220, "category": "North Indian"},
    {"id": 3, "name": "Chicken Biryani", "price": 280, "category": "Rice"},
    {"id": 4, "name": "Veg Hakka Noodles", "price": 180, "category": "Chinese"},
    {"id": 5, "name": "Pav Bhaji", "price": 140, "category": "Street Food"},
    {"id": 6, "name": "Gulab Jamun", "price": 80, "category": "Dessert"},
]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/menu")
def get_menu():
    return DISHES


# FastAPI reads the {dish_id} part of the URL and turns it into an int for us.
# If someone sends text such as /menu/abc, FastAPI rejects it with a 422 error
# before this function runs.
@app.get("/menu/{dish_id}")
def get_dish(dish_id: int):
    for dish in DISHES:
        if dish["id"] == dish_id:
            return dish
    raise HTTPException(status_code=404, detail=f"Dish with id {dish_id} not found")
