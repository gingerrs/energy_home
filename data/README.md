Усі CSV, які читає `energyhome.dataio`, мають мітку часу та потужність.
Після очищення це потік `MeterReading(timestamp, power_kw)`: мітка в UTC на
початку інтервалу, потужність у kW. Енергія інтервалу дорівнює
`power_kw × step_minutes / 60`.

| Шлях | Що це | У git |
|---|---|---|
| `data/demo/meter_15min.csv` | 4 інтервали по 15 хв, рівно 1.0 kWh (fixture W1) | так |
| `data/generated/synthetic_<рік>.csv` | синтетичний рік, крок 15 хв | ні, генерується |
| `data/generated/uci_<рік>.csv` | рік із реального джерела UCI, крок 1 хв | ні, генерується |

Команди нижче виконуються з кореня репозиторію (Python 3.11+). На Windows
змінну `PYTHONPATH` задайте способом своєї оболонки, наприклад
`$env:PYTHONPATH="src"`.

## Реальне джерело: UCI Individual household electric power consumption

- Що це: одне домогосподарство в Со (Франція), вимірювання щохвилини,
  грудень 2006 – листопад 2010, близько 2,07 млн рядків.
- Автори: Georges Hébrail, Alice Bérard. Розповсюджується через
  UCI Machine Learning Repository (dataset 235).
- Ліцензія: CC BY 4.0. Дані можна використовувати й змінювати, але з
  обов'язковим посиланням на авторів і джерело. Перед здачею перевірте умови
  на сторінці набору.
- Як отримати: завантажте архів зі сторінки
  <https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption>
  і розпакуйте `household_power_consumption.txt` (≈130 MB). Сирий файл у git
  не додається.

Формат сирого файлу описує адаптер `dataio.UCI_HOUSEHOLD`:

| Параметр | Значення |
|---|---|
| delimiter | `;` |
| timestamp | колонки `Date` + `Time`, формат `%d/%m/%Y %H:%M:%S` |
| units | `Global_active_power` у kW (`power_unit='kW'`) |
| missing values | `?` або порожнє поле → reject «Missing value» і gap |
| timezone | `Europe/Paris`. Це припущення: у джерелі часовий пояс не вказано |
| крок | 1 хвилина |

Повні роки в наборі: 2007, 2008, 2009. Для Demo 1 рекомендовано 2008.
Щоб отримати рік:

```sh
PYTHONPATH=src python scripts/prepare_data.py uci \
    --input household_power_consumption.txt --year 2008
```

Скрипт читає файл блоками й зупиняється, щойно рік закінчився. Він записує
`data/generated/uci_2008.csv` і друкує кількість rejects і gaps.

## Резервний відтворюваний синтетичний рік

Цей варіант не потребує завантаження і повністю відтворюваний:

```sh
PYTHONPATH=src python scripts/prepare_data.py synthetic --year 2026 --seed 42
```

Скрипт генерує 35 040 інтервалів по 15 хв (≈7 100 kWh на рік) з моделей
приладів W2-A: фон `FixedDevice` 0.25 kW, бойлер `ThermostaticDevice`
2 kW з duty-cycle 0.25 і пральна машина `CyclicDevice` 1.5 kW щодня о
19:00 UTC, плюс шум ±0.02 kW. Однакові `--year` і `--seed` дають
побайтово однаковий файл.

## Як читати річні дані

```python
from energyhome.dataio import QualityReport, SourceConfig, iter_clean_readings

report = QualityReport()
for reading in iter_clean_readings('data/generated/uci_2008.csv', report,
                                   SourceConfig(step_minutes=1)):
    ...  # обробка одного інтервалу
print(len(report.rejects), len(report.gaps))
```

Для синтетичного року достатньо `SourceConfig()` (крок 15 хв).
`load_demo_profile` збирає весь профіль у пам'ять і працює в режимі strict,
тому для річних даних із пропусками він не підходить.
