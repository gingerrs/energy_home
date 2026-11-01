Тиждень 4 (Роль E)

Виконавець: Діана
Reviewer: Аня

Компоненти: src/energyhome/cli.py, tests/test_cli.py

Що реалізовано

Точка входу та CLI-команда report:

Підтримка аргументів: --input, --tariff, --from, --to, --log-level.

Інтеграція з інфраструктурними декораторами: @cache_profile, @log_call, @timed.

Підтримка виклику як модуля: python -m energyhome report ... (через __main__.py).

Чітка семантика кодів виходу:

0: Успішне виконання розрахунку та виведення звіту.

1: Доменні помилки, відсутність даних або некоректні параметри (EnergyError, ValueError, OSError).

2: Синтаксичні помилки парсера CLI або некоректний виклик (argparse).

Модульні тести CLI (tests/test_cli.py):

Збережено повну сумісність із попередніми 12 тестами.

Додано 4 нові обов'язкові тести під вимоги W4:

test_w4_cli_help_flag_displays_usage — перевірка прапорця --help (вивід довідки, exit code 0).

test_w4_missing_input_csv_file_exits_with_error — обробка відсутнього CSV (graceful error, exit code 1).

test_w4_custom_log_level_invocation — перевірка конфігурації рівня логування через --log-level.

test_w4_report_e2e_real_calculation_output — наскрізний запуск Demo 1 із виведенням аналітичних метрик.

Який код ми оновлювали і для чого

Під час інтеграції з реальними даними та прогону суміжних тестів W4 (tests/test_analytics_w4.py) ми оновили логіку в src/energyhome/cli.py:

_parse_datetime:

Що змінили: Додали перевірку наявності роздільника часу 'T' та обов'язкову нормалізацію до timezone-aware об'єктів у UTC (replace(tzinfo=timezone.utc) / astimezone(timezone.utc)).

Для чого:

Усунули фатальну помилку TypeError: can't compare offset-naive and offset-aware datetimes при фільтрації часових міток із CSV.

Забезпечили відхилення неповних дат формату YYYY-MM-DD без компонента часу, повертаючи код 1 замість хибного завершення.

_render_report:

Що змінили:

Додали валідацію парності аргументів періоду: якщо передано --from, обов'язково має бути й --to (і навпаки).

Реалізували розгалуження: якщо rate передано як словник конфігурацій тарифів, CLI задіює потоковий iter_meter_csv(..., allow_gaps=True) і викликає build_annual_report.

Додали перевірку цілісності метаданих якості .quality.json із перехопленням JSONDecodeError і генерацією EnergyError.

Додали контроль порожніх результатів: якщо у вибірці немає вимірювань ("Інтервалів: 0"), викидається EnergyError (код 1).

Для чого:

Виправлено падіння DataImporterError: Missing intervals на датасетах із пропусками часу.

Забезпечено виведення розширених показників (зокрема 'Пік: 2 kW') та порівняння зонних тарифів (single, two_zone, three_zone).

Захищено CLI від зависання на пошкоджених конфігах якості даних.

command_report:

Що змінили: Додали підтримку як стандартного config/tariffs.json (відповідно до tariffs.schema.json), так і спрощеного config/tariffs.demo.json. Якщо в конфігу є розділ tariffs або зонні плани, у розрахунок передається повний словник конфігурації.

Для чого: Забезпечили коректну роботу CLI з багатозонними тарифами (two_zone, three_zone) без помилок приведення типу SingleZoneTariff.

Інструкція для перевірки (Reviewer: Аня)

А. Автоматичні тести

Запуск модульних тестів CLI (16 тестів):
$env:PYTHONPATH="./src"; python -m unittest tests/test_cli.py -v

# 2. Загальний прогін усіх тестів репозиторію:
$env:PYTHONPATH="./src"; python -m unittest discover -s tests -v


Б. Консольна демонстрація (Demo 1)

# 1. Виклик довідки системи:
python -m energyhome --help

# 2. Розрахунок звіту на демо-даних:
python -m energyhome report --input data/demo/meter_15min.csv --tariff single

# 3. Перевірка обробки відсутності файлу (exit code 1, без трейсбеку):
python -m energyhome report --input missing_data.csv

Контекст для наступника (W5)

Точка входу src/energyhome/cli.py стабілізована та підтримує запуск як базового Demo 1 звіту, так і річного streaming-звіту з аналітикою пропусків.

Наступному розробнику на W5 залишається за потреби розширити набір підкоманд (наприклад, додати команди оптимізації розкладу schedule або експорту графіків plot).