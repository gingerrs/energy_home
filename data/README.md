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

Скрипт відбирає календарний рік джерела до поділу на чанки й зупиняється на
першому рядку наступного року. Він записує `data/generated/uci_2008.csv`,
`data/generated/uci_2008.quality.json` і друкує кількість rejects та gap-подій.
Журнал містить тільки вибраний рік; фізичні номери рядків початкового файлу збережені.

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

## W4-B: відтворення перевіреного 2008 року

З кореня репозиторію встановіть пакет через `python -m pip install -e .`.
Завантаження на macOS/Linux:

```sh
mkdir -p data/raw
curl -fL --http1.1 --connect-timeout 20 --max-time 300 -C - \
  'https://archive.ics.uci.edu/static/public/235/individual+household+electric+power+consumption.zip' \
  -o data/raw/uci235.zip
python -m zipfile -e data/raw/uci235.zip data/raw
```

На Windows створіть папку через `New-Item -ItemType Directory -Force data/raw`
і використовуйте `curl.exe` замість псевдоніма PowerShell `curl`.
`-C -` дозволяє повторити незавершене завантаження з того самого джерела.
Сирі й згенеровані дані виключені з Git. Посилання, автори, ліцензія та SHA-256
перевіреного архіву/файлу наведені в [контрольному звіті](quality/w4_2008.json).
Джерело та CC BY 4.0 перевірені на [сторінці UCI](https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption).

Два незалежні прогони того самого річного входу:

```sh
python scripts/prepare_data.py uci --year 2008 --chunk-size 17 \
  --input data/raw/household_power_consumption.txt --out data/generated/uci_2008_c17.csv
python scripts/prepare_data.py uci --year 2008 --chunk-size 1000 \
  --input data/raw/household_power_consumption.txt --out data/generated/uci_2008_c1000.csv
```

| Контроль | Обидва прогони |
|---|---|
| Період джерела | `[2008-01-01, 2009-01-01)` у прийнятому `Europe/Paris` |
| Період у UTC | `[2007-12-31T23:00:00Z, 2008-12-31T23:00:00Z)` |
| Рядків джерела | 527 040, включно з 29 лютого |
| Прийнято / rejects | 526 845 / 195 |
| Gap-подій / відсутніх інтервалів | 16 / 195 |
| Спостережена енергія | `56481077/6000` kWh, приблизно 9413.512833333 kWh |
| Перший / останній початок інтервалу | `2007-12-31T23:00:00Z` / `2008-12-31T22:59:00Z` |

CSV обох прогонів побайтово однаковий. Його SHA-256:
`11bcaaeca48728af3fe5e208a5b26da0ada7b542737f175aea05b3ed665b8cc1`.
Файли `.quality.json` однакові, крім явного поля `chunk_size`.
`source_rows` — accepted + rejects саме вибраного року. `gaps` — кількість подій,
а `missing_intervals` — очікувана календарна сітка мінус accepted, включно з
можливими пропусками на початку/кінці. Одне не слід підміняти іншим.

Із 195 rejects: 135 `Missing value`, 60 `Nonexistent local timestamp`.
У джерелі немає UTC offset/fold, тому `Europe/Paris` є припущенням адаптера,
а не встановленим фактом про годинник вимірювача. Неіснуючі локальні мітки
навесні відхиляються; неоднозначні восени трактуються як `fold=0`, без
вигадування другої години. Осінній розрив фіксується як gap тривалістю 60 хв.
За цим припущенням 30 березня прийнято 1380 із 1440 рядків, 26 жовтня — 1440;
29 лютого — усі 1440. Пропуски не заповнюються й не вважаються нульовим споживанням.
Наведені kWh стосуються лише прийнятих інтервалів.

Контрольний малий зріз: `tests/fixtures/w4_uci_slice.csv` — перші 12 хвилин року,
фізичні рядки 547598–547609 початкового UCI-файлу; залишено три потрібні колонки.
Джерело й авторство — ті самі UCI/Hebrail/Berard, CC BY 4.0. Його незалежна
контрольна енергія — `9461/30000` kWh; всі записи прийняті без gaps.

## Резерв W4 без мережі

```sh
python scripts/prepare_data.py synthetic --year 2008 --seed 42 \
  --out data/generated/synthetic_2008.csv
```

Це явно синтетичний рік у UTC: 35 136 інтервалів по 15 хв, 7136.7368225 kWh,
rejects=0, gaps=0. Повторний запуск з тим самим seed дає побайтово той самий CSV.
SHA-256: `14aeda34733644e53a680ac0af462613e78552b8cc085187c240a115be0bc858`.
Перемикання джерела виконується аргументом команди, без ручних правок файлів.
Помилка реального джерела не підміняється синтетикою автоматично.

```sh
python -m unittest discover -s tests -p test_dataio_w4.py -v
python -m unittest discover -s tests -v
```

Відсутній файл, некоректні параметри, порожній вибраний рік або мітка, рік якої
неможливо визначити, дають ненульовий код завершення. Вихідні файли невдалого
прогону не використовуйте як підтверджені дані. Для `year` роки джерела мають
бути впорядковані; дані після першого рядка наступного року не перевіряються.
Пам'ять потоку — один чанк плюс накопичений журнал rejects/gaps, не весь рік.
