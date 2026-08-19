#!/bin/sh
# Auditable macOS installer for a tagged Freeklaw checkout.
# shellcheck shell=sh
set -eu

MODE=install
case ${1-} in
    '') ;;
    --check) MODE=check; shift ;;
    --upgrade) MODE=upgrade; shift ;;
    -h|--help) printf '%s\n' 'Usage: ./install.sh [--check | --upgrade]' '  --check    inspect compatibility; make no intended writes' '  --upgrade  allow confirmed replacement of incompatible versions'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 64 ;;
esac
[ "$#" -eq 0 ] || { printf 'Only one mode may be selected.\n' >&2; exit 64; }

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=${FREEKLAW_REPO_ROOT:-$SCRIPT_DIR}
LOCK_FILE=${FREEKLAW_LOCK_FILE:-$REPO_ROOT/compatibility.lock}
[ -r "$LOCK_FILE" ] || { printf 'Compatibility manifest not found: %s\n' "$LOCK_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$LOCK_FILE"

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# A movable tag is never accepted for skill distribution. This guard runs
# before destination inspection or any mkdir/mktemp/network operation.
case ${FREEKLAW_RELEASE_COMMIT-} in ????????????????????????????????????????) ;; *) die 'compatibility.lock has no valid Freeklaw release commit; release engineering must set a full 40-hex SHA.' ;; esac
case $FREEKLAW_RELEASE_COMMIT in *[!0-9a-f]*|0000000000000000000000000000000000000000) die 'compatibility.lock Freeklaw release commit is not a valid full lowercase SHA.' ;; esac
FREEKLAW_SKILL_BASE_URL=$FREEKLAW_RAW_REPOSITORY_URL/$FREEKLAW_RELEASE_COMMIT/skills

OS=${FREEKLAW_OS:-$(uname -s)}
ARCH=${FREEKLAW_ARCH:-$(uname -m)}
[ "$OS" = Darwin ] || die "Freeklaw supports macOS only (detected $OS)."
case $ARCH in arm64|x86_64) ;; *) die "Unsupported Mac architecture: $ARCH" ;; esac

PREFIX=${FREEKLAW_PREFIX:-$HOME/.freeklaw}
BIN_DIR=$PREFIX/bin
LIB_DIR=$PREFIX/lib
RUNTIME_DIR=$PREFIX/runtime
NPM_PREFIX=$RUNTIME_DIR/npm
APPLICATIONS_DIR=${FREEKLAW_APPLICATIONS_DIR:-$HOME/Applications}
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
HERMES_INSTALL_DIR=${HERMES_INSTALL_DIR:-$HERMES_HOME/hermes-agent}
HERMES_BIN=$HOME/.local/bin/hermes
AGENT_VAULT_BIN=$NPM_PREFIX/bin/agent-vault
EGO_BROWSER_BIN=$HOME/.local/bin/ego-browser
PROVENANCE_FILE=$RUNTIME_DIR/command-paths
CURL_BIN=${FREEKLAW_CURL_BIN:-curl}
NPM_BIN=${FREEKLAW_NPM_BIN:-npm}
PYTHON3_BIN=${FREEKLAW_PYTHON3_BIN:-}
HDIUTIL_BIN=${FREEKLAW_HDIUTIL_BIN:-hdiutil}
DITTO_BIN=${FREEKLAW_DITTO_BIN:-ditto}
PLUTIL_BIN=${FREEKLAW_PLUTIL_BIN:-plutil}
CODESIGN_BIN=${FREEKLAW_CODESIGN_BIN:-codesign}
SPCTL_BIN=${FREEKLAW_SPCTL_BIN:-spctl}
SHASUM_BIN=${FREEKLAW_SHASUM_BIN:-shasum}
OPENSSL_BIN=${FREEKLAW_OPENSSL_BIN:-openssl}

PYTHONDONTWRITEBYTECODE=1; export PYTHONDONTWRITEBYTECODE
PATH="$BIN_DIR:$NPM_PREFIX/bin:$HOME/.local/bin:$HERMES_HOME/bin:$PATH"
NVM_DIR=${NVM_DIR:-$HOME/.nvm}
for nvm_bin in "$NVM_DIR"/versions/node/*/bin; do [ -d "$nvm_bin" ] && PATH="$nvm_bin:$PATH"; done
export PATH

reject_symlink_or_wrong_type() {
    path=$1 expected=$2
    [ ! -L "$path" ] || die "Refusing symlinked destination: $path"
    [ ! -e "$path" ] && return 0
    case $expected in directory) [ -d "$path" ] || die "Expected a directory destination: $path" ;; file) [ -f "$path" ] || die "Expected a regular-file destination: $path" ;; esac
}

validate_destinations() {
    for directory in "$PREFIX" "$BIN_DIR" "$LIB_DIR" "$RUNTIME_DIR" "$NPM_PREFIX" "$RUNTIME_DIR/python" "$APPLICATIONS_DIR"; do reject_symlink_or_wrong_type "$directory" directory; done
    for file in "$PROVENANCE_FILE" "$BIN_DIR/freeklaw-state" "$BIN_DIR/freeklaw-secret-fill" "$LIB_DIR/freeklaw_state.py" "$LIB_DIR/freeklaw_secret_fill.py" "$HERMES_BIN"; do reject_symlink_or_wrong_type "$file" file; done
}
validate_destinations

version_from_output() { printf '%s\n' "$1" | sed -n 's/^[^0-9]*\([0-9][0-9]*\(\.[0-9][0-9]*\)*\).*$/\1/p' | sed -n '1p'; }

canonical_existing_path() {
    path=$1 loops=0
    [ -e "$path" ] || [ -L "$path" ] || return 1
    while [ -L "$path" ]; do
        loops=$((loops + 1)); [ "$loops" -le 40 ] || return 1
        target=$(readlink "$path") || return 1
        case $target in /*) path=$target ;; *) path=$(dirname "$path")/$target ;; esac
        parent=$(CDPATH='' cd -P -- "$(dirname "$path")" 2>/dev/null && pwd -P) || return 1
        path=$parent/$(basename "$path")
    done
    parent=$(CDPATH='' cd -P -- "$(dirname "$path")" 2>/dev/null && pwd -P) || return 1
    printf '%s/%s\n' "$parent" "$(basename "$path")"
}
path_is_within() { case $1 in "$2"/*) return 0 ;; *) return 1 ;; esac; }
plist_value() { "$PLUTIL_BIN" -extract "$1" raw -o - "$2/Contents/Info.plist" 2>/dev/null; }

signed_by_team() {
    item=$1 expected_identifier=$2
    "$CODESIGN_BIN" --verify --strict "$item" >/dev/null 2>&1 || return 1
    details=$($CODESIGN_BIN -dv --verbose=4 "$item" 2>&1) || return 1
    printf '%s\n' "$details" | grep -F "TeamIdentifier=$EGO_DEVELOPER_TEAM_ID" >/dev/null || return 1
    printf '%s\n' "$details" | grep -F "Identifier=$expected_identifier" >/dev/null || return 1
}

ego_app_path() {
    if [ -n "${FREEKLAW_EGO_APP:-}" ]; then [ -d "$FREEKLAW_EGO_APP" ] && [ ! -L "$FREEKLAW_EGO_APP" ] && { printf '%s\n' "$FREEKLAW_EGO_APP"; return 0; }; return 1; fi
    for candidate in "$APPLICATIONS_DIR/ego lite.app" "/Applications/ego lite.app"; do [ -d "$candidate" ] && [ ! -L "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }; done
    return 1
}

hermes_status=missing hermes_found='' hermes_found_commit=''
if [ -x "$HERMES_BIN" ]; then
    hermes_found=$(version_from_output "$("$HERMES_BIN" --version 2>/dev/null || true)")
    [ ! -d "$HERMES_INSTALL_DIR/.git" ] || hermes_found_commit=$(git -C "$HERMES_INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)
    case " $HERMES_COMPATIBLE_COMMITS " in *" $hermes_found_commit "*) commit_ok=true ;; *) commit_ok=false ;; esac
    launcher_ok=false
    [ ! -L "$HERMES_BIN" ] && grep -F "exec \"$HERMES_INSTALL_DIR/venv/bin/python\" \"$HERMES_INSTALL_DIR/hermes\"" "$HERMES_BIN" >/dev/null 2>&1 && launcher_ok=true
    if [ "$hermes_found" = "$HERMES_CLI_VERSION" ] && [ "$commit_ok" = true ] && [ "$launcher_ok" = true ]; then hermes_status=compatible; else hermes_status=incompatible; hermes_found="${hermes_found:-unknown}/commit:${hermes_found_commit:-unknown}/canonical-launcher:$launcher_ok"; fi
fi

vault_status=missing vault_found='' vault_resolved=''
if [ -x "$AGENT_VAULT_BIN" ]; then
    vault_resolved=$(canonical_existing_path "$AGENT_VAULT_BIN" || true); npm_resolved=$(canonical_existing_path "$NPM_PREFIX" || true)
    vault_found=$(version_from_output "$("$AGENT_VAULT_BIN" --version 2>/dev/null || true)")
    if [ "$vault_found" = "$AGENT_VAULT_VERSION" ] && [ -n "$npm_resolved" ] && path_is_within "$vault_resolved" "$npm_resolved"; then vault_status=compatible; else vault_status=incompatible; vault_found="${vault_found:-unknown}/path:${vault_resolved:-unresolved}"; fi
fi

pyyaml_status=missing pyyaml_found=''
if [ -x "$RUNTIME_DIR/python/bin/python" ]; then pyyaml_found=$("$RUNTIME_DIR/python/bin/python" -B -c 'import yaml; print(yaml.__version__)' 2>/dev/null || true); [ "$pyyaml_found" = "$PYYAML_VERSION" ] && pyyaml_status=compatible || pyyaml_status=incompatible; fi

ego_status=missing ego_found='' ego_existing='' ego_resolved=''
if ego_existing=$(ego_app_path); then
    ego_found=$(plist_value CFBundleShortVersionString "$ego_existing" || true); ego_id=$(plist_value CFBundleIdentifier "$ego_existing" || true)
    if [ "$ego_found" != "$EGO_LITE_VERSION" ] || [ "$ego_id" != "$EGO_LITE_BUNDLE_ID" ] || ! signed_by_team "$ego_existing" "$EGO_LITE_BUNDLE_ID" || ! "$SPCTL_BIN" -a -t exec "$ego_existing" >/dev/null 2>&1; then
        ego_status=incompatible
    elif [ ! -x "$EGO_BROWSER_BIN" ]; then ego_status=setup-incomplete
    else
        ego_resolved=$(canonical_existing_path "$EGO_BROWSER_BIN" || true); app_resolved=$(canonical_existing_path "$ego_existing" || true); ego_cli_version=$(version_from_output "$("$EGO_BROWSER_BIN" --version 2>/dev/null || true)")
        if [ "$ego_cli_version" = "$EGO_LITE_VERSION" ] && [ -n "$app_resolved" ] && path_is_within "$ego_resolved" "$app_resolved" && signed_by_team "$ego_resolved" "$EGO_BROWSER_BUNDLE_ID"; then ego_status=compatible; else ego_status=incompatible; ego_found="$ego_found/launcher:${ego_cli_version:-unknown}/path:${ego_resolved:-unresolved}"; fi
    fi
fi

report_one() { label=$1 status=$2 expected=$3 found=$4; case $status in compatible) printf 'compatible       %-14s %s\n' "$label" "$expected" ;; missing) printf 'missing          %-14s expected %s\n' "$label" "$expected" ;; setup-incomplete) printf 'setup-incomplete %-14s app installed; canonical launcher missing\n' "$label" ;; incompatible) printf 'incompatible     %-14s found %s; expected %s\n' "$label" "${found:-unknown}" "$expected" ;; esac; }
report_one Hermes "$hermes_status" "$HERMES_CLI_VERSION ($HERMES_TAG)" "$hermes_found"
report_one 'ego lite' "$ego_status" "$EGO_LITE_VERSION" "$ego_found"
report_one agent-vault "$vault_status" "$AGENT_VAULT_VERSION" "$vault_found"
report_one PyYAML "$pyyaml_status" "$PYYAML_VERSION" "$pyyaml_found"

all_compatible=true has_incompatible=false has_setup_incomplete=false
for state in "$hermes_status" "$ego_status" "$vault_status" "$pyyaml_status"; do [ "$state" = compatible ] || all_compatible=false; [ "$state" = incompatible ] && has_incompatible=true; [ "$state" = setup-incomplete ] && has_setup_incomplete=true; done
[ "$MODE" = check ] && { [ "$all_compatible" = true ] && exit 0 || exit 1; }
[ "$has_incompatible" = false ] || [ "$MODE" = upgrade ] || die 'An incompatible dependency is installed. Nothing was changed; inspect above, then rerun with --upgrade for an explicitly confirmed replacement.'

confirm() { prompt=$1; if [ "${FREEKLAW_CONFIRM:-}" = YES ]; then say "$prompt [confirmed by FREEKLAW_CONFIRM=YES]"; return 0; fi; [ -t 0 ] || die "$prompt Re-run interactively to confirm."; printf '%s Type YES to continue: ' "$prompt"; IFS= read -r answer; [ "$answer" = YES ] || die 'Confirmation declined; nothing further was changed.'; }
if [ "$all_compatible" = true ]; then say 'All pinned dependencies are compatible; refreshing only the local Freeklaw payload.'; elif [ "$has_incompatible" = true ]; then confirm 'UPGRADE MODE: replace or upgrade the incompatible dependencies listed above?'; elif [ "$has_setup_incomplete" = false ]; then confirm 'Install the missing pinned dependencies?'; fi

ensure_private_dirs() { umask 077; mkdir -p "$BIN_DIR" "$LIB_DIR" "$RUNTIME_DIR"; chmod 700 "$PREFIX" "$BIN_DIR" "$LIB_DIR" "$RUNTIME_DIR"; }

install_hermes() {
    [ "$hermes_status" = compatible ] && return 0
    command -v "$CURL_BIN" >/dev/null 2>&1 || die 'curl is required to install Hermes.'; command -v bash >/dev/null 2>&1 || die 'bash is required by the official Hermes installer.'
    tmp_installer=$(mktemp "${TMPDIR:-/tmp}/freeklaw-hermes.XXXXXX"); trap 'rm -f "$tmp_installer"' EXIT HUP INT TERM
    "$CURL_BIN" -fsSL "$HERMES_INSTALLER_URL" -o "$tmp_installer"
    if [ "$MODE" = upgrade ] && [ "$hermes_status" = incompatible ]; then HERMES_HOME=$HERMES_HOME bash "$tmp_installer" --branch "$HERMES_TAG" --commit "$HERMES_COMMIT" --force-commit --skip-setup --non-interactive; else HERMES_HOME=$HERMES_HOME bash "$tmp_installer" --branch "$HERMES_TAG" --commit "$HERMES_COMMIT" --skip-setup --non-interactive; fi
    rm -f "$tmp_installer"; trap - EXIT HUP INT TERM
    installed=$(version_from_output "$("$HERMES_BIN" --version 2>/dev/null || true)"); installed_commit=$(git -C "$HERMES_INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)
    [ "$installed" = "$HERMES_CLI_VERSION" ] && [ "$installed_commit" = "$HERMES_COMMIT" ] || die 'Hermes post-install version/commit verification failed.'
    grep -F "exec \"$HERMES_INSTALL_DIR/venv/bin/python\" \"$HERMES_INSTALL_DIR/hermes\"" "$HERMES_BIN" >/dev/null || die 'Hermes canonical launcher is not tied to the verified checkout.'
}

install_agent_vault() {
    [ "$vault_status" = compatible ] && return 0
    command -v "$NPM_BIN" >/dev/null 2>&1 || die 'npm is required to install agent-vault.'
    temp_tarball=$(mktemp "${TMPDIR:-/tmp}/freeklaw-agent-vault.XXXXXX"); trap 'rm -f "$temp_tarball"' EXIT HUP INT TERM
    "$CURL_BIN" -fsSL "$AGENT_VAULT_TARBALL_URL" -o "$temp_tarball"
    computed_sri=sha512-$("$OPENSSL_BIN" dgst -sha512 -binary "$temp_tarball" | "$OPENSSL_BIN" base64 -A)
    [ "$computed_sri" = "$AGENT_VAULT_INTEGRITY" ] || die 'agent-vault tarball SRI mismatch; refusing installation.'
    mkdir -p "$NPM_PREFIX"; "$NPM_BIN" install --global --prefix "$NPM_PREFIX" --ignore-scripts --no-audit --no-fund "$temp_tarball"
    rm -f "$temp_tarball"; trap - EXIT HUP INT TERM
    resolved=$(canonical_existing_path "$AGENT_VAULT_BIN" || true); npm_resolved=$(canonical_existing_path "$NPM_PREFIX" || true); installed=$(version_from_output "$("$AGENT_VAULT_BIN" --version 2>/dev/null || true)")
    if [ "$installed" != "$AGENT_VAULT_VERSION" ] || ! path_is_within "$resolved" "$npm_resolved"; then die 'agent-vault canonical path/version verification failed.'; fi
}

install_pyyaml() {
    [ "$pyyaml_status" = compatible ] && return 0
    if [ -z "$PYTHON3_BIN" ] && [ -x "$HERMES_INSTALL_DIR/venv/bin/python3" ]; then PYTHON3_BIN=$HERMES_INSTALL_DIR/venv/bin/python3; elif [ -z "$PYTHON3_BIN" ]; then PYTHON3_BIN=$(command -v python3 || true); fi
    [ -n "$PYTHON3_BIN" ] && [ -x "$PYTHON3_BIN" ] || die 'Python 3.11+ is required for the private Freeklaw runtime.'
    "$PYTHON3_BIN" -B -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info < (3, 15) else 1)' || die 'Python 3.11-3.14 is required by the locked PyYAML wheels.'
    [ -x "$RUNTIME_DIR/python/bin/python" ] || "$PYTHON3_BIN" -B -m venv "$RUNTIME_DIR/python"
    wheel_dir=$(mktemp -d "${TMPDIR:-/tmp}/freeklaw-pyyaml.XXXXXX")
    "$RUNTIME_DIR/python/bin/python" -B -m pip download --disable-pip-version-check --only-binary=:all: --no-deps --dest "$wheel_dir" --index-url "$PYYAML_INDEX_URL" "PyYAML==$PYYAML_VERSION"
    wheel=$(find "$wheel_dir" -type f -name '*.whl' -print); [ -n "$wheel" ] && [ "$(printf '%s\n' "$wheel" | wc -l | tr -d ' ')" = 1 ] || die 'Expected exactly one PyYAML wheel.'
    wheel_hash=$("$SHASUM_BIN" -a 256 "$wheel" | awk '{print $1}'); case " $PYYAML_MACOS_WHEEL_SHA256 " in *" $wheel_hash "*) ;; *) die 'Downloaded PyYAML wheel hash is not locked.' ;; esac
    requirements=$wheel_dir/requirements.txt; printf 'PyYAML @ file://%s --hash=sha256:%s\n' "$wheel" "$wheel_hash" > "$requirements"
    "$RUNTIME_DIR/python/bin/python" -B -m pip install --disable-pip-version-check --no-input --no-index --no-deps --require-hashes -r "$requirements"
    installed=$("$RUNTIME_DIR/python/bin/python" -B -c 'import yaml; print(yaml.__version__)'); [ "$installed" = "$PYYAML_VERSION" ] || die 'PyYAML post-install verification failed.'
    rm -f "$wheel" "$requirements"; rmdir "$wheel_dir"
}

EGO_NEEDS_ONBOARDING=false
install_ego() {
    [ "$ego_status" != missing ] && [ "$ego_status" != incompatible ] && return 0
    warn 'ego lite uses mutable URLs; locked bytes, signing identity, and Gatekeeper acceptance are required.'
    case $ARCH in arm64) dmg_url=$EGO_LITE_ARM64_DMG_URL; dmg_sha=$EGO_LITE_ARM64_DMG_SHA256 ;; x86_64) dmg_url=$EGO_LITE_X86_64_DMG_URL; dmg_sha=$EGO_LITE_X86_64_DMG_SHA256 ;; esac
    temp_root=$(mktemp -d "${TMPDIR:-/tmp}/freeklaw-ego.XXXXXX"); dmg=$temp_root/egolite.dmg; mountpoint=$temp_root/mount; mkdir "$mountpoint"; ego_mounted=false
    cleanup_ego() { [ "$ego_mounted" = false ] || "$HDIUTIL_BIN" detach "$mountpoint" >/dev/null 2>&1 || true; rm -f "$dmg"; rmdir "$mountpoint" "$temp_root" 2>/dev/null || true; }
    trap cleanup_ego EXIT HUP INT TERM
    "$CURL_BIN" -fsSL "$dmg_url" -o "$dmg"; actual_sha=$("$SHASUM_BIN" -a 256 "$dmg" | awk '{print $1}'); [ "$actual_sha" = "$dmg_sha" ] || die 'ego lite DMG SHA-256 mismatch; refusing to mount it.'
    "$HDIUTIL_BIN" attach -nobrowse -readonly -mountpoint "$mountpoint" "$dmg" >/dev/null; ego_mounted=true
    source_app=$mountpoint/ego\ lite.app; [ -d "$source_app" ] && [ ! -L "$source_app" ] || die 'Official ego DMG has no canonical app bundle.'
    [ "$(plist_value CFBundleShortVersionString "$source_app" || true)" = "$EGO_LITE_VERSION" ] || die 'Mounted ego bundle version mismatch.'
    [ "$(plist_value CFBundleIdentifier "$source_app" || true)" = "$EGO_LITE_BUNDLE_ID" ] || die 'Mounted ego bundle identifier mismatch.'
    signed_by_team "$source_app" "$EGO_LITE_BUNDLE_ID" || die 'Mounted ego bundle signature/team verification failed.'; "$SPCTL_BIN" -a -t exec "$source_app" >/dev/null 2>&1 || die 'Gatekeeper rejected the mounted ego bundle.'
    source_cli=$(find "$source_app/Contents/Frameworks" -type f -name ego-browser -print | sed -n '1p'); if [ -z "$source_cli" ] || ! signed_by_team "$source_cli" "$EGO_BROWSER_BUNDLE_ID"; then die 'Embedded ego-browser signature/team verification failed.'; fi
    [ "$(version_from_output "$("$source_cli" --version 2>/dev/null || true)")" = "$EGO_LITE_VERSION" ] || die 'Embedded ego-browser version mismatch.'
    mkdir -p "$APPLICATIONS_DIR"
    target=$APPLICATIONS_DIR/ego\ lite.app; reject_symlink_or_wrong_type "$target" directory
    if [ -e "$target" ]; then [ "$MODE" = upgrade ] || die 'Refusing to replace ego lite without --upgrade.'; backup=$target.freeklaw-backup; [ ! -e "$backup" ] && [ ! -L "$backup" ] || die "Refusing to overwrite ego backup: $backup"; mv "$target" "$backup"; say "Previous ego bundle retained at $backup"; fi
    "$DITTO_BIN" "$source_app" "$target"; "$HDIUTIL_BIN" detach "$mountpoint" >/dev/null; ego_mounted=false; cleanup_ego; trap - EXIT HUP INT TERM
    EGO_NEEDS_ONBOARDING=true
    say 'ego lite app installed, but GUI onboarding must create and validate ~/.local/bin/ego-browser before setup is complete.'
}

atomic_install_file() { source=$1 destination=$2 mode=$3 directory=$(dirname "$destination"); temp=$(mktemp "$directory/.freeklaw-stage.XXXXXX"); cp "$source" "$temp"; chmod "$mode" "$temp"; mv -f "$temp" "$destination"; }
install_local_helpers() {
    for helper in freeklaw_state.py freeklaw_secret_fill.py; do source=$REPO_ROOT/lib/$helper; [ -f "$source" ] && [ ! -L "$source" ] || die "Local helper missing or symlinked: $source"; atomic_install_file "$source" "$LIB_DIR/$helper" 600; done
    for launcher in freeklaw-state freeklaw-secret-fill; do source=$REPO_ROOT/bin/$launcher; [ -f "$source" ] && [ ! -L "$source" ] || die "Local launcher missing or symlinked: $source"; atomic_install_file "$source" "$BIN_DIR/$launcher" 700; done
}
install_instruction_skills() {
    [ -x "$HERMES_BIN" ] || die 'Canonical Hermes launcher is unavailable after installation.'
    for skill_name in freeklaw freeklaw-onboarding; do skill_url=$FREEKLAW_SKILL_BASE_URL/$skill_name/SKILL.md; "$HERMES_BIN" skills inspect "$skill_url"; "$HERMES_BIN" skills install "$skill_url" --name "$skill_name" --yes; done
    say "Installed scanned Freeklaw skills from commit $FREEKLAW_RELEASE_COMMIT (display tag $FREEKLAW_RELEASE_TAG)."
}
record_command_paths() { temp=$(mktemp "$RUNTIME_DIR/.command-paths.XXXXXX"); { printf 'HERMES\t%s\n' "$HERMES_BIN"; printf 'AGENT_VAULT\t%s\n' "$AGENT_VAULT_BIN"; printf 'EGO_BROWSER\t%s\n' "$EGO_BROWSER_BIN"; } > "$temp"; chmod 600 "$temp"; mv -f "$temp" "$PROVENANCE_FILE"; }

ensure_private_dirs
install_hermes
install_agent_vault
install_pyyaml
install_ego
install_local_helpers
install_instruction_skills
record_command_paths

say ''
if [ "$EGO_NEEDS_ONBOARDING" = true ] || [ ! -x "$EGO_BROWSER_BIN" ]; then say 'Freeklaw installation is not complete: ego lite GUI onboarding and launcher smoke check remain.'; say "  1. Open '$APPLICATIONS_DIR/ego lite.app' and finish official onboarding: $EGO_LITE_DOWNLOAD_PAGE"; say '  2. Re-run: ./install.sh --check'; exit 2; fi
say 'Freeklaw dependency installation is complete and canonical command paths were verified.'
say 'Next, complete the user-controlled setup (the installer never reads or stores secrets):'
say "  1. In your own terminal, run: \"$AGENT_VAULT_BIN\" init"
say "  2. Create your Photon project at $PHOTON_SETUP_URL"
say '  3. In your own terminal, run: hermes photon setup --phone YOUR_PHONE_NUMBER'
say '  4. If needed, run hermes gateway install, then hermes gateway start.'
say '  5. Text the assigned Photon number once and verify the inbound message.'
say 'No Hermes model/profile/settings, gateway, or Photon secret was modified.'
