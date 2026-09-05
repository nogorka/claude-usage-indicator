#!/usr/bin/env bash
# Тесты install.sh/uninstall.sh — оркестрация (симлинк хука, systemd --user
# юнит, блок statusLine через scripts/patch-settings.py). Обычный shell-скрипт,
# гоняется напрямую: bash tests/test_install_uninstall.sh.
#
# Внутреннюю логику json-правки patch-settings.py тестирует
# tests/test_patch_settings.py — здесь только оркестрация install.sh/uninstall.sh
# поверх неё: порядок шагов, идемпотентность, dry-run, ветки симлинка хука.
set -uo pipefail   # без -e: хотим собрать все провалы, а не падать на первом

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_SH="$ROOT_DIR/install.sh"
UNINSTALL_SH="$ROOT_DIR/uninstall.sh"
HOOK_SOURCE="$ROOT_DIR/bin/claude-statusline.sh"
UNIT_NAME="claude-usage-indicator.service"

pass_count=0
fail_count=0

fail() { echo "FAIL: $1"; fail_count=$((fail_count + 1)); }
pass() { pass_count=$((pass_count + 1)); }

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then pass
    else fail "$desc: ожидалось [$expected], получено [$actual]"; fi
}

assert_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then pass
    else fail "$desc: ожидалась подстрока [$needle] в [$haystack]"; fi
}

assert_not_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "$haystack" != *"$needle"* ]]; then pass
    else fail "$desc: не ожидалась подстрока [$needle] в [$haystack]"; fi
}

assert_exists() {
    local desc="$1" path="$2"
    if [[ -e "$path" || -L "$path" ]]; then pass
    else fail "$desc: $path должен существовать"; fi
}

assert_absent() {
    local desc="$1" path="$2"
    if [[ ! -e "$path" && ! -L "$path" ]]; then pass
    else fail "$desc: $path не должен существовать"; fi
}

assert_symlink_target() {
    local desc="$1" link="$2" expected_target="$3"
    if [[ ! -L "$link" ]]; then fail "$desc: $link должен быть симлинком"; return; fi
    assert_eq "$desc: цель симлинка" "$expected_target" "$(readlink -f "$link")"
}

assert_regular_file() {
    local desc="$1" path="$2"
    if [[ -f "$path" && ! -L "$path" ]]; then pass
    else fail "$desc: $path должен быть обычным файлом"; fi
}

# Лог стаба systemctl: одна строка на вызов (см. write_systemctl_stub).
assert_systemctl_log_empty() {
    local desc="$1" log="$2"
    if [[ ! -s "$log" ]]; then pass
    else fail "$desc: лог systemctl должен быть пуст, получено: $(cat "$log")"; fi
}

# ---------------------------------------------------------------------------
# Харнесс: temp-каталог на тест, стаб systemctl на $PATH, реальный python3
# ---------------------------------------------------------------------------

if [[ ! -x /usr/bin/python3 ]]; then
    echo "test_install_uninstall.sh: /usr/bin/python3 недоступен — install.sh/uninstall.sh" >&2
    echo "жёстко от него зависят (PYTHON=/usr/bin/python3), тесты не могут продолжаться." >&2
    exit 1
fi

declare -a TMP_DIRS=()

# Плоская mktemp-регистрация в TMP_DIRS: вызывается как обычная команда (не
# через $(...)), иначе присвоение массиву осело бы в подшелле и очистка бы
# не увидела созданный каталог.
new_tmp_dir() {
    NEW_TMP_DIR="$(mktemp -d)"
    TMP_DIRS+=("$NEW_TMP_DIR")
}

cleanup_tmp_dirs() {
    local d
    for d in "${TMP_DIRS[@]:-}"; do
        [[ -n "$d" && -d "$d" ]] && rm -rf "$d"
    done
}
trap cleanup_tmp_dirs EXIT

new_tmp_dir
STUB_BIN_DIR="$NEW_TMP_DIR"
cat > "$STUB_BIN_DIR/systemctl" <<'STUB_EOF'
#!/usr/bin/env bash
# Стаб: реальный systemd не трогаем, только логируем аргументы вызова.
echo "$@" >> "${SYSTEMCTL_LOG:?SYSTEMCTL_LOG is not set}"
exit 0
STUB_EOF
chmod +x "$STUB_BIN_DIR/systemctl"

hook_link_of() { printf '%s/.local/bin/claude-statusline.sh' "$1"; }
unit_dest_of() { printf '%s/.config/systemd/user/%s' "$1" "$UNIT_NAME"; }
unit_dir_of() { printf '%s/.config/systemd/user' "$1"; }
settings_path_of() { printf '%s/.claude/settings.json' "$1"; }
backup_path_of() { printf '%s/.claude/settings.json.bak' "$1"; }
systemctl_log_of() { printf '%s/.systemctl.log' "$1"; }

# Прогоняет install.sh/uninstall.sh на изолированном $HOME со стаб-systemctl
# на $PATH; кладёт результат в глобальные RUN_*. PYTHON=/usr/bin/python3
# в install.sh/uninstall.sh — абсолютный путь, наш стаб на $PATH его не
# затрагивает, реальный python3 остаётся системным.
run_script() {
    local script="$1" home_dir="$2"; shift 2
    local log; log="$(systemctl_log_of "$home_dir")"
    local stderr_file="$home_dir/.stderr"
    : > "$log"
    local out
    out="$(HOME="$home_dir" PATH="$STUB_BIN_DIR:$PATH" SYSTEMCTL_LOG="$log" bash "$script" "$@" 2>"$stderr_file")"
    RUN_EXIT=$?
    RUN_STDOUT="$out"
    RUN_STDERR="$(cat "$stderr_file")"
    RUN_SYSTEMCTL_LOG="$log"
}

# Прогоняет install.sh начисто на $1 и падает видимой ошибкой харнеса, если
# сама установка не удалась — иначе тесты поверх такого фикстура молча
# проверяли бы не то, что думают.
ensure_installed() {
    local home_dir="$1"
    run_script "$INSTALL_SH" "$home_dir"
    if [[ "$RUN_EXIT" -ne 0 ]]; then
        echo "HARNESS ERROR: не удалось подготовить установленный \$HOME: exit=$RUN_EXIT stderr=$RUN_STDERR" >&2
        exit 2
    fi
}

# =============================================================================
# install.sh — link_hook(): 3 ветки функции
# =============================================================================

test_link_hook_already_correct() {
    local desc="link_hook: симлинк уже корректен"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin"
    ln -s "$HOOK_SOURCE" "$(hook_link_of "$home")"
    # Бэкдейтим mtime симлинка — если бы код ошибочно перезаписывал его через
    # ln -sfn, mtime стал бы "сейчас" и отличался бы от бэкдейченного.
    touch -h -d '@1000000000' "$(hook_link_of "$home")"
    local mtime_before; mtime_before="$(stat -c '%Y' "$(hook_link_of "$home")")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение" "$RUN_STDOUT" "hook already installed"
    assert_not_contains "$desc: не переуказывается" "$RUN_STDOUT" "re-pointed"
    assert_symlink_target "$desc" "$(hook_link_of "$home")" "$HOOK_SOURCE"
    assert_eq "$desc: mtime симлинка не изменился" "$mtime_before" "$(stat -c '%Y' "$(hook_link_of "$home")")"
}

test_link_hook_repoint() {
    local desc="link_hook: симлинк указывает на другой чекаут -> переуказывается"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin" "$home/other-repo/bin"
    touch "$home/other-repo/bin/claude-statusline.sh"
    ln -s "$home/other-repo/bin/claude-statusline.sh" "$(hook_link_of "$home")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение" "$RUN_STDOUT" "re-pointed"
    assert_symlink_target "$desc" "$(hook_link_of "$home")" "$HOOK_SOURCE"
}

test_link_hook_blocked_by_regular_file() {
    local desc="link_hook: обычный файл на месте симлинка -> фатально"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin"
    printf 'не симлинк\n' > "$(hook_link_of "$home")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "1" "$RUN_EXIT"
    assert_contains "$desc: сообщение в stderr" "$RUN_STDERR" "already exists and is not a symlink"
    assert_regular_file "$desc: файл остался обычным" "$(hook_link_of "$home")"
    assert_eq "$desc: содержимое не тронуто" "не симлинк" "$(cat "$(hook_link_of "$home")")"
    # Ничего дальше не выполняется: install_unit и enable_unit не достигнуты.
    assert_absent "$desc: unit-каталог не создан" "$(unit_dir_of "$home")"
    assert_systemctl_log_empty "$desc: systemctl не вызван" "$RUN_SYSTEMCTL_LOG"
}

# =============================================================================
# install.sh — happy path и общие свойства
# =============================================================================

test_install_fresh_full() {
    local desc="install: полная свежая установка"
    new_tmp_dir; local home="$NEW_TMP_DIR"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: финальное сообщение" "$RUN_STDOUT" "Done."
    assert_symlink_target "$desc" "$(hook_link_of "$home")" "$HOOK_SOURCE"

    local unit_dest; unit_dest="$(unit_dest_of "$home")"
    assert_regular_file "$desc: unit-файл записан" "$unit_dest"
    assert_contains "$desc: PYTHONPATH подставлен" "$(cat "$unit_dest")" "Environment=PYTHONPATH=$ROOT_DIR/src"
    assert_not_contains "$desc: плейсхолдер заменён" "$(cat "$unit_dest")" "@@PYTHONPATH@@"

    local settings; settings="$(settings_path_of "$home")"
    local status_line; status_line="$(jq -c '.statusLine' "$settings" 2>/dev/null)"
    assert_eq "$desc: statusLine в settings.json" \
        "{\"type\":\"command\",\"command\":\"$(hook_link_of "$home")\"}" "$status_line"

    assert_eq "$desc: systemctl daemon-reload" "--user daemon-reload" "$(sed -n '1p' "$RUN_SYSTEMCTL_LOG")"
    assert_eq "$desc: systemctl enable --now" "--user enable --now $UNIT_NAME" "$(sed -n '2p' "$RUN_SYSTEMCTL_LOG")"
    assert_eq "$desc: ровно 2 вызова systemctl" "2" "$(wc -l < "$RUN_SYSTEMCTL_LOG" | tr -d ' ')"
}

test_install_no_leftover_tmp_files_in_unit_dir() {
    local desc="install: в unit-каталоге нет посторонних временных файлов"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"
    local extra
    extra="$(find "$(unit_dir_of "$home")" -maxdepth 1 -type f ! -name "$UNIT_NAME" | wc -l)"
    assert_eq "$desc" "0" "$extra"
}

test_install_idempotent_rerun() {
    local desc="install: повторный прогон идемпотентен"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"

    local unit_dest; unit_dest="$(unit_dest_of "$home")"
    local settings; settings="$(settings_path_of "$home")"
    local unit_before settings_before target_before
    unit_before="$(cat "$unit_dest")"
    settings_before="$(cat "$settings")"
    target_before="$(readlink -f "$(hook_link_of "$home")")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: хук распознан как уже установленный" "$RUN_STDOUT" "hook already installed"
    assert_not_contains "$desc: без переуказывания" "$RUN_STDOUT" "re-pointed"
    assert_contains "$desc: patch-settings без изменений" "$RUN_STDOUT" "no changes needed"
    assert_eq "$desc: unit-файл не изменился" "$unit_before" "$(cat "$unit_dest")"
    assert_eq "$desc: settings.json не изменился" "$settings_before" "$(cat "$settings")"
    assert_eq "$desc: цель симлинка не изменилась" "$target_before" "$(readlink -f "$(hook_link_of "$home")")"
}

test_install_dry_run_creates_nothing() {
    local desc="install --dry-run: пустой \$HOME остаётся пустым"
    new_tmp_dir; local home="$NEW_TMP_DIR"

    run_script "$INSTALL_SH" "$home" --dry-run

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: план в stdout" "$RUN_STDOUT" "[dry-run]"
    assert_absent "$desc: симлинк не создан" "$(hook_link_of "$home")"
    assert_absent "$desc: unit-каталог не создан" "$(unit_dir_of "$home")"
    assert_absent "$desc: settings.json не создан" "$(settings_path_of "$home")"
    assert_systemctl_log_empty "$desc: systemctl не вызван" "$RUN_SYSTEMCTL_LOG"
}

test_install_unknown_flag() {
    local desc="install: неизвестный флаг"
    new_tmp_dir; local home="$NEW_TMP_DIR"

    run_script "$INSTALL_SH" "$home" --bogus-flag

    assert_eq "$desc: exit" "1" "$RUN_EXIT"
    assert_contains "$desc: сообщение в stderr" "$RUN_STDERR" "unknown argument"
    assert_absent "$desc: ничего не создано" "$(hook_link_of "$home")"
}

# Самое важное свойство оркестрации: patch_settings идёт первой строкой
# исполнения (после определения функций). Падение реально воспроизведено
# через StatusLineConflict из scripts/patch-settings.py::plan_install —
# без моков, чужой блок statusLine реально лежит в settings.json на диске.
test_install_order_guarantee_conflict_aborts_before_side_effects() {
    local desc="install: конфликт statusLine останавливает всё до link_hook/install_unit/enable_unit"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.claude"
    local settings; settings="$(settings_path_of "$home")"
    printf '{"statusLine": {"type": "command", "command": "/opt/other/hook.sh"}}' > "$settings"
    local before; before="$(cat "$settings")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "1" "$RUN_EXIT"
    assert_contains "$desc: сообщение о конфликте" "$RUN_STDERR" "statusLine is already set"
    assert_eq "$desc: settings.json не изменился" "$before" "$(cat "$settings")"
    assert_absent "$desc: симлинк не создан" "$(hook_link_of "$home")"
    assert_absent "$desc: unit-каталог не создан" "$(unit_dir_of "$home")"
    assert_systemctl_log_empty "$desc: systemctl не вызван" "$RUN_SYSTEMCTL_LOG"
}

test_install_preserves_existing_settings_keys() {
    local desc="install: чужие ключи settings.json переживают установку"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.claude"
    printf '{"theme": "dark"}' > "$(settings_path_of "$home")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    local theme; theme="$(jq -r '.theme' "$(settings_path_of "$home")")"
    assert_eq "$desc: theme сохранён" "dark" "$theme"
    local has_status_line; has_status_line="$(jq -r 'has("statusLine")' "$(settings_path_of "$home")")"
    assert_eq "$desc: statusLine добавлен" "true" "$has_status_line"
}

test_install_unit_file_regenerated_not_appended() {
    local desc="install: устаревший unit-файл перезаписывается целиком, не дополняется"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$(unit_dir_of "$home")"
    printf 'устаревшее содержимое от другого REPO_ROOT\n' > "$(unit_dest_of "$home")"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    local content; content="$(cat "$(unit_dest_of "$home")")"
    assert_not_contains "$desc: старое содержимое исчезло" "$content" "устаревшее содержимое"
    assert_contains "$desc: новое содержимое актуально" "$content" "Environment=PYTHONPATH=$ROOT_DIR/src"
}

# =============================================================================
# uninstall.sh — disable_unit()/remove_unit_file()/unlink_hook(): симметричные ветки
# =============================================================================

test_uninstall_unit_absent_is_noop_for_disable_and_remove() {
    local desc="uninstall: юнит-файл отсутствует -> disable_unit и remove_unit_file не трогают systemctl"
    new_tmp_dir; local home="$NEW_TMP_DIR"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение disable_unit" "$RUN_STDOUT" "unit file missing, skipping"
    assert_not_contains "$desc: disable не вызван" "$RUN_STDOUT" "disable"
    assert_systemctl_log_empty "$desc: systemctl не вызван вовсе" "$RUN_SYSTEMCTL_LOG"
    assert_absent "$desc: unit-файл по-прежнему отсутствует" "$(unit_dest_of "$home")"
}

test_uninstall_unlink_hook_missing_symlink() {
    local desc="unlink_hook: симлинк отсутствует вовсе"
    new_tmp_dir; local home="$NEW_TMP_DIR"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение" "$RUN_STDOUT" "hook symlink missing, skipping"
}

test_uninstall_unlink_hook_blocked_by_regular_file() {
    local desc="unlink_hook: обычный файл на месте симлинка -> не трогаю, но не фатально"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin"
    printf 'не симлинок\n' > "$(hook_link_of "$home")"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение в stderr" "$RUN_STDERR" "exists but is not a symlink — leaving it alone"
    assert_contains "$desc: скрипт всё равно доходит до конца" "$RUN_STDOUT" "Done."
    assert_regular_file "$desc: файл остался обычным" "$(hook_link_of "$home")"
    assert_eq "$desc: содержимое не тронуто" "не симлинок" "$(cat "$(hook_link_of "$home")")"
}

test_uninstall_unlink_hook_points_elsewhere() {
    local desc="unlink_hook: симлинк на другой репозиторий -> не трогаю"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin" "$home/other-repo/bin"
    touch "$home/other-repo/bin/claude-statusline.sh"
    ln -s "$home/other-repo/bin/claude-statusline.sh" "$(hook_link_of "$home")"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: сообщение в stderr" "$RUN_STDERR" "points to a different repository"
    assert_symlink_target "$desc: симлинк не тронут" "$(hook_link_of "$home")" \
        "$(readlink -f "$home/other-repo/bin/claude-statusline.sh")"
}

test_uninstall_unlink_hook_removes_correct_symlink() {
    local desc="unlink_hook: симлинк на \$HOOK_SOURCE удаляется"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    mkdir -p "$home/.local/bin"
    ln -s "$HOOK_SOURCE" "$(hook_link_of "$home")"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_absent "$desc: симлинк удалён" "$(hook_link_of "$home")"
}

# =============================================================================
# uninstall.sh — happy path и общие свойства
# =============================================================================

test_uninstall_full_teardown_after_install() {
    local desc="uninstall: полный снос полностью установленного состояния"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"
    local settings_before; settings_before="$(cat "$(settings_path_of "$home")")"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: финальное сообщение" "$RUN_STDOUT" "Done."

    local has_status_line; has_status_line="$(jq -r 'has("statusLine")' "$(settings_path_of "$home")")"
    assert_eq "$desc: statusLine снят" "false" "$has_status_line"
    assert_absent "$desc: unit-файл удалён" "$(unit_dest_of "$home")"
    assert_absent "$desc: симлинк удалён" "$(hook_link_of "$home")"

    assert_regular_file "$desc: резервная копия settings.json создана" "$(backup_path_of "$home")"
    assert_eq "$desc: резервная копия хранит содержимое до правки" "$settings_before" "$(cat "$(backup_path_of "$home")")"

    assert_contains "$desc: systemctl disable --now" "$(cat "$RUN_SYSTEMCTL_LOG")" "--user disable --now $UNIT_NAME"
    assert_contains "$desc: systemctl daemon-reload" "$(cat "$RUN_SYSTEMCTL_LOG")" "--user daemon-reload"
}

test_uninstall_preserves_other_settings_keys() {
    local desc="uninstall: чужие ключи settings.json переживают снятие statusLine"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"
    local settings; settings="$(settings_path_of "$home")"
    local merged; merged="$(jq '. + {theme: "dark"}' "$settings")"
    printf '%s' "$merged" > "$settings"

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    local theme; theme="$(jq -r '.theme' "$settings")"
    assert_eq "$desc: theme сохранён" "dark" "$theme"
    local has_status_line; has_status_line="$(jq -r 'has("statusLine")' "$settings")"
    assert_eq "$desc: statusLine снят" "false" "$has_status_line"
}

test_uninstall_dry_run_removes_nothing() {
    local desc="uninstall --dry-run: полностью установленное состояние остаётся нетронутым"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"

    local unit_dest; unit_dest="$(unit_dest_of "$home")"
    local settings; settings="$(settings_path_of "$home")"
    local unit_before settings_before target_before
    unit_before="$(cat "$unit_dest")"
    settings_before="$(cat "$settings")"
    target_before="$(readlink -f "$(hook_link_of "$home")")"

    run_script "$UNINSTALL_SH" "$home" --dry-run

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: план в stdout" "$RUN_STDOUT" "[dry-run]"
    assert_exists "$desc: симлинк остался" "$(hook_link_of "$home")"
    assert_eq "$desc: цель симлинка не изменилась" "$target_before" "$(readlink -f "$(hook_link_of "$home")")"
    assert_eq "$desc: unit-файл не изменился" "$unit_before" "$(cat "$unit_dest")"
    assert_eq "$desc: settings.json не изменился" "$settings_before" "$(cat "$settings")"
    assert_systemctl_log_empty "$desc: systemctl не вызван" "$RUN_SYSTEMCTL_LOG"
}

test_uninstall_idempotent_on_already_removed() {
    local desc="uninstall: повторный прогон на уже снятом состоянии идемпотентен"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"
    run_script "$UNINSTALL_SH" "$home"
    if [[ "$RUN_EXIT" -ne 0 ]]; then
        fail "$desc: первый прогон uninstall.sh должен был пройти (harness)"; return
    fi

    run_script "$UNINSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_contains "$desc: unpatch_settings без изменений" "$RUN_STDOUT" "no changes needed"
    assert_contains "$desc: disable_unit: юнит-файл отсутствует" "$RUN_STDOUT" "unit file missing, skipping"
    assert_contains "$desc: unlink_hook: симлинк отсутствует" "$RUN_STDOUT" "hook symlink missing, skipping"
    assert_contains "$desc: финальное сообщение" "$RUN_STDOUT" "Done."
    assert_systemctl_log_empty "$desc: systemctl не вызван на втором прогоне" "$RUN_SYSTEMCTL_LOG"
}

test_uninstall_unknown_flag() {
    local desc="uninstall: неизвестный флаг"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    ensure_installed "$home"
    local settings_before; settings_before="$(cat "$(settings_path_of "$home")")"

    run_script "$UNINSTALL_SH" "$home" --bogus-flag

    assert_eq "$desc: exit" "1" "$RUN_EXIT"
    assert_contains "$desc: сообщение в stderr" "$RUN_STDERR" "unknown argument"
    assert_eq "$desc: settings.json не тронут" "$settings_before" "$(cat "$(settings_path_of "$home")")"
    assert_exists "$desc: симлинк не тронут" "$(hook_link_of "$home")"
}

main() {
    test_link_hook_already_correct
    test_link_hook_repoint
    test_link_hook_blocked_by_regular_file

    test_install_fresh_full
    test_install_no_leftover_tmp_files_in_unit_dir
    test_install_idempotent_rerun
    test_install_dry_run_creates_nothing
    test_install_unknown_flag
    test_install_order_guarantee_conflict_aborts_before_side_effects
    test_install_preserves_existing_settings_keys
    test_install_unit_file_regenerated_not_appended

    test_uninstall_unit_absent_is_noop_for_disable_and_remove
    test_uninstall_unlink_hook_missing_symlink
    test_uninstall_unlink_hook_blocked_by_regular_file
    test_uninstall_unlink_hook_points_elsewhere
    test_uninstall_unlink_hook_removes_correct_symlink

    test_uninstall_full_teardown_after_install
    test_uninstall_preserves_other_settings_keys
    test_uninstall_dry_run_removes_nothing
    test_uninstall_idempotent_on_already_removed
    test_uninstall_unknown_flag

    echo "---"
    echo "pass=$pass_count fail=$fail_count"
    if (( fail_count > 0 )); then
        exit 1
    fi
    exit 0
}

main
