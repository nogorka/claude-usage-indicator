# claude-usage-indicator

Индикатор лимитов Claude Code в верхней панели GNOME (Ubuntu, GNOME Shell,
AppIndicator3). Показывает окна 5 часов / 7 дней и любые per-model лимиты
(`model_scoped`, например «Fable»), которые сервер отдаёт в статус-строке.

## Статус

В разработке. Панельный индикатор с текстовым лейблом, меню и обновлением
раз в тик — работает и покрыт тестами. Окно деталей с полноценными барами
(`Gtk.LevelBar`) — следующий шаг, пока не реализовано. Перед тем как считать
демона готовым к постоянному использованию, ещё предстоит: живой прогон
глазами (сценарии из `docs/PLAN.md`), финальный аудит ветки и
`superpowers:verification-before-completion`.

## Как это устроено

Единственный источник данных — блок `rate_limits`, который Claude Code кладёт
в JSON на stdin хука `statusLine`. Схема данных, жёсткие ограничения проекта
и вся история решений — в [docs/PLAN.md](docs/PLAN.md).

```
statusLine (bash, bin/claude-statusline.sh)
    → пишет ~/.local/state/claude-usage/latest.json (права 0600)
        → демон (src/claude_usage_indicator) читает файл, рисует панель
```

Демон не делает сетевых запросов и не читает `~/.claude/.credentials.json`
или `CLAUDE_CODE_OAUTH_TOKEN` — у него нет доступа ни к чему, кроме файла
состояния, который положил хук.

## Жёсткие ограничения

1. Ноль сети — ни одного исходящего запроса ни из одного файла репозитория.
2. Ноль доступа к секретам — ни credentials.json, ни OAuth-токен.
3. Ноль новых зависимостей — только системный `/usr/bin/python3`, stdlib,
   `gi` (PyGObject), `jq`, `systemd --user`. Тесты — на stdlib `unittest`.
4. Ничего не копируется в систему — установка это симлинки и один
   сгенерированный юнит-файл.

## Установка

```bash
./install.sh          # ставит хук, юнит systemd --user, патчит settings.json
./install.sh --dry-run  # только печатает план, ничего не трогает
```

Что делает `install.sh`:

- патчит `~/.claude/settings.json`, добавляя блок `statusLine` (это первый
  шаг: если `statusLine` уже занят чужой конфигурацией, скрипт остановится,
  ничего не поставив — не будет полу-установленного автозапускаемого демона);
- симлинкает `bin/claude-statusline.sh` в `~/.local/bin/`;
- генерирует `~/.config/systemd/user/claude-usage-indicator.service` из
  шаблона (путь до репозитория подставляется в `PYTHONPATH`);
- включает и запускает юнит (`systemctl --user enable --now`).

Идемпотентен — повторный запуск не плодит дублей.

## Удаление

```bash
./uninstall.sh
./uninstall.sh --dry-run
```

Снимает юнит, симлинк и блок `statusLine` (свой — чужую конфигурацию не
трогает). Резервная копия `settings.json` перед правкой всегда лежит рядом:
`~/.claude/settings.json.bak`.

## Ограничение: свежесть данных

Блок `rate_limits` приходит от Claude Code только после первого ответа
модели в сессии и только у подписчиков — до этого момента панель показывает
последнее известное состояние (с визуальным затемнением, если оно устарело),
а не гарантированно свежие цифры. Это ограничение источника данных, не баг
демона.

## Разработка

```bash
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/test_statusline.sh
```

Структура: `src/claude_usage_indicator/` — демон (state.py читает и
валидирует файл состояния, bar.py считает текст/бары, indicator.py —
AppIndicator3/GTK3); `bin/claude-statusline.sh` — хук statusLine;
`scripts/patch-settings.py` — правка `settings.json` в изоляции от
install.sh/uninstall.sh; `systemd/` — шаблон юнита; `docs/PLAN.md` — план и
зафиксированные решения.
