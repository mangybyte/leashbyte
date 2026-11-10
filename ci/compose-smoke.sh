#!/usr/bin/env bash
set -euo pipefail

set -a
source .env
set +a
base="http://127.0.0.1:${LEASHBYTE_PORT}"
auth="X-API-Key: ${LEASHBYTE_API_KEY}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

unauthenticated="$(curl -sS -o "$work/unauthenticated.json" -w '%{http_code}' "$base/v1/config")"
test "$unauthenticated" = "401"

curl -fsS -H "$auth" "$base/readyz" >"$work/ready.json"
curl -fsS -H "$auth" "$base/v1/search?q=LeashByte&limit=2" >"$work/search.json"
curl -fsS -H "$auth" -H 'Content-Type: application/json' \
  --data '{"url":"http://host.docker.internal:18765/html","format":"markdown"}' \
  "$base/v1/fetch" >"$work/html.json"
curl -fsS -H "$auth" -H 'Content-Type: application/json' \
  --data '{"url":"http://host.docker.internal:18765/json","format":"json"}' \
  "$base/v1/fetch" >"$work/json.json"
ssrf="$(curl -sS -H "$auth" -H 'Content-Type: application/json' \
  --data '{"url":"http://169.254.169.254/","format":"markdown"}' \
  -o "$work/ssrf.json" -w '%{http_code}' "$base/v1/fetch")"
test "$ssrf" = "403"

curl -fsS "$base/runs" >"$work/viewer.html"
curl -fsS -H "$auth" -H 'Content-Type: application/json' \
  --data '{"goal":"Smoke test run","start_url":"http://host.docker.internal:18765/html","model":"controlled-model"}' \
  "$base/v1/runs" >"$work/run.json"
run_id="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$work/run.json")"
curl -fsS -H "$auth" "$base/v1/runs/$run_id" >"$work/run-inspect.json"
curl -fsS -H "$auth" "$base/v1/runs?limit=1" >"$work/run-list.json"
curl -fsS -H "$auth" "$base/v1/runs/$run_id/steps" >"$work/run-steps.json"
unauthenticated_artifact="$(curl -sS -o "$work/unauthenticated-artifact.json" -w '%{http_code}' \
  "$base/v1/runs/$run_id/artifacts/result.json")"
test "$unauthenticated_artifact" = "401"
curl -fsS -H "$auth" -H 'Content-Type: application/json' \
  --data '{"reason":"Compose smoke check complete."}' \
  "$base/v1/runs/$run_id/cancel" >"$work/run-cancel.json"
for attempt in $(seq 1 30); do
  curl -fsS -H "$auth" "$base/v1/runs/$run_id" >"$work/run-terminal.json"
  if python - "$work/run-terminal.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1]))["state"]
raise SystemExit(0 if state in {"succeeded", "failed", "cancelled", "limit_reached"} else 1)
PY
  then
    break
  fi
  sleep 1
done
curl -fsS -X DELETE -H "$auth" "$base/v1/runs/$run_id"
deleted_run="$(curl -sS -H "$auth" -o "$work/deleted-run.json" -w '%{http_code}' \
  "$base/v1/runs/$run_id")"
test "$deleted_run" = "404"

python - "$work" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
assert json.loads((root / "ready.json").read_text())["status"] == "ready"
search = json.loads((root / "search.json").read_text())
assert isinstance(search["results"], list)
html = json.loads((root / "html.json").read_text())
assert "LeashByte controlled page" in html["content"]
assert html["format"] == "markdown"
parsed = json.loads((root / "json.json").read_text())
assert parsed["content"] == {"source": "controlled-fixture", "value": 42}
ssrf = json.loads((root / "ssrf.json").read_text())
assert ssrf["error"]["code"] == "blocked_address"
assert "LeashByte runs" in (root / "viewer.html").read_text()
created = json.loads((root / "run.json").read_text())
inspected = json.loads((root / "run-inspect.json").read_text())
assert created["id"] == inspected["id"]
assert inspected["goal"] == "Smoke test run"
listed = json.loads((root / "run-list.json").read_text())
assert any(run["id"] == created["id"] for run in listed["runs"])
assert isinstance(json.loads((root / "run-steps.json").read_text()), list)
terminal = json.loads((root / "run-terminal.json").read_text())
assert terminal["state"] in {"succeeded", "failed", "cancelled", "limit_reached"}
print("Compose smoke checks passed.")
PY
