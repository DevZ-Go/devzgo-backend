From python:3.11

WORKDIR /app

COPY requirements.txt .

# Here we use Run to run the image
RUN pip install --no-cache-dir -r requirements.txt

# Here we use COPY to copy the files from the current directory to the /app directory in the container
COPY . .

#Here CMD is used to START the Container and run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]