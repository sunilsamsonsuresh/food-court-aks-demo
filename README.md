# Mini Food Court

A small learning project for Docker and Kubernetes (AKS). The app is split into
small services, and each one runs in its own container.

📘 **Full step-by-step record of the Azure / AKS deployment:** [DEPLOYMENT.md](DEPLOYMENT.md)

| Service         | What it does                          | Status      |
|-----------------|---------------------------------------|-------------|
| `menu-service`  | FastAPI app that returns the dishes   | ✅ Done     |
| `order-service` | FastAPI app that stores orders in Redis | ✅ Done   |
| `frontend`      | Simple HTML page served by nginx      | ✅ Done     |
| `redis`         | Stores the orders                     | ✅ Done     |

## Run locally

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/), installed and running.

Start everything (builds the images the first time):

```bash
docker compose up --build -d
```

Then open **http://localhost:8080**.

Stop it:

```bash
docker compose down      # stops everything, keeps your orders
docker compose down -v   # stops everything AND deletes the orders (removes the Redis volume)
```

Useful while it's running:

```bash
docker compose ps                     # status and health of each service
docker compose logs -f order-service  # follow one service's logs
```

Only the frontend is published to your machine (port 8080). The other services
are reachable only from inside the compose network.

The sections below show how to run each service by hand with `docker run`,
which is useful for learning what Compose does for you.

## menu-service

Endpoints (port 8000):

- `GET /health`: returns `{"status": "ok"}`
- `GET /menu`: returns all dishes
- `GET /menu/{dish_id}`: returns one dish, or a 404 if it doesn't exist

### Run it with Docker

```bash
cd menu-service
docker build -t menu-service:v1 .
docker run -d --name menu -p 8000:8000 menu-service:v1

curl http://localhost:8000/health
curl http://localhost:8000/menu
curl http://localhost:8000/menu/1

docker stop menu && docker rm menu
```

## order-service

Endpoints (port 8000 inside the container):

- `GET /health`: returns `{"status": "ok"}`
- `POST /orders`: body `{"dish_id": 1, "quantity": 2}`. Checks the dish with menu-service, then saves the order in Redis
- `GET /orders`: returns all orders
- `GET /orders/{order_id}`: returns one order, or a 404 if it doesn't exist

Configuration (environment variables):

| Variable           | Default                     |
|--------------------|-----------------------------|
| `MENU_SERVICE_URL` | `http://menu-service:8000`  |
| `REDIS_HOST`       | `redis`                     |
| `REDIS_PORT`       | `6379`                      |

### Run it with Docker (needs redis + menu-service on a shared network)

```bash
cd order-service
docker build -t order-service:v1 .

docker network create food-court-net
docker run -d --name redis         --network food-court-net redis:7-alpine
docker run -d --name menu-service  --network food-court-net menu-service:v1
docker run -d --name order-service --network food-court-net -p 8001:8000 order-service:v1

curl -X POST localhost:8001/orders -H 'Content-Type: application/json' -d '{"dish_id": 3, "quantity": 2}'
curl localhost:8001/orders

# clean up
docker rm -f order-service menu-service redis
docker network rm food-court-net
```

## frontend

A single HTML page served by nginx. nginx also works as a **reverse proxy**: the
browser only talks to nginx, and nginx forwards API calls to the backends.

| Browser calls     | nginx forwards to         |
|-------------------|---------------------------|
| `/`               | `index.html`              |
| `/healthz`        | answers `ok` itself       |
| `/api/menu/...`   | menu-service `/menu/...`  |
| `/api/orders/...` | order-service `/orders/...` |

Configuration (environment variables):

| Variable            | Default                     |
|---------------------|-----------------------------|
| `MENU_SERVICE_URL`  | `http://menu-service:8000`  |
| `ORDER_SERVICE_URL` | `http://order-service:8000` |

### Run the whole app with Docker

Start the backends first, because nginx looks up their names when it starts.

```bash
docker build -t frontend:v1 ./frontend

docker network create food-court-net
docker run -d --name redis         --network food-court-net redis:7-alpine
docker run -d --name menu-service  --network food-court-net menu-service:v1
docker run -d --name order-service --network food-court-net order-service:v1
docker run -d --name frontend      --network food-court-net -p 8080:80 frontend:v1

# open http://localhost:8080

# clean up
docker rm -f frontend order-service menu-service redis
docker network rm food-court-net
```

## Azure setup

Everything lives in **one resource group**, so a single command deletes it all.

| What                       | Name                          | Notes                              |
|----------------------------|-------------------------------|------------------------------------|
| Resource group             | `rg-foodcourt`                | Location `centralindia`            |
| Azure Container Registry   | `foodcourt48516`              | Basic SKU, admin user **disabled** |

> The registry name must be globally unique, so yours will be different.
> Your own names are saved in `azure-notes.md`, which git ignores.

### Commands used

```bash
# One-time: switch on the Container Registry service for the subscription (free)
az provider register --namespace Microsoft.ContainerRegistry --wait

# Create the resource group and the registry
az group create --name rg-foodcourt --location centralindia
az acr check-name --name foodcourt48516
az acr create --resource-group rg-foodcourt --name foodcourt48516 \
  --sku Basic --location centralindia --admin-enabled false

# Log Docker in to the registry (uses your az login, no passwords)
az acr login --name foodcourt48516

# Build for linux/amd64 (what AKS nodes run) and push straight to the registry
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/menu-service:v1  --push ./menu-service
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/order-service:v1 --push ./order-service
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/frontend:v1      --push ./frontend

# Check what's in the registry
az acr repository list --name foodcourt48516 -o table
az acr repository show-tags --name foodcourt48516 --repository menu-service -o table
```

### Image naming format

```
<registry>.azurecr.io/<name>:<tag>
foodcourt48516.azurecr.io/menu-service:v1
```

- `<registry>.azurecr.io`: the registry's address. Docker uses it to decide **where** to push or pull.
  Without an address, Docker assumes Docker Hub.
- `<name>`: the repository, one per service (`menu-service`, `order-service`, `frontend`).
- `<tag>`: the version (`v1`). Push a new tag (`v2`, ...) for each new version, instead of overwriting.

> **Apple Silicon (ARM) Macs:** always pass `--platform linux/amd64` when building for AKS.
> Otherwise the image is ARM-only, and the pods crash on AKS with `exec format error`.

### Delete everything

```bash
az group delete --name rg-foodcourt --yes --no-wait
```

## Deploy to AKS

The Kubernetes files are in `k8s/`, numbered in the order they should be applied:

| File | Creates |
|------|---------|
| `00-namespace.yaml`     | Namespace `food-court` (a "folder" for the app) |
| `10-redis.yaml`         | Azure disk (PVC) + Redis Deployment + internal Service `redis` |
| `20-menu-service.yaml`  | Deployment + internal Service `menu-service` |
| `30-order-service.yaml` | Deployment + internal Service `order-service` |
| `40-frontend.yaml`      | Deployment + **LoadBalancer** Service (public IP) |

```bash
az aks get-credentials --resource-group rg-foodcourt --name aks-foodcourt
kubectl apply -f k8s/00-namespace.yaml
kubectl config set-context --current --namespace=food-court

kubectl apply -f k8s/10-redis.yaml
kubectl apply -f k8s/20-menu-service.yaml
kubectl apply -f k8s/30-order-service.yaml
kubectl apply -f k8s/40-frontend.yaml

kubectl get service frontend --watch    # wait for EXTERNAL-IP, then open http://<EXTERNAL-IP>
```

Remove the app (including its public IP and disk): `kubectl delete namespace food-court`
