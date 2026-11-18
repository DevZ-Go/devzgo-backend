FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x scripts/entrypoint.sh && sed -i 's/\r$//' scripts/entrypoint.sh scripts/on_test_db.sh || true

# Render injects PORT; entrypoint binds 0.0.0.0:$PORT with reload disabled.
EXPOSE 8000
CMD ["./scripts/entrypoint.sh"]