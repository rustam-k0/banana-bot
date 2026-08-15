#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/opt/banana-bot"
readonly BRANCH="production"
readonly LOCK_FILE="/tmp/banana-bot-deploy.lock"

exec 9>"${LOCK_FILE}"
flock -n 9 || {
  echo "Another deployment is already running"
  exit 1
}

cd "${APP_DIR}"
git fetch --prune origin "${BRANCH}"
git checkout "${BRANCH}"
git merge --ff-only "origin/${BRANCH}"

"${APP_DIR}/.venv/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
"${APP_DIR}/.venv/bin/python" -m unittest discover -s tests -v

sudo /usr/bin/systemctl restart banana-bot.service

for attempt in {1..15}; do
  if curl --fail --silent --show-error http://127.0.0.1:8080/healthz >/dev/null; then
    echo "Deployment completed successfully"
    exit 0
  fi
  sleep 2
done

echo "Health check failed after deployment" >&2
sudo /usr/bin/systemctl status banana-bot.service --no-pager >&2 || true
exit 1
