FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data manuals uploads/signatures

EXPOSE 8006

CMD ["gunicorn", "-b", "0.0.0.0:8006", "--workers", "2", "--threads", "4", "--timeout", "120", "app:app"]
