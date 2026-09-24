From python:3.11

WORKDIR /app

COPY requirements.txt .

# Here we use Run to run the image
RUN pip install --no-cache-dir -r requirements.txt

# Here we use COPY to copy the files from the current directory to the /app directory in the container
COPY . .

RUN chmod +x scripts/entrypoint.sh

# Entrypoint waits for Postgres, bootstraps schema/seed, then starts uvicorn.
CMD ["./scripts/entrypoint.sh"]