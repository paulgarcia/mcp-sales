FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.8.17

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./


# The test suite, with dev dependencies: `docker compose --profile test run --rm tests`.
FROM base AS test
RUN uv sync --frozen --no-install-project
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY .env.example ./
RUN uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest", "-q"]


# The server. Last stage, so a plain `docker build .` produces it.
FROM base AS runtime

# Dependencies first, so editing the code does not reinstall them.
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["sales-mcp", "serve", "--host", "0.0.0.0", "--port", "8000"]
