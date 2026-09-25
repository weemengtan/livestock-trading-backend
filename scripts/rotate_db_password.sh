#!/usr/bin/env bash
# Rotate the Railway Postgres password from your laptop, without the new
# password ever being printed.
#
#   scripts/rotate_db_password.sh
#
# 1. Generates a new random password.
# 2. ALTER USER on the database named by .env.railway (needs public access on).
# 3. Verifies the new password connects, then rewrites .env.railway with it.
# 4. Copies the new password to your clipboard (macOS) for you to paste into
#    Railway: Postgres -> Variables -> POSTGRES_PASSWORD. See the message at the end.
#
# Railway's variables alone do NOT change an existing database's password; the
# ALTER USER here does. Between step 2 and the Railway update, NEW backend
# connections fail (existing ones keep working), so do the Railway steps straight away.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env.railway}"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE." >&2; exit 1; }
case "$ENV_FILE" in /*) ;; *) ENV_FILE="./$ENV_FILE" ;; esac  # `.` needs a path, not a bare name
set -a; . "$ENV_FILE"; set +a
[ -n "${DATABASE_URL:-}" ] || { echo "DATABASE_URL is empty in $ENV_FILE." >&2; exit 1; }

echo "Target database: ${DATABASE_URL##*@}"
printf "Rotate the password of the database user there? Type 'yes': "
read -r answer
[ "$answer" = "yes" ] || { echo "Aborted — nothing was changed."; exit 1; }

NEW_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export NEW_PASSWORD ENV_FILE

.venv/bin/python - <<'PY'
import asyncio, os, pathlib, re

import asyncpg
from sqlalchemy.engine import make_url

env_file = pathlib.Path(os.environ["ENV_FILE"])
new = os.environ["NEW_PASSWORD"]
url = make_url(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1))


async def connect(password: str) -> asyncpg.Connection:
    return await asyncpg.connect(host=url.host, port=url.port, user=url.username, password=password, database=url.database)


async def main() -> None:
    conn = await connect(url.password)
    try:
        stmt = await conn.fetchval("SELECT format('ALTER USER %I PASSWORD %L', current_user, $1::text)", new)
        await conn.execute(stmt)
    finally:
        await conn.close()
    # Prove the new password works before touching the local file.
    check = await connect(new)
    await check.close()

    text = env_file.read_text()
    old_line = re.search(r"^DATABASE_URL=.*$", text, re.M).group(0)
    new_line = "DATABASE_URL=" + url.set(password=new).render_as_string(hide_password=False)
    env_file.write_text(text.replace(old_line, new_line, 1))
    print("Password changed in Postgres, verified, and saved to", env_file)


asyncio.run(main())
PY

if command -v pbcopy >/dev/null; then
  printf %s "$NEW_PASSWORD" | pbcopy
  echo "New password copied to your clipboard (not displayed)."
else
  echo "No pbcopy on this machine: read it from DATABASE_URL in $ENV_FILE."
fi
cat <<'MSG'

NOW, straight away, in Railway:
  1. Postgres service -> Variables -> POSTGRES_PASSWORD -> paste the new password -> save, then Deploy.
     (If PGPASSWORD is a literal, not a reference to POSTGRES_PASSWORD, update it the same way.)
  2. When Postgres is back Online, redeploy the backend service (Deployments -> Redeploy)
     so it picks up the new DATABASE_URL.
  3. Check https://<backend>/api/v1/health returns {"status":"ok","db":true,"redis":true}.
MSG
