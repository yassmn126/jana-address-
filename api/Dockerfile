# Everything the service needs is baked in at BUILD time (network on).
# At RUN time (jury: network off) nothing is downloaded: models, gazetteer and
# dictionaries live in the image. Build for both CPU architectures:
#   docker buildx build --platform linux/amd64,linux/arm64 -t jana-address-api ./api
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    JANA_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jana_address ./jana_address
# artifacts/ holds model.onnx, tokenizer.json, gazetteer.sqlite once they exist (see Makefile)
COPY artifacts ./artifacts
RUN mkdir -p /data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=18 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if b'ok' in urllib.request.urlopen('http://127.0.0.1:8000/health').read() else 1)"
CMD ["uvicorn", "jana_address.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
