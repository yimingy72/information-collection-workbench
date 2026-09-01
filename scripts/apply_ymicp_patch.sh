#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

docker cp "$PROJECT_DIR/scripts/ymicp_jsl.py" ymicp:/icp_Api/ymicp_jsl.py
docker cp "$PROJECT_DIR/scripts/ymicp_patched.py" ymicp:/icp_Api/ymicp.py
docker cp "$PROJECT_DIR/scripts/query_routes_patched.py" ymicp:/icp_Api/routes/query_routes.py
docker cp "$PROJECT_DIR/scripts/batch_routes_patched.py" ymicp:/icp_Api/routes/batch_routes.py
docker exec ymicp python -m pip install --quiet quickjs
docker exec ymicp sed -i 's/retry_times: 10/retry_times: 3/' /icp_Api/config.yml
docker restart ymicp
echo "ymicp patched and restarted"
