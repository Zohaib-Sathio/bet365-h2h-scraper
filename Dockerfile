FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bet365_h2h ./bet365_h2h
COPY webapp.py run.py ./

# The token cache lives on disk; keep it writable for non-root hosts.
RUN mkdir -p /app/.cache /app/output && chmod -R 777 /app/.cache /app/output

EXPOSE 8000
CMD ["python", "webapp.py"]
