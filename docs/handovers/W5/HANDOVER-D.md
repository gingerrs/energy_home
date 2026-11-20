# Передача W5-D — DataFrame-контракт, resample, rolling і календарні ознаки

Авторка: Маргарита. Reviewer: Андрій. Наступний власник ролі D: Андрій.
Завдання: W5-D. Покриття: R01, R02, R09.

## Результат

- `src/energyhome/analytics.py`. Додано 43 рядки, разом із docstrings:
  - `FRAME_COLUMNS` — порядок колонок контракту;
  - `profile_to_dataframe(readings, step_minutes=15, timezone_name='Europe/Kyiv', start=None, end=None)`;
  - `dataframe_summary(frame, freq='D', timezone_name='Europe/Kyiv')` — resample;
  - `rolling_stats(frame, window, min_periods=None)`.
- `tests/test_analytics_w5.py` — 4 нові unittest.
- `pyproject.toml` — залежність `pandas>=2.2,<4`.
- `README.md` — рядок про залежності; `docs/handovers/README.md` — W5 в індексі.

Pandas зосереджено в `analytics.py` і імпортується всередині цих трьох
функцій, як matplotlib у графіках. Тому `energyhome report` і далі не
завантажує NumPy/pandas: це перевіряє наявний тест `test_cli.py`.
Тарифних розрахунків у pandas немає. Вартість лишається в `core`.

## DataFrame-контракт

| Колонка / індекс | Тип | Значення |
|---|---|---|
| індекс `timestamp` | `datetime64[ns, UTC]` | початок інтервалу; унікальний, зростає, порядок як на вході |
| `power_kw` | float64 | середня потужність інтервалу |
| `energy_kwh` | float64 | `power_kw × step_minutes / 60` |
| `date` | `datetime.date` (object) | місцева дата в `timezone_name` |
| `hour` | int | місцева година 0–23 |
| `day_of_week` | int | 0 = понеділок … 6 = неділя (місцево) |

Індекс у UTC, бо він однозначний навіть тоді, коли місцевий час повторюється
(перехід на зимовий час). Календарні ознаки рахуються в місцевому поясі, бо
для аналізу «о котрій годині» й «у який день» потрібен саме місцевий час.

На вхід іде будь-що з парами `(timestamp, power_kw)`: `LoadProfile`,
`SubProfile`, потік `MeterReading` з `iter_clean_readings`. Крок береться з
`resolution_minutes` профілю або з аргументу. `start`/`end` обирають період
`[start, end)`: конвертується лише він, а не весь сирий файл. Пропуски не
заповнюються. Час без поясу, дублікати чи зворотний порядок дають `ValueError`.

## Resample і rolling

`dataframe_summary` групує за **місцевими** календарними періодами
(`'D'`, `'h'`, `'W-MON'`, `'MS'` тощо). Результат: `energy_kwh` (сума),
`mean_power_kw` і `peak_kw` (по спостережених інтервалах) та `intervals`
(кількість). Енергія завжди **сумується**, а не усереднюється, тому
загальна сума зберігається. Порожній період усередині діапазону лишається
видимим з `intervals = 0` і NaN-потужністю, без вигаданих нулів.

`rolling_stats` дає `rolling_mean_kw` і `rolling_peak_kw` за `window`
спостережених інтервалів (рядків). За замовчуванням `min_periods = window`,
тобто перші `window − 1` значень — NaN.

## Відтворення

Python 3.11+, з кореня репозиторію (Windows: `$env:PYTHONPATH="src"`):

```sh
python -m pip install -e .        # підтягне pandas
PYTHONPATH=src python -m unittest tests/test_analytics_w5.py -v
PYTHONPATH=src python -m unittest discover -s tests
```

Пройшли всі 254 тести: 250 попередніх і 4 нові.

Критерії приймання:

- **DataFrame із fixture.** `data/demo/meter_15min.csv` дає UTC-індекс
  00:00, 00:15, 00:30, 00:45 (2026-01-15), 4 рядки, `energy_kwh` =
  [0.25, 0.5, 0.0, 0.25], разом 1.0 kWh. Місцева година 2, четвер (3).
- **Resample зберігає енергію.** Три доби навколо переходу на зимовий
  час (24–26.10.2026): інтервали по днях 96 / 100 / 96, бо доба 25.10 має
  25 годин. Добова енергія збігається з еталонним `daily_summaries`
  (core), а сума — з `LoadProfile.energy_kwh()`. Те саме для `'h'`.
- **Rolling на [1, 2, 3], window = 2.** Середнє [NaN, 1.5, 2.5], пік
  [NaN, 2, 3]; з `min_periods=1` середнє [1, 1.5, 2.5]. `window = 0` дає
  `ValueError`.
- **Порожні дані й межа поясу.** Порожній вхід дає порожній DataFrame з
  колонками контракту, а resample і rolling не падають. На добу 25.10
  місцева 03:00 трапляється 8 разів (дві години), а UTC-індекс лишається
  впорядкованим.

## Приклади для notebook

```python
from energyhome.analytics import dataframe_summary, profile_to_dataframe, rolling_stats
from energyhome.dataio import QualityReport, SourceConfig, iter_clean_readings

report = QualityReport()
readings = iter_clean_readings('data/generated/uci_2008.csv', report,
                               SourceConfig(step_minutes=1))
frame = profile_to_dataframe(readings, step_minutes=1)

daily = dataframe_summary(frame, 'D')                 
heat = frame.pivot_table(index='hour', columns='day_of_week',
                         values='energy_kwh', aggfunc='sum')   
by_hour = frame.groupby('hour')['power_kw'].mean()    
smooth = rolling_stats(frame, 60, min_periods=30)     
daily['energy_kwh'].plot()                            
```

Рік на 526 845 хвилинних інтервалів будується приблизно за 1–2 с і займає
близько 36 MB (перевірено на синтетичному ряді такого розміру). Для меншого
обсягу задайте `start`/`end`.

## Обмеження

- `rolling_stats` рахує вікно в **рядках**, а не в часі. Якщо в даних є
  пропуск, вікно його «перестрибує». Для часових вікон можна викликати
  `frame['power_kw'].rolling('1h')`.
- `dataframe_summary` не рахує вартість: для грошей використовуйте `core`
  (`calculate_cost`, `daily_summaries`), щоб не дублювати тарифну логіку.
- DataFrame тримає вибраний період у пам'яті. Він для аналізу й графіків,
  а не для потокового звіту `report`, який лишається на чистому Python.
- Pandas додано в залежності проєкту. У цьому PR чисте встановлення з
  pandas перевіряє CI (`pip install .` на Ubuntu і Windows).

