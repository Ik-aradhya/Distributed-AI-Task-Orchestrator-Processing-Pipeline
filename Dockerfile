FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr for immediate log output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal system tools (curl for healthchecks/debugging)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and database migrations
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

EXPOSE 8000

# Default entrypoint runs the FastAPI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
