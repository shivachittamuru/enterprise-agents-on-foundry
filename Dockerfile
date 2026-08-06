# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# Foundry Hosted Agent image for the Text-to-SQL Responses agent.
#
# The container reproduces the repository layout the code expects: the package
# stays under /app/src and is installed editable, and database/queries is copied
# alongside it, because config.settings.repository_root resolves paths relative
# to the source tree. It starts only the Responses host and reads all runtime
# configuration from environment variables injected by Foundry and azure.yaml.
# -----------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# pyodbc needs the Microsoft ODBC Driver 18, which is a system package rather
# than a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,armhf,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8088

WORKDIR /app

# Resolve dependencies from the locked manifest first, so the layer is cached
# until the lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra database --extra hosting

# Then install the application and the query files it reads at runtime.
COPY src ./src
COPY database/queries ./database/queries
RUN uv sync --frozen --no-dev --extra database --extra hosting

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8088

# The Responses host binds to PORT and fails fast with a clear error when a
# required setting (model endpoint, SQL FQDN) is absent.
CMD ["python", "-m", "enterprise_agents_on_foundry.hosting.app"]
