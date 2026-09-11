# План: два аккаунта Claude в одной панели

> **Исполнителю:** обязательный суб-скилл — `superpowers:subagent-driven-development`.
> Шаги отмечаются чекбоксами `- [ ]`.

**Спека:** `docs/superpowers/specs/2026-09-10-multi-profile-design.md` — план спорит со
спекой, читать оба.

**Стек:** Python 3.12 stdlib (без внешних зависимостей), GTK3 через `gi.repository`,
Ayatana AppIndicator3, bash + jq в хуке, тесты — stdlib `unittest` плюс два bash-набора
со своим раннером.

## Задача

Научить индикатор показывать в панели состояние двух профилей Claude одновременно и
запускать сессию в любом из них пунктом меню, не трогая ни сеть, ни учётные данные.

## Глобальные ограничения

Действуют на каждую задачу, повторять в шагах не нужно.

- Ни одного обращения к сети. Ни одного чтения `.credentials.json`, `.claude.json`
  и любых токенов. Индикатор читает только собственные файлы состояния.
- Внешних зависимостей не добавлять: только stdlib и уже используемые `gi`, `jq`.
- Функция до 50 строк, файл до 800, вложенность до 4.
- Комментарий отвечает на «почему», а не на «что». Пересказ соседней строки, журнал
  правки и позиционные разделители — мусор, вычищать до коммита.
- Коммит `<type>: <описание>`, типы feat, fix, refactor, docs, test, chore.
- Цикл TDD: RED → GREEN → REFACTOR. Тест пишется первым и сначала обязан упасть.
- Идентификатор профиля выводится **только** из `CLAUDE_CONFIG_DIR`.
  `CLAUDE_USAGE_PROFILE_LABEL` — исключительно косметика, на выбор файла не влияет.

## Критерии приёмки

- [x] Готово, когда при двух файлах состояния бар показывает оба профиля со связывающим
      окном каждого и компактной меткой его сброса (`↻HH:MM` в пределах суток, `↻DD.MM`
      дальше, пусто у уже сброшенного окна), подтверждается `PYTHONPATH=src
      /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`.
- [x] Готово, когда профиль с `updated_epoch` старше часа помечен в баре суффиксом
      несвежести, а в секции меню несёт строку с датой и временем снимка; подтверждается
      `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`,
      тесты `MultiProfileBarTests.test_stale_profile_is_marked_and_the_fresh_one_is_not`
      и `MenuSectionTests.test_section_lines_name_the_profile_and_every_window`.
- [x] Готово, когда окно с `resets_epoch <= now` показывает `0%` и текст «window reset
      at …», а не сохранённый процент; подтверждается `test_bar.py`.
- [x] Готово, когда битый или посторонний `*.json` в каталоге состояния не мешает
      прочитать остальные профили и попадает в список проблем чтения; подтверждается
      `test_state.py`.
- [x] Готово, когда при единственном профиле на непросроченных данных строка бара
      совпадает с сегодняшней посимвольно; подтверждается `PYTHONPATH=src
      /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`, тест
      `MultiProfileBarTests.test_single_profile_renders_exactly_as_before`.
- [x] Готово, когда `discover_profiles` находит `~/.claude-personal` с
      `.credentials.json` и не находит каталог без него; подтверждается `test_profiles.py`.
- [x] Готово, когда хук, запущенный с `CLAUDE_CONFIG_DIR=<tmp>/.claude-personal`, пишет
      `personal.json` с блоком `profile` и `schema: 2`; подтверждается
      `bash tests/test_statusline.sh`.
- [x] Готово, когда время сброса каждого окна показано у обоих профилей независимо от
      свежести снимка; подтверждается `PYTHONPATH=src /usr/bin/python3 -m unittest
      discover -s tests -p 'test_bar.py' -v`, тест
      `MenuSectionTests.test_section_lines_carry_a_reset_text_for_every_window`.
- [ ] Готово, когда меню трея несёт по пункту «Open Claude — …» на каждый найденный на
      диске профиль и пункт стартует сессию с `CLAUDE_CONFIG_DIR` этого профиля;
      автоматическая часть подтверждается `PYTHONPATH=src /usr/bin/python3 -m unittest
      discover -s tests -p 'test_launcher.py' -v`, доставка переменной до оболочки —
      ручной проверкой 8.4.
- [x] Готово, когда полный прогон трёх наборов зелёный.

Критерия на покрытие тестами здесь нет намеренно: решением владелицы 2026-09-11 числовое
покрытие снято с требований. Покрытие обеспечивается порядком работы — каждый шаг
начинается с падающего теста, — а не измерением.

## Мандат автономии

- Решаю сам: имена приватных функций, не названных в шагах; разбиение файлов; порядок
  шагов внутри ветки графа; состав фикстур.
- Не решаю сам: имена тестов, английские строки интерфейса и тексты сообщений, заданные
  в шагах дословно. На них ссылаются критерии приёмки и ручные проверки; переименование
  тихо обесценивает критерий.
- Останавливаюсь и зову: любое действие, которое пишет в `~/.claude`, `~/.claude-*`,
  `.credentials.json` или `settings.json` живого пользователя; отправка чего-либо
  наружу; план неверен по существу; три итерации ревью не сошлись.
- Вопросы копятся и задаются пачкой в финальном отчёте.

## Граф зависимостей

| # | Шаг | Зависит от | Идёт параллельно с | Критерий приёмки шага |
|---|-----|-----------|--------------------|------------------------|
| 1 | `profiles.py`: идентификатор и поиск на диске | — | 2, 3, 4 | `test_profiles.py` зелёный |
| 2 | `state.py`: схема 2 и чтение каталога | — | 1, 3, 4 | `test_state.py` зелёный |
| 3 | `bar.py`: правило просроченного окна | — | 1, 2, 4 | `test_bar.py` зелёный |
| 4 | `patch-settings.py`: запрет писать в симлинк | — | 1, 2, 3 | `test_patch_settings.py` зелёный |
| 5 | `claude-statusline.sh`: профиль и файл на профиль | 2 | 6, 7 | `tests/test_statusline.sh` зелёный |
| 6 | `bar.py`: мультипрофильный бар | 1, 2, 3 | 5, 7 | `test_bar.py` зелёный |
| 7 | `launcher.py`: команда запуска терминала | 1 | 5, 6 | `test_launcher.py` зелёный |
| 8 | `indicator.py`: меню по профилям и запуск | 1, 2, 6, 7 | 9 | ручной прогон демона |
| 9 | `window.py`: окно «Подробнее» по профилям | 1, 2, 6 | 8 | ручной прогон окна |
| 10 | `install.sh`, README, полный прогон | 1-9 | — | три набора зелёные |

Волны: **1** = шаги 1-4, **2** = 5-7, **3** = 8-9, **4** = 10. Внутри волны шаги трогают
разные файлы, worktree не нужен. Рантайм-изоляция неприменима: ни БД, ни портов нет.

Шаги 8 и 9 параллельны только потому, что весь общий код — `menu_section_lines` и
`unreadable_line` — лежит в шаге 6 и к началу третьей волны уже в ветке. Если эти
функции переедут в шаг 8, параллельность исчезнет: шаг 9 в своём дереве получит
`AttributeError`.

---

## Шаги

### 1. `profiles.py` — идентификатор профиля и поиск на диске

**Файлы:**
- Создать: `src/claude_usage_indicator/profiles.py`
- Тест: `tests/test_profiles.py`

**Интерфейсы:**
- Потребляет: ничего.
- Отдаёт: `Profile(id: str, label: str, config_dir: Path)` — frozen dataclass;
  `profile_id_from_config_dir(config_dir: str | os.PathLike[str] | None) -> str`;
  `discover_profiles(home: Path | None = None) -> list[Profile]`;
  `profile_sort_key(profile_id: str) -> tuple[int, str]`.

- [x] **Шаг 1.1: Написать падающий тест**

```python
# tests/test_profiles.py
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_usage_indicator import profiles


class ProfileIdTests(unittest.TestCase):
    def test_unset_config_dir_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(None), "default")

    def test_empty_string_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(""), "default")

    def test_home_claude_dir_is_default_profile(self):
        self.assertEqual(profiles.profile_id_from_config_dir(Path.home() / ".claude"), "default")

    def test_claude_prefix_is_stripped(self):
        self.assertEqual(
            profiles.profile_id_from_config_dir(Path.home() / ".claude-personal"), "personal"
        )

    def test_trailing_slash_does_not_change_id(self):
        self.assertEqual(
            profiles.profile_id_from_config_dir(f"{Path.home()}/.claude-personal/"), "personal"
        )

    def test_unsafe_characters_are_replaced(self):
        self.assertEqual(profiles.profile_id_from_config_dir("/tmp/.claude-Work Acct!"), "work-acct")

    def test_name_that_sanitizes_to_nothing_falls_back_to_default(self):
        self.assertEqual(profiles.profile_id_from_config_dir("/tmp/.claude-!!!"), "default")

    def test_sort_key_puts_default_first(self):
        self.assertEqual(
            sorted(["personal", "default", "alpha"], key=profiles.profile_sort_key),
            ["default", "alpha", "personal"],
        )


class DiscoverProfilesTests(unittest.TestCase):
    def test_directory_without_credentials_is_not_a_profile(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude-empty").mkdir()
            self.assertEqual(profiles.discover_profiles(home), [])

    def test_directory_with_credentials_is_a_profile(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in (".claude", ".claude-personal"):
                (home / name).mkdir()
                (home / name / ".credentials.json").write_text("{}", encoding="utf-8")
            found = profiles.discover_profiles(home)
            self.assertEqual([p.id for p in found], ["default", "personal"])
            self.assertEqual(found[1].config_dir, home / ".claude-personal")

    def test_credentials_file_is_never_opened(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            secret = home / ".claude" / ".credentials.json"
            secret.write_text("{}", encoding="utf-8")
            os.chmod(secret, 0o000)
            self.addCleanup(os.chmod, secret, 0o600)
            self.assertEqual([p.id for p in profiles.discover_profiles(home)], ["default"])
```

- [x] **Шаг 1.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_profiles.py' -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'claude_usage_indicator.profiles'`.

- [x] **Шаг 1.3: Реализовать минимум**

```python
# src/claude_usage_indicator/profiles.py
"""Профили Claude на машине: идентификатор по каталогу конфига и поиск заведённых.

Идентификатор выводится только из пути каталога конфига — из той самой переменной,
которая физически разделяет аккаунты. Отдельного имени профиля намеренно нет: забытая
переменная означала бы два аккаунта в одном файле состояния и молча врущую панель.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ID = "default"
_UNSAFE = re.compile(r"[^a-z0-9_-]+")
_CLAUDE_PREFIX = "claude-"


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    config_dir: Path


def profile_id_from_config_dir(config_dir: str | os.PathLike[str] | None) -> str:
    """Идентификатор профиля по каталогу конфига Claude Code.

    Пустое значение и $HOME/.claude дают DEFAULT_ID: так выглядит установка без
    CLAUDE_CONFIG_DIR, и её файл состояния не должен переезжать при апгрейде.
    """
    if not config_dir:
        return DEFAULT_ID
    resolved = _resolve(Path(os.path.expanduser(str(config_dir))))
    if resolved == _resolve(Path.home() / ".claude"):
        return DEFAULT_ID
    name = resolved.name.lstrip(".").lower()
    if name.startswith(_CLAUDE_PREFIX):
        name = name[len(_CLAUDE_PREFIX):]
    return _UNSAFE.sub("-", name).strip("-") or DEFAULT_ID


def _resolve(path: Path) -> Path:
    """Разрешение пути не должно падать на битом симлинке: сравнение важнее точности."""
    try:
        return path.resolve()
    except OSError:
        return path


def discover_profiles(home: Path | None = None) -> list[Profile]:
    """Профили, реально заведённые на диске.

    Признак — существование `.credentials.json` внутри каталога. Файл именно
    проверяется на существование и никогда не открывается: индикатор не имеет дела
    с учётными данными.
    """
    base = home or Path.home()
    found: dict[str, Profile] = {}
    for candidate in [base / ".claude", *sorted(base.glob(".claude-*"))]:
        if not candidate.is_dir() or not (candidate / ".credentials.json").exists():
            continue
        profile_id = profile_id_from_config_dir(candidate)
        found.setdefault(
            profile_id, Profile(id=profile_id, label=profile_id, config_dir=candidate)
        )
    return sorted(found.values(), key=lambda profile: profile_sort_key(profile.id))


def profile_sort_key(profile_id: str) -> tuple[int, str]:
    """Порядок профилей в интерфейсе: default первым, остальные по алфавиту.

    Порядок обязан быть стабильным между тиками — метки, прыгающие в панели местами,
    нечитаемы. Поэтому сортировка не зависит ни от свежести, ни от расхода.
    """
    return (0 if profile_id == DEFAULT_ID else 1, profile_id)
```

> **Правило снятого правила (фикс-раунд 2).** Санитизация выше — `.lower()` (не ASCII)
> и `.strip("-") or DEFAULT_ID` без хэша — заморожена этим шагом, но заморозка снята
> фикс-раундом 2: правило неинъективно (`~/.claude-личный` и `~/.claude-работа`
> схлопывались в один `default`). Действующее правило — «слаг + хэш там, где слаг
> теряет информацию» — описано в `.superpowers/sdd/2026-09-10-multi-profile/final-fix-round-2-brief.md`
> и реализовано в текущем `src/claude_usage_indicator/profiles.py`; тесты выше и
> тестовый код `profile_id()` в шаге 5.3 ниже показывают код на момент раунда 1,
> а не сегодняшний.

- [x] **Шаг 1.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_profiles.py' -v`
Ожидается: PASS, 11 тестов.

- [x] **Шаг 1.5: Коммит**

```bash
git add src/claude_usage_indicator/profiles.py tests/test_profiles.py
git commit -m "feat: идентификатор профиля Claude и поиск заведённых на диске"
```

---

### 2. `state.py` — схема 2 и чтение каталога целиком

**Файлы:**
- Изменить: `src/claude_usage_indicator/state.py`
- Тест: `tests/test_state.py`
- Фикстуры: `tests/fixtures/`

**Интерфейсы:**
- Потребляет: ничего (`profiles.py` намеренно не импортируется — порядок это забота
  рендера, а не разбора).
- Отдаёт: `ProfileSnapshot(profile_id: str, label: str, snapshot: Snapshot)`;
  `Reading(profiles: Mapping[str, ProfileSnapshot], unreadable: Sequence[str])`;
  `state_dir() -> Path`; `read_all_states(directory: Path | None = None) -> Reading`.
  Существующие `Snapshot`, `Window`, `ExtraUsage`, `state_path()`, `read_state()`
  остаются с прежними сигнатурами и прежним поведением.

- [x] **Шаг 2.1: Написать падающий тест**

Методы ниже добавляются в существующий класс тестов каталога состояния; импорты — в
шапку файла, если их там ещё нет.

```python
# добавить в tests/test_state.py
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from claude_usage_indicator import state


def _write(directory: Path, name: str, payload: dict) -> None:
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


_LIMITS = {"five_hour": {"percent": 42.0, "resets_epoch": 1788550200}}


class ReadAllStatesTests(unittest.TestCase):
    def test_schema_1_file_without_profile_block_reads_as_default(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write(directory, "latest.json", {"schema": 1, "updated_epoch": 10, "limits": _LIMITS})
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"default"})
            self.assertEqual(reading.profiles["default"].label, "default")

    def test_schema_2_file_carries_its_own_id_and_label(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write(
                directory,
                "personal.json",
                {
                    "schema": 2,
                    "profile": {"id": "personal", "label": "own"},
                    "updated_epoch": 20,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(reading.profiles["personal"].label, "own")

    def test_duplicate_ids_keep_the_fresher_file(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write(directory, "latest.json", {"schema": 1, "updated_epoch": 10, "limits": _LIMITS})
            _write(
                directory,
                "default.json",
                {
                    "schema": 2,
                    "profile": {"id": "default", "label": "work"},
                    "updated_epoch": 99,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"default"})
            self.assertEqual(reading.profiles["default"].snapshot.updated_epoch, 99)

    def test_broken_file_does_not_hide_the_others(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "broken.json").write_text("{not json", encoding="utf-8")
            _write(
                directory,
                "personal.json",
                {
                    "schema": 2,
                    "profile": {"id": "personal", "label": "own"},
                    "updated_epoch": 20,
                    "limits": _LIMITS,
                },
            )
            reading = state.read_all_states(directory)
            self.assertEqual(set(reading.profiles), {"personal"})
            self.assertEqual(list(reading.unreadable), ["broken.json"])

    def test_unknown_schema_version_is_unreadable_not_fatal(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write(directory, "future.json", {"schema": 99, "updated_epoch": 1, "limits": _LIMITS})
            reading = state.read_all_states(directory)
            self.assertEqual(reading.profiles, {})
            self.assertEqual(list(reading.unreadable), ["future.json"])

    def test_missing_directory_yields_empty_reading(self):
        with TemporaryDirectory() as tmp:
            reading = state.read_all_states(Path(tmp) / "nope")
            self.assertEqual(reading.profiles, {})
            self.assertEqual(list(reading.unreadable), [])

    def test_state_dir_follows_claude_usage_state_parent(self):
        with unittest.mock.patch.dict(
            "os.environ", {"CLAUDE_USAGE_STATE": "/tmp/x/custom.json"}, clear=False
        ):
            self.assertEqual(state.state_dir(), Path("/tmp/x"))
```

- [x] **Шаг 2.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_state.py' -v`
Ожидается: FAIL, `AttributeError: module 'claude_usage_indicator.state' has no attribute 'read_all_states'`.

- [x] **Шаг 2.3: Реализовать минимум**

```python
# добавить в src/claude_usage_indicator/state.py

_SUPPORTED_SCHEMAS = (1, 2)


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_id: str
    label: str
    snapshot: Snapshot


@dataclass(frozen=True)
class Reading:
    """Снимок всего каталога состояния.

    `unreadable` отделён от `problem` внутри снимков намеренно: у файла, который не
    разобрался, профиля нет по определению, и приписать его проблему чужому профилю
    значило бы соврать.
    """

    profiles: Mapping[str, ProfileSnapshot]
    unreadable: Sequence[str]


def state_dir() -> Path:
    """Каталог файлов состояния — по файлу на профиль.

    CLAUDE_USAGE_STATE указывает на файл, а не на каталог: он остаётся точкой
    переопределения для тестов и ручного пиннинга, и тогда каталогом считается его
    родитель.
    """
    override = os.environ.get("CLAUDE_USAGE_STATE")
    if override:
        return Path(override).parent
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "claude-usage"


def read_all_states(directory: Path | None = None) -> Reading:
    """Все профили каталога. Мусорный файл пропускается, а не роняет чтение целиком:
    панель с одним битым файлом обязана показывать остальные профили."""
    base = directory if directory is not None else state_dir()
    freshest: dict[str, tuple[ProfileSnapshot, int]] = {}
    unreadable: list[str] = []
    for path in _state_files(base):
        parsed = _read_profile_file(path)
        if parsed is None:
            unreadable.append(path.name)
            continue
        current = freshest.get(parsed.profile_id)
        age = parsed.snapshot.updated_epoch if parsed.snapshot.updated_epoch is not None else -1
        if current is None or age > current[1]:
            freshest[parsed.profile_id] = (parsed, age)
    return Reading(
        profiles=MappingProxyType({key: entry for key, (entry, _) in freshest.items()}),
        unreadable=tuple(unreadable),
    )


def _state_files(base: Path) -> list[Path]:
    try:
        return sorted(base.glob("*.json"))
    except OSError:
        return []


def _read_profile_file(path: Path) -> ProfileSnapshot | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") not in _SUPPORTED_SCHEMAS:
        return None
    block = payload.get("profile")
    if not isinstance(block, dict):
        block = {}
    profile_id = block.get("id")
    if not isinstance(profile_id, str) or not profile_id:
        profile_id = "default"
    # config_dir в файл не пишется: читателю он не нужен ни для чего. Путь к каталогу
    # конфигурации берётся с диска в discover_profiles, и запись его ещё и в состояние
    # создала бы второй источник правды, который некому сверять.
    label = block.get("label")
    if not isinstance(label, str) or not label:
        label = profile_id
    return ProfileSnapshot(profile_id=profile_id, label=label, snapshot=_parse_payload(payload))
```

Разбор тела снимка уже существует внутри `read_state()`. Вынести его в
`_parse_payload(payload: dict) -> Snapshot` и вызвать из обоих мест: дублировать разбор
`limits`/`order`/`extra_usage` нельзя, иначе схемы разъедутся. Поведение и сигнатура
`read_state()` при этом не меняются — на них держатся существующие 44 теста.

- [x] **Шаг 2.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_state.py' -v`
Ожидается: PASS, 44 прежних теста плюс 7 новых.

- [x] **Шаг 2.5: Коммит**

```bash
git add src/claude_usage_indicator/state.py tests/test_state.py
git commit -m "feat: схема состояния 2 и чтение каталога профилей целиком"
```

---

### 3. `bar.py` — правило просроченного окна

**Файлы:**
- Изменить: `src/claude_usage_indicator/bar.py:52-125`
- Тест: `tests/test_bar.py`

**Интерфейсы:**
- Потребляет: `state.Window`.
- Отдаёт: `is_expired(window: Window, now_epoch: float) -> bool`;
  `effective_percent(window: Window, now_epoch: float) -> float`.
- **Меняет сигнатуры** (это ломающее изменение внутри пакета, не дополнение):
  `panel_label(snapshot, now_epoch)` вместо `panel_label(snapshot)`;
  `is_alarm(snapshot, now_epoch, threshold=80.0)` вместо `is_alarm(snapshot, threshold=80.0)`.
  `panel_state(snapshot, now_epoch)` и `format_reset(resets_epoch, now_epoch)` уже
  принимают время — их сигнатуры не меняются, меняется только поведение `format_reset`
  в ветке `delta_s <= 0`.

Одиннадцать вызовов обязаны обновиться в этом же шаге, иначе набор красный:
`src/claude_usage_indicator/bar.py:85` (`is_alarm`) и `:86` (`panel_label`);
`tests/test_bar.py:85, 98, 109, 115, 119` (`panel_label`) и `:126, 131, 140, 143`
(`is_alarm`). Вне `bar.py` эти две функции не вызываются — `indicator.py:131,135`
идут через `panel_state`, который время уже получает; проверить перед правкой:
`grep -rn 'panel_label\|is_alarm' src/ tests/`.

- [x] **Шаг 3.1: Написать падающий тест**

Строка импорта добавляется в шапку `tests/test_bar.py` один раз: файл сегодня
импортирует конкретные имена, а блоки ниже обращаются через модуль.

```python
# добавить в шапку tests/test_bar.py
from claude_usage_indicator import bar, state
```

```python
# добавить в tests/test_bar.py
class ExpiredWindowTests(unittest.TestCase):
    def _window(self, percent, resets_epoch):
        return state.Window(percent=percent, resets_epoch=resets_epoch, label="5h")

    def test_window_past_its_reset_is_expired(self):
        self.assertTrue(bar.is_expired(self._window(87.0, 1000), now_epoch=2000))

    def test_window_before_its_reset_is_not_expired(self):
        self.assertFalse(bar.is_expired(self._window(87.0, 3000), now_epoch=2000))

    def test_window_without_reset_epoch_is_never_expired(self):
        self.assertFalse(bar.is_expired(self._window(87.0, None), now_epoch=2000))

    def test_expired_window_reports_zero_not_the_stale_number(self):
        self.assertEqual(bar.effective_percent(self._window(87.0, 1000), now_epoch=2000), 0.0)

    def test_live_window_reports_its_own_number(self):
        self.assertEqual(bar.effective_percent(self._window(87.0, 3000), now_epoch=2000), 87.0)

    def test_expired_window_does_not_raise_the_alarm(self):
        snapshot = state.Snapshot(
            updated_epoch=500,
            windows={"five_hour": self._window(87.0, 1000)},
            order=("five_hour",),
            extra_usage=None,
            problem=None,
        )
        self.assertFalse(bar.is_alarm(snapshot, now_epoch=2000))

    def test_expired_window_renders_zero_in_the_panel(self):
        snapshot = state.Snapshot(
            updated_epoch=500,
            windows={"five_hour": self._window(87.0, 1000)},
            order=("five_hour",),
            extra_usage=None,
            problem=None,
        )
        self.assertIn("0%", bar.panel_label(snapshot, now_epoch=2000))
        self.assertNotIn("87%", bar.panel_label(snapshot, now_epoch=2000))

    def test_expired_reset_text_names_the_moment_and_promises_nothing(self):
        text = bar.format_reset(1000, now_epoch=2000)
        self.assertIn("window reset at", text)
        self.assertIn("first session", text)
        self.assertNotIn("in 0h", text)
```

- [x] **Шаг 3.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`
Ожидается: FAIL, `AttributeError: module ... has no attribute 'is_expired'`, плюс падения
`panel_label`/`is_alarm` по числу аргументов.

- [x] **Шаг 3.3: Реализовать минимум**

```python
# src/claude_usage_indicator/bar.py

def is_expired(window: Window, now_epoch: float) -> bool:
    """Срок окна истёк: сохранённый процент относится к уже закрытому окну."""
    return window.resets_epoch is not None and window.resets_epoch <= now_epoch


def effective_percent(window: Window, now_epoch: float) -> float:
    """Процент, который честно показать сейчас.

    После сброса сохранённое число заведомо неверно, а ноль — оценка: аккаунтом могли
    пользоваться с телефона или с claude.ai, и тогда расход больше нуля. Порог
    достоверности задан владелицей как «правдоподобно», и оценка ему отвечает,
    а старое число — нет.
    """
    return 0.0 if is_expired(window, now_epoch) else window.percent
```

`panel_label(snapshot, now_epoch)` и `is_alarm(snapshot, now_epoch, threshold=80.0)`
получают параметр `now_epoch` и считают через `effective_percent`. Параметр обязателен,
а не со значением по умолчанию: умолчание вида `now_epoch=time.time()` вернуло бы ровно
тот скрытый источник времени, который `panel_state` однажды уже убрал. Одиннадцать
вызовов из списка выше обновляются здесь же.

`format_reset` в ветке `delta_s <= 0` возвращает:

```python
        date_str = reset_dt.strftime("%d.%m")
        return f"window reset at {time_str} on {date_str}; next window starts with the first session"
```

Следующее время сброса не вычисляется: пятичасовое окно стартует от первой сессии,
а не по расписанию, и вычисленное значение было бы выдумкой.

- [x] **Шаг 3.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`
Ожидается: сначала **FAIL** — девять прежних тестов зовут `panel_label`/`is_alarm` без
`now_epoch` и падают с `TypeError: missing 1 required positional argument`. Это ожидаемое
следствие смены сигнатуры, а не регрессия: вызовы правятся по списку из «Интерфейсов»,
после чего прогон PASS. Прежние тесты, ожидавшие старый процент у просроченного окна,
обновляются отдельно — старое поведение и есть починенный баг.

- [x] **Шаг 3.5: Коммит**

```bash
git add src/claude_usage_indicator/bar.py tests/test_bar.py
git commit -m "fix: просроченное окно больше не показывает процент закрытого окна"
```

---

### 4. `patch-settings.py` — запрет писать в симлинк

**Файлы:**
- Изменить: `scripts/patch-settings.py`
- Тест: `tests/test_patch_settings.py`

**Интерфейсы:**
- Потребляет: ничего.
- Отдаёт: исключение `SettingsIsSymlink(RuntimeError)`.

- [x] **Шаг 4.1: Написать падающий тест**

```python
# добавить в tests/test_patch_settings.py
    def test_symlinked_settings_is_refused_with_an_explanation(self):
        with TemporaryDirectory() as tmp:
            real = Path(tmp) / "settings.json"
            real.write_text("{}", encoding="utf-8")
            link = Path(tmp) / "linked.json"
            link.symlink_to(real)
            with self.assertRaises(patch_settings.SettingsIsSymlink) as caught:
                patch_settings._apply("install", "/bin/true", link, dry_run=False)
            self.assertIn(str(real), str(caught.exception))
            self.assertTrue(link.is_symlink())
```

- [x] **Шаг 4.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_patch_settings.py' -v`
Ожидается: FAIL, `AttributeError: ... has no attribute 'SettingsIsSymlink'`.

- [x] **Шаг 4.3: Реализовать минимум**

```python
class SettingsIsSymlink(RuntimeError):
    """Цель — символьная ссылка, а запись идёт через временный файл и replace.

    Такая запись заменила бы ссылку обычным файлом, и профили, делящие один
    settings.json, разъехались бы молча. Правится настоящий файл, не ссылка.
    """
```

В начале `_apply()`, до любой записи:

```python
    if settings_path.is_symlink():
        raise SettingsIsSymlink(
            f"{settings_path} is a symlink to {settings_path.resolve()}; "
            f"patch the real file instead: --settings {settings_path.resolve()}"
        )
```

`main()` ловит его и печатает текст в stderr с кодом возврата 1.

- [x] **Шаг 4.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_patch_settings.py' -v`
Ожидается: PASS, 28 прежних тестов плюс 1 новый.

- [x] **Шаг 4.5: Коммит**

```bash
git add scripts/patch-settings.py tests/test_patch_settings.py
git commit -m "fix: patch-settings отказывается подменять симлинк settings.json"
```

---

### 5. `claude-statusline.sh` — профиль и файл на профиль

**Файлы:**
- Изменить: `bin/claude-statusline.sh:186-216` (`state_path`, `write_state`) и `:246-250`
  (сборка итогового объекта в `main`, отдельный инлайновый `jq` — **не** `JQ_FILTER`,
  который строит только `{limits, order, status_line}` и о схеме ничего не знает)
- Тест: `tests/test_statusline.sh`

**Интерфейсы:**
- Потребляет: контракт схемы 2 из шага 2.
- Отдаёт: файл `<state_dir>/<id>.json` со `schema: 2` и блоком `profile`.

- [x] **Шаг 5.1: Написать падающий тест**

Хелперы берутся только существующие. `run_hook` **не годится**: он жёстко ставит
`CLAUDE_USAGE_STATE` на фиксированный путь, а `CLAUDE_USAGE_STATE` по контракту
приоритетнее `CLAUDE_CONFIG_DIR` — через него имя файла по профилю не проявится никогда.
Берётся `run_hook_env(input, cwd, VAR=val...)` (`tests/test_statusline.sh:420`): он чистит
окружение через `env -i` и принимает произвольные присваивания. Ожидаемый путь тест
считает сам — ровно как уже делают `test_state_path_xdg_state_home` и
`test_state_path_default_home`.

`assert_state_file` не переиспользуется: она хардкодит `schema == "1"`
(`tests/test_statusline.sh:63`) и на схеме 2 краснела бы не по делу. Поля сверяются
прямым `jq -r`, как в `test_model_scoped_unparsable_resets_at`.

Вставить перед `main() {`; вызовы дописать внутрь `main()` — автообнаружения тестов
в этом наборе нет, функция без вызова просто не выполнится.

```bash
# добавить в tests/test_statusline.sh
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
    for name in ".claude-!!!" ".claude-Work  Acct" ".claude-my--profile" ".claude-личн"; do
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
}
```

Регистрация — внутри `main()`, рядом с прочими вызовами:

```bash
    test_profile_default_no_config_dir
    test_profile_named_from_config_dir
    test_profile_schema_and_fields
    test_profile_label_defaults_to_id
    test_profile_claude_usage_state_overrides_path
    test_profile_id_matches_python_on_degenerate_names
```

- [x] **Шаг 5.2: Прогнать и убедиться, что падает**

Запуск: `bash tests/test_statusline.sh`
Ожидается: FAIL — пишется `latest.json`, `schema` равно 1, ключа `.profile` в файле нет
вовсе. Исключение — проверка пути в `test_profile_claude_usage_state_overrides_path`:
она зелёная и сегодня, потому что это прежняя семантика `CLAUDE_USAGE_STATE`, которую
шаг обязан сохранить; красными в этом тесте остаются сверки `schema` и `profile.id`.

- [x] **Шаг 5.3: Реализовать минимум**

```bash
# Правило совпадает с profiles.profile_id_from_config_dir: bash пишет, python читает.
# Две конструкции здесь намеренно неочевидны, «очевидное» упрощение их ломает:
#   sed -E, а не tr -c: tr заменяет каждый запрещённый байт на дефис, а питоновский
#   [^a-z0-9_-]+ схлопывает последовательность в один. На «Work  Acct» это дало бы
#   work--acct против work-acct. Схлопывать всё подряд через tr -s тоже нельзя:
#   тогда разъедется легитимное имя my--profile, где дефисы разрешены.
#   Циклы while, а не ${name#-}: снятие префикса убирает ровно один дефис,
#   а python str.strip("-") — все. На «.claude-!!!» это дало бы «-» против «default».
profile_id() {
    local dir="${CLAUDE_CONFIG_DIR:-}"
    if [[ -z "$dir" ]]; then printf 'default'; return; fi
    dir="${dir%/}"
    if [[ "$dir" == "$HOME/.claude" ]]; then printf 'default'; return; fi
    local name="${dir##*/}"
    name="${name#.}"
    name="${name#claude-}"
    name="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]+/-/g')"
    while [[ "$name" == -* ]]; do name="${name#-}"; done
    while [[ "$name" == *- ]]; do name="${name%-}"; done
    printf '%s' "${name:-default}"
}

state_path() {
    if [[ -n "${CLAUDE_USAGE_STATE:-}" ]]; then printf '%s' "$CLAUDE_USAGE_STATE"; return; fi
    local id; id="$(profile_id)"
    if [[ -n "${XDG_STATE_HOME:-}" ]]; then printf '%s/claude-usage/%s.json' "$XDG_STATE_HOME" "$id"; return; fi
    if [[ -n "${HOME:-}" ]]; then printf '%s/.local/state/claude-usage/%s.json' "$HOME" "$id"; return; fi
}
```

> **`profile_id()` показан на момент раунда 1** — без разрешения симлинков (фикс-раунд
> 1) и без слага-с-хэшем (фикс-раунд 2). Живая версия — в `bin/claude-statusline.sh`,
> правило описано в `.superpowers/sdd/2026-09-10-multi-profile/final-fix-round-2-brief.md`.

Шаблон `mktemp "$dir/latest.json.XXXXXX"` в `write_state` заменяется на
`mktemp "$dir/.tmp.XXXXXX"`: имя `latest.json.*` теперь совпало бы с маской `*.json`
читателя только по случайности, а отдельный префикс исключает гонку в принципе.

Итоговый объект собирается **не** в `JQ_FILTER`, а вторым инлайновым `jq` в `main()`
(`bin/claude-statusline.sh:246-250`) — сегодня там `{schema: 1, ...}` с единственным
`--argjson epoch`. Он и правится:

```bash
    local state_json
    state_json="$(printf '%s' "$result" | jq -c \
        --argjson epoch "$(date +%s)" \
        --arg profile_id "$(profile_id)" \
        --arg profile_label "${CLAUDE_USAGE_PROFILE_LABEL:-$(profile_id)}" '
        {schema: 2, profile: {id: $profile_id, label: $profile_label},
         updated_epoch: $epoch, limits: .limits, order: .order}
        + (if has("extra_usage") then {extra_usage: .extra_usage} else {} end)
    ' 2>/dev/null)" || state_json=""
```

`config_dir` в файл не пишется: читателю он не нужен ни для чего, а вторая запись пути
создала бы источник правды, который некому сверять с диском.

- [x] **Шаг 5.4: Прогнать и убедиться, что зелено**

Запуск: `bash tests/test_statusline.sh`
Ожидается: все проверки PASS.

- [x] **Шаг 5.5: Коммит**

```bash
git add bin/claude-statusline.sh tests/test_statusline.sh
git commit -m "feat: хук пишет файл состояния на профиль со схемой 2"
```

---

### 6. `bar.py` — мультипрофильный бар

**Файлы:**
- Изменить: `src/claude_usage_indicator/bar.py:52-91`; добавить `format_reset_panel`
  сразу за существующим `format_reset` (сейчас `:114-125`) — полная и компактная форма
  одного понятия физически рядом; добавить константу `_STALE_MARK` в шапку файла рядом
  с `_STALE_SUFFIX` (`:22`), сам `_STALE_SUFFIX` не трогать.
- Тест: `tests/test_bar.py`

**Интерфейсы:**
- Потребляет: `state.Reading`, `state.ProfileSnapshot`, `profiles.profile_sort_key`,
  `bar.effective_percent` из шагов 1-3.
- Отдаёт: `binding_window(snapshot: Snapshot, now_epoch: float) -> tuple[str, Window] | None`;
  `format_reset_panel(resets_epoch: int | None, now_epoch: float) -> str` — компактный
  маркер сброса для панели, `format_reset` не меняет и не заменяет;
  `panel_state_for(reading: Reading, now_epoch: float) -> tuple[str, bool]`;
  `panel_label_no_data() -> str`;
  `menu_section_lines(entry: ProfileSnapshot, now_epoch: float) -> list[str]`;
  `unreadable_line(names: Sequence[str]) -> str | None`.

Последние две — текст для меню и для окна «Подробнее». Они живут здесь, а не в шаге 8,
потому что это функции `bar.py` и их потребляют оба шага третьей волны сразу: оставь их
в шаге 8 — и шаг 9 в своём дереве получит `AttributeError`.

- [x] **Шаг 6.1: Написать падающий тест**

```python
# добавить в tests/test_bar.py
class FormatResetPanelTests(_FixedTzMixin, unittest.TestCase):
    """Компактная метка сброса для панели: HH:MM в пределах суток, DD.MM дальше,
    пусто после сброса — контраст с полной формой `format_reset` из меню."""

    def test_under_24_hours_shows_time(self) -> None:
        self.assertEqual(bar.format_reset_panel(18_300, now_epoch=0), "↻05:05")

    def test_exactly_24_hours_shows_date_not_time(self) -> None:
        self.assertEqual(bar.format_reset_panel(86_400, now_epoch=0), "↻02.01")

    def test_more_than_24_hours_shows_date(self) -> None:
        self.assertEqual(bar.format_reset_panel(190_800, now_epoch=0), "↻03.01")

    def test_already_reset_has_no_marker(self) -> None:
        self.assertEqual(bar.format_reset_panel(1_000, now_epoch=2_000), "")

    def test_no_reset_epoch_has_no_marker(self) -> None:
        self.assertEqual(bar.format_reset_panel(None, now_epoch=0), "")


class MultiProfileBarTests(unittest.TestCase):
    def _reading(self, *entries):
        return state.Reading(profiles={e.profile_id: e for e in entries}, unreadable=())

    def _entry(self, profile_id, label, five, seven, updated):
        snapshot = state.Snapshot(
            updated_epoch=updated,
            windows={
                "five_hour": state.Window(percent=five, resets_epoch=9_000, label="5h"),
                "seven_day": state.Window(percent=seven, resets_epoch=9_000, label="7d"),
            },
            order=("five_hour", "seven_day"),
            extra_usage=None,
            problem=None,
        )
        return state.ProfileSnapshot(profile_id=profile_id, label=label, snapshot=snapshot)

    def test_single_profile_renders_exactly_as_before(self):
        entry = self._entry("default", "work", 42.0, 27.0, updated=8_000)
        legacy, legacy_alarm = bar.panel_state(entry.snapshot, now_epoch=8_100)
        new, new_alarm = bar.panel_state_for(self._reading(entry), now_epoch=8_100)
        self.assertEqual(new, legacy)
        self.assertEqual(new_alarm, legacy_alarm)

    def test_two_profiles_each_contribute_their_binding_window(self):
        label, _ = bar.panel_state_for(
            self._reading(
                self._entry("default", "work", 42.0, 27.0, updated=8_000),
                self._entry("personal", "own", 11.0, 61.0, updated=8_000),
            ),
            now_epoch=8_100,
        )
        self.assertIn("work", label)
        self.assertIn("42%", label)
        self.assertNotIn("27%", label)
        self.assertIn("own", label)
        self.assertIn("61%", label)
        self.assertNotIn("11%", label)
        self.assertIn("↻", label)

    def test_default_profile_comes_first_regardless_of_freshness(self):
        label, _ = bar.panel_state_for(
            self._reading(
                self._entry("personal", "own", 90.0, 90.0, updated=9_999),
                self._entry("default", "work", 1.0, 1.0, updated=1),
            ),
            now_epoch=8_100,
        )
        self.assertLess(label.index("work"), label.index("own"))

    def test_stale_profile_is_marked_and_the_fresh_one_is_not(self):
        label, _ = bar.panel_state_for(
            self._reading(
                self._entry("default", "work", 42.0, 27.0, updated=8_000),
                self._entry("personal", "own", 61.0, 11.0, updated=1),
            ),
            now_epoch=8_100,
        )
        head, tail = label.split("own")
        self.assertNotIn("*", head.split("work")[1])
        self.assertIn("*", tail)

    def test_alarm_in_any_profile_raises_the_alarm(self):
        _, alarm = bar.panel_state_for(
            self._reading(
                self._entry("default", "work", 1.0, 1.0, updated=8_000),
                self._entry("personal", "own", 95.0, 1.0, updated=8_000),
            ),
            now_epoch=8_100,
        )
        self.assertTrue(alarm)

    def test_no_profiles_at_all_is_the_no_data_label(self):
        label, alarm = bar.panel_state_for(state.Reading(profiles={}, unreadable=()), now_epoch=1)
        self.assertEqual(label, bar.panel_label_no_data())
        self.assertFalse(alarm)

    def test_expired_binding_window_has_no_reset_marker_in_the_panel(self):
        expired_window = state.Window(percent=87.0, resets_epoch=1_000, label="5h")
        expired_snapshot = state.Snapshot(
            updated_epoch=8_000,
            windows={"five_hour": expired_window},
            order=("five_hour",),
            extra_usage=None,
            problem=None,
        )
        expired_entry = state.ProfileSnapshot(
            profile_id="personal", label="own", snapshot=expired_snapshot
        )
        label, _ = bar.panel_state_for(
            self._reading(
                self._entry("default", "work", 42.0, 27.0, updated=8_000),
                expired_entry,
            ),
            now_epoch=8_100,
        )
        self.assertNotIn("↻", label.split("own")[1])


class MenuSectionTests(unittest.TestCase):
    def _entry(self):
        return state.ProfileSnapshot(
            profile_id="personal",
            label="own",
            snapshot=state.Snapshot(
                updated_epoch=1_000,
                windows={
                    "five_hour": state.Window(percent=61.0, resets_epoch=9_000, label="5h"),
                    "seven_day": state.Window(percent=11.0, resets_epoch=9_000, label="7d"),
                },
                order=("five_hour", "seven_day"),
                extra_usage=None,
                problem=None,
            ),
        )

    def test_section_lines_name_the_profile_and_every_window(self):
        lines = bar.menu_section_lines(self._entry(), now_epoch=8_000)
        self.assertEqual(lines[0], "own")
        self.assertTrue(any("61%" in line for line in lines))
        self.assertTrue(any("11%" in line for line in lines))
        self.assertTrue(any("as of" in line for line in lines))

    def test_section_lines_carry_a_reset_text_for_every_window(self):
        lines = bar.menu_section_lines(self._entry(), now_epoch=8_000)
        self.assertEqual(sum("reset" in line for line in lines), 2)

    def test_unreadable_files_produce_one_honest_line(self):
        line = bar.unreadable_line(("broken.json", "junk.json"))
        self.assertIn("broken.json", line)
        self.assertIn("junk.json", line)

    def test_no_unreadable_files_produce_no_line(self):
        self.assertIsNone(bar.unreadable_line(()))
```

- [x] **Шаг 6.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`
Ожидается: FAIL, `AttributeError: ... has no attribute 'format_reset_panel'`, затем то же
про `panel_state_for` и про `menu_section_lines`.

- [x] **Шаг 6.3: Реализовать минимум**

```python
# Два профиля делят ширину панели пополам, поэтому несвежесть помечается одним символом.
# Легаси-суффикс ` (stale)` остаётся за одиночным режимом: критерий 5 требует от него
# посимвольного совпадения с сегодняшней строкой.
_STALE_MARK = "*"


def binding_window(snapshot: Snapshot, now_epoch: float) -> tuple[str, Window] | None:
    """Окно, которое сейчас связывает: с наибольшим эффективным процентом.

    Ничья разрешается порядком из `order`, а не произвольным: метка панели не должна
    менять окно между тиками при равных числах.
    """
    best: tuple[str, Window, float] | None = None
    for key in snapshot.order:
        window = snapshot.windows.get(key)
        if window is None:
            continue
        percent = effective_percent(window, now_epoch)
        if best is None or percent > best[2]:
            best = (key, window, percent)
    return (best[0], best[1]) if best is not None else None


def panel_state_for(reading: Reading, now_epoch: float) -> tuple[str, bool]:
    """Текст метки и статус тревоги для всего каталога состояния.

    Один профиль отдаётся в прежний panel_state без изменений: пока второй аккаунт не
    заведён, панель обязана выглядеть ровно как раньше.
    """
    entries = [reading.profiles[key] for key in sorted(reading.profiles, key=profile_sort_key)]
    if not entries:
        return panel_label_no_data(), False
    if len(entries) == 1:
        return panel_state(entries[0].snapshot, now_epoch)
    alarm = any(is_alarm(entry.snapshot, now_epoch) for entry in entries)
    chunks = [_profile_chunk(entry, now_epoch) for entry in entries]
    label = _SEPARATOR.join(chunks)
    if alarm:
        label = _ATTENTION_PREFIX + label
    return label, alarm


def _profile_chunk(entry: ProfileSnapshot, now_epoch: float) -> str:
    """Один профиль в метке панели: связывающее окно, процент и компактная метка
    его сброса.

    Все окна каждого профиля в панель GNOME не помещаются; полная разбивка по всем
    окнам и полное время сброса каждого живут в меню и в окне «Подробнее».
    """
    binding = binding_window(entry.snapshot, now_epoch)
    if binding is None:
        return f"{entry.label} {_NO_DATA_LABEL}"
    key, window = binding
    percent = effective_percent(window, now_epoch)
    chunk = f"{entry.label} {panel_key(key, window)} {render_bar(percent)} {round_percent(percent)}%"
    if is_stale(entry.snapshot, now_epoch):
        chunk += _STALE_MARK
    reset_marker = format_reset_panel(window.resets_epoch, now_epoch)
    if reset_marker:
        chunk += " " + reset_marker
    return chunk
```

`format_reset_panel` физически ложится в файл сразу за `format_reset` (`bar.py:114-125`),
а не рядом с `_profile_chunk`: полная и компактная форма одного понятия остаются рядом
в исходнике, `_profile_chunk` вызывает её через имя модуля как любую другую функцию
файла.

```python
def format_reset_panel(resets_epoch: int | None, now_epoch: float) -> str:
    """Компактная метка сброса связывающего окна для панели: `↻HH:MM`/`↻DD.MM`.

    Не полная форма `format_reset` — та несёт «через Nч Mм» и остаётся только в меню,
    где ширина не ограничена. Здесь ширина панели фиксирована: дальше суток точность
    падает до дня. Окно уже сброшено или срок неизвестен — пустая строка: следующее
    время сброса демону неизвестно, пока новая сессия не запишет состояние, а врать
    нельзя.
    """
    if resets_epoch is None or resets_epoch <= now_epoch:
        return ""
    reset_dt = datetime.fromtimestamp(resets_epoch, tz=timezone.utc).astimezone()
    delta_s = resets_epoch - now_epoch
    if delta_s < 86400:
        return "↻" + reset_dt.strftime("%H:%M")
    return "↻" + reset_dt.strftime("%d.%m")
```

Решение владелицы 2026-09-11 отменяет прежнее «время сброса в панель не помещается» из
спеки: замер строки `work 5h ▓▓░░░░░░ 42% ↻01:20 · own 7d ▓▓▓▓▓░░░ 61%* ↻12.09` шрифтом
13px DejaVu Sans Mono дал 446 px; на экране 1920×1080 в правой зоне панели GNOME доступно
около 850 px — помещается с запасом даже при третьем профиле. Это обоснование, не
догадка: до замера в спеке было зафиксировано обратное.

`panel_label_no_data()` — тонкая обёртка над существующей константой `_NO_DATA_LABEL`,
чтобы тесты не зависели от приватного имени.

Там же — текст для меню и для окна «Подробнее»:

```python
def menu_section_lines(entry: ProfileSnapshot, now_epoch: float) -> list[str]:
    """Секция одного профиля: метка, все окна с процентом и сбросом, возраст снимка.

    В отличие от панели — там только связывающее окно и компактный маркер его сброса —
    здесь показываются все окна с полным временем сброса каждого: это то, ради чего меню
    открывают, и прятать его за выбором одного окна нельзя.
    """
    snapshot = entry.snapshot
    lines = [entry.label]
    for key in snapshot.order:
        window = snapshot.windows.get(key)
        if window is None:
            continue
        percent = effective_percent(window, now_epoch)
        lines.append(
            f"{panel_key(key, window)} {render_bar(percent)} {round_percent(percent)}% · "
            f"{format_reset(window.resets_epoch, now_epoch)}"
        )
    lines.append(f"as of {format_age(snapshot.updated_epoch, now_epoch)}")
    return lines


def unreadable_line(names: Sequence[str]) -> str | None:
    """Одна строка про файлы, которые не разобрались. None, если таких нет.

    Молчать о них нельзя: пропавший профиль иначе неотличим от профиля, которым
    сегодня просто не пользовались.
    """
    if not names:
        return None
    return "couldn't read: " + ", ".join(names)
```

- [x] **Шаг 6.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_bar.py' -v`
Ожидается: PASS.

- [x] **Шаг 6.5: Коммит**

```bash
git add src/claude_usage_indicator/bar.py tests/test_bar.py
git commit -m "feat: бар и текст меню по профилям"
```

---

### 7. `launcher.py` — команда запуска терминала

**Файлы:**
- Создать: `src/claude_usage_indicator/launcher.py`
- Тест: `tests/test_launcher.py`

**Интерфейсы:**
- Потребляет: `profiles.Profile`.
- Отдаёт: `launch_command(profile: Profile, home: Path | None = None) -> list[str]`;
  `launch(profile: Profile, spawn=subprocess.Popen, home: Path | None = None) -> str | None`
  — возвращает `None` при успехе, текст ошибки для показа человеку при неудаче.

- [x] **Шаг 7.1: Написать падающий тест**

Сверять внутреннюю команду подстрокой нельзя, и это не придирка к стилю: `shlex.quote`
скомпилирован с `re.ASCII` (`/usr/lib/python3.12/shlex.py:321`), поэтому любую строку с
кириллицей он считает небезопасной и берёт в кавычки целиком — `мой личный` превращается
в `'мой личный'`. Кавычки здесь правильные, а вот `assertIn("...=мой личный", inner)`
был бы вечно красным. Команда разбирается тем же `shlex`, которым её прочтёт shell.

```python
# tests/test_launcher.py
import shlex
import unittest
from pathlib import Path

from claude_usage_indicator import launcher, profiles


class LaunchCommandTests(unittest.TestCase):
    def _profile(self, profile_id, label, config_dir):
        return profiles.Profile(id=profile_id, label=label, config_dir=Path(config_dir))

    def _assignments(self, command):
        """Присваивания из `env VAR=... claude`, разобранные как их прочтёт shell."""
        words = shlex.split(command[-1])
        self.assertEqual(words[0], "env")
        self.assertEqual(words[-1], "claude")
        return dict(word.split("=", 1) for word in words[1:-1])

    def test_default_profile_does_not_set_config_dir(self):
        command = launcher.launch_command(
            self._profile("default", "work", "/home/u/.claude"), home=Path("/home/u")
        )
        self.assertNotIn("CLAUDE_CONFIG_DIR", self._assignments(command))

    def test_named_profile_sets_config_dir_and_label(self):
        command = launcher.launch_command(
            self._profile("personal", "own", "/home/u/.claude-personal"), home=Path("/home/u")
        )
        assignments = self._assignments(command)
        self.assertEqual(assignments["CLAUDE_CONFIG_DIR"], "/home/u/.claude-personal")
        self.assertEqual(assignments["CLAUDE_USAGE_PROFILE_LABEL"], "own")

    def test_label_with_a_space_survives_the_shell(self):
        command = launcher.launch_command(
            self._profile("personal", "мой личный", "/home/u/.claude-personal"), home=Path("/home/u")
        )
        # Разбор в одно слово и есть доказательство, что кавычки на месте:
        # без них shlex.split вернул бы «мой» и «личный» отдельными словами.
        self.assertEqual(
            self._assignments(command)["CLAUDE_USAGE_PROFILE_LABEL"], "мой личный"
        )

    def test_terminal_is_gnome_terminal(self):
        command = launcher.launch_command(
            self._profile("default", "work", "/home/u/.claude"), home=Path("/home/u")
        )
        self.assertEqual(command[0], "gnome-terminal")


class LaunchTests(unittest.TestCase):
    def test_successful_spawn_reports_no_error(self):
        calls = []
        result = launcher.launch(
            profiles.Profile(id="default", label="work", config_dir=Path("/home/u/.claude")),
            spawn=lambda *a, **k: calls.append((a, k)),
            home=Path("/home/u"),
        )
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_failed_spawn_returns_text_for_the_human(self):
        def boom(*_args, **_kwargs):
            raise OSError("gnome-terminal: not found")

        result = launcher.launch(
            profiles.Profile(id="default", label="work", config_dir=Path("/home/u/.claude")),
            spawn=boom,
            home=Path("/home/u"),
        )
        self.assertIn("gnome-terminal", result)
```

- [x] **Шаг 7.2: Прогнать и убедиться, что падает**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_launcher.py' -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'claude_usage_indicator.launcher'`.

- [x] **Шаг 7.3: Реализовать минимум**

```python
# src/claude_usage_indicator/launcher.py
"""Запуск сессии Claude в выбранном профиле.

Глобального переключения аккаунта не происходит и не предполагается: CLAUDE_CONFIG_DIR
действует на запускаемый процесс, поэтому обе сессии могут идти рядом. Решение, куда
идти, принимает человек — автоматической подмены нет.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from .profiles import DEFAULT_ID, Profile

_TERMINAL = "gnome-terminal"
_LOG = logging.getLogger(__name__)


def launch_command(profile: Profile, home: Path | None = None) -> list[str]:
    """Аргументы запуска терминала с сессией в этом профиле.

    Профиль по умолчанию запускается без CLAUDE_CONFIG_DIR: так работает обычный
    `claude`, и подменять его окружение незачем. `bash -lc` нужен ради login-shell:
    без него в PATH может не оказаться claude, установленного в ~/.local/bin.
    """
    base = home or Path.home()
    assignments = [f"CLAUDE_USAGE_PROFILE_LABEL={shlex.quote(profile.label)}"]
    if profile.id != DEFAULT_ID and profile.config_dir != base / ".claude":
        assignments.append(f"CLAUDE_CONFIG_DIR={shlex.quote(str(profile.config_dir))}")
    return [_TERMINAL, "--", "bash", "-lc", " ".join(["env", *assignments, "claude"])]


def launch(profile: Profile, spawn=subprocess.Popen, home: Path | None = None) -> str | None:
    """Запустить сессию. None при успехе, текст для человека при неудаче.

    Ошибка запуска не должна ронять демон: без окна пользователь останется, без панели —
    нет. Поэтому исключение превращается в текст, который вызывающий показывает диалогом,
    а подробность уходит в лог.
    """
    command = launch_command(profile, home)
    try:
        spawn(command, start_new_session=True)
    except OSError as error:
        _LOG.warning("failed to launch profile %s with %r: %s", profile.id, command, error)
        return f"Could not start a session for “{profile.label}”: {error}"
    return None
```

- [x] **Шаг 7.4: Прогнать и убедиться, что зелено**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_launcher.py' -v`
Ожидается: PASS, 6 тестов.

`shlex.quote` остаётся, а не заменяется на `subprocess.Popen(env=...)`: `env=` подменило бы
окружение процесса `gnome-terminal`, который в GNOME — клиент D-Bus-сервиса, и до оболочки
в новом окне переменные могут не дойти. Присваивание внутри самой команды доходит всегда.
Проверяется это не тестом, а ручной проверкой шага 8.

- [x] **Шаг 7.5: Коммит**

```bash
git add src/claude_usage_indicator/launcher.py tests/test_launcher.py
git commit -m "feat: запуск сессии Claude в выбранном профиле"
```

---

### 8. `indicator.py` — меню по профилям и пункты запуска

**Файлы:**
- Изменить: `src/claude_usage_indicator/indicator.py:110-126,197-214`
- Тест: `tests/test_bar.py` (сборка текста секций — чистые функции), ручной прогон демона

**Интерфейсы:**
- Потребляет: `state.read_all_states`, `bar.panel_state_for`, `bar.menu_section_lines`,
  `bar.unreadable_line`, `profiles.discover_profiles`, `launcher.launch`.
- Отдаёт: меню трея; в остальном публичного контракта не меняет.

Автотестов в этом шаге нет, и это осознанно: весь остаток — код GTK, который без живого
дисплея не поднимается, а стенда для GTK в репозитории нет и заводить его ради двух
обработчиков дороже, чем проверить глазами. Вся тестируемая логика — сборка текста —
вынесена в `bar.py` шагом 6 и покрыта там. Критерий шага — ручной прогон ниже.

- [ ] **Шаг 8.1: Зафиксировать текущее поведение (RED руками)**

Подложить два файла состояния и запустить демон **до** правки:

```bash
mkdir -p /tmp/cui-demo/claude-usage
# default.json и personal.json со схемой 2 — образцы из шага 2
XDG_STATE_HOME=/tmp/cui-demo PYTHONPATH=src /usr/bin/python3 -m claude_usage_indicator
```

Ожидается: в панели один профиль, второго нет вовсе; в меню одна секция; пунктов запуска
нет. Это и есть RED — записать, что увидено, чтобы после правки было с чем сравнить.

- [x] **Шаг 8.2: Изменить indicator.py**

В `indicator.py`:

- `refresh()` вызывает `state.read_all_states()` вместо `state.read_state()`;
- `_apply()` вызывает `bar.panel_state_for(reading, now)`;
- `_build_menu()` получает `reading` и `discover_profiles()`, собирает по секции на
  профиль из `bar.menu_section_lines`, затем строку `bar.unreadable_line`, затем по пункту
  «Open Claude — `<метка>`» на каждый найденный на диске профиль, затем прежние пункты
  автостарта, «Details…» и «Quit»;
- нажатие пункта запуска вызывает `launcher.launch(profile)`; непустой возврат
  показывается `Gtk.MessageDialog` — пользователю внятный текст, подробность в логе.

Профили для пунктов запуска берутся с диска, а не из файлов состояния: у нового профиля
файла состояния ещё нет, а кнопка нужна сразу.

- [x] **Шаг 8.3: Прогнать питоновский набор**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v`
Ожидается: PASS — шаг не должен ничего сломать в уже покрытом.

- [ ] **Шаг 8.4: Ручная проверка (GREEN руками)**

Тот же запуск, что в шаге 8.1. Ожидается: в панели два профиля, у каждого — компактная
метка сброса связывающего окна; в меню две секции с полным временем сброса у каждого
окна и два пункта «Open Claude — …».

Отдельно проверяется то, что автотест проверить не может: нажать «Open Claude — own»
и в открывшемся окне выполнить `echo "$CLAUDE_CONFIG_DIR"` — должен напечататься путь
второго профиля, а `echo "$CLAUDE_USAGE_PROFILE_LABEL"` — его метка. Это единственная
проверка того, что переменные действительно дошли через `gnome-terminal` до оболочки;
если они пустые — присваивание до shell не доходит, и шаг 7 надо переделывать, а не
подкручивать тест.

Живого GTK-сеанса нет — отметить шаг как невыполненный и сказать об этом в отчёте,
а не считать сделанным.

- [x] **Шаг 8.5: Коммит**

```bash
git add src/claude_usage_indicator/indicator.py
git commit -m "feat: меню трея по профилям и пункты запуска сессии"
```

---

### 9. `window.py` — окно «Подробнее» по профилям

**Файлы:**
- Изменить: `src/claude_usage_indicator/window.py:83-108`
- Тест: текстовая часть покрыта `MenuSectionTests` шага 6; сборка окна — ручной прогон

**Интерфейсы:**
- Потребляет: `state.read_all_states`, `bar.menu_section_lines`, `bar.unreadable_line`,
  `profiles.profile_sort_key` — всё готово к началу третьей волны, шаг 8 не нужен.
- Отдаёт: публичного контракта не меняет.

Как и шаг 8, этот шаг — код GTK без автотестов, и по той же причине. RED и GREEN
проверяются глазами.

- [ ] **Шаг 9.1: Зафиксировать текущее поведение (RED руками)**

Открыть «Details…» на тех же двух подложенных файлах состояния, что в шаге 8.1.
Ожидается: одна секция, второго профиля в окне нет. Записать увиденное.

- [x] **Шаг 9.2: Изменить сборку содержимого**

`_refresh_content` переходит на `state.read_all_states()`. `_build_content` обходит
профили в порядке `profiles.profile_sort_key` и для каждого рисует секцию: заголовок с
меткой, затем окна, `extra_usage` и возраст снимка — то есть прежний набор блоков,
повторённый на профиль. Строка `unreadable_line` добавляется последней, если она есть.

Глобальный синглтон `_window` остаётся один: окно одно на все профили, а не по окну на
профиль — иначе их пришлось бы закрывать по одному.

- [x] **Шаг 9.3: Прогнать полный питоновский набор**

Запуск: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v`
Ожидается: PASS.

- [ ] **Шаг 9.4: Ручная проверка (GREEN руками)**

Открыть «Details…» при двух подложенных файлах состояния: две секции, у спящей — строка
с датой снимка, у просроченного окна — `0%` и текст «window reset at …».
Живого GTK-сеанса нет — отметить как невыполненное, а не как сделанное.

- [x] **Шаг 9.5: Коммит**

```bash
git add src/claude_usage_indicator/window.py
git commit -m "feat: окно «Подробнее» показывает секцию на профиль"
```

---

### 10. `install.sh`, README и полный прогон

**Файлы:**
- Изменить: `install.sh`, `README.md`
- Тест: `tests/test_install_uninstall.sh`

Уборка `latest.json` — **закрытие риска, а не критерий приёмки**: ни один пункт «Критериев
приёмки» её не требует. Читатель понимает схему 1 и без уборки; шаг существует, чтобы
после появления `default.json` в панели не осталось двух записей об одном аккаунте.
Выпадет — задача останется выполненной.

- [x] **Шаг 10.1: Написать падающий тест**

Хелперы — только существующие: `new_tmp_dir` (кладёт путь в глобальную `NEW_TMP_DIR`,
вызывать **не** в подстановке), `run_script "$INSTALL_SH" "$home"`, `assert_absent
"$desc" "$path"`, `assert_exists "$desc" "$path"` — во всех описание идёт первым
аргументом. Перепутанный порядок в `assert_absent` не краснеет, а всегда проходит:
тест соврал бы про работающую уборку.

`run_script`, в отличие от `run_hook_env`, окружение не чистит — переопределяет только
`HOME`, `PATH` и `SYSTEMCTL_LOG`. Поэтому `XDG_STATE_HOME` машины, где идёт прогон,
протёк бы внутрь и увёл каталог состояния в сторону; тесты снимают его явно.

Путь-хелпер добавляется тем же однострочным идиомом, что и пять соседних, сразу после
`systemctl_log_of()` (`tests/test_install_uninstall.sh:115`):

```bash
# Каталог состояния хука: ветка по умолчанию без XDG_STATE_HOME.
state_dir_of() { printf '%s/.local/state/claude-usage' "$1"; }
```

```bash
# добавить в tests/test_install_uninstall.sh
test_install_removes_legacy_latest_json_when_default_exists() {
    local desc="install: legacy latest.json удаляется, если рядом уже есть default.json"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    unset -v XDG_STATE_HOME
    local state_dir; state_dir="$(state_dir_of "$home")"
    mkdir -p "$state_dir"
    printf '{"schema":1,"limits":{}}\n' > "$state_dir/latest.json"
    printf '{"schema":2,"profile":{"id":"default"},"limits":{}}\n' > "$state_dir/default.json"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_absent "$desc: latest.json удалён" "$state_dir/latest.json"
    assert_exists "$desc: default.json остался" "$state_dir/default.json"
    assert_eq "$desc: default.json не тронут" \
        '{"schema":2,"profile":{"id":"default"},"limits":{}}' "$(cat "$state_dir/default.json")"
}

test_install_keeps_latest_json_when_it_is_the_only_state_file() {
    local desc="install: latest.json — единственный файл состояния — не удаляется"
    new_tmp_dir; local home="$NEW_TMP_DIR"
    unset -v XDG_STATE_HOME
    local state_dir; state_dir="$(state_dir_of "$home")"
    mkdir -p "$state_dir"
    printf '{"schema":1,"limits":{}}\n' > "$state_dir/latest.json"

    run_script "$INSTALL_SH" "$home"

    assert_eq "$desc: exit" "0" "$RUN_EXIT"
    assert_exists "$desc: latest.json остался" "$state_dir/latest.json"
}
```

Регистрация — внутри `main()`, рядом с прочими вызовами:

```bash
    test_install_removes_legacy_latest_json_when_default_exists
    test_install_keeps_latest_json_when_it_is_the_only_state_file
```

- [x] **Шаг 10.2: Прогнать и убедиться, что падает**

Запуск: `bash tests/test_install_uninstall.sh`
Ожидается: FAIL в первом тесте — `install.sh` сегодня в каталог состояния не заглядывает
вовсе. Второй тест зелёный и сегодня: он сторожит то, что уборка не должна тронуть, и
краснеет только если реализация окажется слишком жадной.

- [x] **Шаг 10.3: Реализовать минимум**

В `install.sh` добавляется шаг уборки: если рядом с `latest.json` уже лежит
`default.json`, старый файл удаляется. Единственный файл состояния не удаляется никогда —
иначе апгрейд стёр бы данные до первого запуска сессии.

- [x] **Шаг 10.4: Обновить README**

Раздел про файл состояния: путь стал `~/.local/state/claude-usage/<profile>.json`,
идентификатор выводится из `CLAUDE_CONFIG_DIR`, `CLAUDE_USAGE_PROFILE_LABEL` — косметика.
Раздел про два аккаунта: как это работает, почему нет обращений к API и к учётным данным,
и что переключения в глобальном смысле не происходит.

- [x] **Шаг 10.5: Полный прогон**

```bash
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/test_statusline.sh
bash tests/test_install_uninstall.sh
```
Ожидается: все три набора зелёные.

- [x] **Шаг 10.6: Коммит**

```bash
git add install.sh README.md tests/test_install_uninstall.sh
git commit -m "chore: уборка старого файла состояния и документация профилей"
```

---

## Проверки

- Целевой прогон в цикле: `PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p '<файл>' -v`
- Полный прогон перед ревью и перед финишем:
  ```
  PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
  bash tests/test_statusline.sh
  bash tests/test_install_uninstall.sh
  ```
- Линтера и проверки типов в репозитории нет — не заводить в этой задаче.
- Миграций нет.
- Покрытие не измеряется: решением владелицы 2026-09-11 числовое требование снято.
  Ставить `coverage` не нужно — это была бы первая внешняя зависимость проекта.

## Риски и допущения

| Что | Допущение или риск | Как проверяется |
|-----|--------------------|-----------------|
| Правило идентификатора | Живёт в двух реализациях, bash и Python, как и `state_path` сегодня | `test_profile_id_matches_python_on_degenerate_names` гоняет хук и питоновскую функцию на одних и тех же вырожденных именах и сверяет результат. Именно вырожденных: на «personal» сойдётся любая пара реализаций, а разъезжаются они на `!!!`, двойном пробеле и не-ASCII |
| `tr -c` против `[^a-z0-9_-]+` | `tr` заменяет каждый запрещённый байт, регексп схлопывает последовательность; `tr -s` сломал бы легитимное `my--profile` | Замерено на девяти именах до написания шага: `sed -E` сходится с Python на всех, `tr -c` расходится на «Work  Acct» |
| Правило вывода id в bash-тестах | Формула «basename без ведущего `.claude-`» взята из шага 1, а не из сегодняшнего кода: в хуке профилей нет вовсе | Паритет-тест выше сверяет обе реализации между собой, а не с выдуманным эталоном |
| `run_script` не чистит окружение | `XDG_STATE_HOME` машины прогона увёл бы install-тесты в чужой каталог | Тесты шага 10 снимают переменную явно (`unset -v`) |
| Просроченное окно = 0% | Оценка, а не факт: аккаунтом могли пользоваться с другого устройства | Не проверяется технически; порог достоверности задан владелицей как «правдоподобно» |
| `patch-settings.py` и симлинк | Запись через `replace` заменила бы ссылку файлом | Шаг 4, отказ с внятным текстом |
| Claude Code сам перепишет `settings.json` атомарно | Симлинк может быть заменён извне нашего кода | Этой задачей не чинится, зафиксировано как известный риск схемы симлинков |
| Одинаковый basename у двух каталогов конфига | Один идентификатор на два аккаунта | Граница схемы, зафиксирована в спеке |
| `gnome-terminal` отсутствует | Пункт запуска не сработает | Шаг 7: текст ошибки человеку, демон продолжает работать |
| Ручные проверки шагов 8 и 9 | Требуют живого GTK-сеанса, в headless-прогоне не воспроизводятся | Выполняются человеком; агент отмечает как невыполненное, если сеанса нет |
| Переменные через `gnome-terminal` | На GNOME это клиент D-Bus-сервиса; присваивание внутри команды доходит до оболочки, `subprocess(env=…)` — не обязательно | Ручная проверка шага 8.4: `echo "$CLAUDE_CONFIG_DIR"` в открывшемся окне |
| Порог несвежести — час | У второго аккаунта звёздочка будет гореть почти всегда. Это не дефект: звёздочка и есть запрошенная пометка «спящий», а точный момент снимка лежит в меню | **Решено на планировании 2026-09-11:** порог не трогать. `is_stale` — одна функция на оба режима (`bar.py:69`), смена умолчания изменила бы сегодняшнюю строку одиночного профиля вопреки её критерию; отдельный порог только для второго профиля дал бы одному значку два смысла в одной строке |
| Покрытие от 80% | Числом не подтверждается: `coverage` в системе нет, а установка нарушила бы запрет на новые зависимости | **Закрыто решением владелицы 2026-09-11:** требование снято, измерять нечем и не нужно. Остаётся процедурная гарантия TDD — шаг не начинается без падающего теста |

## Вне объёма

- Заведение второго профиля `~/.claude-personal`: живой секрет и необратимое действие,
  поставляется отдельным визардом для исполнения человеком.
- Любые обращения к `api.anthropic.com`, чтение токенов, подмена учётных данных.
- Автоматическое переключение аккаунтов.
- Установка и использование сторонних инструментов (`cswap`, `claude_ai_usage_widget`).
- Перенос MCP-логинов во второй профиль: часть визарда, не часть кода.
- Замена `latest.json` миграционным скриптом: читатель понимает схему 1 сам.

## Журнал решений

Ведёт агент `logger` на границах волн: решение, обоснование, отклонение от плана, долг.
Файл: `~/.claude/journal.md`, тег области `[claude-usage-indicator]`.

## Хэндофф исполнителю

- Репозиторий и дерево: `/home/nogorka/Documents/PROJECTS/claude-usage-indicator`, ветка `feat/multi-profile`
- Первая волна: шаги 1, 2, 3, 4 — одновременно, четыре разных файла, worktree не нужен
- Не переоткрывать: push-схему через statusline-хук вместо опроса API (правила Anthropic);
  вывод идентификатора профиля только из `CLAUDE_CONFIG_DIR`; файл состояния на профиль
  вместо одного общего; схему 2 с совместимостью со схемой 1 вместо миграции; связывающее
  окно в баре вместо всех окон; `0%` у просроченного окна вместо сохранённого процента;
  компактный маркер сброса связывающего окна прямо в панели (решение владелицы
  2026-09-11, замер ширины — 446 px из 850 px доступных) вместо прежнего «время сброса
  живёт только в меню»; поиск профилей по `.credentials.json` на диске вместо файла
  конфигурации; перенос всех MCP-логинов во второй профиль (решение владелицы, цена
  записана в спеке); `sed -E` вместо `tr -c` в `profile_id` (замерено, `tr` расходится
  с Python); `shlex.quote` внутри команды вместо `subprocess.Popen(env=…)`; отсутствие
  автотестов в шагах 8 и 9 при вынесенной в `bar.py` текстовой логике; подписи профилей
  по умолчанию `work` и `own` (решение владелицы 2026-09-11); отсутствие числового
  требования к покрытию тестами (решение владелицы 2026-09-11, `coverage` не ставить);
  порог несвежести `is_stale` остаётся часом
