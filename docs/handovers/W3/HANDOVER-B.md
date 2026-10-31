Виконавиця: Маргарита. Reviewer:Андрій. Наступний власник ролі B: Андрій.
Завдання: W3-B · Chunk pipeline для річного набору з коректними межами.
Покриття: R01, R02, R05, R11.

Гілка `marharyta/w3-B`, ціль PR — `main`. Залежить від W2-B (`clean_readings`,
`QualityReport`), реалізацію якого перевикористано без змін.

- `src/energyhome/dataio.py`:
  - `read_chunks(path, source_config=None, chunk_size=1000)` — лінивий
    ітератор блоків `(номер_рядка, сирий_запис)`;
  - `iter_clean_readings(path, report, source_config=None, chunk_size=1000,
    mode='reject')` — потік очищених вимірювань;
  - `SourceConfig` розширено колонками, одиницями та missing values;
  - додано адаптер `UCI_HOUSEHOLD`.
- `scripts/prepare_data.py` — потокова підготовка року: `uci` (реальне
  джерело) або `synthetic` (резервний відтворюваний рік).
- `data/README.md` — опис джерела, ліцензії, отримання повного року,
  формату й synthetic-року.
- `tests/test_dataio_w3.py` — 5 нових unittest.
- `.gitignore` — `data/generated/` (згенеровані річні файли не комітяться).

Нових рядків у `dataio.py` 66, разом із docstrings.

## Як працює pipeline
`read_chunks` бере записи з наявного `_iter_meter_rows` через
`islice`, тому в пам'яті одночасно є не більше одного блоку. Останній неповний
блок теж повертається. Помилка структури CSV виникає тільки тоді, коли
читається її блок, а не на старті.

`iter_clean_readings` передає кожен блок у `clean_readings` з W2-B з **тим
самим** `QualityReport`. Через це `last_timestamp` (дублі, зворотний час,
пропуски) і номер рядка переходять між чанками. Дубль або розрив на межі
чанка обробляється так само, як усередині. Фізичні номери рядків
зберігаються й при читанні наперед: їх відновлює `_numbered`.

На виході для D потік `MeterReading(timestamp, power_kw)`: UTC-мітка початку
інтервалу, kW. Разом із ним `report.rejects` і `report.gaps`, тобто quality report.
Пропуски не заповнюються. Режим за замовчуванням `reject`, `strict` теж
підтримується.

## Адаптер джерела
Нові поля `SourceConfig` (значення за замовчуванням зберігають поведінку W1/W2):

| Поле | За замовчуванням | Призначення |
|---|---|---|
| `timestamp_columns` | `("timestamp",)` | колонки мітки, з'єднуються пробілом |
| `power_column` | `"power_kw"` | колонка потужності |
| `power_unit` | `"kW"` | `"kW"` або `"W"` (×0.001); інше — `DataImporterError` |
| `missing_values` | `("", "?", "NA")` | reject «Missing value» → далі gap |

`UCI_HOUSEHOLD` описує набір UCI Individual household electric power
consumption (CC BY 4.0): `;`, `Date`+`Time` у форматі `%d/%m/%Y %H:%M:%S`,
`Global_active_power` у kW, `?` — пропуск, крок 1 хв, `Europe/Paris`.
Деталі, ліцензія та посилання на завантаження — у `data/README.md`.

## Відтворення
Python 3.11+, команди з кореня репозиторію (на Windows:
`$env:PYTHONPATH="src"`):

```sh
PYTHONPATH=src python -m unittest tests/test_dataio_w3.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/prepare_data.py synthetic --year 2026
```

Пройшли 5 нових тестів і всі 89 тестів проєкту, включно з попередніми 84.
Synthetic-рік має 35 040 інтервалів по 15 хв, ≈7 100 kWh. Генерується за
<1 с, повторний запуск дає побайтово той самий файл. Повторне читання через
`iter_clean_readings` дає 0 rejects і 0 gaps.

Критерії приймання:
- Chunk size 1/17/1000 → той самий впорядкований набір і quality report.
  Перевірено для всіх розмірів 1…41 на ряді з дублем, некоректним
  значенням, `?` і пропуском; результат збігається з `iter_meter_csv`.
- Дублі й розриви на межі чанків обробляються так само, як усередині: ті
  самі розміри ставлять кожну проблему і на межу, і всередину блоку.
- Останній неповний блок зберігається (40 рядків / 17 → 17, 17, 6), а
  iterator не вичерпує файл наперед: перший блок читається навіть тоді, коли
  в кінці файлу зламаний CSV.

Приклад для D:

```python
from energyhome.dataio import QualityReport, iter_clean_readings

report = QualityReport()
energy = sum(r.power_kw for r in iter_clean_readings(path, report)) * 15 / 60
print(energy, len(report.rejects), len(report.gaps))
```

## Обмеження

- Журнал `report.rejects`/`gaps` накопичується в пам'яті. Для реального
  року UCI це тисячі записів, а не мільйони, але він не потоковий.
- Часовий пояс UCI в джерелі не вказано, тому `Europe/Paris` — це
  припущення. Під час переходу на літній/зимовий час можливі дублі або
  зсуви. Вони потрапляють у rejects, а не тихо виправляються.
- Дані UCI мають крок 1 хв. Агрегація до 15 хв не входить у W3-B, бо
  потребує окремого рішення щодо неповних інтервалів.
- `load_demo_profile` лишився strict і будує весь профіль у пам'яті. Для
  річних даних використовуйте `iter_clean_readings`.
- Сирий файл UCI (≈130 MB) у репозиторій не додається.
