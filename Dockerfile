# BloomSense: the client app (default) and the processing pipeline in one image.
# The organizer data is NOT in the image: mount the repository's data/ at /app/data (see README.md, "Docker").
#   docker build -t bloomsense .
#   docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" bloomsense
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/output:/app/output" -e OUT=/app/output bloomsense /app/scripts/run_all.sh

FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# typechecks, then builds web/dist
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy PATH="/app/backend/.venv/bin:$PATH"
RUN pip install --no-cache-dir uv==0.10.9
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
# the locked torch (sam group, needed by canopy_net) resolves to the Linux wheel with its CUDA runtime; it runs on CPU here
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --group sam --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --group sam
COPY models/canopy_net.pt /app/models/canopy_net.pt
COPY scripts/ /app/scripts/
COPY --from=web /app/web/dist /app/web/dist
# marcaj resolves the repo root from its own path (/app): data at /app/data, weights at /app/models, client at /app/web/dist
VOLUME /app/data
EXPOSE 8000
CMD ["uvicorn", "marcaj.api:app", "--host", "0.0.0.0", "--port", "8000"]
