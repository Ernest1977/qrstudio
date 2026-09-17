# Image runtime : Django + gunicorn/uvicorn worker. Python 3.13 slim, multi-stage pour ne pas
# embarquer les outils de compilation dans l'image finale.
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY --from=build /install /usr/local
COPY . /app
RUN chown -R app:app /app && mkdir -p /app/staticfiles && chown -R app:app /app/staticfiles
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=25s CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# 4 workers uvicorn : la vue de redirection est asynchrone, un seul processus en tient 3 a 4 fois plus
# qu'un worker sync. La regle « 2*CPU+1 » s'applique aux workers sync, pas a celle-ci.
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "-w", "4", "--timeout", "30", "--graceful-timeout", "20", "--keep-alive", "75", "--access-logfile", "-", "--access-logformat", "%(h)s %({X-Forwarded-For}i)s %l \"%r\" %(s)s %(b)s %(L)ss"]
