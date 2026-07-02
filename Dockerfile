FROM python:3.10-slim

# Hugging Face Spaces runs the container as uid 1000 — create that user so the
# app directory (and its disk cache) is writable at runtime.
RUN useradd -m -u 1000 user

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code, owned by the runtime user
COPY --chown=user:user . .
RUN rm -rf .venv __pycache__ .pytest_cache .cache

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    CHAINLIT_HOST=0.0.0.0 \
    CHAINLIT_PORT=8000 \
    CHAINLIT_NO_AUTO_OPEN=true

EXPOSE 8000

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
