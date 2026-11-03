# Mini Food Court: deploying to Azure Kubernetes Service (AKS)

This document records how the Mini Food Court app went from "working on my Mac
in Docker" to "running on the internet on Azure Kubernetes Service".
It lists every command we ran and every click in the Azure Portal, in the order
we did them, with a short plain-English explanation of each step.

> **Starting point:** the four services (`menu-service`, `order-service`,
> `frontend`, `redis`) were already built as Docker images and tested locally
> with `docker compose`. Everything below happens **after** that.

---

## Contents

1. [The big picture](#1-the-big-picture)
2. [Step 1: Pre-checks on the Mac](#step-1-pre-checks-on-the-mac)
3. [Step 2: Install the Azure CLI and log in](#step-2-install-the-azure-cli-and-log-in)
4. [Step 3: Create the resource group and container registry](#step-3-create-the-resource-group-and-container-registry)
5. [Step 4: Build the images for amd64 and push them to the registry](#step-4-build-the-images-for-amd64-and-push-them-to-the-registry)
6. [Step 5: Check the images in the registry](#step-5-check-the-images-in-the-registry)
7. [Step 6: Prepare the subscription for AKS (Portal)](#step-6-prepare-the-subscription-for-aks-portal)
8. [Step 7: Create the AKS cluster (Portal)](#step-7-create-the-aks-cluster-portal)
9. [Step 8: Connect the Mac to the cluster and test it](#step-8-connect-the-mac-to-the-cluster-and-test-it)
10. [Step 9: Write the Kubernetes files](#step-9-write-the-kubernetes-files)
11. [Step 10: Deploy the app](#step-10-deploy-the-app)
12. [Step 11: Open the app and fix the browser timeout](#step-11-open-the-app-and-fix-the-browser-timeout)
13. [Step 12: Find everything in the Azure Portal](#step-12-find-everything-in-the-azure-portal)
14. [Step 13: Costs and cleanup](#step-13-costs-and-cleanup)
15. [Step 14: CI/CD with GitHub Actions](#step-14-cicd-with-github-actions)
16. [Problems we hit and how we fixed them](#problems-we-hit-and-how-we-fixed-them)
17. [Command cheat sheet](#command-cheat-sheet)

---

## 1. The big picture

### The food court analogy

| Real thing | Food court analogy |
|---|---|
| **Docker image** | A sealed meal kit: recipe and ingredients packed together |
| **Container registry (ACR)** | A warehouse in the cloud where the meal kits are stored |
| **AKS cluster** | The food court building in the cloud |
| **Node** (a virtual machine) | A kitchen in the building |
| **Pod** | A cook working in a kitchen, making one dish from one meal kit |
| **Deployment** | The manager who makes sure the right number of cooks is always working, and replaces any who leave |
| **Service** | A fixed phone extension: "call `menu-service`" always reaches a working cook, even when cooks change |
| **LoadBalancer Service** | The front door with a street address (public IP) that customers use |
| **Namespace** | A labelled section of the building that holds everything belonging to the food court |
| **PersistentVolumeClaim** | A rented storage locker (Azure disk) that keeps the order book safe when a cook is replaced |

### What we built

```
                     Internet (your browser)
                              │
                     http://<PUBLIC-IP>
                              │
┌──────────────── Azure: resource group rg-foodcourt ───────────────────┐
│                                                                        │
│  ┌──────────────── AKS cluster: aks-foodcourt ──────────────────────┐  │
│  │  namespace: food-court                                           │  │
│  │                                                                  │  │
│  │   frontend (nginx) ── Service type LoadBalancer (public IP)      │  │
│  │      │  /api/menu  ─────►  menu-service  (internal Service)      │  │
│  │      │  /api/orders ────►  order-service (internal Service)      │  │
│  │                                │                                 │  │
│  │                                ├──► menu-service                 │  │
│  │                                └──► redis ── Azure disk (PVC)    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                         ▲ pulls images                                 │
│  ┌───────────── Azure Container Registry: foodcourt48516 ──────────┐   │
│  │  menu-service:v1   order-service:v1   frontend:v1  (linux/amd64)│   │
│  └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
                          ▲ docker buildx ... --push
                     Your Mac (Apple Silicon)
```

### Names used throughout

| Item | Value |
|---|---|
| Subscription | Azure subscription 1 (free trial) |
| Resource group | `rg-foodcourt` |
| Region | `centralindia` (Central India) |
| Container registry | `foodcourt48516` → `foodcourt48516.azurecr.io` |
| AKS cluster | `aks-foodcourt` |
| Kubernetes namespace | `food-court` |
| Infrastructure resource group (created by AKS) | `MC_rg-foodcourt_aks-foodcourt_centralindia` |

> Registry names must be unique across all of Azure, so if you repeat this,
> yours will be different. Ours is also saved in `azure-notes.md`, which git ignores.

---

## Step 1: Pre-checks on the Mac

**Where:** Terminal on the Mac · **Cost:** free

Before touching Azure, we checked which tools were installed and what kind of
CPU the Mac has.

```bash
docker version            # Docker 29.9.2 (client and engine)
docker buildx version     # buildx 0.38.1, needed to build for a different CPU type
uname -m                  # arm64  → Apple Silicon (M5)

# Which CPU type were our local images built for?
docker image inspect -f '{{.Os}}/{{.Architecture}}' menu-service:v1   # linux/arm64
docker image ls           # menu-service:v1, order-service:v1, frontend:v1
```

**What we learned:** the Mac is **ARM**, so every image built on it is ARM by
default. AKS machines are **Intel/AMD (amd64)**. An ARM image on an amd64
machine is like a DVD in a Blu-ray-only player: it won't play, and Kubernetes
shows `exec format error`. So the images had to be **rebuilt for amd64** before
being uploaded (Step 4).

---

## Step 2: Install the Azure CLI and log in

**Where:** Terminal · **Cost:** free

The Azure CLI (`az`) wasn't installed, so we installed it with Homebrew:

```bash
brew install azure-cli
az --version              # azure-cli 2.91.0
```

Logging in opens a browser window, so **you** ran this one yourself. In Claude Code
the `!` prefix runs a command in the session:

```bash
az login
```

Then we checked which subscription was active:

```bash
az account show -o table      # Azure subscription 1, Enabled, Default Directory
az account list -o table      # it's the only subscription on the account
```

✅ You confirmed **Azure subscription 1** was the right one.

---

## Step 3: Create the resource group and container registry

**Where:** Terminal · **Rule we followed:** show every billable command first and wait for a "yes"

### 3a. Read-only checks first

```bash
# Is the Container Registry service switched on for this subscription?
az provider show -n Microsoft.ContainerRegistry --query registrationState -o tsv   # NotRegistered

# Does the resource group already exist?
az group exists -n rg-foodcourt                                                     # false

# Is our chosen registry name free? (names: 5–50 chars, lowercase letters + digits, globally unique)
az acr check-name -n foodcourt48516 --query nameAvailable -o tsv                    # true
```

### 3b. Switch on the Container Registry service (free, one time)

A new subscription has to switch on each type of Azure service before it can
use it. Azure calls this **registering a resource provider**. Think of it as
ticking "yes, I want to use this service" once per subscription.

```bash
az provider register --namespace Microsoft.ContainerRegistry --wait
```

### 3c. Create the resource group (free)

A **resource group** is a folder for Azure resources. Putting everything for
this project in one group means **one command deletes it all** at the end.

```bash
az group create --name rg-foodcourt --location centralindia
```

### 3d. Create the container registry (billable: about US$0.17/day)

```bash
az acr create \
  --resource-group rg-foodcourt \
  --name foodcourt48516 \
  --sku Basic \
  --location centralindia \
  --admin-enabled false
```

| Option | Meaning |
|---|---|
| `--sku Basic` | The cheapest tier (10 GB storage included) |
| `--admin-enabled false` | No shared admin username/password. We use our Azure login instead, which is safer |

Result: registry **`foodcourt48516`**, address **`foodcourt48516.azurecr.io`**.

---

## Step 4: Build the images for amd64 and push them to the registry

**Where:** Terminal · **Cost:** included in the registry price

### 4a. Let Docker into the registry

```bash
az acr login --name foodcourt48516      # → Login Succeeded
```

Docker doesn't know about your Azure login. This command swaps your Azure login
for a **temporary visitor pass** (valid about 3 hours) to the registry and hands
it to Docker. No password is created or shown. If a push later says
`unauthorized`, run it again.

### 4b. Rebuild for amd64 and upload in one go

Run from the project folder:

```bash
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/menu-service:v1  --push ./menu-service
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/order-service:v1 --push ./order-service
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/frontend:v1      --push ./frontend
```

| Part | What it does |
|---|---|
| `docker buildx build` | Builds the image, and can build for a different CPU than the Mac's |
| `--platform linux/amd64` | Builds for Intel/AMD machines (what AKS runs) |
| `-t foodcourt48516.azurecr.io/menu-service:v1` | The image name. The first part is the registry's address, so Docker knows **where** to send it |
| `--push` | Uploads the image as soon as it's built |
| `./menu-service` | The folder with the Dockerfile and code |

### Image naming format

```
<registry>.azurecr.io/<name>:<tag>
foodcourt48516.azurecr.io/menu-service:v1
```

The image name works like a **shipping label**. `menu-service:v1` with no address
means "Docker Hub". With the registry address in front, Docker delivers it to
**our** warehouse.

> **Why not `docker tag` + `docker push`?** Many tutorials do this, but it would
> have uploaded the existing **ARM** images from the Mac. Rebuilding with
> `--platform linux/amd64` was required.

---

## Step 5: Check the images in the registry

**Where:** Terminal and Portal · **Cost:** free

```bash
az acr repository list --name foodcourt48516 -o table
# frontend, menu-service, order-service

az acr repository show-tags --name foodcourt48516 --repository menu-service -o table    # v1
az acr repository show-tags --name foodcourt48516 --repository order-service -o table   # v1
az acr repository show-tags --name foodcourt48516 --repository frontend -o table        # v1

az acr repository show --name foodcourt48516 --image menu-service:v1     # digest, dates

# Image size (preview command; it prints a warning you can ignore)
az acr manifest list-metadata --registry foodcourt48516 --name menu-service -o table

# Confirm the CPU type
docker buildx imagetools inspect foodcourt48516.azurecr.io/menu-service:v1
# linux/amd64  (plus "unknown/unknown", a small build record, not a second image)
```

| Image | Size in registry (compressed) | CPU type |
|---|---|---|
| menu-service:v1 | 49.5 MB | linux/amd64 ✅ |
| order-service:v1 | 50.6 MB | linux/amd64 ✅ |
| frontend:v1 | 20.0 MB | linux/amd64 ✅ |

**In the Portal:** **foodcourt48516 → Services → Repositories → menu-service → v1**
shows the tag, its platform (`linux/amd64`) and its details.

> **Can the Portal upload images?** No. There's no "upload image" button. Images
> are always pushed from a machine with Docker (what we did), or built in Azure
> with `az acr build` from Cloud Shell. Free-trial subscriptions usually can't use
> `az acr build`, so we pushed from the Mac.

---

## Step 6: Prepare the subscription for AKS (Portal)

**Where:** Azure Portal · **Cost:** free

### 6a. Register the resource providers AKS needs

1. In the Portal search bar, type **Subscriptions** and open **Azure subscription 1**.
2. In the left menu, open **Settings → Resource providers**.
3. Search for each of these, select it, and click **Register**:
   - `Microsoft.ContainerService` (AKS itself)
   - `Microsoft.Compute` (virtual machines)
   - `Microsoft.Network` (networking, load balancer, public IPs)
   - `Microsoft.Storage` (disks)
4. Wait until each shows **Registered**. Click **Refresh** to update.

### 6b. Check your CPU quota

1. Same subscription → **Settings → Usage + quotas**.
2. Filter **Region = Central India**.
3. Look at **Total Regional vCPUs**.

Our free-trial subscription had a limit of **4 vCPUs in total**. That matters
when choosing the node size and node count (Step 7).

Terminal equivalent (read-only):

```bash
az vm list-usage -l centralindia -o table
```

---

## Step 7: Create the AKS cluster (Portal)

**Where:** Azure Portal · **Cost:** billable (the VMs, disks, load balancer and IPs; see Step 13)

**Kubernetes services → + Create → Kubernetes cluster**

Choose **Kubernetes cluster**, not "Automatic Kubernetes cluster", which makes
the choices for you and costs more.

### Basics tab

| Field | Value | Why |
|---|---|---|
| Subscription | Azure subscription 1 | |
| Resource group | **rg-foodcourt** | Same group as the registry, so one delete removes everything |
| Cluster preset configuration | **Dev/Test** | Cheapest sensible defaults |
| Kubernetes cluster name | **aks-foodcourt** | |
| Region | **(Asia Pacific) Central India** | Same region as the registry: faster, and no download charges |
| Availability zones | **None** | Spreading across data centres is for production |
| AKS pricing tier | **Free** | The cluster's control plane (its "brain") costs nothing |
| Kubernetes version | Default | Ours became **1.36.5** |
| Authentication and Authorization | **Local accounts with Kubernetes RBAC** | The Entra/Azure RBAC options need extra roles and an extra login tool before `kubectl` works |

### Node pools tab

Click the **agentpool** row and set:

| Field | Value |
|---|---|
| Node size | **Standard_D2ls_v5** (2 vCPUs, 4 GB RAM, Intel/AMD) |
| Scale method | **Manual** |
| Node count | **1** |
| Availability zones | **Zone 3** only, or **None** |

Click **Update**.

**What went wrong here, and why:**

| Problem we saw | Cause | Fix |
|---|---|---|
| Default size `Standard_B2pls_v2` + error *"B-series node sizes cannot be scheduled"* | AKS no longer allows B-series. Also, the **"p"** in the name means an **ARM** machine, which would crash our amd64 images | Picked **Standard_D2ls_v5** (no "p", so Intel/AMD) |
| Many D-series sizes unavailable | Free-trial subscriptions block many sizes (`NotAvailableForSubscription`) | `D2ls_v5` was allowed |
| Node count showed **2 – 5** (autoscaling) | The default from the Dev/Test preset | Should be **Manual, 1**. With a 4-vCPU quota, 2 nodes use everything and scale-ups and upgrades fail |

**Rule of thumb:** a VM size with a **"p"** in its name (e.g. `D2ps_v5`, `B2pls_v2`) is ARM. Avoid these for amd64 images.

### Networking tab

Leave everything as default. That gives a public cluster with the default Azure networking.

### Integrations tab ⭐

| Field | Value |
|---|---|
| Container registry | **foodcourt48516** |

This gives the cluster permission to **pull** images from the registry (Azure
calls it the *AcrPull* role). There are no passwords and no secrets in the
Kubernetes files. Without it, pods fail with `ImagePullBackOff` / `401 Unauthorized`.

### Monitoring tab: turn things off to save credit

- ☐ Container logs / Container insights
- ☐ Managed Prometheus
- ☐ Managed Grafana
- ☐ Enable recommended alert rules

### Security and Advanced tabs

- Leave the defaults. Don't enable Microsoft Defender (paid).
- On **Advanced**, note the **Infrastructure resource group**:
  `MC_rg-foodcourt_aks-foodcourt_centralindia`. AKS keeps the VMs, disks, load
  balancer and public IPs there. You never edit it by hand, and it's deleted with the cluster.

### Review + create

Click **Review + create**, then **Create**. It takes about 5–10 minutes.

### When validation failed: how we found out why

The Portal said **"Validation failed"**. Azure records failed requests in the
**activity log**, so we read it from the terminal:

```bash
az monitor activity-log list --offset 2h --status Failed -o json
```

It said:

```
AvailabilityZoneNotSupported:
The zone(s) '1' for resource 'agentpool' is not supported.
The supported zones for location 'centralindia' are '3'.
```

We confirmed which zones the size is allowed in:

```bash
az vm list-skus -l centralindia --size Standard_D2ls_v5 -o json
# restrictions: NotAvailableForSubscription in zones 1,2  → only zone 3 allowed
```

**Fix:** Node pools → agentpool → **Availability zones: Zone 3 only** (or **None**) → Update → Review + create.

Central India has three separate data centres, called zones 1, 2 and 3. Our
subscription may only use this VM size in zone 3, but the Portal had picked zone 1.

---

## Step 8: Connect the Mac to the cluster and test it

**Where:** Terminal · **Cost:** free

### 8a. Look at the cluster

```bash
az aks show -g rg-foodcourt -n aks-foodcourt -o table
# aks-foodcourt  Succeeded  Running  1.36.5  Free  centralindia

az aks nodepool list -g rg-foodcourt --cluster-name aks-foodcourt -o table
# agentpool  Standard_D2ls_v5  count 2  autoscale True (min 2, max 5)
```

### 8b. Connect kubectl

`kubectl` is the remote control for Kubernetes. This command downloads the
cluster's address and login into `~/.kube/config`, so `kubectl` knows which
cluster to talk to:

```bash
az aks get-credentials --resource-group rg-foodcourt --name aks-foodcourt
kubectl config current-context        # aks-foodcourt
```

### 8c. Check the nodes and the registry connection

```bash
kubectl get nodes -L kubernetes.io/arch
# 2 nodes, Ready, ARCH = amd64 ✅

az aks check-acr --resource-group rg-foodcourt --name aks-foodcourt --acr foodcourt48516.azurecr.io
# Validating image pull permission: SUCCEEDED
# Your cluster can pull images from foodcourt48516.azurecr.io!
```

### 8d. Smoke test: run one container from the registry, then delete it

```bash
kubectl run menu-test --image=foodcourt48516.azurecr.io/menu-service:v1 --restart=Never
kubectl wait --for=condition=Ready pod/menu-test --timeout=120s
kubectl get events --field-selector involvedObject.name=menu-test     # Pulled ... in 2.8s, Started
kubectl exec menu-test -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/menu/1').read().decode())"
# {"id":1,"name":"Masala Dosa","price":120,"category":"South Indian"}
kubectl delete pod menu-test
```

This one test proves three things together: the registry connection works, the
image's CPU type is right, and the app runs on the cluster.

---

## Step 9: Write the Kubernetes files

**Where:** project folder `k8s/` · **Cost:** free (they're just text files)

The Kubernetes files describe the same app as `docker-compose.yml`, in
Kubernetes language:

| docker compose | Kubernetes |
|---|---|
| A service (`docker run`) | **Deployment**: runs the container and replaces it if it dies |
| Containers find each other by service name | **Service**: a stable internal name and address (`menu-service`, `redis`, ...) |
| `environment:` | `env:` on the Deployment |
| `healthcheck:` | **readinessProbe** ("ready for traffic?") and **livenessProbe** ("still alive? if not, restart") |
| `ports: "8080:80"` on the frontend only | frontend Service with **`type: LoadBalancer`**, which gets a public IP |
| `redis-data` volume | **PersistentVolumeClaim**, which becomes an Azure disk |

### The files

| File | Creates |
|---|---|
| `k8s/00-namespace.yaml` | Namespace `food-court`, the app's own section of the cluster |
| `k8s/10-redis.yaml` | PVC `redis-data` (1 GB Azure disk) + Redis Deployment + internal Service `redis` |
| `k8s/20-menu-service.yaml` | Deployment (image from ACR) + internal Service `menu-service` |
| `k8s/30-order-service.yaml` | Deployment with env vars + internal Service `order-service` |
| `k8s/40-frontend.yaml` | Deployment with env vars + **LoadBalancer** Service (public IP) |

The number prefixes give the start-up order: storage first, then the services
that depend on it, and the frontend last.

### Example: `k8s/20-menu-service.yaml`

This is a shortened copy: some nested settings are written on one line
(`{ ... }`). That means exactly the same thing as the multi-line form in the real file.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: menu-service
  namespace: food-court
spec:
  replicas: 1                     # how many copies (pods) to keep running
  selector:
    matchLabels:
      app: menu-service           # "the pods I manage are the ones labelled app=menu-service"
  template:                       # the blueprint for each pod
    metadata:
      labels:
        app: menu-service
    spec:
      containers:
        - name: menu-service
          image: foodcourt48516.azurecr.io/menu-service:v1   # from our registry
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /health, port: 8000 }
            periodSeconds: 10
          resources:
            requests: { cpu: 50m, memory: 64Mi }   # 50m = 5% of one CPU, reserved
            limits: { memory: 256Mi }              # restarted if it uses more
---
apiVersion: v1
kind: Service                     # no "type:" = ClusterIP = reachable only inside the cluster
metadata:
  name: menu-service
  namespace: food-court
spec:
  selector:
    app: menu-service             # send traffic to pods with this label
  ports:
    - port: 8000
      targetPort: 8000
```

### Points in the other files that aren't obvious

| File | Line | Why |
|---|---|---|
| `10-redis.yaml` | `storageClassName: managed-csi` | Built into AKS: creates an Azure Standard SSD disk |
| `10-redis.yaml` | `strategy: type: Recreate` | Stop the old Redis pod **before** starting a new one, so two pods never fight over the same disk |
| `10-redis.yaml` | `args:` instead of `command:` | In Kubernetes, `command` replaces the image's own start-up script (which fixes folder permissions). `args` only passes arguments to it |
| `30-order-service.yaml` | `enableServiceLinks: false` | Kubernetes normally adds env vars for every Service, e.g. `REDIS_PORT=tcp://10.0.x.x:6379`. That clashes with our `REDIS_PORT=6379` |
| `30-order-service.yaml` / `40-frontend.yaml` | `MENU_SERVICE_URL: http://menu-service:8000` etc. | The same env vars as in docker compose. The names point at the Kubernetes Services |
| `40-frontend.yaml` | `type: LoadBalancer` | Azure creates a public IP for this Service. Only the frontend gets one |

### Checking the files without touching the cluster

`kubectl apply --dry-run=client` still needs a cluster connection, so we
checked the files offline with a small Python + PyYAML script. All objects
parsed, every one was in the `food-court` namespace, and every Service selector
matched a Deployment.

---

## Step 10: Deploy the app

**Where:** Terminal, run by **you**, one file at a time, checking each before the next · **Cost:** the public IP and the Redis disk add a little

### 10a. Before you start

```bash
kubectl config current-context        # must say: aks-foodcourt
# if not:
az aks get-credentials --resource-group rg-foodcourt --name aks-foodcourt
```

### 10b. Namespace

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl config set-context --current --namespace=food-court   # so you don't need "-n food-court" every time
kubectl get namespaces
```

### 10c. Redis

```bash
kubectl apply -f k8s/10-redis.yaml
kubectl get pvc,pods,services                 # wait for: PVC Bound, pod 1/1 Running
kubectl exec deploy/redis -- redis-cli ping   # PONG
```

The PVC shows **Pending** until the Redis pod starts. That's normal: Azure waits
until it knows which node needs the disk.

### 10d. menu-service

```bash
kubectl apply -f k8s/20-menu-service.yaml
kubectl rollout status deployment/menu-service

# Optional test through a temporary tunnel from the Mac into the cluster:
kubectl port-forward service/menu-service 8000:8000
# in a second terminal:  curl localhost:8000/menu/1      (Ctrl+C the tunnel afterwards)
```

### 10e. order-service

```bash
kubectl apply -f k8s/30-order-service.yaml
kubectl rollout status deployment/order-service

# Optional test:
kubectl port-forward service/order-service 8001:8000
# second terminal:
#   curl -X POST localhost:8001/orders -H 'Content-Type: application/json' -d '{"dish_id": 1, "quantity": 2}'
#   curl localhost:8001/orders
```

### 10f. frontend (gets the public IP)

```bash
kubectl apply -f k8s/40-frontend.yaml
kubectl rollout status deployment/frontend
kubectl get service frontend --watch     # EXTERNAL-IP: <pending> → an IP after 1–3 min (Ctrl+C)
```

### 10g. Check everything

```bash
kubectl get pods,svc -o wide
```

Our result:

```
pod/frontend-...        1/1  Running
pod/menu-service-...    1/1  Running
pod/order-service-...   1/1  Running
pod/redis-...           1/1  Running

service/frontend        LoadBalancer  10.0.82.13   <PUBLIC-IP>  80:30992/TCP
service/menu-service    ClusterIP     10.0.111.67  <none>          8000/TCP
service/order-service   ClusterIP     10.0.79.142  <none>          8000/TCP
service/redis           ClusterIP     10.0.83.245  <none>          6379/TCP
```

Only `frontend` has an external IP. The others are private, exactly as in docker compose.

> **Why nginx is happy here:** in Docker, nginx looks up backend addresses once at
> start-up and can get stuck with an old one. A Kubernetes Service's address never
> changes while the Service exists, even when its pods are replaced.

---

## Step 11: Open the app and fix the browser timeout

```bash
curl http://<PUBLIC-IP>/healthz     # ok  (HTTP 200)
curl http://<PUBLIC-IP>/            # HTTP 200, the web page
```

### The problem

The browser (Brave) showed **"This site can't be reached: took too long to respond"**
(`ERR_CONNECTION_TIMED_OUT`).

### What we checked

```bash
kubectl get pods,svc -n food-court -o wide                # all Running, external IP assigned
curl -m 8 http://<PUBLIC-IP>/healthz                    # HTTP 200 ✅
curl -m 8 https://<PUBLIC-IP>/                          # timed out ❌
```

### The cause

The app only serves **http** (port 80). Brave automatically upgrades typed
addresses to **https** (port 443), where nothing is listening. Azure's load
balancer silently drops that traffic, so the browser waits and times out.

### The fix

- Type the full address: **`http://<PUBLIC-IP>`**
- or turn Brave Shields off for that site (lion icon), or set **Settings → Shields →
  Upgrade connections to HTTPS** to Standard/Disabled
- or use Safari or Chrome

✅ The app loaded, and ordering worked from the public internet.

The browser shows "Not secure" because there's no HTTPS certificate yet. Adding
HTTPS is a possible next step.

---

## Step 12: Find everything in the Azure Portal

### The app, inside the cluster

**aks-foodcourt → Kubernetes resources** (set the **Namespace** filter to **food-court**)

| Menu item | What you see |
|---|---|
| **Namespaces** | `food-court`, plus the system namespaces |
| **Workloads → Deployments** | `redis`, `menu-service`, `order-service`, `frontend`, each Ready 1/1 |
| **Workloads → Pods** | The running pods. Click one for **Live logs** and **Events** |
| **Services and ingresses** | The 4 Services. `frontend` shows the **External IP** (clickable) |
| **Storage → Persistent volume claims** | `redis-data`, Bound |

### The Azure resources Kubernetes created

**Resource groups → `MC_rg-foodcourt_aks-foodcourt_centralindia`**

| Resource | What it is |
|---|---|
| Virtual machine scale set | The node(s) |
| Public IP `kubernetes-...`, tag `k8s-azure-service: food-court/frontend` | **The app's public IP**, created by `40-frontend.yaml` |
| Public IP with a long GUID name | The cluster's **outbound** IP (pods → internet, e.g. pulling `redis:7-alpine`) |
| Load balancer `kubernetes` | Sends internet traffic to the frontend. See **Frontend IP configuration** |
| Disk `pvc-...` | The Azure disk behind `redis-data` |

⚠️ **Look, but don't change anything in the `MC_` group by hand.** Kubernetes
manages these resources. Deleting the frontend Service (or the namespace)
removes the public IP, and re-applying the file gives a **new** IP.

---

## Step 13: Costs and cleanup

### What it costs (free trial)

On a **free trial**, everything is taken from the free credit (about US$200 for
30 days). The card is **not charged** unless you choose *Upgrade to
pay-as-you-go*. When the credit or the 30 days run out, Azure stops the resources.

Central India list prices, from Azure's public price list:

| Resource | Price | Per day |
|---|---|---|
| Node VM `Standard_D2ls_v5` | $0.085/hour each | $2.04 each |
| Node OS disk (Premium SSD 128 GB, P10) | $19.71/month each | $0.66 each |
| Load balancer (Standard, approx.) | ~$0.025/hour | ~$0.60 |
| Public IP (Standard) | $0.005/hour each | $0.12 each |
| Container Registry (Basic) | $0.1666/day | $0.17 |
| Redis disk (1 GB Standard SSD) | | ~$0.01 |

| Setup | ≈ per day | ≈ 30 days |
|---|---|---|
| **2 nodes** (as created) | **$6.40** | **$192** (almost all the credit) |
| **1 node** | **$3.70** | **$111** |
| Cluster **stopped** | ~$1 | Depends on use |
| Everything **deleted** | $0 | $0 |

### Ways to save credit

**1. Scale the node pool to 1 node** (recommended; our pool was still 2 nodes, autoscaling 2–5)

- Portal: **aks-foodcourt → Settings → Node pools → agentpool → Scale node pool → Manual → 1 → Apply**
- or terminal:

```bash
az aks nodepool update --resource-group rg-foodcourt --cluster-name aks-foodcourt --name agentpool --disable-cluster-autoscaler
az aks nodepool scale  --resource-group rg-foodcourt --cluster-name aks-foodcourt --name agentpool --node-count 1
```

**2. Stop the cluster when you're not using it.** The app comes back on Start.

- Portal: **aks-foodcourt → Overview → Stop / Start**
- or terminal:

```bash
az aks stop  -g rg-foodcourt -n aks-foodcourt
az aks start -g rg-foodcourt -n aks-foodcourt
```

**3. Set a budget alert (free):** **Cost Management → Budgets → + Add**, scope Azure
subscription 1, e.g. US$50, email alerts at 50% and 90%.

**4. Check what's been spent:**

- **Subscriptions → Azure subscription 1 → Overview**: credit remaining and days left
- **Cost Management → Cost analysis**: spending by resource (can lag up to 24 hours)

### Cleanup

| Goal | Command | What happens |
|---|---|---|
| Remove the app, keep the cluster | `kubectl delete namespace food-court` | Deletes everything in `k8s/`, **including the public IP and the Redis disk (and its orders)** |
| Pause | `az aks stop -g rg-foodcourt -n aks-foodcourt` | VMs off, so most of the cost stops |
| **Delete everything in Azure** | `az group delete --name rg-foodcourt --yes --no-wait` | Cluster, `MC_` group, registry, images, IPs, disks: all gone |

Everything can be recreated from this document and the files in the project.

---

## Step 14: CI/CD with GitHub Actions

**Where:** `.github/workflows/ci-cd.yml` in the repo, plus a one-time setup in Azure and GitHub · **Cost:** GitHub Actions is free for public repos. Each deployment adds about 120 MB of images to the registry (10 GB is included).

Steps 4 and 10 were done by hand. CI/CD makes GitHub do them automatically on every change.

| Term | Food court analogy | In this project |
|---|---|---|
| **CI** (Continuous Integration) | A taste-tester checks every new recipe before it reaches customers | Job `test`: on every pull request and push, start the whole app with docker compose and run smoke tests |
| **CD** (Continuous Delivery/Deployment) | A delivery van restocks the food court with the approved recipe | Job `deploy`: on `main` only, and only if `test` passed, build the images, push them to ACR and roll them out to AKS |
| **OIDC federated credential** | A guest list at the door: "anyone sent by repo X, branch main, may enter". No keys are handed out | Azure trusts GitHub's short-lived tokens for this repo's `main` branch only. No passwords are stored in GitHub |

### How a change flows

```
 you: git push a branch ──► open a pull request
                               │
                               ▼
                     GitHub Actions: test (CI)        ← docker compose + smoke tests
                               │ ✅
 you: merge the PR into main ──┘
                               │
                               ▼
                     GitHub Actions: test (CI) ──✅──► deploy (CD)
                                                       1. log in to Azure (OIDC)
                                                       2. build + push images  :<commit-id>
                                                       3. kubectl apply -f k8s/   (with :<commit-id>)
                                                       4. wait for the rollout
```

**Image tags:** the pipeline tags images with the short commit ID (e.g.
`menu-service:3f2a9c1`) instead of `v1`, so every running version can be traced
back to its exact code. The `k8s/` files in the repo still say `:v1`. The
pipeline swaps the tag on GitHub's machine only, just before `kubectl apply`.

> ⚠️ Once CI/CD is in use, **deploy through the pipeline**. Running
> `kubectl apply -f k8s/` by hand from the Mac would roll the cluster back to the old `:v1` images.

### 14a. Give GitHub permission to deploy (Azure Portal, one time)

We create a **user-assigned managed identity**: an Azure identity that isn't a
person. GitHub logs in as it. It lives in `rg-foodcourt`, so deleting the
resource group deletes it too. Managed identities are free.

**1. Create the identity**

1. Search **Managed Identities → + Create**
2. Subscription: Azure subscription 1 · Resource group: **rg-foodcourt** · Region: **Central India** · Name: **id-github-foodcourt**
3. **Review + create → Create**

**2. Trust GitHub's tokens for this repo's main branch (federated credential)**

1. Open **id-github-foodcourt → Settings → Federated credentials → + Add credential**
2. Federated credential scenario: **GitHub Actions deploying Azure resources**
3. Organization: **sunilsamsonsuresh** · Repository: **food-court-aks-demo**
4. Entity: **Branch** · Branch: **main**
5. Name: **github-main** → **Add**

The subject it creates is `repo:sunilsamsonsuresh/food-court-aks-demo:ref:refs/heads/main`.
Pull requests (including from forks) and other branches **can't** log in. Only code on `main` can deploy.

**3. Let it push images: `AcrPush` on the registry**

1. Open **foodcourt48516 → Access control (IAM) → + Add → Add role assignment**
2. Role: search **AcrPush** → select → **Next**
3. Assign access to: **Managed identity** → **+ Select members** → Managed identity: **User-assigned managed identity** → pick **id-github-foodcourt** → **Select**
4. **Review + assign**

**4. Let it connect to the cluster: `Azure Kubernetes Service Cluster User Role` on the cluster**

1. Open **aks-foodcourt → Access control (IAM) → + Add → Add role assignment**
2. Role: **Azure Kubernetes Service Cluster User Role** → **Next**
3. Members: same as above → **id-github-foodcourt**
4. **Review + assign**

> Our cluster uses *local accounts*, so this role gives the pipeline full
> control of the cluster. That's fine for a learning project. Production clusters use
> Microsoft Entra ID with narrower Kubernetes permissions.

**Terminal equivalent of 14a**

```bash
az identity create -g rg-foodcourt -n id-github-foodcourt -l centralindia

az identity federated-credential create -g rg-foodcourt --identity-name id-github-foodcourt \
  --name github-main \
  --issuer https://token.actions.githubusercontent.com \
  --subject repo:sunilsamsonsuresh/food-court-aks-demo:ref:refs/heads/main \
  --audiences api://AzureADTokenExchange

PRINCIPAL_ID=$(az identity show -g rg-foodcourt -n id-github-foodcourt --query principalId -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role AcrPush --scope "$(az acr show -n foodcourt48516 --query id -o tsv)"
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role "Azure Kubernetes Service Cluster User Role" \
  --scope "$(az aks show -g rg-foodcourt -n aks-foodcourt --query id -o tsv)"
```

### 14b. Tell GitHub which identity to use (three repository secrets)

These are IDs, not passwords. We store them as **secrets** anyway, because
GitHub then hides them in the (public) logs.

| Secret | Where to find it in the Portal |
|---|---|
| `AZURE_CLIENT_ID` | **id-github-foodcourt → Overview → Client ID** |
| `AZURE_TENANT_ID` | **Microsoft Entra ID → Overview → Tenant ID** |
| `AZURE_SUBSCRIPTION_ID` | **Subscriptions → Azure subscription 1 → Subscription ID** |

**On GitHub:** repo → **Settings → Secrets and variables → Actions → New repository secret**, once for each.

**Or from the terminal** (copies the values directly, without printing them):

```bash
gh secret set AZURE_CLIENT_ID       --body "$(az identity show -g rg-foodcourt -n id-github-foodcourt --query clientId -o tsv)"
gh secret set AZURE_TENANT_ID       --body "$(az account show --query tenantId -o tsv)"
gh secret set AZURE_SUBSCRIPTION_ID --body "$(az account show --query id -o tsv)"
```

### 14c. Watch it run

- **GitHub → Actions tab**: every run, with each step's log
- **A pull request's "Checks"** section: the `Test (docker compose)` result
- Run a deployment without a code change: **Actions → CI/CD → Run workflow** (on `main`)
- Terminal: `gh run list` · `gh run watch`

Check the result on the cluster:

```bash
kubectl -n food-court get deployment -o wide    # IMAGES column shows :<commit-id>
```

### Things to know

| Situation | What happens |
|---|---|
| The cluster is **stopped** (`az aks stop`) | The deploy job fails at "Connect to the AKS cluster". Start the cluster and click **Re-run jobs** |
| A docs-only change (`*.md`) is pushed to `main` | No run (`paths-ignore`) |
| The smoke tests fail | `deploy` is skipped. Nothing broken reaches the cluster |
| Many deployments over time | Old image tags pile up in the registry. Delete old ones in **foodcourt48516 → Repositories** |
| Public repo, public logs | The workflow never prints the app's public IP, and secrets are masked |

---

## Problems we hit and how we fixed them

| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | `az: command not found` | Azure CLI not installed | `brew install azure-cli` |
| 2 | Local images were `linux/arm64` | Apple Silicon Mac | Rebuilt with `docker buildx build --platform linux/amd64 ... --push` |
| 3 | `Microsoft.ContainerRegistry` NotRegistered | New subscription | `az provider register --namespace Microsoft.ContainerRegistry --wait` |
| 4 | AKS needed Compute/Network/Storage/ContainerService | New subscription | Registered them in the Portal (**Subscription → Resource providers**) |
| 5 | *"B-series node sizes cannot be scheduled"* | AKS doesn't allow B-series. The default `B2pls_v2` was also ARM | Chose **Standard_D2ls_v5** |
| 6 | Many VM sizes unavailable | Free-trial restrictions (`NotAvailableForSubscription`) | `az vm list-skus` to see what's allowed |
| 7 | Validation failed: `AvailabilityZoneNotSupported` | Size allowed only in zone 3, but the Portal picked zone 1 | Availability zones: **Zone 3** or **None** |
| 8 | Node pool ended up at 2 nodes, autoscaling 2–5 | Dev/Test preset default | Scale to **Manual, 1** (fits the 4-vCPU quota and halves the cost) |
| 9 | `kubectl apply --dry-run=client` errored without a cluster | Even client dry-run needs the cluster's API list | Checked YAML offline with Python instead |
| 10 | Browser: `ERR_CONNECTION_TIMED_OUT` | Brave forced **https**, but the app serves only **http** | Open `http://<IP>` explicitly |
| 11 | Python `CERTIFICATE_VERIFY_FAILED` when fetching prices | python.org Python on macOS has no certificates until "Install Certificates" is run | Used `curl` instead |

### Troubleshooting quick reference

```bash
kubectl get pods                      # status of everything
kubectl describe pod <name>           # the Events section at the bottom explains most problems
kubectl logs <name>                   # app output
kubectl logs <name> --previous        # output from the last crashed run
```

| You see | Likely cause |
|---|---|
| `ImagePullBackOff` / `ErrImagePull` | Wrong image name, or the registry isn't attached (`az aks check-acr ...`) |
| `CrashLoopBackOff` + `exec format error` | Image built for ARM |
| `CrashLoopBackOff` on frontend + `host not found in upstream` | menu/order Services didn't exist yet. Apply files 20 and 30 first |
| Pod `Pending` + `Insufficient cpu/memory` | Node is full. Lower `replicas` |
| `EXTERNAL-IP <pending>` for more than 5 min | Check `kubectl describe service frontend` events |

---

## Command cheat sheet

```bash
# ── Azure login & context ────────────────────────────────────────────
az login
az account show -o table

# ── Registry ─────────────────────────────────────────────────────────
az acr login --name foodcourt48516
docker buildx build --platform linux/amd64 -t foodcourt48516.azurecr.io/<service>:<tag> --push ./<service>
az acr repository list --name foodcourt48516 -o table
az acr repository show-tags --name foodcourt48516 --repository <service> -o table

# ── Cluster ──────────────────────────────────────────────────────────
az aks get-credentials --resource-group rg-foodcourt --name aks-foodcourt
kubectl get nodes -L kubernetes.io/arch
az aks check-acr -g rg-foodcourt -n aks-foodcourt --acr foodcourt48516.azurecr.io
az aks stop  -g rg-foodcourt -n aks-foodcourt
az aks start -g rg-foodcourt -n aks-foodcourt

# ── App ──────────────────────────────────────────────────────────────
kubectl config set-context --current --namespace=food-court
kubectl apply -f k8s/                         # apply everything (files run in name order)
kubectl get pods,svc -o wide
kubectl rollout status deployment/<name>
kubectl logs deploy/<name> --tail=20
kubectl port-forward service/<name> <local-port>:<service-port>
kubectl scale deployment <name> --replicas=<n>
kubectl delete namespace food-court           # remove the app (IP + disk too)

# ── Delete everything in Azure ───────────────────────────────────────
az group delete --name rg-foodcourt --yes --no-wait
```
