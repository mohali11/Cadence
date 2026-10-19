# Hosting Cadence

This is the step-by-step version of the README's Deploying section: what to
put on a server so Cadence runs at a stable address and keeps its data
across restarts and updates.

None of this changes the app. Cadence stays a single Flask process reading
one SQLite file, and there is no HTTPS code inside it.

## What you need

- A server you can SSH into. A small VPS with 1 CPU, 1 GB of RAM and a
  local disk is plenty: the app serves one person at a time and SQLite is
  happiest on a local disk.
- Linux is assumed for the paths below. The same steps work elsewhere with
  the paths changed.
- Python 3.9 or newer, or Docker if you would rather skip the Python setup.
- Optionally a domain name pointing at the server. Only the HTTPS example
  at the end needs one; the app itself runs fine without.

## Install the app

Copy the project to the server (git clone or scp), then create a
virtualenv and install the requirements:

```bash
cd study_tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` pulls in Flask, Flask-WTF and waitress. There is nothing
to build and no JavaScript step.

## Set SECRET_KEY and DATABASE_PATH

The app reads both from the environment at startup.

Generate a secret key on the server:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then put the values where the app will see them. For a shell test run:

```bash
export SECRET_KEY="paste the generated string here"
export DATABASE_PATH="/var/lib/cadence/study.db"
```

For a service, the systemd unit below reads them from `/etc/cadence.env`.

- `SECRET_KEY` signs the session cookies and CSRF tokens. If it is not set,
  the app falls back to the dev-only key and prints a warning. That fallback
  is fine on your laptop and unsafe on a server: anyone can read the source
  and forge the tokens it protects.
- `DATABASE_PATH` is the SQLite file. Give it an absolute path outside the
  app folder, as in the next section.

## Keep the database on a persistent path

Point `DATABASE_PATH` at a directory that survives deploys and gets backed
up, for example `/var/lib/cadence`:

```bash
sudo useradd --system --home /srv/cadence --shell /usr/sbin/nologin cadence
sudo mkdir -p /var/lib/cadence
sudo chown cadence:cadence /var/lib/cadence
```

The systemd unit below runs the app as this same `cadence` user, so give it
read access to the project folder as well.

The app creates the file and runs its migrations on first start. If you
leave `DATABASE_PATH` unset, the database lives next to `app.py`, so a fresh
checkout or a rebuilt container starts empty. Back it up by copying the one
file while the app is stopped, or with SQLite's `.backup` command.

## Run waitress

waitress is the production WSGI server. The Flask dev server (`python
app.py`) never goes on a server: it runs in debug mode with auto-reload.

From the project folder, with the virtualenv active and the environment set:

```bash
waitress-serve --host=127.0.0.1 --port=8080 --call app:create_app
```

`--host=127.0.0.1` keeps the app off the public internet; only the reverse
proxy should talk to it. Check it with `curl http://127.0.0.1:8080/` on the
server.

To keep it running across reboots, a minimal systemd unit:

```ini
# /etc/systemd/system/cadence.service
[Unit]
Description=Cadence study tracker
After=network.target

[Service]
User=cadence
WorkingDirectory=/srv/cadence/study_tracker
EnvironmentFile=/etc/cadence.env
ExecStart=/srv/cadence/study_tracker/.venv/bin/waitress-serve --host=127.0.0.1 --port=8080 --call app:create_app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/cadence.env` holds the two variables, one per line:

```
SECRET_KEY=paste-the-generated-string-here
DATABASE_PATH=/var/lib/cadence/study.db
```

Then enable and start it, and check the status:

```bash
sudo systemctl enable --now cadence
systemctl status cadence
```

## Run the Docker image

If you would rather not manage Python on the server, the image does it for
you:

```bash
docker build -t cadence .
docker run -d --name cadence -p 127.0.0.1:8080:8080 \
  -e SECRET_KEY="paste the generated string here" \
  -v cadence-data:/data cadence
```

- The image runs waitress as a non-root user, on port 8080.
- `-p 127.0.0.1:8080:8080` publishes the port to localhost only, so the
  reverse proxy can reach it and the internet cannot.
- `-v cadence-data:/data` is the persistent path: the named volume holds
  `/data/cadence.db`, which is the `DATABASE_PATH` inside the image.
  Without it, a rebuilt container starts empty.
- To update, build the new image, then `docker rm -f cadence` and run it
  again. The volume keeps the data.

Check it with `docker logs cadence`.

## HTTPS with a reverse proxy (example)

TLS terminates at the proxy, and the proxy forwards plain HTTP to waitress
on localhost. This example is a starting point to adapt, not a tested
recipe: it has not been run against a real domain here, and your DNS,
firewall and hosting provider can differ.

Point your domain's DNS record at the server, install Caddy, and give it
this Caddyfile:

```
example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy gets and renews a Let's Encrypt certificate for `example.com`
automatically, so `https://example.com` reaches waitress with no extra
configuration. Replace `example.com` with your domain. nginx with certbot
works the same way: proxy the site to `http://127.0.0.1:8080`.

Once it is up, check that:

- `https://<your domain>/` loads the dashboard.
- `http://<your domain>/` redirects to HTTPS (Caddy does this by default).
- The padlock shows a real certificate, not a warning.
