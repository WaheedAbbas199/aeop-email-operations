FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]"
COPY . .
RUN useradd -m aeop && mkdir -p /app/data && chown -R aeop /app/data
USER aeop
EXPOSE 8000
HEALTHCHECK --interval=30s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["sh", "-c", "alembic upgrade head && python main.py"]
