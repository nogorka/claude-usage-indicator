#!/usr/bin/env bash
# Установка claude-usage-indicator: симлинк хука в ~/.local/bin, systemd --user
# юнит демона, блок statusLine в ~/.claude/settings.json. Ничего не копируется
# в систему — только симлинки и один сгенерированный юнит-файл.
#
# Идемпотентен: повторный запуск не плодит дублей и не портит уже установленное.
# --dry-run печатает план действий и не трогает диск — так install.sh проверяется
# без реальной установки.
set -euo pipefail

PYTHON=/usr/bin/python3
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *)
            echo "install.sh: unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_SOURCE="$REPO_ROOT/bin/claude-statusline.sh"
HOOK_LINK="$HOME/.local/bin/claude-statusline.sh"
UNIT_NAME="claude-usage-indicator.service"
UNIT_TEMPLATE="$REPO_ROOT/systemd/$UNIT_NAME.in"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_DEST="$UNIT_DIR/$UNIT_NAME"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/claude-usage"

# Обёртка мутирующих команд: в dry-run печатает команду вместо выполнения,
# чтобы план был виден построчно тем же кодом, что реально исполняется.
run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

link_hook() {
    run mkdir -p "$(dirname "$HOOK_LINK")"
    if [[ -L "$HOOK_LINK" ]]; then
        local current
        current="$(readlink -f "$HOOK_LINK")"
        if [[ "$current" == "$HOOK_SOURCE" ]]; then
            echo "hook already installed: $HOOK_LINK -> $HOOK_SOURCE"
            return
        fi
        echo "hook re-pointed to the current repository: $current -> $HOOK_SOURCE"
        run ln -sfn "$HOOK_SOURCE" "$HOOK_LINK"
        return
    fi
    if [[ -e "$HOOK_LINK" ]]; then
        echo "install.sh: $HOOK_LINK already exists and is not a symlink — leaving it alone" >&2
        exit 1
    fi
    run ln -s "$HOOK_SOURCE" "$HOOK_LINK"
}

install_unit() {
    run mkdir -p "$UNIT_DIR"
    local rendered
    rendered="$(sed "s|@@PYTHONPATH@@|$REPO_ROOT/src|g" "$UNIT_TEMPLATE")"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[dry-run] would write unit %s (PYTHONPATH=%s/src)\n' "$UNIT_DEST" "$REPO_ROOT"
        return
    fi
    # Юнит генерируется целиком — перезапись существующего файла безопасна
    # и идемпотентна, содержимое детерминировано зависит только от REPO_ROOT.
    local tmp
    tmp="$(mktemp "$UNIT_DIR/.$UNIT_NAME.XXXXXX")"
    printf '%s\n' "$rendered" > "$tmp"
    mv -f "$tmp" "$UNIT_DEST"
    echo "unit written: $UNIT_DEST"
}

enable_unit() {
    run systemctl --user daemon-reload
    run systemctl --user enable --now "$UNIT_NAME"
}

# Уборка после перехода на схему 2 (файл состояния на профиль): старый
# общий latest.json рядом со свежим default.json дублировал бы default в
# панели. Единственный файл состояния не трогается — апгрейд без хотя бы
# одного запуска хука на схеме 2 не должен стирать последний известный снимок.
cleanup_legacy_state() {
    local legacy="$STATE_DIR/latest.json" default_state="$STATE_DIR/default.json"
    if [[ ! -e "$legacy" || ! -e "$default_state" ]]; then
        return
    fi
    run rm -f "$legacy"
    echo "legacy state file removed: $legacy"
}

patch_settings() {
    local args=(install --command "$HOOK_LINK")
    [[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
    "$PYTHON" "$REPO_ROOT/scripts/patch-settings.py" "${args[@]}"
}

# Сначала самый вероятный сбой (statusLine занят кем-то ещё) — если он
# упадёт, симлинк и юнит остаются нетронутыми, автозапуска не будет вовсе.
patch_settings
link_hook
install_unit
enable_unit
cleanup_legacy_state

echo "Done. Status: systemctl --user status $UNIT_NAME"
