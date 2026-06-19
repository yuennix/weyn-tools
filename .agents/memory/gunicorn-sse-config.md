---
name: Gunicorn SSE configuration
description: Why gthread workers + timeout 0 are required for the live stats SSE stream to work
---

The app uses Server-Sent Events (SSE) at `/api/stats` for live stats streaming.

**Rule:** Always run gunicorn with `--worker-class gthread --threads 4 --timeout 0`.

**Why:** The default gunicorn sync worker has a 30-second worker timeout. When it serves the long-lived SSE `/api/stats` stream, it gets killed after 30 s and a fresh process is spawned with all-zero counters. This makes live stats always show 0. The `gthread` worker handles the SSE connection and concurrent start/stop requests in separate threads with no timeout.

**How to apply:** Any time the gunicorn run command is changed or a new workflow is configured, ensure these three flags are present:
- `--worker-class gthread`
- `--threads 4`
- `--timeout 0`

Current working command:
```
python3 -m gunicorn --bind 0.0.0.0:5000 --reuse-port --reload --worker-class gthread --threads 4 --timeout 0 main:app
```
