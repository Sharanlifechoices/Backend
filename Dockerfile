FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Render injects $PORT; default to 8000 locally. File logging is off in the container
# (Render captures stdout).
ENV LOG_FILE=""
# No --proxy-headers: client IP is derived in app code from the rightmost X-Forwarded-For entry.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
