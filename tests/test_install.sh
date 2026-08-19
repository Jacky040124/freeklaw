#!/bin/sh
# Network-free security and behavior tests for install.sh.
# shellcheck disable=SC2016
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/freeklaw-install-tests.XXXXXX")
trap 'rm -rf "$TEST_ROOT"' EXIT HUP INT TERM
PASS=0 FAIL=0
pass() { PASS=$((PASS + 1)); printf 'ok %d - %s\n' "$PASS" "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'not ok - %s\n' "$1" >&2; }

make_executable() { path=$1; shift; mkdir -p "$(dirname "$path")"; printf '%s\n' '#!/bin/sh' "$@" > "$path"; chmod 700 "$path"; }

new_case() {
    name=$1; CASE_DIR=$TEST_ROOT/$name; HOME=$CASE_DIR/home; MOCK_BIN=$CASE_DIR/mocks; PREFIX=$HOME/.freeklaw; APPS=$HOME/Applications; FIXTURE_REPO=$CASE_DIR/repo
    mkdir -p "$HOME" "$MOCK_BIN" "$FIXTURE_REPO/lib" "$FIXTURE_REPO/bin" "$FIXTURE_REPO/skills/freeklaw" "$FIXTURE_REPO/skills/freeklaw-onboarding"
    sed \
        -e "s/FREEKLAW_RELEASE_COMMIT='[^']*'/FREEKLAW_RELEASE_COMMIT='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'/" \
        -e "s/EGO_LITE_ARM64_DMG_SHA256='[^']*'/EGO_LITE_ARM64_DMG_SHA256='testegohash'/" \
        -e "s/AGENT_VAULT_INTEGRITY='[^']*'/AGENT_VAULT_INTEGRITY='sha512-testSRI'/" \
        -e "s/PYYAML_MACOS_WHEEL_SHA256='[^']*'/PYYAML_MACOS_WHEEL_SHA256='testwheelhash'/" \
        "$ROOT/compatibility.lock" > "$FIXTURE_REPO/compatibility.lock"
    printf '%s\n' '# helper' > "$FIXTURE_REPO/lib/freeklaw_state.py"; printf '%s\n' '# secret helper' > "$FIXTURE_REPO/lib/freeklaw_secret_fill.py"
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$FIXTURE_REPO/bin/freeklaw-state"; printf '%s\n' '#!/bin/sh' 'exit 0' > "$FIXTURE_REPO/bin/freeklaw-secret-fill"
    printf '%s\n' '---' 'name: freeklaw' '---' > "$FIXTURE_REPO/skills/freeklaw/SKILL.md"; printf '%s\n' '---' 'name: freeklaw-onboarding' '---' > "$FIXTURE_REPO/skills/freeklaw-onboarding/SKILL.md"
    export HOME MOCK_BIN PREFIX APPS FIXTURE_REPO
}

make_hermes_launcher() {
    make_executable "$1" \
        'case ${1-} in' \
        '  --version) printf "%s\n" "hermes 0.20.1";;' \
        '  skills) printf "%s\n" "$*" >> "$HOME/hermes-skills.log";;' \
        'esac' \
        "if [ \"\${1-}\" = __never__ ]; then exec \"$HOME/.hermes/hermes-agent/venv/bin/python\" \"$HOME/.hermes/hermes-agent/hermes\" \"\$@\"; fi"
}

make_ego_app() {
    app=$1
    cli=$app/Contents/Frameworks/ego\ Framework.framework/Versions/0.4.6.14/Helpers/ego-browser
    mkdir -p "$(dirname "$cli")"
    printf '%s\n' version=0.4.6.14 id=com.citrolabs.ego.lite > "$app/Contents/Info.plist"
    make_executable "$cli" 'printf "%s\n" "ego-browser 0.4.6.14"'
}

make_common_mocks() {
    make_executable "$MOCK_BIN/plutil" 'key=$2' 'last=' 'for arg in "$@"; do last=$arg; done' 'case $key in CFBundleShortVersionString) sed -n "s/^version=//p" "$last";; CFBundleIdentifier) sed -n "s/^id=//p" "$last";; esac'
    make_executable "$MOCK_BIN/codesign" 'last=' 'for arg in "$@"; do last=$arg; done' 'case " $* " in *" -dv "*) case $last in *ego-browser) printf "%s\n" Identifier=com.citrolabs.ego.ego-browser TeamIdentifier=JGQLC6YQYJ >&2;; *) printf "%s\n" Identifier=com.citrolabs.ego.lite TeamIdentifier=JGQLC6YQYJ >&2;; esac;; *) exit 0;; esac'
    make_executable "$MOCK_BIN/spctl" 'exit 0'
    make_executable "$MOCK_BIN/shasum" 'last=' 'for arg in "$@"; do last=$arg; done' 'case $last in *.dmg) hash=testegohash;; *.whl) hash=testwheelhash;; *) hash=other;; esac' 'printf "%s  %s\n" "$hash" "$last"'
    make_executable "$MOCK_BIN/openssl" 'case $1 in dgst) printf raw;; base64) cat >/dev/null; printf testSRI;; esac'
    make_executable "$MOCK_BIN/git" 'printf "%s\n" f80f453ae0679347e38abc917c7f94f717bf96c5'
}

make_install_mocks() {
    make_common_mocks
    make_hermes_launcher "$MOCK_BIN/hermes-template"
    make_executable "$MOCK_BIN/hermes-upstream" \
        'printf "%s\n" "$*" > "$HOME/hermes-installer.args"' \
        'mkdir -p "$HOME/.local/bin" "$HOME/.hermes/hermes-agent/.git" "$HOME/.hermes/hermes-agent/venv/bin"' \
        'cp "$FREEKLAW_TEST_MOCK_BIN/hermes-template" "$HOME/.local/bin/hermes"' 'chmod 700 "$HOME/.local/bin/hermes"'
    make_executable "$MOCK_BIN/agent-vault-template" 'printf "%s\n" "agent-vault 0.4.0"'
    make_executable "$MOCK_BIN/curl" \
        'out= url=' 'while [ "$#" -gt 0 ]; do case $1 in -o) out=$2; shift 2;; -*) shift;; *) url=$1; shift;; esac; done' \
        'case $url in *install.sh) cp "$(dirname "$0")/hermes-upstream" "$out";; *agent-vault*) printf vault > "$out";; *egolite.dmg) printf dmg > "$out";; *) exit 1;; esac'
    make_executable "$MOCK_BIN/npm" \
        'printf "%s\n" "$*" >> "$HOME/npm.log"' \
        'while [ "$#" -gt 0 ]; do if [ "$1" = --prefix ]; then prefix=$2; shift 2; else shift; fi; done' \
        'mkdir -p "$prefix/bin"' 'cp "$(dirname "$0")/agent-vault-template" "$prefix/bin/agent-vault"' 'chmod 700 "$prefix/bin/agent-vault"'
    make_executable "$MOCK_BIN/runtime-python" \
        'case " $* " in' \
        '  *" import yaml; print(yaml.__version__) "*) printf "%s\n" 6.0.3;;' \
        '  *" pip download "*) dest=; while [ "$#" -gt 0 ]; do if [ "$1" = --dest ]; then dest=$2; shift 2; else shift; fi; done; printf wheel > "$dest/PyYAML.whl";;' \
        '  *" pip install "*) printf "%s\n" "$*" >> "$HOME/pip.log";;' \
        'esac'
    make_executable "$MOCK_BIN/python3" \
        'case " $* " in *" -m venv "*) target=; for arg in "$@"; do target=$arg; done; mkdir -p "$target/bin"; cp "$(dirname "$0")/runtime-python" "$target/bin/python"; chmod 700 "$target/bin/python";; *) exit 0;; esac'
    make_executable "$MOCK_BIN/hdiutil" \
        'printf "%s\n" "$*" >> "$HOME/hdiutil.log"' \
        'case $1 in attach) while [ "$#" -gt 0 ]; do if [ "$1" = -mountpoint ]; then mount=$2; shift 2; else shift; fi; done; cli="$mount/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.14/Helpers/ego-browser"; mkdir -p "$(dirname "$cli")"; printf "%s\n" version=0.4.6.14 id=com.citrolabs.ego.lite > "$mount/ego lite.app/Contents/Info.plist"; cp "$(dirname "$0")/ego-cli-template" "$cli"; chmod 700 "$cli";; esac'
    make_executable "$MOCK_BIN/ego-cli-template" 'printf "%s\n" "ego-browser 0.4.6.14"'
    make_executable "$MOCK_BIN/ditto" 'cp -R "$1" "$2"'
}

make_compatible_state() {
    make_common_mocks
    mkdir -p "$HOME/.local/bin" "$HOME/.hermes/hermes-agent/.git" "$HOME/.hermes/hermes-agent/venv/bin" "$PREFIX/runtime/npm/bin" "$PREFIX/runtime/python/bin"
    make_hermes_launcher "$HOME/.local/bin/hermes"
    make_executable "$PREFIX/runtime/npm/bin/agent-vault" 'printf "%s\n" "agent-vault 0.4.0"'
    cp "$MOCK_BIN/runtime-python" "$PREFIX/runtime/python/bin/python" 2>/dev/null || make_executable "$PREFIX/runtime/python/bin/python" 'printf "%s\n" 6.0.3'
    make_ego_app "$APPS/ego lite.app"
    ln -s "$APPS/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.14/Helpers/ego-browser" "$HOME/.local/bin/ego-browser"
}

run_installer() {
    output=$1; shift
    env HOME="$HOME" PATH="$MOCK_BIN:/usr/bin:/bin" FREEKLAW_TEST_MOCK_BIN="$MOCK_BIN" FREEKLAW_OS=Darwin FREEKLAW_ARCH=arm64 FREEKLAW_PREFIX="$PREFIX" FREEKLAW_APPLICATIONS_DIR="$APPS" FREEKLAW_EGO_APP="$APPS/ego lite.app" FREEKLAW_REPO_ROOT="$FIXTURE_REPO" \
        FREEKLAW_CURL_BIN="$MOCK_BIN/curl" FREEKLAW_NPM_BIN="$MOCK_BIN/npm" FREEKLAW_PYTHON3_BIN="$MOCK_BIN/python3" FREEKLAW_HDIUTIL_BIN="$MOCK_BIN/hdiutil" FREEKLAW_DITTO_BIN="$MOCK_BIN/ditto" FREEKLAW_PLUTIL_BIN="$MOCK_BIN/plutil" FREEKLAW_CODESIGN_BIN="$MOCK_BIN/codesign" FREEKLAW_SPCTL_BIN="$MOCK_BIN/spctl" FREEKLAW_SHASUM_BIN="$MOCK_BIN/shasum" FREEKLAW_OPENSSL_BIN="$MOCK_BIN/openssl" FREEKLAW_CONFIRM=YES "$ROOT/install.sh" "$@" > "$output" 2>&1
}

snapshot_home() { find "$HOME" -type f -exec /usr/bin/shasum {} \; -o -type l -exec /bin/ls -ld {} \; | sort; }

test_release_guard() {
    new_case release_guard
    sed "s/FREEKLAW_RELEASE_COMMIT='[^']*'/FREEKLAW_RELEASE_COMMIT='RELEASE_COMMIT_REQUIRED'/" "$ROOT/compatibility.lock" > "$FIXTURE_REPO/compatibility.lock"
    before=$(snapshot_home)
    if env HOME="$HOME" PATH=/usr/bin:/bin FREEKLAW_OS=Darwin FREEKLAW_REPO_ROOT="$FIXTURE_REPO" "$ROOT/install.sh" --check > "$CASE_DIR/out" 2>&1; then fail 'release commit placeholder guard'; return; fi
    after=$(snapshot_home)
    if [ "$before" = "$after" ] && grep -q 'release commit' "$CASE_DIR/out"; then pass 'release commit placeholder fails before writes'; else fail 'release commit placeholder fails before writes'; fi
}

test_wrong_os() {
    new_case wrong_os
    if env HOME="$HOME" PATH=/usr/bin:/bin FREEKLAW_OS=Linux FREEKLAW_REPO_ROOT="$FIXTURE_REPO" "$ROOT/install.sh" --check > "$CASE_DIR/out" 2>&1; then fail 'wrong OS'; elif grep -q 'macOS only' "$CASE_DIR/out"; then pass 'wrong OS is rejected'; else fail 'wrong OS is rejected'; fi
}

test_check_compatible_no_writes() {
    new_case compatible; make_install_mocks; make_compatible_state; before=$(snapshot_home)
    if run_installer "$CASE_DIR/out" --check; then after=$(snapshot_home); if [ "$before" = "$after" ] && [ ! -e "$PREFIX/runtime/python/bin/__pycache__" ]; then pass 'compatible check is canonical and write-free'; else fail 'compatible check is canonical and write-free'; fi; else sed 's/^/# /' "$CASE_DIR/out" >&2; fail 'compatible check is canonical and write-free'; fi
}

test_missing_check_no_writes() {
    new_case missing; make_install_mocks; before=$(snapshot_home)
    if run_installer "$CASE_DIR/out" --check; then fail 'missing check'; else after=$(snapshot_home); if [ "$before" = "$after" ] && grep -q 'missing.*Hermes' "$CASE_DIR/out"; then pass 'missing check reports drift without writes'; else fail 'missing check reports drift without writes'; fi; fi
}

test_symlink_prefix_refused() {
    new_case symlink; make_install_mocks; mkdir "$CASE_DIR/target"; ln -s "$CASE_DIR/target" "$PREFIX"
    if run_installer "$CASE_DIR/out"; then fail 'symlinked prefix refusal'; elif grep -q 'symlinked destination' "$CASE_DIR/out" && [ -z "$(find "$CASE_DIR/target" -mindepth 1 -print)" ]; then pass 'symlinked prefix is refused before destination writes'; else fail 'symlinked prefix is refused before destination writes'; fi
}

test_refuses_incompatible_default() {
    new_case incompatible; make_install_mocks; make_compatible_state; make_executable "$HOME/.local/bin/hermes" 'printf "%s\n" "hermes 0.19.0"'; before=$(snapshot_home)
    if run_installer "$CASE_DIR/out"; then fail 'default incompatible refusal'; else after=$(snapshot_home); if [ "$before" = "$after" ] && grep -q -- '--upgrade' "$CASE_DIR/out"; then pass 'default refuses incompatible canonical dependency without writes'; else fail 'default refuses incompatible canonical dependency without writes'; fi; fi
}

test_install_onboarding_and_scanner() {
    new_case install; make_install_mocks
    set +e; run_installer "$CASE_DIR/first"; rc=$?; set -e
    if [ "$rc" -ne 2 ]; then sed 's/^/# /' "$CASE_DIR/first" >&2; fail 'install hashes, scanner, and onboarding boundary'; return; fi
    cli="$APPS/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.14/Helpers/ego-browser"
    scanner_ok=true
    grep -q 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/freeklaw/SKILL.md' "$HOME/hermes-skills.log" || scanner_ok=false
    grep -q -- '--force' "$HOME/hermes-skills.log" && scanner_ok=false
    grep -q -- '--ignore-scripts' "$HOME/npm.log" || scanner_ok=false
    grep -q -- '--require-hashes' "$HOME/pip.log" || scanner_ok=false
    [ -f "$PREFIX/runtime/command-paths" ] || scanner_ok=false
    [ "$scanner_ok" = true ] || { fail 'install hashes, scanner, and onboarding boundary'; return; }
    ln -s "$cli" "$HOME/.local/bin/ego-browser"
    if run_installer "$CASE_DIR/second" --check && grep -q 'compatible.*ego lite' "$CASE_DIR/second"; then pass 'install enforces hashes/signatures, immutable scanner URLs, and GUI completion'; else sed 's/^/# /' "$CASE_DIR/second" >&2; fail 'install enforces hashes/signatures, immutable scanner URLs, and GUI completion'; fi
}

test_upgrade_backup() {
    new_case upgrade; make_install_mocks; make_compatible_state
    make_executable "$HOME/.local/bin/hermes" 'printf "%s\n" "hermes 0.19.0"' "if [ \"\${1-}\" = __never__ ]; then exec \"$HOME/.hermes/hermes-agent/venv/bin/python\" \"$HOME/.hermes/hermes-agent/hermes\" \"\$@\"; fi"
    sed -i '' 's/version=0.4.6.14/version=0.4.6.13/' "$APPS/ego lite.app/Contents/Info.plist"
    set +e; run_installer "$CASE_DIR/out" --upgrade; rc=$?; set -e
    if [ "$rc" -eq 2 ] && [ -d "$APPS/ego lite.app.freeklaw-backup" ] && grep -q -- '--force-commit' "$HOME/hermes-installer.args" && grep -q 'GUI onboarding' "$CASE_DIR/out"; then pass 'confirmed upgrade retains backup and requires renewed ego onboarding'; else sed 's/^/# /' "$CASE_DIR/out" >&2; fail 'confirmed upgrade retains backup and requires renewed ego onboarding'; fi
}

test_release_guard
test_wrong_os
test_check_compatible_no_writes
test_missing_check_no_writes
test_symlink_prefix_refused
test_refuses_incompatible_default
test_install_onboarding_and_scanner
test_upgrade_backup
printf '1..%d\n' "$((PASS + FAIL))"
[ "$FAIL" -eq 0 ]
