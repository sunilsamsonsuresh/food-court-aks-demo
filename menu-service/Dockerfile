# Start from a small official Python image ("slim" = fewer extras, smaller download).
FROM python:3.12-slim

# Every command below runs inside /app in the container.
WORKDIR /app

# Copy ONLY requirements.txt first, then install the packages.
# Why: Docker saves each step as a cached "layer". If a step's inputs haven't
# changed, Docker reuses the saved result instead of running it again.
# Your code changes often but your package list rarely does, so installing
# packages before copying the code means editing main.py does NOT trigger a
# slow re-install. Rebuilds then take seconds instead of minutes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the application code. Only the steps from here down re-run when the code changes.
COPY app/ ./app/

# Documents which port the app listens on. It doesn't open the port by itself;
# you still map it with `docker run -p`.
EXPOSE 8000

# 0.0.0.0 means "accept connections from outside the container".
# With the default 127.0.0.1, the app would only be reachable from inside
# the container itself, so your browser or curl couldn't reach it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
