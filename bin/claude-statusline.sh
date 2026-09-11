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

# Смещение UTC из хвоста ISO-строки: "Z" или ±HH:MM/±HHMM. Возвращает секунды
# со знаком; null на любом сбое разбора. capture() без совпадения — это jq
# empty (не ошибка), поэтому ловим через "// null", а не try/catch: иначе
# try пропускает empty насквозь и весь элемент model_scoped исчезает молча.
def offset_seconds(off):
  if off == "Z" then 0
  else
    # hh/mm ограничены реальным диапазоном часового пояса (00-23:00-59) —
    # без этого "+99:99" проходил бы как валидное смещение.
    ((off | capture("^(?<sign>[+-])(?<hh>[01][0-9]|2[0-3]):?(?<mm>[0-5][0-9])$")?) // null) as $o
    | if $o == null then null
      else
        (($o.hh | tonumber) * 3600 + ($o.mm | tonumber) * 60)
        * (if $o.sign == "-" then -1 else 1 end)
      end
  end;

# resets_at у model_scoped — ISO-8601 строка (Date.toISOString(), то есть
# всегда с миллисекундами) либо, по спецификации, число-эпоха напрямую.
# jq 1.7 fromdateiso8601 понимает только "...Z" без дробных секунд и без
# смещения — нормализуем сами: дробную часть отбрасываем, наивную часть
# парсим как UTC, вычитаем смещение. Что не разобралось — null, без ошибок.
def parse_iso_epoch(v):
  if (v | type) == "number" then (v | floor)
  elif (v | type) != "string" then null
  else
    ((v | capture(
        "^(?<naive>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\\.[0-9]+)?(?<off>Z|[+-][0-9]{2}:?[0-9]{2})$"
      )?) // null) as $m
    | if $m == null then null
      else
        (try ($m.naive + "Z" | fromdateiso8601) catch null) as $naive_epoch
        # fromdateiso8601 не проверяет календарь — "2026-02-30" молча
        # нормализуется в 2026-03-02 вместо ошибки. Круговой прогон через
        # обратную todateiso8601 ловит это: невалидная дата не воспроизведёт
        # исходную строку.
        | if $naive_epoch == null or (($naive_epoch | todateiso8601) != ($m.naive + "Z"))
          then null
          else (offset_seconds($m.off)) as $off_secs
               | if $off_secs == null then null else $naive_epoch - $off_secs end
          end
      end
  end;

# Ключ-слаг из display_name для model:<slug>: нижний регистр, пробелы в дефис,
# всё лишнее выбрасывается.
def slug(name):
  (name | ascii_downcase | gsub(" "; "-")) as $lowered
  | ($lowered | gsub("[^a-z0-9._-]"; ""));

# label идёт в статус-строку и файл состояния как есть — управляющие символы
# (перевод строки в т.ч.) и разделитель "·" её бы разломали, поэтому санируется
# при записи, а не полагается на источник.
# Управляющий символ схлопывается в пробел (это разделитель слов, не мусор:
# без замены "Fable\nBeta" превратилось бы в слипшееся "FableBeta"), а "·"
# убирается совсем — это не разделитель слов, а знак, который нельзя спутать
# с разделителем самой статус-строки.
def sanitize_label(name):
  (name | gsub("[\\x00-\\x1f\\x7f]"; " ") | gsub("·"; "")) as $stripped
  | ($stripped | gsub(" +"; " ")) as $collapsed
  | ($collapsed | sub("^ +"; "") | sub(" +$"; ""));

# Фиксированное окно (five_hour|seven_day): resets_at уже epoch-секунды.
def fixed_window(w):
  if (w | type) == "object" and ((w.used_percentage) | type) == "number"
  then { percent: w.used_percentage }
       + (if (w.resets_at | type) == "number"
          then {resets_epoch: (w.resets_at | floor)} else {} end)
  else null
  end;

# Элемент model_scoped: не-объект в массиве (мусор с сервера) пропускается
# так же, как невалидное фиксированное окно — не роняя разбор остального.
# utilization:null или пустой/отсутствующий display_name — окно целиком
# пропускается (не ошибка, а «его не было»). Пустой после санации label —
# тоже пропуск: печатать нечего.
def model_entry(item):
  if (item | type) != "object" then null
  else
    (item.display_name) as $name
    | (item.utilization) as $util
    | if ($name | type) == "string" and ($name | length) > 0
         and ($util | type) == "number"
      then
        (sanitize_label($name)) as $label
        | if ($label | length) == 0 then null
          else
            (slug($name)) as $key
            | (parse_iso_epoch(item.resets_at)) as $epoch
            | { key: ("model:" + $key), label: $label, percent: $util }
              + (if $epoch != null then {resets_epoch: ($epoch | floor)} else {} end)
          end
      else null
      end
  end;

def label_of(key; limits):
  if key == "five_hour" then "5h"
  elif key == "seven_day" then "7d"
  else limits[key].label
  end;

# Дедуп по ключу-слагу: два display_name, дающие один слаг, иначе давали бы
# дубль и в limits (reduce молча берёт последний), и в order (панель печатала
# бы один сегмент дважды). Побеждает первое вхождение.
def dedup_by_key:
  reduce .[] as $it ([]; if any(.[]; .key == $it.key) then . else . + [$it] end);

. as $input
| ($input | if type == "object" then (.rate_limits // null) else null end) as $rl0
| ($rl0 | if type == "object" then . else {} end) as $rl

| fixed_window($rl.five_hour) as $five
| fixed_window($rl.seven_day) as $seven

| ($rl.model_scoped | if type == "array" then . else [] end) as $ms_list
| ([$ms_list[] | model_entry(.) | select(. != null)] | dedup_by_key) as $models

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
| (if ($segments | length) == 0 then "Claude: no data" else ($segments | join(" · ")) end) as $status_line

| {limits: $limits, order: $order, status_line: $status_line, profile_label: sanitize_label($raw_profile_label)}
  + (if $extra_usage != null then {extra_usage: $extra_usage} else {} end)
JQ_EOF
)"

# Разрешает путь до канонической формы (симлинк на цель, "..", повторные "/"),
# как Path.resolve() на python-стороне. -m, не -f: -f падает, если цель
# симлинка лежит за несуществующим промежуточным каталогом (readlink -f
# требует существования всех компонентов кроме последнего целиком, включая
# то, что лежит по ту сторону цепочки симлинков), а python resolve() по
# умолчанию нестрогий и резолвит такую цепочку всегда — bash с -f откатывался
# на имя ссылки вместо имени цели ровно в этом случае (см. фикс-раунд 2).
# -m не требует существования вообще ничего, откат на исходный путь при
# пустом выводе остаётся страховкой, а не основным путём.
resolve_config_dir() {
    local input="$1" resolved
    resolved="$(readlink -m -- "$input" 2>/dev/null)" || resolved=""
    printf '%s' "${resolved:-$input}"
}

# Идентификатор профиля из CLAUDE_CONFIG_DIR — правило совпадает с
# profiles.profile_id_from_config_dir (bash пишет имя файла, python его читает,
# расхождение значило бы, что один профиль виден в панели как два разных).
# Идентификатор берётся с разрешённого пути: симлинк на другой каталог обязан
# дать имя цели, а не имя ссылки, иначе один профиль по двум маршрутам молча
# раздвоится на два файла состояния. Тем же разрешением снимается повторный
# хвостовой "/" — readlink -m его убирает по пути.
# LC_ALL=C на каждом tr/sed: под чужой локалью класс [^a-z0-9_-] и регистр
# верхний/нижний ведут себя иначе, чем питоновский re/str.translate по
# таблице ASCII — паритет реализаций требует фиксированной локали, а не
# той, что досталась окружению вызова.
# Перевод строки внутри CLAUDE_CONFIG_DIR вырезается раньше любой другой
# обработки: sed/tr построчны и не видят "\n" как часть санируемой строки
# (её отдаёт как разделитель между вызовами), а python re.sub видит и
# заменяет — без общего среза здесь реализации разъезжались бы на путях
# с переводом строки внутри.
# Слаг, потерявший информацию при санитизации (изменился, опустел или занял
# зарезервированное имя "default"), дополняется хэшем канонического пути —
# иначе разные каталоги молча писали бы в один файл состояния. Регистр
# basename потерей не считается и не хэшируется: ".claude-Work" и
# ".claude-work" сознательно дают один и тот же id.
# Ещё одна конструкция неочевидна намеренно, «очевидное» упрощение её ломает:
#   sed -E, а не tr -c: tr заменяет каждый запрещённый байт на дефис, а питоновский
#   [^a-z0-9_-]+ схлопывает последовательность в один. На «Work  Acct» это дало бы
#   work--acct против work-acct. Схлопывать всё подряд через tr -s тоже нельзя:
#   тогда разъедется легитимное имя my--profile, где дефисы разрешены.
profile_id() {
    local dir="${CLAUDE_CONFIG_DIR:-}"
    dir="${dir//$'\n'/}"
    if [[ -z "$dir" ]]; then printf 'default'; return; fi
    # Стоп на "/", а не на пустой строке: путь из одних слэшей срезается до
    # корня, как это делает Path.resolve() на python-стороне, а не до "",
    # которая хэшировалась бы в другой id.
    while [[ "$dir" == */ && "$dir" != "/" ]]; do dir="${dir%/}"; done
    dir="$(resolve_config_dir "$dir")"
    # "${HOME:-}", не голый $HOME: под set -u вызов без HOME в окружении
    # (env -i без HOME, но с CLAUDE_USAGE_STATE и CLAUDE_CONFIG_DIR) уронит
    # разбор параметра раньше, чем сработает любая внешняя обёртка "|| true".
    if [[ -n "${HOME:-}" ]]; then
        local home_claude; home_claude="$(resolve_config_dir "$HOME/.claude")"
        if [[ "$dir" == "$home_claude" ]]; then printf 'default'; return; fi
    fi
    local name="${dir##*/}"
    while [[ "$name" == .* ]]; do name="${name#.}"; done
    name="$(LC_ALL=C printf '%s' "$name" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
    name="${name#claude-}"
    local slug
    slug="$(LC_ALL=C printf '%s' "$name" | LC_ALL=C sed -E 's/[^a-z0-9_-]+/-/g')"
    while [[ "$slug" == -* ]]; do slug="${slug#-}"; done
    while [[ "$slug" == *- ]]; do slug="${slug%-}"; done
    if [[ "$slug" == "$name" && -n "$slug" && "$slug" != "default" ]]; then
        printf '%s' "$slug"
        return
    fi
    # Хэш — от канонического пути ($dir, уже без хвостового "/"), а не от
    # имени каталога: два разных каталога с одним и тем же лоссовым слагом
    # (например, оба нечитаемых в ASCII) обязаны разойтись, а хэш от имени
    # их бы не различил.
    local digest
    digest="$(LC_ALL=C printf '%s' "$dir" | sha256sum | cut -c1-6)"
    if [[ -n "$slug" ]]; then
        printf '%s-%s' "$slug" "$digest"
    else
        printf 'profile-%s' "$digest"
    fi
}

# Путь файла состояния: CLAUDE_USAGE_STATE (тестируемость) важнее XDG-пути.
# $HOME читаем через "${HOME:-}" — под set -u голый $HOME на окружении без
# HOME (напр. cron) уронит разбор параметра с текстом в stderr раньше, чем
# сработает любая обёртка "|| true" в вызывающем коде. Если не задан и
# XDG_STATE_HOME, и HOME — печатаем пустую строку: писать состояние всё
# равно некуда, write_state() эту пустоту ниже явно пропускает.
# Имя файла — profile_id().json: один профиль = один файл, читатель на
# python-стороне перечисляет каталог, не полагаясь на фиксированное имя.
state_path() {
    if [[ -n "${CLAUDE_USAGE_STATE:-}" ]]; then
        printf '%s' "$CLAUDE_USAGE_STATE"
        return
    fi
    local id; id="$(profile_id)"
    if [[ -n "${XDG_STATE_HOME:-}" ]]; then
        printf '%s/claude-usage/%s.json' "$XDG_STATE_HOME" "$id"
        return
    fi
    if [[ -n "${HOME:-}" ]]; then
        printf '%s/.local/state/claude-usage/%s.json' "$HOME" "$id"
        return
    fi
}

# Атомарная запись: mktemp в целевом каталоге (гарантия одной ФС с mv),
# 0600 выставляется до переименования, mv -f поверх старого файла.
# Префикс .tmp.* вместо latest.json.*: с файлом на профиль имя латентно
# совпало бы с маской *.json читателя только по случайности; отдельный
# префикс исключает эту гонку в принципе.
write_state() {
    local content="$1"
    local path dir tmp
    path="$(state_path)"
    if [[ -z "$path" ]]; then
        return 0
    fi
    dir="$(dirname -- "$path")"
    mkdir -p -m 0700 "$dir" 2>/dev/null || return 1
    tmp="$(mktemp "$dir/.tmp.XXXXXX" 2>/dev/null)" || return 1
    chmod 0600 "$tmp" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    printf '%s' "$content" > "$tmp" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
    mv -f "$tmp" "$path" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 1; }
}

main() {
    local raw
    raw="$(cat)" || raw=""

    # Полностью нечитаемый вход (пустой stdin) — файл состояния не трогаем,
    # печатаем пустую строку молча: контракт хука требует кода 0 и тишины
    # в stderr на любом входе, а не только на валидном JSON с лимитами.
    if [[ -z "$raw" ]]; then
        exit 0
    fi

    # jq на пустом/из-одних-пробелов вводе тихо возвращает "" с кодом 0
    # (ноль JSON-значений в потоке — ноль применений фильтра), поэтому
    # синтаксической проверкой `jq empty` тут не обойтись: нужен ещё и
    # непустой результат основного фильтра.
    local result
    result="$(printf '%s' "$raw" | jq -c \
        --arg raw_profile_label "${CLAUDE_USAGE_PROFILE_LABEL:-$(profile_id)}" \
        "$JQ_FILTER" 2>/dev/null)" || result=""
    if [[ -z "$result" ]]; then
        exit 0
    fi

    # Все три jq-вызова здесь защищены одинаково (2>/dev/null + || var=""):
    # под set -euo pipefail несловленный сбой любого из них уронит скрипт
    # или потянет текст ошибки в stderr, а контракт хука это запрещает
    # даже на входах, которые сегодня не могут сюда так сломаться.
    local status_line
    status_line="$(printf '%s' "$result" | jq -r '.status_line' 2>/dev/null)" || status_line=""

    # config_dir в файл не пишется: читателю он не нужен ни для чего, а вторая
    # запись пути создала бы источник правды, который некому сверять с диском.
    # profile.label берётся уже санированным из $result (.profile_label,
    # см. JQ_FILTER) — той же sanitize_label, что чистит display_name модели,
    # а не второй копией той же регулярки.
    local state_json
    state_json="$(printf '%s' "$result" | jq -c \
        --argjson epoch "$(date +%s)" \
        --arg profile_id "$(profile_id)" '
        {schema: 2, profile: {id: $profile_id, label: .profile_label},
         updated_epoch: $epoch, limits: .limits, order: .order}
        + (if has("extra_usage") then {extra_usage: .extra_usage} else {} end)
    ' 2>/dev/null)" || state_json=""

    # Запись состояния не должна ронять печать статус-строки — это
    # единственный вывод, который Claude Code реально показывает.
    if [[ -n "$state_json" ]]; then
        write_state "$state_json" || true
    fi

    printf '%s\n' "$status_line"
}

main
