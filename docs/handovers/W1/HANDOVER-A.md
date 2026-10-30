# Передача W1-A

Відповідальна: Anna Yarosh. Наступна власниця ролі A на W2: Маргарита.

Завдання: мінімальний domain для реального розрахунку — спільні класи для B/C/D, енергія 1.0 kWh на спільному fixture та завершені публічні конструктори в межах W1.

## Що реалізовано

- `src/energyhome/domain.py`: `MeterReading`, `LoadProfile`, абстрактний `Tariff`, `SingleZoneTariff` та ієрархія винятків.
- `tests/test_domain.py`: 17 доменних тестів.
- `tests/fixtures/w1_profile.csv`: контрольний профіль [1, 3] kW із кроком 15 хвилин, разом 1.0 kWh.

Робота W1-A міститься в гілці `anna/w1` (коміт `879167dc141d9a1f1e7f967e4ed6320ea3ac5033`). Її доменний код уже є в `main`; розширення W2-A задокументоване окремо.

## Публічні конструктори та правила

- `MeterReading(timestamp, power_kw)` — незмінний запис сирого вимірювання; перевірка значень виконується під час створення профілю.
- `LoadProfile(timestamps, power_kw, resolution_minutes)` — дві послідовності однакової ненульової довжини та додатний цілий крок у хвилинах. Вхідні послідовності копіюються.
- `SingleZoneTariff(tariff_id, name, timezone_name, rate)` — непорожній ідентифікатор, назва-рядок, відома IANA-зона та скінченна невід'ємна ставка.
- `Tariff` — абстрактна основа: нащадок реалізує `rate_at(timestamp)` і `zone_at(timestamp)`.

Часові мітки профілю мають містити часовий пояс, переводяться в UTC та утворювати регулярну зростаючу сітку без дублікатів і пропусків. Потужність задається в kW, повинна бути числовою, скінченною й невід'ємною; булеві значення не приймаються.

Некоректний профіль дає `MeterDataError`, некоректний тариф — `TariffError`. Вони, як і `DeviceError` та `ScheduleConflictError`, успадковуються від `EnergyError`.

## Підключення B/C/D

Імпортуйте класи з `energyhome.domain`.

- B створює `LoadProfile` зі списків часових міток і потужностей. Для порожнього CSV поточний loader повертає `None`: конструктор W1 не приймає порожній профіль.
- C перебирає профіль як пари `(timestamp, power_kw)`, використовує `resolution_minutes` і `tariff.rate_at(timestamp)`.
- D отримує `len(profile)`, `start_time()`, `end_time()`, `energy_kwh()` та `peak_kw()`. Кінець періоду виключний: остання мітка плюс один крок.

Енергія: `sum(power_kw) * resolution_minutes / 60`. `SingleZoneTariff.zone_at()` повертає `single`. Метод `Tariff.cost(profile)` повертає неокруглену суму; остаточне грошове округлення у звіті належить розрахунковому модулю C.

## Відтворення та перевірка

Потрібен Python 3.11+. З кореня репозиторію:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_domain.py -v
```

Результат перевірки W1-A: 17 тестів проходять. Покрито енергію, пік, часові межі, UTC, копіювання даних, помилкові параметри, нульову потужність, нульову ставку й абстрактність тарифу.

Для спільного fixture запустіть `PYTHONPATH=src python3` та виконайте:

```python
from energyhome.dataio import load_demo_profile
from energyhome.domain import LoadProfile

profile = load_demo_profile('data/demo/meter_15min.csv')
assert isinstance(profile, LoadProfile)
print(profile.energy_kwh())
```

Очікувано: `1.0`. У спільному CSV чотири інтервали по 15 хвилин із потужністю 1 kW. Часовий пояс для його міток задає `SourceConfig` модуля B.

На знімку `main` `1bd9a9a` додатково перевірено, що B використовує справжній `domain.LoadProfile`, а C отримує для цього fixture 1.0 kWh і 2.50 UAH за умовної ставки 2.50 UAH/kWh. Це перевірка A/B/C на fixture, а не підтвердження всього командного CLI.

## Межі W1 та наступний крок

`House`, `EnergyMeter` і типи приладів належать W2-A та не входять у цю передачу. Їхня передача: [W2/HANDOVER-A.md](../W2/HANDOVER-A.md). Власниця W2-A продовжує спільний domain, зберігаючи сумісність із профілем і тарифом W1.

