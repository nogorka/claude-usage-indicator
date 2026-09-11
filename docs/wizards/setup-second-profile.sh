#!/usr/bin/env bash
#
# Заведение второго профиля Claude Code на этой машине.
#
# Исполняет человек за клавиатурой. Агент этот скрипт не запускает: внутри живой
# OAuth-логин в браузере и запись в хранилище учётных данных.
#
# Проза, «зачем» и разбор рисков — README.md рядом с этим файлом.

set -euo pipefail

MAIN_DIR="$HOME/.claude"
NEW_DIR="$HOME/.claude-personal"
MAIN_CRED="$MAIN_DIR/.credentials.json"
NEW_CRED="$NEW_DIR/.credentials.json"
STAMP="$(date +%Y%m%d-%H%M%S)"
SAFETY_COPY="$HOME/.claude-credentials-safety-$STAMP.json"

# Остаётся своим у каждого профиля. Всё, чего здесь нет, симлинкается в общий
# ~/.claude: так решено владелицей — «симлинкать всё общее». Список исключений
# держится коротким и печатается перед действием, чтобы решение было видно.
NEVER_SHARE=(
    # Учётные данные и переписка — прямое требование.
    .credentials.json projects history.jsonl
    # Состояние живых сессий и процессов: два профиля работают одновременно,
    # общие локи и снапшоты приводят к тому, что одна сессия видит чужой PID.
    sessions session-env session-data shell-snapshots ide paste-cache debug file-history
    # Расход и лимиты привязаны к аккаунту. Общий policy-limits.json отравил бы
    # ровно тот индикатор, ради которого второй профиль и заводится.
    telemetry metrics usage-data cost-tracker.log bash-commands.log
    policy-limits.json remote-settings.json mcp-health-cache.json mcp-needs-auth-cache.json
    .last-cleanup .last-update-result.json
    # Инертные склады конфигов MCP: Claude Code их не читает (серверы приходят
    # из плагинов), а внутри лежат литеральные токены сторонних сервисов.
    mcp.json mcp-configs
    # Машинные кэши и рабочее состояние скиллов.
    cache downloads state tasks .superpowers-sdd
    # ~/.claude — сам git-репозиторий; второй профиль не его рабочая копия.
    .git .gitignore .pytest_cache
)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '\033[33m   ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m\nОстановка: %s\033[0m\n' "$*" >&2; exit 1; }

# Страховочная копия — живые токены рабочего аккаунта на диске (шаг 6 предлагает
# её удалить, но только на нормальном финише). Всё, что выходит раньше — die() на
# любом шаге после копирования, Ctrl-C, любая другая причина — не должно оставлять
# человека с забытым файлом секретов: он про него больше ниоткуда не узнает.
# Удалять здесь нельзя: при неудачном логине это единственный путь назад.
WIZARD_COMPLETED=0
warn_if_safety_copy_left() {
    [[ -f "$SAFETY_COPY" && "$WIZARD_COMPLETED" != 1 ]] || return 0
    warn "визард остановился, страховочная копия токенов ещё на диске: $SAFETY_COPY"
    warn "удали её руками, когда убедишься, что рабочий профиль цел, или используй как откат:"
    warn "  cp -p '$SAFETY_COPY' '$MAIN_CRED'"
}
trap warn_if_safety_copy_left EXIT

confirm() {
    local ans
    read -rp "$1 [y/N] " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]]
}

in_never_share() {
    local needle="$1" item
    for item in "${NEVER_SHARE[@]}"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

# Идентичность аккаунта без печати токенов: только вердикт «совпало / не совпало».
cred_field() {
    python3 - "$1" "$2" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except (OSError, ValueError):
    print("")
    sys.exit(0)
key = sys.argv[2]
if key == "org":
    print(data.get("organizationUuid", ""))
elif key == "mcp_count":
    print(len(data.get("mcpOAuth", {})))
elif key == "account_token":
    print(data.get("claudeAiOauth", {}).get("accessToken", ""))
PY
}

say "Предполётные проверки"

[[ "$(id -u)" != "0" ]] || die "запущено от root. Профиль должен принадлежать твоему пользователю."
[[ -z "${CLAUDE_CONFIG_DIR:-}" ]] || die "в этой оболочке уже выставлен CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR}. Открой чистый терминал: иначе неясно, в какой профиль пойдёт логин."
command -v claude >/dev/null || die "claude не найден в PATH."
command -v python3 >/dev/null || die "python3 не найден: без него не перенести логины MCP."
[[ -d "$MAIN_DIR" ]] || die "нет каталога $MAIN_DIR."
[[ -f "$MAIN_CRED" ]] || die "нет $MAIN_CRED — рабочий профиль не залогинен, переносить нечего."

note "claude: $(claude --version)"
note "рабочий профиль: $MAIN_DIR"
note "новый профиль:   $NEW_DIR"

HOME_CONFIG="$HOME/.claude.json"
HOME_CONFIG_HASH_BEFORE="$( [[ -f "$HOME_CONFIG" ]] && sha256sum "$HOME_CONFIG" | cut -d' ' -f1 || echo "нет файла" )"
MAIN_ORG_BEFORE="$(cred_field "$MAIN_CRED" org)"
MAIN_MCP_BEFORE="$(cred_field "$MAIN_CRED" mcp_count)"
MAIN_HASH_BEFORE="$(sha256sum "$MAIN_CRED" | cut -d' ' -f1)"
note "логинов MCP в рабочем профиле: $MAIN_MCP_BEFORE"

if pgrep -u "$USER" -f '(^|/)claude(\.exe)?( |$)' >/dev/null 2>&1; then
    warn "на машине есть живые процессы claude."
    warn "работающая сессия может перезаписать .credentials.json поверх того, что сделает визард."
    confirm "Всё равно продолжать?" || die "закрой сессии Claude и запусти визард заново."
fi

say "Страховочная копия учётных данных рабочего профиля"
note "Копия нужна ровно на один случай: если логин уйдёт не в тот профиль и затрёт"
note "рабочий аккаунт вместе с $MAIN_MCP_BEFORE логинами MCP. Удалим её на шаге 6."
confirm "Сделать копию в $SAFETY_COPY?" || die "без страховки визард не идёт: цена ошибки — повторный логин в 15 сервисов."
cp -p "$MAIN_CRED" "$SAFETY_COPY"
chmod 600 "$SAFETY_COPY"
note "готово. Аварийный откат рабочего профиля: cp -p '$SAFETY_COPY' '$MAIN_CRED'"

say "Шаг 1. Создать каталог второго профиля"
if [[ -e "$NEW_DIR" ]]; then
    [[ -d "$NEW_DIR" ]] || die "$NEW_DIR существует и это не каталог."
    note "каталог уже есть — пропускаю (шаг идемпотентный)."
else
    note "будет создан $NEW_DIR с правами 700"
    confirm "Создать?" || die "отменено на шаге 1."
    mkdir -m 700 "$NEW_DIR"
    note "создан. Откат: rmdir '$NEW_DIR'"
fi

say "Шаг 2. Симлинки общей части харнесса"

linked=(); skipped=(); conflicts=(); already=()
while IFS= read -r -d '' entry; do
    if in_never_share "$entry"; then
        skipped+=("$entry")
    elif [[ -L "$NEW_DIR/$entry" ]]; then
        if [[ "$(readlink "$NEW_DIR/$entry")" == "$MAIN_DIR/$entry" ]]; then
            already+=("$entry")
        else
            conflicts+=("$entry")
        fi
    elif [[ -e "$NEW_DIR/$entry" ]]; then
        conflicts+=("$entry")
    else
        linked+=("$entry")
    fi
done < <(find "$MAIN_DIR" -mindepth 1 -maxdepth 1 -printf '%f\0' | sort -z)

note "СВОИМ у второго профиля останется (${#skipped[@]}):"
printf '     %s\n' "${skipped[@]}"
note ""
note "ОБЩИМ через симлинк станет (${#linked[@]} новых, ${#already[@]} уже на месте):"
[[ ${#linked[@]} -eq 0 ]] || printf '     %s\n' "${linked[@]}"
if [[ ${#conflicts[@]} -gt 0 ]]; then
    warn "в $NEW_DIR уже лежит своё — файл или симлинк на сторону; не трогаю (${#conflicts[@]}):"
    printf '     %s\n' "${conflicts[@]}"
fi
note ""
note "Смотри список глазами: всё, что окажется общим, второй аккаунт сможет читать и писать."
note "В списке нет .claude.json: он лежит не внутри $MAIN_DIR, а рядом — $HOME/.claude.json."
note "У второго профиля он должен появиться свой, в $NEW_DIR. Это проверяется на шаге 3."

if [[ ${#linked[@]} -gt 0 ]]; then
    confirm "Создать ${#linked[@]} симлинков?" || die "отменено на шаге 2."
    for entry in "${linked[@]}"; do
        ln -s "$MAIN_DIR/$entry" "$NEW_DIR/$entry"
    done
    rollback=""
    for entry in "${linked[@]}"; do
        rollback+="'$NEW_DIR/$entry' "
    done
    note "готово. Откат: rm -f $rollback"
else
    note "создавать нечего."
fi

say "Шаг 3. Логин второго аккаунта — НЕОБРАТИМЫЙ ШАГ"
warn "Откроется браузер. Входи ВТОРЫМ аккаунтом, не рабочим."
warn "Если войти тем же аккаунтом, второй подписки не появится — будет две копии одной."
note "Команда: CLAUDE_CONFIG_DIR='$NEW_DIR' claude auth login --claudeai"
note "Переменная выставляется только для этой команды, оболочка её не наследует."
note "Что нужно под рукой: почта и пароль второго аккаунта Claude (вводятся в браузере,"
note "визард их не спрашивает и нигде не сохраняет)."
confirm "Запускать логин?" || die "отменено на шаге 3."

login_rc=0
CLAUDE_CONFIG_DIR="$NEW_DIR" claude auth login --claudeai || login_rc=$?
[[ $login_rc -eq 0 ]] || warn "claude auth login вернул код $login_rc — проверки ниже покажут, что реально произошло."

say "Проверка: рабочий профиль не задет"
MAIN_ORG_AFTER="$(cred_field "$MAIN_CRED" org)"
MAIN_MCP_AFTER="$(cred_field "$MAIN_CRED" mcp_count)"
MAIN_HASH_AFTER="$(sha256sum "$MAIN_CRED" | cut -d' ' -f1)"

if [[ "$MAIN_ORG_AFTER" != "$MAIN_ORG_BEFORE" || "$MAIN_MCP_AFTER" != "$MAIN_MCP_BEFORE" ]]; then
    warn "рабочий профиль ИЗМЕНИЛСЯ: организация $( [[ "$MAIN_ORG_AFTER" == "$MAIN_ORG_BEFORE" ]] && echo 'та же' || echo 'ДРУГАЯ'), логинов MCP было $MAIN_MCP_BEFORE, стало $MAIN_MCP_AFTER."
    die "логин ушёл не в тот профиль. Восстанови рабочий профиль: cp -p '$SAFETY_COPY' '$MAIN_CRED'"
fi
if [[ "$MAIN_HASH_AFTER" != "$MAIN_HASH_BEFORE" ]]; then
    note "файл рабочего профиля переписан, но аккаунт и все $MAIN_MCP_AFTER логинов MCP на месте"
    note "(так выглядит обычное обновление токена живой сессией)."
else
    note "рабочий профиль байт в байт тот же."
fi

[[ -f "$NEW_CRED" ]] || die "во втором профиле не появился .credentials.json — логин не состоялся. Откат: rm -rf '$NEW_DIR'"
note "второй профиль залогинен. Откат шага: CLAUDE_CONFIG_DIR='$NEW_DIR' claude auth logout"

say "Проверка: изоляция конфигурации"
if [[ -f "$NEW_DIR/.claude.json" ]]; then
    note "у второго профиля появился свой .claude.json — CLAUDE_CONFIG_DIR изолирует конфигурацию."
else
    HOME_CONFIG_HASH_AFTER="$( [[ -f "$HOME_CONFIG" ]] && sha256sum "$HOME_CONFIG" | cut -d' ' -f1 || echo "нет файла" )"
    if [[ "$HOME_CONFIG_HASH_AFTER" != "$HOME_CONFIG_HASH_BEFORE" ]]; then
        warn "своего .claude.json у второго профиля нет, а рабочий $HOME_CONFIG изменился."
        warn "Похоже, CLAUDE_CONFIG_DIR не уводит .claude.json и профили делят конфигурацию."
        warn "Это единственное место процедуры, где факт взят чтением бинарника, а не запуском."
        note "Проверь руками: CLAUDE_CONFIG_DIR='$NEW_DIR' claude — и смотри, появился ли $NEW_DIR/.claude.json"
        confirm "Продолжать, зная это?" || die "остановлено до выяснения. Откат: rm -rf '$NEW_DIR'"
    else
        note ".claude.json второго профиля появится при первом запуске сессии."
        note "Проверь тогда, что он лёг в $NEW_DIR, а не в $HOME."
    fi
fi

say "Проверка: аккаунты разные"
if [[ "$(cred_field "$MAIN_CRED" account_token)" == "$(cred_field "$NEW_CRED" account_token)" ]]; then
    warn "в оба профиля залогинен ОДИН И ТОТ ЖЕ аккаунт: два окна лимитов будут показывать одно и то же."
    confirm "Всё равно продолжать перенос логинов MCP?" || die "перелогинься вторым аккаунтом: CLAUDE_CONFIG_DIR='$NEW_DIR' claude auth login --claudeai"
else
    note "токены аккаунтов различаются — профили независимы."
fi

say "Шаг 4. Перенос логинов MCP во второй профиль"
note "Переносится ключ mcpOAuth целиком ($MAIN_MCP_AFTER записей): решение владелицы — «перенести все»."
note "Ключи claudeAiOauth и organizationUuid во втором профиле не трогаются."
warn "Последствие, которое стоит знать: токены сторонних сервисов копируются, но не"
warn "синхронизируются. Когда один профиль обновит токен, копия в другом протухнет и"
warn "тот сервис попросит авторизацию заново — это нормально, а не поломка."
confirm "Переносить?" || { note "пропущено. Второй профиль будет просить авторизацию MCP сам."; SKIP_MCP=1; }

if [[ -z "${SKIP_MCP:-}" ]]; then
    cp -p "$NEW_CRED" "$NEW_CRED.bak-$STAMP"
    chmod 600 "$NEW_CRED.bak-$STAMP"
    python3 - "$MAIN_CRED" "$NEW_CRED" <<'PY'
import json, os, sys, tempfile

src_path, dst_path = sys.argv[1], sys.argv[2]
with open(src_path) as fh:
    src = json.load(fh)
with open(dst_path) as fh:
    dst = json.load(fh)

carried = src.get("mcpOAuth", {})
merged = dict(dst.get("mcpOAuth", {}))
merged.update(carried)
dst["mcpOAuth"] = merged

# Личность второго аккаунта не трогаем: переносятся только логины MCP.
assert dst.get("claudeAiOauth") is not None, "во втором профиле нет claudeAiOauth"

directory = os.path.dirname(dst_path)
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".credentials.json.tmp")
try:
    with os.fdopen(fd, "w") as fh:
        json.dump(dst, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst_path)
except BaseException:
    os.unlink(tmp)
    raise

print(f"   перенесено записей: {len(carried)}, всего во втором профиле: {len(merged)}")
for name in sorted(carried):
    print(f"     {name}")
PY
    note "готово. Откат: cp -p '$NEW_CRED.bak-$STAMP' '$NEW_CRED'"
fi

say "Шаг 5. Сверка обоих профилей"
note "рабочий профиль:"
claude auth status --text 2>&1 | sed 's/^/     /' || warn "claude auth status вернул ошибку"
note "второй профиль:"
CLAUDE_CONFIG_DIR="$NEW_DIR" claude auth status --text 2>&1 | sed 's/^/     /' || warn "claude auth status вернул ошибку"
note "логинов MCP: рабочий $(cred_field "$MAIN_CRED" mcp_count), второй $(cred_field "$NEW_CRED" mcp_count)"
note "права на хранилища: $(stat -c '%a' "$MAIN_CRED") и $(stat -c '%a' "$NEW_CRED") (должно быть 600 и 600)"

say "Шаг 6. Убрать страховочную копию"
warn "В $SAFETY_COPY лежат живые токены рабочего аккаунта. Пока она есть — это лишняя копия секретов на диске."
note "Удаляй, только если проверки выше сошлись."
if confirm "Удалить страховочную копию?"; then
    rm -f "$SAFETY_COPY"
    note "удалена."
else
    warn "оставлена: $SAFETY_COPY — удали руками, когда убедишься, что всё работает."
fi

say "Готово"
note "Запуск второго профиля:"
note "  CLAUDE_CONFIG_DIR='$NEW_DIR' claude"
note "Подпись профиля для индикатора (по плану мультипрофиля):"
note "  CLAUDE_CONFIG_DIR='$NEW_DIR' CLAUDE_USAGE_PROFILE_LABEL='Личный' claude"
note ""
if [[ -f "$NEW_CRED.bak-$STAMP" ]]; then
    note "Бэкап шага 4 остался на диске — это копия токенов. Удали, когда всё сойдётся:"
    note "  rm -f '$NEW_CRED.bak-$STAMP'"
    note ""
fi
note "Полный откат всей процедуры:"
note "  CLAUDE_CONFIG_DIR='$NEW_DIR' claude auth logout && rm -rf '$NEW_DIR'"
note "Рабочего профиля это не касается: в нём ничего не менялось."

WIZARD_COMPLETED=1
