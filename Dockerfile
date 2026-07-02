FROM python:3.10

RUN useradd -m -u 1000 user

# Create /app and give it to user BEFORE switching
RUN mkdir -p /app && chown -R user:user /app

USER user
ENV PATH="/home/user/.local/bin:$PATH"
WORKDIR /app

COPY --chown=user ./requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=user . /app

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "7860"]

