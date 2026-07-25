FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# uv provides fast, reproducible installs from the committed lockfile. Pin the uv version too,
# so the toolchain is as reproducible as the lockfile it consumes.
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached unless the lockfile changes).
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

# Install the application itself.
COPY . .
RUN uv sync --frozen --no-dev

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 8000
# Serve the app; it applies pending migrations itself on startup (see the FastAPI lifespan).
ENTRYPOINT ["dizzchat"]
