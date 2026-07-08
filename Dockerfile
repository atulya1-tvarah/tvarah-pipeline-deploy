FROM python:3.12-slim

WORKDIR /app

# System deps for torch/transformers wheels and PDF parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ml.txt ./
# CPU-only torch first -- Railway has no GPU, and the default PyPI torch wheel
# drags in several GB of unused nvidia-cuda-* packages that just slow every
# build/push/pull for no benefit here.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt -r requirements-ml.txt \
    && pip install --no-cache-dir pdfplumber==0.11.10 pypdf==6.14.2 python-docx==1.1.2 \
       bcrypt==5.0.0 psycopg2-binary==2.9.12 PyJWT==2.13.0

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
