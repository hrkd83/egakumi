FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home audit
COPY --chown=audit:audit audit_relay ./audit_relay
USER audit
EXPOSE 8000
CMD ["python", "-B", "-m", "audit_relay"]
