#!/usr/bin/env bash
# Снимает всё, что ставит install.sh: блок statusLine, systemd --user юнит,
# симлинк хука. Порядок обратный install.sh — сначала статус-строка перестаёт
# писать состояние, потом гасится демон, потом убирается симлинк.
#
# Идемпотентен: повторный запуск на уже снятой установке ничего не ломает.
# --dry-run печатает план действий и не трогает диск (см. install.sh).
set -euo pipefail

PYTHON=/usr/bin/python3
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *)
            echo "uninstall.sh: неизвестный аргумент: $arg" >&2
            exit 1
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_SOURCE="$REPO_ROOT/bin/claude-statusline.sh"
HOOK_LINK="$HOME/.local/bin/claude-statusline.sh"
UNIT_NAME="claude-usage-indicator.service"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_DEST="$UNIT_DIR/$UNIT_NAME"

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

unpatch_settings() {
    local args=(uninstall --command "$HOOK_LINK")
    [[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
    "$PYTHON" "$REPO_ROOT/scripts/patch-settings.py" "${args[@]}"
}

disable_unit() {
    # Юнит-файла нет — значит, systemd про нас никогда не знал, disable
    # на неизвестном юниту systemd имени завершится ошибкой под set -e.
    if [[ ! -f "$UNIT_DEST" ]]; then
        echo "юнит-файл отсутствует, пропускаю: $UNIT_DEST"
        return
    fi
    run systemctl --user disable --now "$UNIT_NAME"
}

remove_unit_file() {
    if [[ ! -e "$UNIT_DEST" ]]; then
        return
    fi
    run rm -f "$UNIT_DEST"
    run systemctl --user daemon-reload
}

unlink_hook() {
    if [[ ! -L "$HOOK_LINK" ]]; then
        if [[ -e "$HOOK_LINK" ]]; then
            echo "uninstall.sh: $HOOK_LINK существует, но не симлинок — не трогаю" >&2
        else
            echo "симлинк хука отсутствует, пропускаю: $HOOK_LINK"
        fi
        return
    fi
    local current
    current="$(readlink -f "$HOOK_LINK")"
    if [[ "$current" != "$HOOK_SOURCE" ]]; then
        echo "uninstall.sh: $HOOK_LINK указывает на другой репозиторий ($current) — не трогаю" >&2
        return
    fi
    run rm -f "$HOOK_LINK"
}

unpatch_settings
disable_unit
remove_unit_file
unlink_hook

echo "Готово."
