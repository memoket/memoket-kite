#!/usr/bin/env bash
set -euo pipefail
umask 077

# Use a fixed runtime so the packaged skill and CLI contracts stay in sync.
runtime_revision=f61cfd719c9a1189626821a4291bcd3217547a35
runtime="memoket-kite[agent] @ https://github.com/memoket/memoket-kite/archive/${runtime_revision}.tar.gz"
uv_version=0.11.16
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/kite/codex"

case "$(uname -s)" in
    Darwin|Linux) ;;
    *) printf '%s\n' 'KITE for Codex requires macOS or Linux.' >&2; exit 1 ;;
esac

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum
    else
        shasum -a 256
    fi
}

plugin_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)
bundled=("$plugin_dir"/runtime/memoket_kite-*-py3-none-any.whl)
if [[ -f "${bundled[0]:-}" ]]; then
    if (( ${#bundled[@]} != 1 )); then
        printf '%s\n' 'KITE needs exactly one bundled runtime wheel.' >&2
        exit 1
    fi
    fingerprint=$(sha256 < "${bundled[0]}")
    runtime_revision="${fingerprint%% *}"
    runtime="${bundled[0]}[agent]"
fi
runtime_dir="$cache_dir/runtime-$runtime_revision"
runtime_bin="$runtime_dir/bin/kite-tool"

find_uv() {
    local candidate
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    return 1
}

install_uv() (
    local directory="$1" staging
    mkdir -p "$directory" || exit 1
    staging=$(mktemp -d "$directory/setup.XXXXXXXX") || exit 1
    trap 'rm -rf "$staging"' EXIT
    curl -fsSL --connect-timeout 10 --max-time 120 --retry 2 \
        "https://astral.sh/uv/$uv_version/install.sh" -o "$staging/install.sh" || exit 1
    UV_UNMANAGED_INSTALL="$staging/bin" /bin/sh "$staging/install.sh" >&2 || exit 1
    mv -f "$staging/bin/uv" "$directory/uv"
)

if [[ -z "${KITE_DATA_DIR:-}" ]]; then
    project=$(pwd -P)
    if git_dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
        project=$(cd "$git_dir" && pwd -P)
    fi
    checksum=$(printf '%s' "$project" | sha256)
    export KITE_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kite/codex/${checksum%% *}"
fi

if [[ ! -x "$runtime_bin" ]]; then
    if [[ "${KITE_SKIP_BOOTSTRAP:-}" = 1 ]]; then
        exit 1
    fi
    if ! uv_bin=$(find_uv); then
        uv_dir="$cache_dir/uv-$uv_version"
        uv_bin="$uv_dir/uv"
        if [[ ! -x "$uv_bin" ]] && ! install_uv "$uv_dir"; then
            printf '%s\n' 'KITE could not install uv. Check network access or install uv from https://docs.astral.sh/uv/.' >&2
            exit 1
        fi
    fi
    UV_TOOL_DIR="$runtime_dir/tools" UV_TOOL_BIN_DIR="$runtime_dir/bin" \
        "$uv_bin" tool install --no-config --python 3.12 "$runtime" >&2
fi

export PYTHONDONTWRITEBYTECODE=1
exec "$runtime_bin" "$@"
