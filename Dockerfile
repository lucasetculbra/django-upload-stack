# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------------------
# Stage 1: build the virtualenv. Keeping pip and the build tooling out of the final image
# makes it smaller and reduces its attack surface.
# ---------------------------------------------------------------------------------------
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------------------
# Stage 2: runtime image. Runs as a non-root user and only carries the virtualenv
# plus the application code.
# ---------------------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_DB=postgres \
    MEDIA_ROOT=/app/media \
    STATIC_ROOT=/app/staticfiles

# uid/gid 1000 keeps file ownership predictable on the named volumes.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/sh app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# These directories become the mount points for the media/static named volumes. Docker
# seeds an empty named volume from the image's directory, so creating them with the right
# owner here is what lets the non-root process write uploads later.
RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app

COPY --chown=app:app manage.py entrypoint.sh ./
COPY --chown=app:app config ./config
COPY --chown=app:app uploads ./uploads

# Strip CRLF (the repository is authored on Windows) and set the executable bit, which
# Git on Windows does not preserve.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

# ---------------------------------------------------------------------------------------
# Stage 3: image with the test tooling, used by docker-compose.test.yml.
# ---------------------------------------------------------------------------------------
FROM runtime AS test

USER root
COPY requirements.txt requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt
USER app
