# Demo 1

Python 3.11+. Усі команди виконуються з кореня встановленого проєкту.
Тарифи config/tariffs.json навчальні, а не офіційні.

## Малий приклад

```sh
python -m pip install -e .
python -m energyhome report --input data/demo/meter_15min.csv
python scripts/plot_demo.py
```

Спільний CSV містить чотири точки `[1,2,0,1]` kW з кроком 15 хвилин,
починаючи з 2026-01-15T00:00:00Z. Енергія — 1 kWh, single — 2.50 UAH,
two_zone — 1.25 UAH, peak — 2 kW, mean — 1 kW, peak/mean — 2.
Фактичний текст звіряється з [expected_small.txt](expected_small.txt).

[Line-графік](daily_profile.png) використовує ці самі точки й two_zone.rate_at.
Часова вісь у timezone тарифу, ставка змінюється на початку інтервалу.
[Stacked bar](device_stack.png) — явно синтетичні профілі трьох CyclicDevice:
1 kW на 0–30 хв, 15–30 хв і 45–60 хв. Разом вони дають `[1,2,0,1]`.
Це моделювання приладів, а не розпізнавання приладів у реальному UCI.

Якщо передано лише виміряний total, plot_device_stack показує його одним
рядом без вигаданих часток. Без профілів і total функція відхиляє непорожній
період. Графічні функції приймають step_minutes, типово 15.

## Річний потік

Отримання й очищення джерела: [інструкція даних](../../data/README.md).
Нормалізований UCI CSV має крок 1 хвилина у сусідньому quality.json.

```sh
python -m energyhome report --input data/generated/uci_2008.csv
python -m energyhome report --input data/generated/uci_2008.csv \
    --from 2008-01-01T00:00:00Z --to 2008-01-02T00:00:00Z
python -m energyhome report --input data/generated/uci_2008.csv \
    --tariff two_zone --tariffs config/tariffs.json
```

Перевірені дані: 526845 інтервалів, 9413.5128 kWh, single 23533.78 UAH,
two_zone 21128.57 UAH, three_zone 19752.66 UAH. [Контрольний текст](annual_2008.txt),
[числовий звіт попереднього приймання](verification_2008.json),
[якість джерела](../../data/quality/w4_2008.json).
195 rejects і 16 gap-подій не підміняються нульовою енергією.
Спостережені UTC-межі: [2007-12-31T23:00Z, 2008-12-31T23:00Z).
Рік джерела задано у Europe/Paris, доби звіту — у Europe/Kyiv;
367 локальних дат включають дві часткові крайні доби.

## Семантика

build_annual_report використовує той самий добовий accumulator, що й
iter_daily_summaries та daily_summaries. У пам'яті є одна сира доба й компактні
summary. Покриття враховує DST. Дублікати й неправильні записи відхиляються;
прогалини зберігаються. Погані записи поза вибраним періодом теж не приховуються.

Доба отримує різницю накопичених округлених сум. Тому показані дні й тижні
сумуються до фінального core total. Може переноситися 0.01 UAH або
0.0001 kWh між сусідніми днями; це підписано в тексті звіту.

Відбір застосовується до початків інтервалів: from <= timestamp < to.
Потрібен явний Z/offset. Нульові вимірювання зберігають період і count;
нерівномірність та середня ціна за нульової енергії не визначені.
Порожній відбір дає помилку. Для графіка довільного зрізу передавайте ті самі
відфільтровані часові мітки й потужності; перевірки звіряють числа, фактичні
часові позиції та точку зміни тарифу.

## Перевірки

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_cli.py -v
python -m unittest discover -s tests -p test_project_fixes.py -v
```

test_cli запускає реальний процес і звіряє stdout зі збереженим малим звітом.
Тести охоплюють також порожні/нульові дані, поганий config, явний timezone,
півкопійки через північ, 23/25-годинні доби, кеш і графіки.
Інтерфейси: [spec.md](../spec.md).

## Повторне приймання всіх тарифів

```sh
python scripts/verify_demo1.py --input data/generated/uci_2008.csv \
    --out docs/demo1/verification_fix_all.json
```

Скрипт звіряє SHA256 очищеного джерела, незалежні decimal-суми енергії та
вартості, CLI для всіх тарифів, суми добових/тижневих таблиць і пропуски.
[Новий запис перевірки](verification_fix_all.json) доповнює попередній контроль;
оригінальний single-звіт залишився незмінним.
