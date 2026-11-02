# Same pattern as menu-service: small official Python image.
FROM python:3.12-slim

WORKDIR /app

# Install packages BEFORE copying the code so Docker can reuse the cached
# install step when only the code changes (much faster rebuilds).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

# Settings such as MENU_SERVICE_URL and REDIS_HOST are NOT set here. They're
# passed in when the container starts (docker run -e ..., or Kubernetes later).
# The code falls back to sensible defaults if they're not set.

# 0.0.0.0 = accept connections from outside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
