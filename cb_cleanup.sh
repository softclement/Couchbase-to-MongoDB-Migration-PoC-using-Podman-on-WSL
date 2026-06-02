#!/bin/bash
# cb_cleanup.sh — stop and remove PoC containers and the Podman network.

echo "Stopping containers..."
podman stop mongodb couchbase 2>/dev/null || true

echo "Removing containers..."
podman rm mongodb couchbase 2>/dev/null || true

echo "Removing Podman network..."
podman network rm nosql-net 2>/dev/null || true

echo "Cleanup complete."
