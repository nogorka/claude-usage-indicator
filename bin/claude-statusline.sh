#!/usr/bin/env bash
# Хук statusLine для Claude Code.
#
# Читает JSON сессии со stdin, вытаскивает rate_limits (схема реального
# бандла Claude Code, не путать со старым мокапом: used_percentage у
# фиксированных окон, utilization у model_scoped, resets_at — epoch у
# фиксированных окон и ISO-8601 у model_scoped), атомарно обновляет файл
# состояния для демона-индикатора и печатает статус-строку панели.
#
# Контракт устойчивости: Claude Code показывает stdout хука пользователю
# как есть, поэтому на любом входе — валидном или нет — скрипт обязан
# завершиться кодом 0 и не написать ни байта в stderr.
set -euo pipefail

# jq-фильтр: вся разборка rate_limits и рендер бара — в одном месте, чтобы
# bash-часть занималась только вводом/выводом и атомарной записью.
# Одинарные кавычки снаружи (см. вызов ниже) — внутри полно "$..." для
# переменных jq, которые не должны трогаться подстановкой bash.
JQ_FILTER="$(cat <<'JQ_EOF'
def clamp_filled(f): if f < 0 then 0 elif f > 8 then 8 else f end;
# jq умножает строку на 0 в null, а не в "" — без этой обёртки бар из
# нулевой заполненности/пустоты ломался бы на границах 0% и 100%.
def rep(s; n): if n <= 0 then "" else s * n end;
def bar(percent):
  (clamp_filled((((percent / 100) * 8) + 0.5) | floor)) as $filled
  | rep("▓"; $filled) + rep("░"; 8 - $filled);
def rounded(percent): (percent + 0.5) | floor;

# ISO-8601 может не распарситься (не тот формат, мусор) — тогда окно всё
# равно остаётся, просто без resets_epoch.
def parse_iso_epoch(s): try (s | fromdateiso8601) catch null;

# Ключ-слаг из display_name: нижний регистр, пробелы в дефис, всё лишнее
# выбрасывается — так задано контроллером для model:<slug>.
def slug(name):
  (name | ascii_downcase | gsub(" "; "-")) as $lowered
  | ($lowered | gsub("[^a-z0-9._-]"; ""));

# Фиксированное окно (five_hour|seven_day): resets_at уже epoch-секунды.
def fixed_window(w):
  if (w | type) == "object" and ((w.used_percentage) | type) == "number"
  then { percent: w.used_percentage }
       + (if (w.resets_at | type) == "number"
          then {resets_epoch: (w.resets_at | floor)} else {} end)
  else null
  end;

# Элемент model_scoped: utilization:null или пустой/отсутствующий
# display_name — окно целиком пропускается (не ошибка, а «его не было»).
def model_entry(item):
  (item.display_name) as $name
  | (item.utilization) as $util
  | if ($name | type) == "string" and ($name | length) > 0
       and ($util | type) == "number"
    then
      (slug($name)) as $key
      | (if (item.resets_at | type) == "string"
         then parse_iso_epoch(item.resets_at) else null end) as $epoch
      | { key: ("model:" + $key), label: $name, percent: $util }
        + (if $epoch != null then {resets_epoch: ($epoch | floor)} else {} end)
    else null
    end;

def label_of(key; limits):
  if key == "five_hour" then "5h"
  elif key == "seven_day" then "7d"
  else limits[key].label
  end;

. as $input
| ($input | if type == "object" then (.rate_limits // null) else null end) as $rl0
| ($rl0 | if type == "object" then . else {} end) as $rl

| fixed_window($rl.five_hour) as $five
| fixed_window($rl.seven_day) as $seven

| ($rl.model_scoped | if type == "array" then . else [] end) as $ms_list
| [$ms_list[] | model_entry(.) | select(. != null)] as $models

| ({}
   + (if $five  != null then {five_hour: $five} else {} end)
   + (if $seven != null then {seven_day: $seven} else {} end)
   + (reduce $models[] as $m
       ({}; . + {($m.key): ({percent: $m.percent, label: $m.label}
                             + (if $m.resets_epoch then {resets_epoch: $m.resets_epoch} else {} end))})
     )
  ) as $limits

| ((if $five  != null then ["five_hour"] else [] end)
   + (if $seven != null then ["seven_day"] else [] end)
   + [$models[] | .key]
  ) as $order

| ($rl.extra_usage | if type == "object" then . else {} end) as $eu
| (if (($eu.utilization) | type) == "number"
   then {percent: $eu.utilization}
        + (if ($eu.used_credits  | type) == "number" then {used_credits:  $eu.used_credits}  else {} end)
        + (if ($eu.monthly_limit | type) == "number" then {monthly_limit: $eu.monthly_limit} else {} end)
        + (if ($eu.currency      | type) == "string" then {currency:      $eu.currency}      else {} end)
   else null
   end) as $extra_usage

| ([$order[] as $k
    | "\(label_of($k; $limits)) \(bar($limits[$k].percent)) \(rounded($limits[$k].percent))%"
   ]) as $segments
| (if ($segments | length) == 0 then "Claude: нет данных" else ($segments | join(" · ")) end) as $status_line

| {limits: $limits, order: $order, status_line: $status_line}
  + (if $extra_usage != null then {extra_usage: $extra_usage} else {} end)
JQ_EOF
)"

# Путь файла состояния: CLAUDE_USAGE_STATE (тестируемость) важнее XDG-пути.
state_path() {
    if [[ -n "${CLAUDE_USAGE_STATE:-}" ]]; then
        printf '%s' "$CLAUDE_USAGE_STATE"
        return
    fi
    printf '%s/claude-usage/latest.json' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

# Атомарная запись: mktemp в целевом каталоге (гарантия одной ФС с mv),
# 0600 выставляется до переименования, mv -f поверх старого файла.
write_state() {
    local content="$1"
    local path dir tmp
    path="$(state_path)"
    dir="$(dirname -- "$path")"
    mkdir -p -m 0700 "$dir"
    tmp="$(mktemp "$dir/latest.json.XXXXXX")"
    chmod 0600 "$tmp"
    printf '%s' "$content" > "$tmp"
    mv -f "$tmp" "$path"
}

main() {
    local raw
    raw="$(cat)" || raw=""

    # Полностью нечитаемый вход (пустой stdin) — файл состояния не трогаем,
    # печатаем пустую строку молча (см. пункт 5 решений контроллера).
    if [[ -z "$raw" ]]; then
        exit 0
    fi

    # jq на пустом/из-одних-пробелов вводе тихо возвращает "" с кодом 0
    # (ноль JSON-значений в потоке — ноль применений фильтра), поэтому
    # синтаксической проверкой `jq empty` тут не обойтись: нужен ещё и
    # непустой результат основного фильтра.
    local result
    result="$(printf '%s' "$raw" | jq -c "$JQ_FILTER" 2>/dev/null)" || result=""
    if [[ -z "$result" ]]; then
        exit 0
    fi

    local status_line
    status_line="$(printf '%s' "$result" | jq -r '.status_line')"

    local state_json
    state_json="$(printf '%s' "$result" | jq -c --argjson epoch "$(date +%s)" '
        {schema: 1, updated_epoch: $epoch, limits: .limits, order: .order}
        + (if has("extra_usage") then {extra_usage: .extra_usage} else {} end)
    ')"

    # Запись состояния не должна ронять печать статус-строки — это
    # единственный вывод, который Claude Code реально показывает.
    write_state "$state_json" || true

    printf '%s\n' "$status_line"
}

main
