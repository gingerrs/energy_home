Передача W2-A Дата: 30.10.2026. Відповідальна за роль A: Маргарита. Reviewer: Андрій. Наступний власник ролі A: Андрій. Моделі приладів отримує генератор (роль B) і оптимізатор.

Результат src/energyhome/domain.py: після коду W1 (без змін у ньому) додано ValidationError, EnergyMeter, House, Device, FixedDevice, CyclicDevice, ThermostaticDevice. tests/test_devices.py: 7 тестів моделей приладів, будинку й лічильника. docs/HANDOVER-A.md: ця передача. Device має device_id, power_kw, ознаку shiftable і спільний метод profile_for(period, step_minutes), який повертає LoadProfile з W1. Три моделі відрізняються лише поведінкою is_on(minute), а не полями: FixedDevice увімкнений завжди, CyclicDevice робить один запуск тривалістю cycle_minutes із start_minute, ThermostaticDevice — простий on/off duty-cycle.

Модель термостата: у кожному вікні cycle_minutes (за замовчуванням 60) прилад увімкнений перші duty_cycle × cycle_minutes хвилин, решту вимкнений. Наприклад, 0.25 → 15 хв on, 45 хв off. duty_cycle = 0 — завжди вимкнений, 1 — завжди увімкнений.

ValidationError — нащадок DeviceError, тому код, що ловить DeviceError чи EnergyError, працює як раніше. Помилку дають: порожній id, від'ємна чи нескінченна потужність, cycle_minutes ≤ 0, start_minute < 0, duty_cycle поза [0, 1], ліміт лічильника ≤ 0, крок ≤ 0, період без жодного кроку, дублікати id приладів у House.

Відтворення Потрібен Python 3.11+. Команди виконуються з кореня репозиторію:

PYTHONPATH=src python3 -m unittest discover -s tests -v PYTHONPATH=src python3 -m unittest tests/test_devices.py -v

7 нових тестів разом із 17 тестами domain з W1 проходять — 24 зелені, перевірено на справжньому fixture w1_profile.csv. Критерії приймання покрито: FixedDevice 1 kW за годину = 1 kWh; CyclicDevice (2 kW, 30 хв зі зсувом 15 хв) на сітці 15 хв дає [off, on, on, off] і 1.0 kWh; ThermostaticDevice (2 kW, 0.25) за 2 години дає [on, off, off, off] × 2 і 1.0 kWh; некоректні параметри дають ValidationError.
На Windows потрібен pip install tzdata,щоб коректно працювало

Приклад:

from datetime import datetime, timedelta, timezone from energyhome.domain import ThermostaticDevice start = datetime(2026, 1, 1, tzinfo=timezone.utc) profile = ThermostaticDevice('boiler', 2.0, duty_cycle=0.25).profile_for((start, start + timedelta(hours=1)), 15) print(profile.power_kw, profile.energy_kwh()) # [2.0, 0.0, 0.0, 0.0] 0.5

Підключення Період передається як пара (start, end) із timezone-aware datetime. Результат — звичайний LoadProfile, тож його можна одразу передавати в тарифи (C) та аналітику (D).

B: генератор має викликати device.profile_for(period, step_minutes) замість копіювання фізики приладів. Оптимізатор: переносити можна лише CyclicDevice (shiftable = True, змінюється start_minute). FixedDevice і ThermostaticDevice не переносяться.

Обмеження Стан приладу береться на початку кожного кроку; якщо крок довший за відрізок «on», енергія наближена, тому краще брати крок, що ділить цикл націло. CyclicDevice запускається один раз за період. House поки не перевіряє сумарну потужність приладів проти max_power_kw лічильника.
