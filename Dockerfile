FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first, from the lockfile, so that a source edit does not
# re-resolve them (the install layer is keyed on the lock and pyproject only).
# `requirements.lock` is exported from `uv.lock` with
# `uv export --frozen --no-dev --no-emit-project --format requirements.txt`;
# regenerate it whenever `uv.lock` moves, or the image drifts from the lock.
COPY pyproject.toml README.md requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src /app/src/
RUN pip install --no-cache-dir --no-deps .

# Non-root user, plus the two directories the runtime touches. `runs/` is
# bind-mounted by the deployment guide, but it has to exist and be owned here
# first: the runner creates `runs/<game_id>` lazily, so a mount point that
# lands root-owned (a fresh bind mount, or a named volume) fails the run for
# this user before a single event is written. `configs/` is read-only at
# runtime and likewise mounted.
RUN useradd -m -u 1000 appuser \
 && mkdir -p /app/runs /app/configs \
 && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["simulation"]
