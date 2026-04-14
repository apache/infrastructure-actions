#!/usr/bin/env bash
# Copyright (c) The stash contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Unit tests for download_stash.sh. A fake `gh` binary is placed on PATH
# so that the retry logic can be exercised without touching the network.

set -u

this_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
script="$this_dir/download_stash.sh"

tmp_root=$(mktemp -d)
trap 'rm -rf "$tmp_root"' EXIT

failures=0
cases=0
case_dir=""

fail() { echo "FAIL: $1"; failures=$(( failures + 1 )); }
ok()   { echo "ok:   $1"; }

# Create a fake `gh` binary in $1 that returns successive exit codes taken
# from the comma-separated list $2. Once the list is exhausted, the last
# code is reused for subsequent calls. Every invocation is logged to
# "$1/.log" so tests can assert how many times `gh` was called.
make_fake_gh() {
    local dir=$1
    local codes=$2
    mkdir -p "$dir"
    cat >"$dir/gh" <<EOF
#!/usr/bin/env bash
counter_file="$dir/.counter"
codes="$codes"
IFS=',' read -ra arr <<< "\$codes"
n=\$(cat "\$counter_file" 2>/dev/null || echo 0)
idx=\$n
if (( idx >= \${#arr[@]} )); then idx=\$(( \${#arr[@]} - 1 )); fi
echo \$(( n + 1 )) > "\$counter_file"
echo "fake-gh n=\$n rc=\${arr[\$idx]} args: \$*" >> "$dir/.log"
exit "\${arr[\$idx]}"
EOF
    chmod +x "$dir/gh"
    : > "$dir/.counter"
    : > "$dir/.log"
}

count_calls() {
    local log="$1/.log"
    if [[ -f "$log" ]]; then
        wc -l < "$log" | tr -d ' '
    else
        echo 0
    fi
}

# Prepare a fresh case directory and export the env vars the script needs.
# After this, the caller can tweak RETRY_COUNT / FAIL_ON_DOWNLOAD / CLEAN
# and invoke the script.
run_case() {
    cases=$(( cases + 1 ))
    case_dir="$tmp_root/case_$cases"
    mkdir -p "$case_dir"
    export STASH_RUN_ID="42"
    export STASH_NAME="fake-stash"
    export STASH_DIR="$case_dir/target"
    export REPO="test/repo"
    export GITHUB_OUTPUT="$case_dir/github_output"
    : > "$GITHUB_OUTPUT"
    mkdir -p "$STASH_DIR"
}

# --- Case 1: success on first attempt ---------------------------------------
run_case
make_fake_gh "$case_dir/bin" "0"
prev=$failures
RETRY_COUNT=3 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=success" "$GITHUB_OUTPUT" || fail "case 1: expected download=success"
[[ $rc -eq 0 ]] || fail "case 1: expected exit 0, got $rc"
[[ $calls -eq 1 ]] || fail "case 1: expected 1 gh call, got $calls"
(( failures == prev )) && ok "success on first attempt"

# --- Case 2: success after two retries on exit code 1 -----------------------
run_case
make_fake_gh "$case_dir/bin" "1,1,0"
prev=$failures
RETRY_COUNT=3 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=success" "$GITHUB_OUTPUT" || fail "case 2: expected download=success"
[[ $rc -eq 0 ]] || fail "case 2: expected exit 0, got $rc"
[[ $calls -eq 3 ]] || fail "case 2: expected 3 gh calls, got $calls"
(( failures == prev )) && ok "retry on exit 1 until success"

# --- Case 3: all retries fail; fail-on-download=false -> exit 0 -------------
run_case
make_fake_gh "$case_dir/bin" "1"
prev=$failures
RETRY_COUNT=3 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=failed" "$GITHUB_OUTPUT" || fail "case 3: expected download=failed"
[[ $rc -eq 0 ]] || fail "case 3: expected exit 0, got $rc"
[[ $calls -eq 3 ]] || fail "case 3: expected 3 gh calls, got $calls"
(( failures == prev )) && ok "all retries fail, tolerated by default"

# --- Case 4: all retries fail; fail-on-download=true -> exit 1 --------------
run_case
make_fake_gh "$case_dir/bin" "1"
prev=$failures
RETRY_COUNT=2 FAIL_ON_DOWNLOAD=true CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=failed" "$GITHUB_OUTPUT" || fail "case 4: expected download=failed"
[[ $rc -eq 1 ]] || fail "case 4: expected exit 1, got $rc"
[[ $calls -eq 2 ]] || fail "case 4: expected 2 gh calls, got $calls"
grep -q "fail-on-download is true" "$case_dir/out" \
    || fail "case 4: expected error annotation in script output"
(( failures == prev )) && ok "fail-on-download=true causes exit 1"

# --- Case 5: exit code != 1 does not retry ----------------------------------
run_case
make_fake_gh "$case_dir/bin" "2"
prev=$failures
RETRY_COUNT=5 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=failed" "$GITHUB_OUTPUT" || fail "case 5: expected download=failed"
[[ $rc -eq 0 ]] || fail "case 5: expected exit 0, got $rc"
[[ $calls -eq 1 ]] || fail "case 5: expected 1 gh call, got $calls"
grep -q "not retrying" "$case_dir/out" \
    || fail "case 5: expected 'not retrying' message"
(( failures == prev )) && ok "exit code != 1 is not retried"

# --- Case 6: CLEAN=true removes STASH_DIR before download -------------------
run_case
make_fake_gh "$case_dir/bin" "0"
touch "$STASH_DIR/leftover"
prev=$failures
RETRY_COUNT=1 FAIL_ON_DOWNLOAD=false CLEAN=true \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
[[ ! -e "$STASH_DIR/leftover" ]] || fail "case 6: expected stash dir cleaned"
grep -q "download=success" "$GITHUB_OUTPUT" || fail "case 6: expected download=success"
[[ $rc -eq 0 ]] || fail "case 6: expected exit 0, got $rc"
(( failures == prev )) && ok "CLEAN=true removes stash dir before download"

# --- Case 7: CLEAN=false leaves STASH_DIR contents alone --------------------
run_case
make_fake_gh "$case_dir/bin" "0"
touch "$STASH_DIR/leftover"
prev=$failures
RETRY_COUNT=1 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
[[ -e "$STASH_DIR/leftover" ]] || fail "case 7: expected leftover file to remain"
grep -q "download=success" "$GITHUB_OUTPUT" || fail "case 7: expected download=success"
[[ $rc -eq 0 ]] || fail "case 7: expected exit 0, got $rc"
(( failures == prev )) && ok "CLEAN=false leaves stash dir contents"

# --- Case 8: mixed transient+fatal — stops on first non-1 code --------------
run_case
make_fake_gh "$case_dir/bin" "1,2,0"
prev=$failures
RETRY_COUNT=5 FAIL_ON_DOWNLOAD=false CLEAN=false \
    PATH="$case_dir/bin:$PATH" bash "$script" > "$case_dir/out" 2>&1
rc=$?
calls=$(count_calls "$case_dir/bin")
grep -q "download=failed" "$GITHUB_OUTPUT" || fail "case 8: expected download=failed"
[[ $rc -eq 0 ]] || fail "case 8: expected exit 0, got $rc"
[[ $calls -eq 2 ]] || fail "case 8: expected 2 gh calls (1 retried, 2 stops), got $calls"
(( failures == prev )) && ok "stops retrying on first non-1 exit code"

echo
echo "Ran $cases test cases, $failures failure(s)."
(( failures == 0 ))
