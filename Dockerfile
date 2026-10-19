# Cadence in a container.
#
# Build:
#   docker build -t cadence .
#
# Run (SECRET_KEY is required in practice; the named volume keeps the
# database across rebuilds):
#   docker run -d --name cadence -p 8080:8080 \
#     -e SECRET_KEY="<a long random string>" -v cadence-data:/data cadence
#
# The README's Deploying section has the walkthrough and the surrounding
# pieces (TLS, backups).

FROM python:3.12-slim

# No .pyc files; unbuffered logs so `docker logs` shows them immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/cadence.db

WORKDIR /app

# Dependencies first: editing the app does not re-run pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a normal user, and give it the directory it writes the database to.
RUN useradd --create-home --uid 1000 cadence \
    && mkdir -p /data \
    && chown -R cadence:cadence /app /data
USER cadence

# The SQLite file lives on the volume, not in the image layer.
VOLUME ["/data"]
EXPOSE 8080

# waitress, not the Flask dev server: no debug mode, no auto-reload, and it
# binds all interfaces so the port mapping works.
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8080", "--call", "app:create_app"]
