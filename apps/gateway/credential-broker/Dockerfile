FROM python:3.12-slim-bookworm
WORKDIR /app
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unixodbc \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -o /tmp/msprod.deb \
    && dpkg -i /tmp/msprod.deb && rm /tmp/msprod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home broker
# 管理VMから渡すソースは所有者限定(600/700)。実行ユーザーに所有させる。
COPY --chown=broker:broker credential_broker ./credential_broker
COPY --chown=broker:broker sql ./sql
USER broker
EXPOSE 8000
CMD ["uvicorn", "credential_broker.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-level", "warning"]
