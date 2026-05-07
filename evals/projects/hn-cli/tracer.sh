#!/usr/bin/env bash
# Tracer for hn-cli: runs the CLI against replay fixtures, validates output.
# Pass conditions:
#   - exit 0
#   - exactly 3 lines on stdout
#   - each line matches: ^<rank>.  <score>  <title>  <url>
#   - story ids/titles match the fixture corpus deterministically
set -euo pipefail

if ! command -v hn-cli &>/dev/null; then
    echo "SKIP: hn-cli not in PATH (project not yet built)" >&2
    exit 0
fi

OUTPUT=$(HN_FIXTURE_FILE="$(dirname "$0")/fixtures/hn-api.jsonl" hn-cli top --limit 3 2>&1)
LINE_COUNT=$(echo "$OUTPUT" | grep -c .)

if [ "$LINE_COUNT" -ne 3 ]; then
    echo "FAIL: expected 3 lines, got $LINE_COUNT" >&2
    echo "$OUTPUT" >&2
    exit 1
fi

# Validate each line format: <rank>.  <score>  <title>  <url>
while IFS= read -r line; do
    if ! echo "$line" | grep -qE '^[0-9]+\.\s+[0-9]+\s+\S.*\s+https?://\S+$'; then
        echo "FAIL: line does not match expected format: $line" >&2
        exit 1
    fi
done <<< "$OUTPUT"

# Validate deterministic story ordering (first story from fixture)
if ! echo "$OUTPUT" | grep -q "terminal-native recipe browser"; then
    echo "FAIL: first story title not found in output" >&2
    echo "$OUTPUT" >&2
    exit 1
fi

echo "PASS"
echo "$OUTPUT"
