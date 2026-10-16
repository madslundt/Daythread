FROM node:22-slim AS web
WORKDIR /web
COPY src/web/package*.json ./
RUN npm ci
COPY src/web ./
RUN npm run build

FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src/omi_local ./src/omi_local
COPY --from=web /web/dist ./src/omi_local/web
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home omi && mkdir /data && chown omi:omi /data
COPY --from=build /build/.venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" OMI_DATA_DIR=/data
USER omi
WORKDIR /home/omi
EXPOSE 8787
CMD ["python", "-m", "uvicorn", "omi_local.api:create_app", "--factory", "--workers", "1", "--host", "0.0.0.0", "--port", "8787"]
