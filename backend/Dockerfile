FROM python:3.11-slim

WORKDIR /app

# Gerekli bağımlılıkları kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Tüm backend klasör içeriğini Docker içine kopyala
COPY . .

# Uygulamayı Render'ın dinamik portu üzerinden tetikle
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
