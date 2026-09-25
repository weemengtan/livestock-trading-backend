#!/usr/bin/env bash
# Run the dev-data scripts against the Railway TEST database from your laptop.
# Reads DATABASE_URL / ENVIRONMENT / SEED_PASSWORD from .env.railway
# (see DEPLOY.md §3). The scripts' own safeguards still apply: the wipe
# refuses unless ENVIRONMENT=dev, prints the target host, and asks for 'yes'.
#
#   scripts/cloud.sh migrate         alembic upgrade head (normally automatic on deploy)
#   scripts/cloud.sh seed            orgs + demo accounts (once; wipe keeps accounts)
#   scripts/cloud.sh reference FILE  reference data from your bootstrap JSON (once; wipe keeps it)
#   scripts/cloud.sh wipe            truncate business data + audit log (asks to confirm)
#   scripts/cloud.sh market          (re)seed demo market observations
#   scripts/cloud.sh reset           wipe, then market
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.railway ] || { echo "Missing .env.railway — create it as described in DEPLOY.md §3 (DATABASE_URL, ENVIRONMENT=dev, SEED_PASSWORD)." >&2; exit 1; }
set -a; . ./.env.railway; set +a
[ -n "${DATABASE_URL:-}" ] || { echo "DATABASE_URL is empty in .env.railway." >&2; exit 1; }
[ -n "${SEED_PASSWORD:-}" ] || unset SEED_PASSWORD

case "${1:-}" in
  migrate)   uv run alembic upgrade head ;;
  seed)      uv run python -m scripts.seed ;;
  reference) [ -n "${2:-}" ] || { echo "usage: cloud.sh reference /path/to/bootstrap.json" >&2; exit 1; }
             uv run python -m scripts.seed_reference_data --file "$2" ;;
  wipe)      uv run python -m scripts.wipe_business_data ;;
  market)    uv run python -m scripts.seed_market_intel ;;
  reset)     # Confirm once here (the wipe script's own prompt returns success on "no", which
             # would let the market reseed run anyway), then run both steps non-interactively.
             echo "Target database: ${DATABASE_URL##*@}"
             printf "Wipe ALL business data + audit log there, then reseed market intel? Type 'yes': "
             read -r answer
             [ "$answer" = "yes" ] || { echo "Aborted — nothing was changed."; exit 1; }
             uv run python -m scripts.wipe_business_data --yes && uv run python -m scripts.seed_market_intel ;;
  *)         sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
