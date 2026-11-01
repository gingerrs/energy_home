# EnergyHome

Python 3.11+ · аналіз домашнього споживання електроенергії.

## Встановлення

Створіть віртуальне середовище з кореня репозиторію:

```sh
python -m venv .venv
```

Активуйте його: `source .venv/bin/activate` на macOS/Linux або
`.venv\Scripts\Activate.ps1` у Windows PowerShell. Далі:

```sh
python -m pip install -e .
python -m energyhome --help
```

Залежності встановлюються автоматично: matplotlib для наявних графіків,
а на Windows також tzdata для часових поясів.

## Звіт

Запускайте з кореня репозиторію: CLI читає `config/tariffs.json`
відносно поточної директорії та формує потоковий аналітичний звіт.

```sh
python -m energyhome report --input data/demo/meter_15min.csv --tariff single
```

Очікувано: 4 інтервали, 1.0 kWh, 2.50 UAH. Це синтетичний навчальний приклад;
2.50 UAH/kWh — тестова ставка. Підтримуються `single`, `two_zone`, `three_zone`.
Звіт містить метрики, порівняння тарифів і добові/тижневі таблиці.
Відсутній або пошкоджений CSV, порожній вибір і неправильний період дають
повідомлення у stderr та код завершення 1.

```sh
python -m energyhome report --input data/generated/uci_2008.csv
python -m energyhome report --input data/generated/uci_2008.csv \
    --from 2008-01-01T00:00:00Z --to 2008-01-02T00:00:00Z
```

Річний CSV спочатку підготуйте за [інструкцією даних](data/README.md).
Крок UCI у 1 хвилину читається з сусіднього `uci_2008.quality.json`;
без такого файла використовується стандартний крок 15 хвилин.
Межі задавайте з `Z` або UTC offset, кінець не включається.
Два графіки, контрольний текст і числа реального року: [Demo 1](docs/demo1/README.md).
Якщо основного реєстру немає, збережена підтримка старого `tariffs.demo.json`.

Для часу виконання та діагностики додайте глобальний прапорець перед командою:

```sh
python -m energyhome --log-level DEBUG report --input data/demo/meter_15min.csv
```

Типово рівень `INFO`: успішний звіт іде в stdout, помилки — у stderr.
`DEBUG` додає час виконання `command_report` у секундах. Також доступні рівні
`WARNING`, `ERROR` і `CRITICAL`. Імпорт пакета не налаштовує логування.

## Тести та CI

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_infra.py -v
python -m unittest discover -s tests -p test_cli.py -v
```

Workflow `.github/workflows/tests.yml` встановлює пакет через `pip install .`,
перевіряє залежності, весь набір тестів, CLI-тести в окремому процесі та реальний
демо-звіт. Матриця: Ubuntu/Windows × Python 3.11/3.12; запуск — push і PR до main.
Адміністратор може окремо зробити ці checks обов'язковими через branch protection.

Контракти: [docs/spec.md](docs/spec.md).
Передачі: [docs/handovers/README.md](docs/handovers/README.md).
W2-E API та обмеження: [HANDOVER-E.md](docs/handovers/W2/HANDOVER-E.md).
