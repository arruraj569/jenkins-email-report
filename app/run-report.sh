#!/usr/bin/env bash
set -eu
umask 077
app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
export SOLAR_REPORT_ROOT="${SOLAR_REPORT_ROOT:-$(dirname "$app_dir")}"
cd "$SOLAR_REPORT_ROOT"
exec "${SOLAR_PYTHON:-python3}" "$app_dir/runner.py" "$@"
