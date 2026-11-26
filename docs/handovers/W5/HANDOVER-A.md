> Запис приймання W5-A на вказаному нижче коміті. Поточна інтеграція профілів та конфігурацій описана в ../../spec.md.

# Передача W5-A — операції профілів та JSON-схема домену

Відповідальна: Діана. Reviewer та наступна власниця ролі A: Аня.
База: `main`. Гілка: `diana/w5-A`.
Задача: W5-A — операції профілів та JSON-модель будинку.

## Результат і межі

У `domain.py` додано поелементне додавання профілів `LoadProfile.__add__`,
магічні методи порівняння пристроїв (`Device.__eq__`, `Device.__lt__`) та
єдину версіоновану JSON-серіалізацію (`to_dict` / `from_dict`, `SCHEMA_VERSION = 1`)
для `House`, `EnergyMeter`, усіх 3 типів `Device` (`fixed`, `cyclic`, `thermostatic`),
усіх 3 типів `Tariff` (`single`, `two_zone`, `three_zone`) і `Schedule`.

Домен працює виключно зі словниками (`dict`) і не читає файлів: файловий I/O
залишається відповідальністю `dataio.py`, а GUI/CLI не вигадують власну схему.
Ієрархія винятків (`EnergyError`, `ValidationError`, `TariffValidationError`) та
існуючі публічні сигнатури збережені без змін.

## API

```python
from energyhome.domain import (
    CyclicDevice,
    EnergyMeter,
    FixedDevice,
    House,
)

# 1. Додавання сумісних профілів
combined = profile_a + profile_b

# 2. Порівняння пристроїв
d1 = CyclicDevice('washer', 1.8, 60, start_minute=10)
d2 = FixedDevice('heater', 2.5)
assert d1 < d2

# 3. Round-trip моделі будинку
house = House('demo-house-01', EnergyMeter('meter-main', 10.0), [d1, d2])
restored_house = House.from_dict(house.to_dict())
```

`LoadProfile.__add__(other)` виконує поелементну суму потужностей (`power_kw`)
лише за умови повного збігу `resolution_minutes` та часової сітки `timestamps`.
Будь-яка невідповідність кроку або часових міток піднімає `ValidationError`;
для сторонніх типів повертається `NotImplemented`.

Порівняння пристроїв має явно зафіксований критерій:
* `Device.__eq__` перевіряє збіг класу пристрою, `device_id`, `power_kw` та всіх
  специфічних параметрів підкласу (`cycle_minutes`, `start_minute` для `CyclicDevice`;
  `duty_cycle`, `cycle_minutes` для `ThermostaticDevice`).
* `Device.__lt__` впорядковує пристрої за номінальною потужністю `power_kw` за
  зростанням, а при рівній потужності — лексикографічно за `device_id`.

Усі методи `to_dict()` формують словник зі службовими полями `'schema_version': 1`
та явним дискримінатором `'type'`. `from_dict()` виконує сувору валідацію:
* відхиляє невідомі чи відсутні `schema_version` або `type` через `ValidationError`;
* перевіряє точний набір ключів без підстановки мовчазних дефолтів (`silent defaults`);
* зберігає реєстр дозволених `device_ids` усередині `Schedule.to_dict()` / `from_dict()`.

## Конфігурації

Читання та запис JSON-файлів залишається відповідальністю B/E. Domain оперує
виключно розібраними словниками:

```python
import json
from pathlib import Path
from energyhome.domain import House, tariffs_from_dict, tariffs_to_dict

house_data = json.loads(Path('config/house.json').read_text(encoding='utf-8'))
house = House.from_dict(house_data)

tariffs_data = json.loads(Path('config/tariffs.json').read_text(encoding='utf-8'))
tariffs = tariffs_from_dict(tariffs_data)
roundtrip_tariffs = tariffs_from_dict(tariffs_to_dict(tariffs))
```

Підготовлено приклад конфігурації будинку `config/house.json` (`schema_version: 1`,
`synthetic: true`), узгоджений за стилем із наявним `config/tariffs.json`.
`tariffs_from_dict` / `tariffs_to_dict` повністю сумісні з `config/tariffs.schema.json`
та делегують валідацію записів у наявну `tariff_from_config`.

## Контрольні приклади та перевірка

```sh
python -m unittest tests/test_domain.py -v
python -m unittest discover -s tests -v
```

У `tests/test_domain.py` додано нові юніт-тести W5-A:
1. `test_load_profile_addition_and_energy` — поелементна сума двох узгоджених
   профілів і точність інтегральної енергії (`energy_kwh()`).
2. `test_incompatible_profiles_and_device_comparison` — `ValidationError` при
   різних `step` / `timestamps`, а також перевірка `__eq__` та `__lt__` для пристроїв.
3. `test_json_roundtrip_and_strict_schema_validation` — повний round-trip
   `to_dict -> from_dict` для `House` (з `config/house.json`), реєстру `Tariff`
   (з `config/tariffs.json`) і `Schedule`, а також відхилення невідомих
   `schema_version` та `type`.

Для контрольного прикладу з двох профілів по 2 інтервали по 30 хв (`[1.0, 2.0]` kW
та `[0.5, 1.5]` kW) сумарний профіль має потужності `[1.5, 3.5]` kW та енергію `2.5 kWh`.

Підключення нового `config/house.json` до файлового завантажувача `dataio.py` та
екранів/команд належить ролям B та E. Перед фінальним Done потрібні review Анею,
узгодження контракту `Schedule` з командою та усна передача.