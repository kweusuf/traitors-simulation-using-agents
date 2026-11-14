#!/usr/bin/env bash
# Example script for remote deployment: build, transfer, run, monitor, pull artifacts back.
# Run this from agent/ (the Docker build context is the current directory).
# DO NOT commit real values (USER, REMOTE_HOST, paths, registry). Copy and customize locally.

set -euo pipefail

# Placeholders - replace with real values locally (never commit)
REGISTRY="${REGISTRY:-registry.example/traitors-sim}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REMOTE_USER="${REMOTE_USER:-USER}"
REMOTE_HOST="${REMOTE_HOST:-REMOTE_HOST}"
REMOTE_RUNS_DIR="${REMOTE_RUNS_DIR:-/path/to/runs}"
REMOTE_CONFIGS_DIR="${REMOTE_CONFIGS_DIR:-/path/to/configs}"
LOCAL_RUNS_DIR="${LOCAL_RUNS_DIR:-$(pwd)/runs}"
CONFIG_FILE="${CONFIG_FILE:-configs/traitors/basic.yaml}"
MODEL="${MODEL:-}"
PROVIDER="${PROVIDER:-ollama}"
BASE_URL="${BASE_URL:-http://localhost:11434}"
CONTAINER_NAME="${CONTAINER_NAME:-traitors-run}"

# A real model name is required. Without one the app would receive the literal
# placeholder and fail deep inside a model call instead of here.
if [ -z "$MODEL" ] || [ "$MODEL" = "<model>" ]; then
  echo "Set MODEL to a model pulled on the remote host (e.g. MODEL=llama3.1:8b)." >&2
  exit 2
fi

# Build image with Podman (fallback to Docker if needed)
if command -v podman >/dev/null 2>&1; then
  BUILDER="podman"
else
  BUILDER="docker"
fi

echo "Building image with $BUILDER..."
"$BUILDER" build -t "traitors-sim:$IMAGE_TAG" .

echo "Pushing to registry..."
"$BUILDER" tag "traitors-sim:$IMAGE_TAG" "$REGISTRY:$IMAGE_TAG"
"$BUILDER" push "$REGISTRY:$IMAGE_TAG"

echo "Pulling on remote and running..."
# `$REMOTE_RUNS_DIR` must be writable by the container user (uid 1000). On a
# rootless remote host, either chown the directory to 1000:1000 or run the
# container with --userns=keep-id.
ssh "$REMOTE_USER@$REMOTE_HOST" "
  set -euo pipefail
  podman pull '$REGISTRY:$IMAGE_TAG'
  mkdir -p '$REMOTE_RUNS_DIR' '$REMOTE_CONFIGS_DIR'
  podman rm -f '$CONTAINER_NAME' 2>/dev/null || true
  podman run -d --name '$CONTAINER_NAME' --network host \
    -v '$REMOTE_RUNS_DIR:/app/runs' \
    -v '$REMOTE_CONFIGS_DIR:/app/configs:ro' \
    '$REGISTRY:$IMAGE_TAG' run '$CONFIG_FILE' \
      --provider '$PROVIDER' --base-url '$BASE_URL' --model '$MODEL'
  echo 'Container started: $CONTAINER_NAME'
"

echo "Monitoring logs from remote (Ctrl+C to stop following)..."
if ! ssh "$REMOTE_USER@$REMOTE_HOST" "podman logs -f '$CONTAINER_NAME'"; then
  echo "Could not follow the container logs; leaving the remote run as-is." >&2
fi

echo "Game finished (or monitoring stopped). Pulling artifacts back..."
mkdir -p "$LOCAL_RUNS_DIR"
rsync -avz --exclude='*.tmp' "$REMOTE_USER@$REMOTE_HOST:$REMOTE_RUNS_DIR/" "$LOCAL_RUNS_DIR/"

echo "Done. Artifacts pulled to $LOCAL_RUNS_DIR"
