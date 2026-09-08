FROM python:3.11-slim

WORKDIR /app

# psycopg2-binary ships wheels, so no libpq build step is needed. Dependencies
# are copied and installed before the source so that editing code does not
# invalidate the (slow) pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Unbuffered so the startup warnings and error logs reach the platform's log
# viewer immediately rather than sitting in a pipe buffer.
ENV PYTHONUNBUFFERED=1

EXPOSE 10000

# Single worker on purpose. The login rate limiter holds its counters in
# process memory (see auth.py), so a second worker would silently double the
# allowed attempts; and the connection pool is sized per process, so workers
# multiply the database connections. Scale by raising DB_POOL_MAX first, and
# move the throttle into the database before adding workers.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
