# Аналітична частина Demo 1 — W4-D

База реалізації: `main`, `3de77d5`. Python 3.11+. Тарифи в
`config/tariffs.json` навчальні, а не офіційні. Дані UCI описані в
[`data/README.md`](../../data/README.md).

## Малий контрольний приклад

Чотири потужності `[1, 2, 0, 1]` kW, 15 хвилин, початок
`2026-01-15T00:00:00Z`. Вручну: 1 kWh; single — 2.50 UAH;
two_zone — 1.25 UAH, бо всі інтервали потрапляють у нічну зону Києва.
Пік — 2 kW, середня потужність — 1 kW, нерівномірність — 2.
Середня ціна single — 2.50 UAH/kWh. Спостереження покривають 4.17% доби.

- [Очікуваний консольний текст](expected_small.txt).
- [Line-графік потужності та ставки two_zone](daily_profile.png).
- [Stacked bar](device_stack.png): явно синтетичні частки 30%/70% із W3-D,
  які разом дорівнюють виміряному навантаженню. Це не розпізнані прилади.

Відтворення з кореня репозиторію:

```sh
python -m pip install -e .
python - <<'PY'
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt

from energyhome.analytics import build_annual_report, plot_daily_profile, plot_device_stack
from energyhome.domain import MeterReading, tariff_from_config

registry = json.loads(Path('config/tariffs.json').read_text())['tariffs']
single = tariff_from_config('single', registry)
two = tariff_from_config('two_zone', registry)
start = datetime(2026, 1, 15, tzinfo=UTC)
timestamps = [start + timedelta(minutes=15 * i) for i in range(4)]
powers = [1, 2, 0, 1]
readings = (MeterReading(t, p) for t, p in zip(timestamps, powers))
text = build_annual_report(readings, single, two)
print(text)
Path('docs/demo1/expected_small.txt').write_text(text + '\n', encoding='utf-8')
line = plot_daily_profile(timestamps, powers, two)
stack = plot_device_stack(timestamps, total_power_kw=powers)
for figure, name in ((line, 'daily_profile'), (stack, 'device_stack')):
    figure.axes[0].set_xlabel('Час (UTC), 15 січня 2026')
    figure.savefig(f'docs/demo1/{name}.png', bbox_inches='tight')
    plt.close(figure)
PY
```

## Річний потік

Спочатку отримайте `data/generated/uci_2008.csv` за інструкціями W4-B.
Це нормалізовані UTC-мітки прийнятих вимірювань із кроком 1 хвилина.
Звіт про якість джерела зберігається поряд із CSV; він не замінюється
нульовими значеннями чи домисленими інтервалами в аналітиці.

```sh
python - <<'PY'
import json
from pathlib import Path

from energyhome.analytics import build_annual_report
from energyhome.dataio import SourceConfig, iter_meter_csv
from energyhome.domain import tariff_from_config

registry = json.loads(Path('config/tariffs.json').read_text())['tariffs']
single = tariff_from_config('single', registry)
two = tariff_from_config('two_zone', registry)
readings = iter_meter_csv('data/generated/uci_2008.csv', SourceConfig(step_minutes=1),
                          allow_gaps=True)
print(build_annual_report(readings, single, two, step_minutes=1))
PY
```

Функція споживає ітератор один раз. `groupby` відділяє локальні доби тарифу
single. У пам'яті є одна доба, точні накопичені `CostBreakdown`, компактні
добові/тижневі підсумки. `allow_gaps=True` залишає строгі перевірки значень
і порядку, дозволяючи прогалини без накопичення їхнього журналу. Перший
неправильний запис одразу завершує обробку. Сирий рік не перетворюється на list.
Помилки джерела передаються викликачеві. Від'ємні, нескінченні значення,
дублі та порушений порядок вимірювань дають помилку; пропуски не заповнюються.

Перевірений реальний прогін: [повний текст](annual_2008.txt) і
[числові підтвердження](verification_2008.json). Прийнято 526 845 інтервалів,
енергія — 9413.5128 kWh; single — 23 533.78 UAH, two_zone — 21 128.57 UAH.
Точний результат додатково звірено з незалежним поблоковим викликом core
для блоків по 1000 точок. Суми показаних днів і тижнів збігаються з total.

Межі UCI-року взято в часовому поясі джерела `Europe/Paris`, тоді як доби
звіту — `Europe/Kyiv`. Тому є 367 локальних дат: перша й остання часткові.
Остання — 1 січня 2009, а фактичні UTC-межі залишаються незмінними.
195 rejects і 16 gap-подій джерела описані в журналі W4-B. Ці дані не
вважаються повністю виміряним роком. Остаточна команда CLI завершилася з кодом 0 за 30.147 с. Старий час API
47.612 с і peak RSS у JSON позначені як вимірювання до виправлень рев'ю.
Це спостереження цього середовища, не гарантований benchmark.

Звіт явно показує кількість пропущених інтервалів між спостереженими
межами. Повністю пропущені доби не заповнюються вигаданими нульовими
вимірюваннями.

Звіт має спостережені межі `[first_timestamp, last_timestamp + step)`,
кількість наявних інтервалів і покриття кожної локальної доби. Назва
`build_annual_report` не означає, що неповний вхід оголошується повним роком.
Нульова потужність є даними: період зберігається, обидва відношення позначені
«не визначено». Порожній період завершується `ValueError`.

Енергія й ціна в заголовку, добовій і тижневій таблицях стосуються першого
переданого тарифу — типово single; його ID явно вказаний у звіті.
Порівняння single/two_zone розраховане за тими самими інтервалами. Peak,
середня потужність доби та зважена ціна надходять із `core`. Середня потужність
періоду зважується кількістю інтервалів; нерівномірність — пік / середня.

Точний reducer C працює до остаточного округлення. Щоб показані доби й тижні
також сумувалися до заголовка, доба отримує різницю двох округлених
накопичених підсумків. Перенесення округлення може змінити показану суму дня
на 0.01 UAH, а енергію — на 0.0001 kWh порівняно з окремим округленням доби.
Це розподіл округлення для звіту; алгоритм тарифікації не змінено.
Консольний текст містить явну примітку про перенесення округлення.

## Контракт для W4-E та межі CLI

`build_annual_report(readings, single, two, step_minutes=15,
date_from=None, date_to=None) -> str` повертає повний консольний текст.
Функція не друкує й не читає CSV. E передає потік B, справжні тарифи з
реєстру та правильний крок джерела: 1 для UCI, 15 для synthetic.

`--from` передається як `date_from`, `--to` — як `date_to`, після
`cli._parse_datetime`. Потрібен явний UTC offset або `Z`. Межі відбору
застосовуються до початків інтервалів: `from <= timestamp < to`.
Рівні/переставлені й naive-межі відхиляються. Після відбору таблиці,
ціни та метрики використовують тільки ці точки. Порівняння меж враховує
повторну годину восени.

Для обох графіків оберіть одну добу всередині цього самого відфільтрованого
потоку. У прикладі вище це весь малий період; для року не передавайте
в plot увесь список сирих точок. Підпишіть часовий пояс осі. Перевірка
`test_parsed_bounds_select_the_same_values_for_report_and_both_plots` звіряє
точні x/y графіків, ставки та суму stacked bar із вибраними інтервалами.

Після рев'ю додано мінімальний інтеграційний адаптер CLI:

```sh
python -m energyhome report --input data/generated/uci_2008.csv
python -m energyhome report --input data/generated/uci_2008.csv \
    --from 2008-01-01T00:00:00Z --to 2008-01-02T00:00:00Z
```

Команда читає сучасний реєстр `config/tariffs.json`, створює справжні класи
тарифів, передає потік і межі у звіт. `.quality.json` поруч із CSV задає крок
(для UCI — 1 хв); без нього крок дорівнює 15 хв. Пропуски дозволені, але
неправильні значення/порядок зупиняють обробку. `--tariff` обирає базовий тариф:
для single порівнюється two_zone, для two_zone/three_zone — single.
Збережено сумісність із попереднім demo-реєстром та старим числовим викликом
`_render_report`; сучасний реєстр завжди використовує новий потоковий шлях.
`TestAnnualCli` запускає справжній процес CLI для нормального звіту, відбору,
пропусків, UCI-кроку, зіпсованих даних/метаданих і порожнього періоду.
Наскрізний річний запуск також записаний у JSON перевірки. Остаточне командне
рев'ю інтеграційних змін і репетиція з E ще потрібні.

## Показ аналітики на Demo 1

1. Відкрити малий текст: пояснити 1 kWh та ручне single/two_zone порівняння.
2. Відкрити обидва PNG, пояснити kW, ставку й позначку Synthetic.
3. Показати річні підсумки, фактичні межі, доби з неповним покриттям і тижні.
4. Показати однаковий короткий відбір у числах і графіках.
5. Запустити порожній відбір: пояснити помилку замість успішного нульового року.

Свою усну відповідь кожен учасник готує самостійно.

```sh
python -m unittest discover -s tests -p test_analytics_w4.py -v
python -m unittest discover -s tests -v
```
