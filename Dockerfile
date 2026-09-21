FROM python:3.12-slim
WORKDIR /app
COPY app.py config.json /app/
ENV PORT=8130 ROUTER_CONFIG=/app/config.json
EXPOSE 8130
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8130/health',timeout=4).status==200 else 1)"
CMD ["python", "app.py"]
