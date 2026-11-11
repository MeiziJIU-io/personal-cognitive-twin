FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY collector /app/collector
COPY server /app/server
RUN useradd --uid 10001 --create-home capture && mkdir -p /data/objects && chown -R capture:capture /data
USER capture
EXPOSE 8000
CMD ["uvicorn", "server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
