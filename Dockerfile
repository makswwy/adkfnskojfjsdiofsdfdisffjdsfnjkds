FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=UTF-8
ENV PYTHONUTF8=1
ENV APP_MODE=bot
ENV ENABLE_WEB_LOGS=0
ENV PORT=3000

CMD ["sh", "-c", "if [ \"$APP_MODE\" = \"web\" ]; then if [ \"$ENABLE_WEB_LOGS\" = \"1\" ]; then exec gunicorn -b 0.0.0.0:${PORT} app:app; else echo \"[web] Logs web app is disabled. Set ENABLE_WEB_LOGS=1 to start it.\"; exit 0; fi; else exec python main.py; fi"]
