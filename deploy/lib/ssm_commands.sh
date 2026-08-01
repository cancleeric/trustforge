#!/usr/bin/env bash
# Sourced library: helpers to build AWS SSM AWS-RunShellScript `commands`
# JSON arrays for `aws ssm send-command --parameters commands=<json>`.
#
# Using jq to construct the array avoids the brittle manual shell-quote /
# JSON-escape nesting that previously produced malformed parameters
# (AWS CLI `ParamValidation: Expected ',', received 'e'`), which made the
# activation post-verify step report failure even when the deployment itself
# had succeeded.
#
# This file is intended to be sourced, not executed.
#
# Requires: jq or python3 on PATH. activate_release.sh validates this at
# startup (before any production mutation) so a missing JSON encoder fails fast
# rather than breaking post-verify/rollback mid-activation.

# build_ssm_commands_json
# Read shell commands from stdin (one command per line) and emit a compact
# JSON array string on stdout. Each input line becomes one JSON string
# element; jq performs all required escaping, so single quotes, double
# quotes, backslashes and variable-expanded paths inside a command are
# preserved verbatim and never break the surrounding JSON.
#
# Usage (variable expansion happens before the pipe, in the caller's shell):
#   params=$(printf '%s\n' \
#       "set -e" \
#       "systemctl show svc -p Environment --value | grep -F 'KEY=${PATH_VAR}'" \
#       'echo "[activate] done"' \
#     | build_ssm_commands_json)
#   aws ssm send-command ... --parameters "commands=${params}" ...
build_ssm_commands_json() {
  if command -v jq >/dev/null 2>&1; then
    jq -R . | jq -s -c .
    return
  fi
  python3 -c 'import json, sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin], ensure_ascii=False, separators=(",", ":")))'
}
