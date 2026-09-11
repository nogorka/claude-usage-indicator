#!/usr/bin/env bash
# Тесты хука bin/claude-statusline.sh. Обычный shell-скрипт (bash + jq +
# coreutils), без pytest — гоняется напрямую: tests/test_statusline.sh.
set -uo pipefail   # без -e: хотим собрать все провалы, а не падать на первом

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT_DIR/bin/claude-statusline.sh"
FIXTURES="$ROOT_DIR/tests/fixtures"

pass_count=0
fail_count=0

fail() { echo "FAIL: $1"; fail_count=$((fail_count + 1)); }
pass() { pass_count=$((pass_count + 1)); }

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then pass
    else fail "$desc: ожидалось [$expected], получено [$actual]"; fi
}

# Прогоняет хук на входе $1 в свежем временном каталоге состояния и кладёт
# результат в глобальные HOOK_*. Свежий каталог на кейс — тесты не текут
# друг в друга и заодно проверяют автосоздание каталога состояния.
# CLAUDE_CONFIG_DIR/CLAUDE_USAGE_PROFILE_LABEL сняты явно (env -u), а не
# просто не заданы здесь: они могут реально сидеть в окружении, где гоняются
# тесты (у владелицы репозитория второй профиль заведён), и без -u вывод
# хука тайком перестал бы быть "default" — тесты, ожидающие default,
# ловили бы это не как свой провал, а как чужую переменную окружения.
run_hook() {
    local input="$1"
    HOOK_TMP_DIR="$(mktemp -d)"
    HOOK_STATE_FILE="$HOOK_TMP_DIR/state/claude-usage/latest.json"
    HOOK_STDOUT="$(printf '%s' "$input" | env -u CLAUDE_CONFIG_DIR -u CLAUDE_USAGE_PROFILE_LABEL \
        CLAUDE_USAGE_STATE="$HOOK_STATE_FILE" "$HOOK" 2>"$HOOK_TMP_DIR/stderr")"
    HOOK_EXIT=$?
    HOOK_STDERR="$(cat "$HOOK_TMP_DIR/stderr")"
}

# Общие для всех кейсов инварианты контракта: код возврата 0 и тишина в stderr.
assert_common() {
    local desc="$1"
    assert_eq "$desc: exit code" "0" "$HOOK_EXIT"
    assert_eq "$desc: stderr пуст" "" "$HOOK_STDERR"
}

assert_stdout() {
    local desc="$1" expected="$2"
    assert_eq "$desc: stdout" "$expected" "$HOOK_STDOUT"
}

assert_no_state_file() {
    local desc="$1"
    if [[ -e "$HOOK_STATE_FILE" ]]; then fail "$desc: файл состояния не должен создаваться"
    else pass; fi
}

# Сверяет schema/updated_epoch(свежесть)/limits/order/extra_usage файла состояния
# с ожидаемым JSON-объектом (без ключа updated_epoch — время проверяется отдельно).
assert_state_file() {
    local desc="$1" expected_json="$2"
    if [[ ! -f "$HOOK_STATE_FILE" ]]; then
        fail "$desc: файл состояния не создан"; return
    fi
    local schema epoch now diff body expected_body
    schema="$(jq -r '.schema' "$HOOK_STATE_FILE")"
    epoch="$(jq -r '.updated_epoch' "$HOOK_STATE_FILE")"
    now="$(date +%s)"
    assert_eq "$desc: schema" "2" "$schema"
    diff=$(( now - epoch ))
    if (( diff < 0 || diff > 10 )); then
        fail "$desc: updated_epoch не похож на текущее время ($epoch, now=$now)"
    else pass; fi
    body="$(jq -S -c 'del(.updated_epoch)' "$HOOK_STATE_FILE")"
    expected_body="$(printf '%s' "$expected_json" | jq -S -c 'del(.updated_epoch)')"
    assert_eq "$desc: тело файла (без updated_epoch)" "$expected_body" "$body"
}

assert_file_mode() {
    local desc="$1" expected_mode="$2"
    assert_eq "$desc: права файла" "$expected_mode" "$(stat -c '%a' "$HOOK_STATE_FILE")"
}

assert_dir_mode() {
    local desc="$1" expected_mode="$2"
    assert_eq "$desc: права каталога" "$expected_mode" "$(stat -c '%a' "$(dirname "$HOOK_STATE_FILE")")"
}

fixture() { cat "$FIXTURES/$1"; }

# ---------------------------------------------------------------------------
# Оба фиксированных окна
# ---------------------------------------------------------------------------
test_both_windows() {
    local desc="оба окна"
    run_hook "$(fixture both_windows.json)"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓▓░░░░░ 42% · 7d ▓▓▓▓░░░░ 55%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {
            "five_hour": {"percent": 42.3, "resets_epoch": 1788550200},
            "seven_day": {"percent": 55.0, "resets_epoch": 1788700000}
        },
        "order": ["five_hour", "seven_day"]
    }'
    assert_file_mode "$desc" "600"
    assert_dir_mode "$desc" "700"
}

# ---------------------------------------------------------------------------
# Одно окно
# ---------------------------------------------------------------------------
test_five_hour_only() {
    local desc="только 5h"
    run_hook "$(fixture five_hour_only.json)"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓░░░░░░ 20%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"five_hour": {"percent": 20.0, "resets_epoch": 1700000000}},
        "order": ["five_hour"]
    }'
}

test_seven_day_only() {
    local desc="только 7d"
    run_hook "$(fixture seven_day_only.json)"
    assert_common "$desc"
    assert_stdout "$desc" "7d ▓▓▓▓▓░░░ 63%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"seven_day": {"percent": 63.0, "resets_epoch": 1700000500}},
        "order": ["seven_day"]
    }'
}

# ---------------------------------------------------------------------------
# Ноль окон
# ---------------------------------------------------------------------------
test_no_rate_limits_key() {
    local desc="rate_limits отсутствует"
    run_hook "$(fixture no_rate_limits.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

test_empty_rate_limits() {
    local desc="rate_limits пустой объект"
    run_hook "$(fixture empty_rate_limits.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

test_rate_limits_not_object() {
    local desc="rate_limits не объект"
    run_hook "$(fixture rate_limits_not_object.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

test_window_without_used_percentage() {
    local desc="окно без used_percentage пропускается, соседнее остаётся"
    run_hook "$(fixture window_without_used_percentage.json)"
    assert_common "$desc"
    assert_stdout "$desc" "7d ▓░░░░░░░ 10%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"seven_day": {"percent": 10.0}},
        "order": ["seven_day"]
    }'
}

# ---------------------------------------------------------------------------
# Полностью нечитаемый вход: файл состояния не трогается вовсе
# ---------------------------------------------------------------------------
test_malformed_json() {
    local desc="битый JSON"
    run_hook "$(fixture malformed.json)"
    assert_common "$desc"
    assert_stdout "$desc" ""
    assert_no_state_file "$desc"
}

test_empty_stdin() {
    local desc="пустой stdin"
    run_hook ""
    assert_common "$desc"
    assert_stdout "$desc" ""
    assert_no_state_file "$desc"
}

test_whitespace_only_stdin() {
    # jq empty / jq -c '.' на потоке из пробелов тихо возвращают "" с кодом 0 —
    # это не «валидный JSON без лимитов», а тот же битый вход, что и пустой stdin.
    local desc="stdin из одних пробелов"
    run_hook "
    "
    assert_common "$desc"
    assert_stdout "$desc" ""
    assert_no_state_file "$desc"
}

# ---------------------------------------------------------------------------
# model_scoped
# ---------------------------------------------------------------------------
test_model_scoped_two_items() {
    local desc="model_scoped: два валидных элемента"
    run_hook "$(fixture model_scoped_two_items.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Fable ▓▓░░░░░░ 21% · Opus 4.5 ▓▓▓▓░░░░ 56%"
    local fable_epoch opus_epoch
    fable_epoch="$(date -d "2026-09-06T09:00:00Z" +%s)"
    opus_epoch="$(date -d "2026-09-07T00:00:00Z" +%s)"
    assert_state_file "$desc" "{
        \"schema\": 2,
        \"profile\": {\"id\": \"default\", \"label\": \"default\"},
        \"limits\": {
            \"model:fable\": {\"percent\": 21.0, \"label\": \"Fable\", \"resets_epoch\": $fable_epoch},
            \"model:opus-4.5\": {\"percent\": 55.5, \"label\": \"Opus 4.5\", \"resets_epoch\": $opus_epoch}
        },
        \"order\": [\"model:fable\", \"model:opus-4.5\"]
    }"
}

test_model_scoped_null_utilization_skipped() {
    local desc="model_scoped: utilization:null пропускается целиком"
    run_hook "$(fixture model_scoped_null_utilization.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

test_model_scoped_empty_display_name_skipped() {
    local desc="model_scoped: пустой display_name пропускается целиком"
    run_hook "$(fixture model_scoped_empty_display_name.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

test_model_scoped_unparsable_resets_at() {
    local desc="model_scoped: непарсящийся ISO resets_at — окно остаётся, ключ resets_epoch отсутствует"
    run_hook "$(fixture model_scoped_unparsable_resets_at.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Fable ▓░░░░░░░ 10%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"model:fable": {"percent": 10.0, "label": "Fable"}},
        "order": ["model:fable"]
    }'
    if jq -e '.limits["model:fable"] | has("resets_epoch")' "$HOOK_STATE_FILE" >/dev/null; then
        fail "$desc: resets_epoch не должен присутствовать при непарсящейся дате"
    else pass; fi
}

test_display_name_sanitization() {
    local desc="display_name с пробелом и цифрой -> model:fable-5"
    run_hook "$(fixture display_name_sanitization.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Fable 5 ▓▓▓▓░░░░ 50%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"model:fable-5": {"percent": 50.0, "label": "Fable 5"}},
        "order": ["model:fable-5"]
    }'
}

test_model_scoped_non_object_item_skipped() {
    local desc="model_scoped: не-объект в массиве пропускается, соседнее окно остаётся"
    run_hook "$(fixture model_scoped_non_object_item.json)"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓▓░░░░░ 42%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"five_hour": {"percent": 42.3, "resets_epoch": 1788550200}},
        "order": ["five_hour"]
    }'
}

test_model_scoped_iso8601_forms() {
    local desc="model_scoped: формы resets_at (дробные секунды, смещения, эпоха числом)"
    run_hook "$(fixture model_scoped_iso8601_forms.json)"
    assert_common "$desc"
    local expect_z expect_off_plus expect_off_minus key epoch
    expect_z="$(date -d "2026-09-07T00:00:00Z" +%s)"
    expect_off_plus="$(date -d "2026-09-07T00:00:00+03:00" +%s)"
    expect_off_minus="$(date -d "2026-09-07T00:00:00-05:00" +%s)"
    for key in "model:z-bare" "model:z-millis" "model:z-micros"; do
        epoch="$(jq -r --arg k "$key" '.limits[$k].resets_epoch' "$HOOK_STATE_FILE")"
        assert_eq "$desc: $key" "$expect_z" "$epoch"
    done
    epoch="$(jq -r '.limits["model:offset-plus-colon"].resets_epoch' "$HOOK_STATE_FILE")"
    assert_eq "$desc: +03:00" "$expect_off_plus" "$epoch"
    epoch="$(jq -r '.limits["model:offset-minus-colon"].resets_epoch' "$HOOK_STATE_FILE")"
    assert_eq "$desc: -05:00" "$expect_off_minus" "$epoch"
    epoch="$(jq -r '.limits["model:offset-plus-nocolon"].resets_epoch' "$HOOK_STATE_FILE")"
    assert_eq "$desc: +0300 без двоеточия" "$expect_off_plus" "$epoch"
    epoch="$(jq -r '.limits["model:offset-fractional"].resets_epoch' "$HOOK_STATE_FILE")"
    assert_eq "$desc: дробная часть смещения отброшена" "$expect_off_plus" "$epoch"
    epoch="$(jq -r '.limits["model:numeric-epoch"].resets_epoch' "$HOOK_STATE_FILE")"
    assert_eq "$desc: resets_at числом" "1788739200" "$epoch"
}

test_model_scoped_slug_collision_first_wins() {
    local desc="model_scoped: коллизия слагов — побеждает первое вхождение"
    run_hook "$(fixture model_scoped_slug_collision.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Fable ▓░░░░░░░ 10%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"model:fable": {"percent": 10.0, "label": "Fable"}},
        "order": ["model:fable"]
    }'
}

test_model_scoped_label_middle_dot_removed() {
    local desc="model_scoped: · в label убирается, чтобы не разломать разделитель статус-строки"
    run_hook "$(fixture model_scoped_label_middle_dot.json)"
    assert_common "$desc"
    assert_stdout "$desc" "FableBeta ▓▓▓░░░░░ 33%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"model:fablebeta": {"percent": 33.0, "label": "FableBeta"}},
        "order": ["model:fablebeta"]
    }'
}

test_model_scoped_label_control_chars_collapsed() {
    local desc="model_scoped: управляющие символы становятся пробелом, пробелы схлопнуты и обрезаны по краям"
    run_hook "$(fixture model_scoped_label_control_chars.json)"
    assert_common "$desc"
    assert_stdout "$desc" "Fable Beta Gamma ▓▓▓▓░░░░ 44%"
    local label
    label="$(jq -r '.limits["model:--fablebeta---gamma--"].label' "$HOOK_STATE_FILE")"
    assert_eq "$desc: label" "Fable Beta Gamma" "$label"
}

test_model_scoped_label_empty_after_sanitization_skipped() {
    local desc="model_scoped: label, пустой после санации, — окно пропускается целиком"
    run_hook "$(jq -n '{rate_limits: {model_scoped: [{display_name: "\n\t·  ", utilization: 44.0, resets_at: null}]}}')"
    assert_common "$desc"
    assert_stdout "$desc" "Claude: no data"
    assert_state_file "$desc" '{"schema": 2, "profile": {"id": "default", "label": "default"}, "limits": {}, "order": []}'
}

# ---------------------------------------------------------------------------
# extra_usage
# ---------------------------------------------------------------------------
test_extra_usage_present() {
    local desc="extra_usage присутствует, но не попадает в статус-строку"
    run_hook "$(fixture extra_usage_present.json)"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓▓░░░░░ 42%"
    assert_state_file "$desc" '{
        "schema": 2,
        "profile": {"id": "default", "label": "default"},
        "limits": {"five_hour": {"percent": 42.3, "resets_epoch": 1788550200}},
        "order": ["five_hour"],
        "extra_usage": {"percent": 31.0, "used_credits": 12.4, "monthly_limit": 40.0, "currency": "USD"}
    }'
}

test_extra_usage_absent() {
    local desc="extra_usage отсутствует в исходном JSON"
    run_hook "$(fixture extra_usage_absent.json)"
    assert_common "$desc"
    if jq -e 'has("extra_usage")' "$HOOK_STATE_FILE" >/dev/null; then
        fail "$desc: ключ extra_usage не должен появляться"
    else pass; fi
}

test_extra_usage_null_utilization_treated_as_absent() {
    local desc="extra_usage.utilization:null -> ключ extra_usage отсутствует"
    run_hook "$(fixture extra_usage_null_utilization.json)"
    assert_common "$desc"
    if jq -e 'has("extra_usage")' "$HOOK_STATE_FILE" >/dev/null; then
        fail "$desc: ключ extra_usage не должен появляться при utilization:null"
    else pass; fi
}

# ---------------------------------------------------------------------------
# order без seven_day
# ---------------------------------------------------------------------------
test_order_without_seven_day() {
    local desc="order пропускает отсутствующий seven_day без дырки"
    run_hook "$(fixture order_without_seven_day.json)"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓▓░░░░░ 42% · Fable ▓▓░░░░░░ 21%"
    local order
    order="$(jq -c '.order' "$HOOK_STATE_FILE")"
    assert_eq "$desc: order" '["five_hour","model:fable"]' "$order"
}

# ---------------------------------------------------------------------------
# Граничные проценты (алгоритм бара из брифа, применяется к five_hour)
# ---------------------------------------------------------------------------
boundary_input() {
    jq -n --argjson p "$1" '{rate_limits: {five_hour: {used_percentage: $p, resets_at: null}}}'
}

test_boundary_percent() {
    local percent="$1" expected_bar="$2" expected_pct="$3"
    local desc="граница $percent%"
    run_hook "$(boundary_input "$percent")"
    assert_common "$desc"
    assert_stdout "$desc" "5h $expected_bar $expected_pct%"
}

run_boundary_tests() {
    test_boundary_percent "0"    "░░░░░░░░" "0"
    test_boundary_percent "6.2"  "░░░░░░░░" "6"
    test_boundary_percent "6.3"  "▓░░░░░░░" "6"
    test_boundary_percent "80"   "▓▓▓▓▓▓░░" "80"
    test_boundary_percent "99.5" "▓▓▓▓▓▓▓▓" "100"
    test_boundary_percent "100"  "▓▓▓▓▓▓▓▓" "100"
}

# ---------------------------------------------------------------------------
# state_path(): ветки без CLAUDE_USAGE_STATE
# ---------------------------------------------------------------------------

# Прогоняет хук в полностью очищенном окружении (env -i) с явным cwd — только
# так проверяются ветки state_path(), не завязанные на CLAUDE_USAGE_STATE.
run_hook_env() {
    local input="$1" cwd="$2"; shift 2
    HOOK_STDOUT="$(cd "$cwd" && printf '%s' "$input" | env -i PATH="$PATH" "$@" "$HOOK" 2>"$cwd/stderr")"
    HOOK_EXIT=$?
    HOOK_STDERR="$(cat "$cwd/stderr")"
}

test_state_path_xdg_state_home() {
    local desc="state_path: ветка XDG_STATE_HOME"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" "XDG_STATE_HOME=$tmp/xdg"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓░░░░░░ 20%"
    if [[ -f "$tmp/xdg/claude-usage/default.json" ]]; then pass
    else fail "$desc: файл состояния не создан по XDG_STATE_HOME"; fi
}

test_state_path_default_home() {
    local desc="state_path: дефолт \$HOME/.local/state"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" "HOME=$tmp/home"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓░░░░░░ 20%"
    if [[ -f "$tmp/home/.local/state/claude-usage/default.json" ]]; then pass
    else fail "$desc: файл состояния не создан по умолчанию \$HOME/.local/state"; fi
}

test_state_path_home_and_xdg_unset() {
    local desc="state_path: HOME и XDG_STATE_HOME не заданы — запись пропускается"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓░░░░░░ 20%"
    local leftover
    leftover="$(find "$tmp" -mindepth 1 -type f ! -name stderr | wc -l)"
    assert_eq "$desc: посторонних файлов в cwd не появилось" "0" "$leftover"
}

# ---------------------------------------------------------------------------
# Атомарность: временных файлов не остаётся
# ---------------------------------------------------------------------------
test_no_leftover_tmp_files() {
    local desc="после записи в каталоге состояния нет посторонних файлов"
    run_hook "$(fixture both_windows.json)"
    local extra
    extra="$(find "$(dirname "$HOOK_STATE_FILE")" -maxdepth 1 -type f ! -name 'latest.json' | wc -l)"
    assert_eq "$desc" "0" "$extra"
}

# ---------------------------------------------------------------------------
# Профиль: имя файла состояния и блок profile в схеме 2
# ---------------------------------------------------------------------------
test_profile_default_no_config_dir() {
    local desc="профиль: без CLAUDE_CONFIG_DIR — файл default.json"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" "HOME=$tmp/home"
    assert_common "$desc"
    assert_stdout "$desc" "5h ▓▓░░░░░░ 20%"
    if [[ -f "$tmp/home/.local/state/claude-usage/default.json" ]]; then pass
    else fail "$desc: файл состояния не создан как default.json"; fi
}

test_profile_named_from_config_dir() {
    local desc="профиль: CLAUDE_CONFIG_DIR=.../.claude-personal — файл personal.json"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-personal"
    assert_common "$desc"
    if [[ -f "$tmp/home/.local/state/claude-usage/personal.json" ]]; then pass
    else fail "$desc: файл состояния не создан как personal.json"; fi
}

test_profile_schema_and_fields() {
    local desc="профиль: schema=2, profile.id и profile.label из CLAUDE_USAGE_PROFILE_LABEL"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-personal" \
        "CLAUDE_USAGE_PROFILE_LABEL=own"
    assert_common "$desc"
    local state_file="$tmp/home/.local/state/claude-usage/personal.json"
    if [[ ! -f "$state_file" ]]; then fail "$desc: файл состояния не создан"; return; fi
    assert_eq "$desc: schema" "2" "$(jq -r '.schema' "$state_file")"
    assert_eq "$desc: profile.id" "personal" "$(jq -r '.profile.id' "$state_file")"
    assert_eq "$desc: profile.label" "own" "$(jq -r '.profile.label' "$state_file")"
}

test_profile_label_defaults_to_id() {
    local desc="профиль: без CLAUDE_USAGE_PROFILE_LABEL — profile.label равен id"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-personal"
    assert_common "$desc"
    local state_file="$tmp/home/.local/state/claude-usage/personal.json"
    if [[ ! -f "$state_file" ]]; then fail "$desc: файл состояния не создан"; return; fi
    assert_eq "$desc: profile.label" "personal" "$(jq -r '.profile.label' "$state_file")"
}

# Регрессия: CLAUDE_USAGE_PROFILE_LABEL — пользовательская строка, но раньше
# писалась в файл состояния как есть. "·" в ней читался бы как разделитель
# самой статус-строки, а управляющий символ дошёл бы неповреждённым до
# однострочной метки в трее. sanitize_label уже решает ровно эту задачу для
# display_name модели — здесь та же санация, не вторая копия.
test_profile_label_is_sanitized_like_model_display_name() {
    local desc="профиль: CLAUDE_USAGE_PROFILE_LABEL санируется как display_name"
    local tmp; tmp="$(mktemp -d)"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-personal" \
        "CLAUDE_USAGE_PROFILE_LABEL=$(printf 'Work \x01· Fake')"
    assert_common "$desc"
    local state_file="$tmp/home/.local/state/claude-usage/personal.json"
    if [[ ! -f "$state_file" ]]; then fail "$desc: файл состояния не создан"; return; fi
    assert_eq "$desc: profile.label" "Work Fake" "$(jq -r '.profile.label' "$state_file")"
}

test_profile_claude_usage_state_overrides_path() {
    local desc="профиль: CLAUDE_USAGE_STATE по-прежнему определяет путь целиком"
    local tmp; tmp="$(mktemp -d)"
    local state_file="$tmp/custom/state.json"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "CLAUDE_USAGE_STATE=$state_file" \
        "CLAUDE_CONFIG_DIR=$tmp/home/.claude-personal"
    assert_common "$desc"
    if [[ ! -f "$state_file" ]]; then fail "$desc: файл не создан по CLAUDE_USAGE_STATE"; return; fi
    assert_eq "$desc: schema" "2" "$(jq -r '.schema' "$state_file")"
    assert_eq "$desc: profile.id" "personal" "$(jq -r '.profile.id' "$state_file")"
}

# Паритет двух реализаций правила: bash пишет имя файла, python читает каталог.
# Расхождение здесь означает, что один профиль виден в панели как два разных, —
# ровно тот молчаливый обман, который вся схема должна исключать. Проверяются
# вырожденные имена, потому что на «personal» сойдётся любая реализация.
test_profile_id_matches_python_on_degenerate_names() {
    local desc="профиль: bash и python дают один id"
    local name produced expected tmp found
    for name in ".claude-!!!" ".claude-Work  Acct" ".claude-my--profile" ".claude-личн" \
                ".Claude-Personal" "..claude-work" ".claude-work//" ".claude-work!!" \
                ".claude-Work" ".claude-личный" ".claude-работа" ".claude-Café" \
                ".claude-default"; do
        tmp="$(mktemp -d)"
        run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
            "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/$name"
        found="$(find "$tmp/home/.local/state/claude-usage" -name '*.json' 2>/dev/null | head -1)"
        if [[ -z "$found" ]]; then fail "$desc [$name]: файл состояния не создан"; continue; fi
        produced="$(basename "$found" .json)"
        expected="$(PYTHONPATH="$ROOT_DIR/src" /usr/bin/python3 -c \
            'import sys
from claude_usage_indicator.profiles import profile_id_from_config_dir
print(profile_id_from_config_dir(sys.argv[1]))' "$tmp/home/$name")"
        assert_eq "$desc [$name]" "$expected" "$produced"
    done

    # CLAUDE_CONFIG_DIR из одних слэшей не имеет basename: срез хвостового "/"
    # не должен схлопывать его в пустую строку раньше резолва пути (штатно
    # недостижимо, но инвариант паритета заявлен без исключений).
    local abs_dir
    for abs_dir in "/" "//" "///"; do
        tmp="$(mktemp -d)"
        run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
            "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$abs_dir"
        found="$(find "$tmp/home/.local/state/claude-usage" -name '*.json' 2>/dev/null | head -1)"
        if [[ -z "$found" ]]; then fail "$desc [$abs_dir]: файл состояния не создан"; continue; fi
        produced="$(basename "$found" .json)"
        expected="$(PYTHONPATH="$ROOT_DIR/src" /usr/bin/python3 -c \
            'import sys
from claude_usage_indicator.profiles import profile_id_from_config_dir
print(profile_id_from_config_dir(sys.argv[1]))' "$abs_dir")"
        assert_eq "$desc [$abs_dir]" "$expected" "$produced"
    done

    # Симлинк на другой каталог: идентификатор обязан выйти из имени цели,
    # а не имени ссылки, иначе один и тот же профиль по двум маршрутам
    # молча раздваивается на два файла состояния (см. фикс-раунд 1).
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/home/.claude-real-work-target"
    ln -s ".claude-real-work-target" "$tmp/home/.claude-alias"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-alias"
    found="$(find "$tmp/home/.local/state/claude-usage" -name '*.json' 2>/dev/null | head -1)"
    if [[ -z "$found" ]]; then
        fail "$desc [симлинк]: файл состояния не создан"
    else
        produced="$(basename "$found" .json)"
        expected="$(PYTHONPATH="$ROOT_DIR/src" /usr/bin/python3 -c \
            'import sys
from claude_usage_indicator.profiles import profile_id_from_config_dir
print(profile_id_from_config_dir(sys.argv[1]))' "$tmp/home/.claude-alias")"
        assert_eq "$desc [симлинк]" "$expected" "$produced"
    fi

    # Симлинк, чья цель лежит за несуществующим промежуточным каталогом:
    # readlink -f требует существования всех компонентов кроме последнего и
    # падает здесь, python Path.resolve() нестрогий и резолвит цель всегда
    # (см. фикс-раунд 2) — без -m bash откатывался на имя ссылки вместо
    # имени цели.
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/home"
    ln -s "nested-missing/.claude-deep-target" "$tmp/home/.claude-alias-deep"
    run_hook_env "$(fixture five_hour_only.json)" "$tmp" \
        "HOME=$tmp/home" "CLAUDE_CONFIG_DIR=$tmp/home/.claude-alias-deep"
    found="$(find "$tmp/home/.local/state/claude-usage" -name '*.json' 2>/dev/null | head -1)"
    if [[ -z "$found" ]]; then
        fail "$desc [симлинк через несуществующий каталог]: файл состояния не создан"
    else
        produced="$(basename "$found" .json)"
        expected="$(PYTHONPATH="$ROOT_DIR/src" /usr/bin/python3 -c \
            'import sys
from claude_usage_indicator.profiles import profile_id_from_config_dir
print(profile_id_from_config_dir(sys.argv[1]))' "$tmp/home/.claude-alias-deep")"
        assert_eq "$desc [симлинк через несуществующий каталог]" "$expected" "$produced"
    fi
}

main() {
    test_both_windows
    test_five_hour_only
    test_seven_day_only
    test_no_rate_limits_key
    test_empty_rate_limits
    test_rate_limits_not_object
    test_window_without_used_percentage
    test_malformed_json
    test_empty_stdin
    test_whitespace_only_stdin
    test_model_scoped_two_items
    test_model_scoped_null_utilization_skipped
    test_model_scoped_empty_display_name_skipped
    test_model_scoped_unparsable_resets_at
    test_display_name_sanitization
    test_model_scoped_non_object_item_skipped
    test_model_scoped_iso8601_forms
    test_model_scoped_slug_collision_first_wins
    test_model_scoped_label_middle_dot_removed
    test_model_scoped_label_control_chars_collapsed
    test_model_scoped_label_empty_after_sanitization_skipped
    test_extra_usage_present
    test_extra_usage_absent
    test_extra_usage_null_utilization_treated_as_absent
    test_order_without_seven_day
    run_boundary_tests
    test_state_path_xdg_state_home
    test_state_path_default_home
    test_state_path_home_and_xdg_unset
    test_no_leftover_tmp_files
    test_profile_default_no_config_dir
    test_profile_named_from_config_dir
    test_profile_schema_and_fields
    test_profile_label_defaults_to_id
    test_profile_label_is_sanitized_like_model_display_name
    test_profile_claude_usage_state_overrides_path
    test_profile_id_matches_python_on_degenerate_names

    echo "---"
    echo "pass=$pass_count fail=$fail_count"
    if (( fail_count > 0 )); then
        exit 1
    fi
    exit 0
}

main
