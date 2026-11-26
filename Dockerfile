FROM python:3.12-slim
WORKDIR /app
# ランクカード画像の日本語描画用フォント
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python", "bot.py"]
