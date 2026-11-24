# Hugging Face Space (Docker SDK): runs the MAPLES browser app on port 7860.
# The AI key and service come from the Space secrets; USE_ENV_TOKEN_ONLY=true (or
# MAPLES_HOSTED=true) switches on hosted mode, so visitors cannot change them.
FROM python:3.10

RUN useradd -m -u 1000 user
RUN mkdir -p /app && chown -R user:user /app

USER user
ENV PATH="/home/user/.local/bin:$PATH" \
    MAPLES_HOSTED=true \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --chown=user pyproject.toml README.md /app/
COPY --chown=user maples /app/maples
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

CMD ["maples", "ui", "--host", "0.0.0.0", "--port", "7860", "--no-browser"]
