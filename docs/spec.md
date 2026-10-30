# Контракт аналітики EnergyHome

## API та залежності

Потрібен Python 3.11+. Аналітика використовує справжні `LoadProfile`, `Tariff`
і `core.calculate_cost`, не читає файлів і не друкує результатів.

`ReportSummary`, `DailySummary` та `WeeklySummary` — незмінні dataclass із `slots`.

`ReportSummary` містить:

- `period_start: datetime | None`;
- `period_end_exclusive: datetime | None`;
- `interval_count: int`;
- `energy_kwh: float`;
- `cost_uah: Decimal`.

`build_demo1_summary(profile, tariff)` бере часові межі й кількість інтервалів
із профілю. Енергію читає з `CostBreakdown.energy_kwh`, вартість — з
`CostBreakdown.total_cost`. Це наявні публічні поля core, обидва типу `float`.
В аналітиці вартість перетворюється через `Decimal(str(total_cost))`.
Розрахунок і фінальне округлення до копійок залишаються в core.
Помилки залежностей передаються викликачеві без підміни нульовими значеннями.

Порожні дані (`None` або профіль із нульовою довжиною) дають
`ReportSummary(None, None, 0, 0.0, Decimal("0.00"))` без виклику core.
Нульове споживання за наявності вимірювань зберігає справжній період і count.

`format_console_report(summary)` повертає чотири рядки без кінцевого переносу.
Період має межі `[start, end)`, дати — ISO із часовим зсувом, гроші — два
десяткові знаки. Непорожній summary без часових меж відхиляється з `ValueError`.

Приклад для `[1, 2, 0, 1]` kW, кроку 15 хвилин і ставки 2.50 UAH/kWh:

```text
Період: [2026-01-15T00:00:00+00:00, 2026-01-15T01:00:00+00:00)
Інтервалів: 4
Енергія: 1.0 kWh
Вартість: 2.50 UAH
```

## Добові й тижневі підсумки

`daily_summaries(profile, tariff)` групує початки інтервалів за локальною датою
в `tariff.timezone_name`. Вихідний UTC-профіль не змінюється.
Порожні дані дають `[]`. Прогалини не заповнюються; `SubProfile` передає core
лише наявні вимірювання з їхнім кроком.

`DailySummary` містить `day`, `interval_count`, `energy_kwh`, `cost_uah`,
`coverage_pct`. Покриття — тривалість наявних інтервалів, поділена на фактичну
тривалість локальної доби. Вона визначається між двома локальними півночами
в UTC, тому враховує доби на 23 або 25 годин. Інтервал належить даті свого
початку; для точного добового розподілу сітка має збігатися з локальною північчю.

`weekly_summaries(daily_list)` групує дні за календарними тижнями з понеділка
до неділі, зокрема через межу року. Результати впорядковані за початком тижня.
`WeeklySummary` містить `days_count`, `interval_count`, `energy_kwh`, `cost_uah`.
Вхід — не більше одного `DailySummary` на дату. Пропущені дні не додаються.
Енергія та вже округлена добова вартість підсумовуються без повторного виклику
core. Через добове округлення сума вартості днів може відрізнятися від одного
розрахунку core за весь період. Порожній список дає `[]`.

## Моделі приладів і детектор

`Device.profile_for((start, end), step_minutes)` приймає timezone-aware межі,
переводить їх у UTC і будує рівномірну сітку в абсолютному часі. Тривалість має
бути додатною й кратною додатному цілому кроку. Неповний останній інтервал
відхиляється через `ValidationError`, а не мовчки відкидається.

`cycle_minutes` — додатне ціле число, `start_minute` — невід'ємне ціле число.
Дробові значення та `bool` відхиляються без обрізання через `int()`.
`detect_events` приймає лише скінченні числові пороги без `bool`, причому
`on_threshold > off_threshold`; некоректні пороги дають `ValueError`.

## CLI та перевірка

CLI читає CSV через dataio, створює `SingleZoneTariff` і друкує готовий звіт
аналітики. Приклад запуску з кореня репозиторію:

```sh
PYTHONPATH=src python3 -m energyhome report --input data/demo/meter_15min.csv --tariff single
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Стандартний запуск включає `tests/test_integration_analytics.py`: справжні
core, domain, CSV-loader і CLI без mocks. `tests/test_w2_regressions.py`
перевіряє часові межі, агрегації, незмінність DTO, валідацію моделей і зв'язок
генератора та CSV-fixtures із реальним детектором.
