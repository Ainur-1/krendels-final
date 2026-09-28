# syntax=docker/dockerfile:1

# Две стадии: uv и кеш сборки нужны, чтобы поставить зависимости, и им нечего делать в
# контейнере, который отвечает на запросы. Сборки интерфейса нет вовсе — он написан без
# сборщика и лежит в пакете готовым, поэтому Node в образ не попадает.

# --- зависимости ----------------------------------------------------------------
FROM python:3.12-slim AS builder

# Версия закреплена той, которой получен uv.lock: образ ничего не разрешает заново и
# ставит ровно то, на чём прогонялись тесты.
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Сначала только файлы, закрепляющие дерево зависимостей: правка кода не обесценивает
# этот слой. Ставится только основной набор — генератор моделей, pytest и ruff из
# набора dev в работающем сервисе не нужны.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY src/ ./src/
# README и LICENSE входят в метаданные пакета: без них сборка проекта не проходит.
COPY README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

# --- то, что работает -----------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="${VCS_REF}"

# Сервис разбирает сообщения извне, поэтому работает не под root.
RUN useradd --create-home --uid 10001 zd

WORKDIR /app

COPY --from=builder --chown=zd:zd /app/.venv /app/.venv
COPY --from=builder --chown=zd:zd /app/src /app/src
# Конфигурация, схемы контракта и сценарии — часть приложения: сервис читает их при
# запуске, а демонстрационный набор проигрывается в пустой журнал.
COPY --chown=zd:zd config/ /app/config/
COPY --chown=zd:zd contracts/ /app/contracts/
COPY --chown=zd:zd data/ /app/data/

# Ключи и якорь журнала живут в томе, журнал — в PostgreSQL (адрес в ZD_STORAGE_URL).
# Каталог создаётся от имени пользователя сервиса, чтобы именованный том унаследовал
# владельца. Ключи создаются при первом запуске внутри тома и в образ не попадают.
RUN install -d -o zd -g zd /app/var

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER zd
EXPOSE 8000
VOLUME ["/app/var"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "zero_defect.serving.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
