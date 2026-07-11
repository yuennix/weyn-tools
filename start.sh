#!/bin/sh
# Railway (and other PaaS) inject the port to bind to via $PORT at runtime.
# Some platforms invoke the container's start command without going through
# a shell, so relying on Docker's shell-form CMD for ${PORT:-8080} expansion
# is not reliable everywhere. This script guarantees the shell expansion
# happens regardless of how the platform invokes it.
PORT="${PORT:-8080}"
exec gunicorn --bind "0.0.0.0:${PORT}" --worker-class gthread --threads 4 --timeout 0 main:app
