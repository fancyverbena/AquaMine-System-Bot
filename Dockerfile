FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home appuser \
    && mkdir -p /app/config /app/data /app/logs \
    && chown -R appuser:appuser /app
USER appuser
CMD ["python", "bot.py"]
