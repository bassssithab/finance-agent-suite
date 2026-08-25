#!/usr/bin/env bash
# Runs the full suite as one command. Each platform/agent module is invoked
# as its own pytest process, deliberately: several modules reuse eval-helper
# filenames (fixtures.py, fakes.py, test_end_to_end.py) with no __init__.py,
# and pytest's import machinery can't disambiguate hyphenated agent
# directory names (agents/technical-accounting-agent/...) when collecting
# them together in a single session, so collecting them separately here is
# what keeps them from colliding.
set -u

cd "$(dirname "$0")/.."

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

modules=()
for dir in platform/*/ agents/*/; do
  dir="${dir%/}"
  if find "$dir" -name 'test_*.py' -not -path '*/__pycache__/*' | grep -q .; then
    modules+=("$dir")
  fi
done

failed=()
for module in "${modules[@]}"; do
  echo "=================== $module ==================="
  if ! python -m pytest "$module" -q; then
    failed+=("$module")
  fi
  echo
done

echo "=================== summary ==================="
echo "${#modules[@]} module(s) tested, ${#failed[@]} failed."
if [ "${#failed[@]}" -gt 0 ]; then
  printf 'FAILED: %s\n' "${failed[@]}"
  exit 1
fi
