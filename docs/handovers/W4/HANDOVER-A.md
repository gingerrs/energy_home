# W4-A — Schedule і контракти оптимізації

Власниця задачі: Софія. Reviewer за ротацією: Діана.

ScheduledRun перевіряє id, aware start і додатну цілу тривалість у хвилинах.
Schedule — незмінний контейнер із сортуванням за UTC start і device_id.
Непорожній розклад потребує device_ids; невідомий прилад відхиляється.
Для House реєстр отримується з його devices. Повна перевірка windows/power
належить W6; її майбутня сигнатура й DTO SavingsResult/OptimizationRequest/
OptimizationResult описані у ../../spec.md. Заглушок методів немає.

Перевірка: python -m unittest discover -s tests -v.
Тести: test_domain.py і test_project_fixes.py — відомі/невідомі id, порядок,
незмінність, naive start і нульова/від'ємна/неціла тривалість.
Незалежне review та усна передача не засвідчуються самим цим документом.
