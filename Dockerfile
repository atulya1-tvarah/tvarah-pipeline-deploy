FROM python:3.12-slim

WORKDIR /app

# System deps for torch/transformers wheels and PDF parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
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

# Railway's automatic GitHub build fetches a repo snapshot rather than a full
# LFS-aware git clone, so the *.safetensors files land here as tiny LFS
# pointer stubs, not the real weights. Overwrite them with the actual content
# from GitHub's LFS media endpoint (works unauthenticated since the repo is
# public) -- this is the reliable path, independent of whatever checkout
# mechanism the build platform uses.
RUN for m in dna_fit role_family skill_depth; do \
      curl -fL --retry 3 -o "trained_models_release_v4/${m}/model.safetensors" \
        "https://media.githubusercontent.com/media/atulya1-tvarah/tvarah-pipeline-deploy/main/trained_models_release_v4/${m}/model.safetensors"; \
    done

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
