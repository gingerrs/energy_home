# Передача W2-E

Відповідальний: Андрій. Наступна власниця ролі E та reviewer: Софія.
База: `main` на `1566e02`. Гілка: `feature/andrii-w2-E-infra`.
Задача: [W2-E у Notion](https://app.notion.com/p/3f57884616a381469175da7c54e726df).

## Результат

- `src/energyhome/infra.py`: завершені `configure_logging`, `timed`, `validate_positive`.
- CLI налаштовує логування після розбору аргументів; час `command_report` видно на DEBUG.
- CI встановлює пакет і запускає всі unittest на Ubuntu/Windows із Python 3.11/3.12.
- PR-шаблон містить результат, тести, API impact і посилання на передачу.
- README описує встановлення, реальний запуск і перевірку.

Мінімальні інтеграційні виправлення для CI: у pyproject оголошено matplotlib,
який уже імпортує аналітика W3; на Windows додано tzdata. Два CLI-тести тепер
підміняють конкретні функції, а не неповні модулі в `sys.modules`, тому проходять
як окремо, так і разом з іншими тестами.

## API

### `configure_logging(level: int | str = logging.INFO) -> logging.Logger`

Явно налаштовує logger `energyhome`, один іменований StreamHandler у поточний
stderr і формат `LEVEL logger: message`. Повторний виклик оновлює рівень та потік
без дублювання handler. Чужі handlers і root logger залишаються без змін.
Записи `energyhome.*` обробляються на рівні пакета (`propagate=False`).
Функцію викликають при старті CLI, не під час імпорту. Викликайте її послідовно
на старті застосунку; довільна паралельна реконфігурація не входить у контракт.

### `@timed`

Декоратор синхронної функції. Викликає `perf_counter`, повертає початковий
результат і не замінює виняток функції. Блок `finally` записує тривалість у секундах
через `energyhome.timing` на рівні DEBUG і для успішного, і для невдалого виклику.
`functools.wraps` зберігає назву, docstring, анотації та `__wrapped__`.
Coroutine/generator-функції цим декоратором не вимірюйте: він вимірює лише
безпосереднє синхронне виконання виклику.

### `@validate_positive("parameter")`

Перевіряє один явно вибраний параметр перед викликом функції. Приймає скінченні
`int`/`float` строго більші за нуль, без перетворення значення. Відхиляє zero,
negative, NaN, infinity, bool та інші типи через `ValueError`.
Підтримує позиційні й іменовані аргументи, defaults, keyword-only, positional-only
та методи. Помилкова назва параметра або вибір `*args`/`**kwargs` відхиляється
при декоруванні. Неправильний набір аргументів зберігає `TypeError` Python.
Декоратор не застосовано до ставки тарифу: нульова ставка дозволена domain.

```python
from energyhome.infra import timed, validate_positive

@timed
@validate_positive("step_minutes")
def interval_hours(step_minutes: int = 15) -> float:
    return step_minutes / 60
```

## Відтворення

Після встановлення за README, з кореня репозиторію:

```sh
python -m pip check
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_infra.py -v
python -m unittest discover -s tests -p test_cli.py -v
python -m energyhome report --input data/demo/meter_15min.csv --tariff single
python -m energyhome --log-level DEBUG report --input data/demo/meter_15min.csv
```

Локально перевірено звичайне wheel-встановлення на Python 3.12.3: 149 тестів
проходять (130 попередніх + 19 W2-E); окремі 9 CLI-тестів проходять.
`pip check` не знаходить зламаних залежностей. Реальний fixture дає 4 інтервали,
1.0 kWh, 2.50 UAH; DEBUG-лог не потрапляє в текст звіту.

## Приймання

Workflow запускається на push та PR до main; `continue-on-error` немає.
Перевірено на GitHub Actions:

- [Зелений прогін](https://github.com/gingerrs/energy_home/actions/runs/41297576420)
  на `1cfcfd6`: усі 4 jobs успішні, включно зі встановленням, повним набором тестів,
  окремими CLI-тестами й реальним звітом.
- [Навмисно зламаний тест](https://github.com/gingerrs/energy_home/actions/runs/41297591807)
  на `2548064`: усі 4 jobs упали саме на кроці `Run all tests`; наступні кроки пропущено.
  Гілка `test/andrii-w2-E-ci-failure` містить єдиний додатковий failure probe.
  Її не зливати: зламаного тесту немає в гілці W2-E та її PR.

Незалежне review Софією, усна передача та погодження нового infra API командою
потрібні окремо. До цього не вважати Status=Done підтвердженим.
Branch protection налаштовує адміністратор; готовність workflow не залежить
від наявності такого доступу у виконавця.

Наявне обмеження CLI: тарифний JSON читається відносно поточної директорії;
команди вище запускаються з кореня репозиторію. Завантаження конфігурації та
розширення CLI наступного тижня передаються Софії.
