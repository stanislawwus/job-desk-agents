# Відео-демо (2–3 хвилини) і пояснення для пари 2

У завданні: у демо має бути видно **запуск системи**, **виклики інструментів у лозі**, **одну помилку і вихід із неї** і **кінцевий результат** (новий рядок у файлі).

## Підготовка (до запису)

```bash
uv sync
uv run pytest -q          # усі тести проходять
rm -rf runs               # чистий старт
```

## Сценарій запису

| Час | Що показати | Що сказати |
|---|---|---|
| 0:00–0:25 | Слайд [`architecture.png`](architecture.png) | Два агенти працюють із вакансіями Djinni. Scout шукає й пише листи, інструмента `apply` в нього немає. Reviewer перевіряє й єдиний має право писати. Інструменти дає наш MCP-сервер `jobs` (перероблений з `jobs_helper_MCP`): `search_jobs`, `get_job`, `apply`. |
| 0:25–0:40 | `cat data/candidates/iryna/cv.md` (прогорнути) | Кандидатка вигадана, мануальна QA з 1,5 року досвіду. Вакансії справжні, з Djinni. Агенти мають право використовувати лише факти з цього CV. |
| 0:40–2:10 | Запуск нижче; у консолі видно кожен виклик інструмента | Коментувати, коли з'являться червоні рядки (див. «Що має бути видно»). |
| 2:10–2:40 | Блок «applications.csv» наприкінці запуску, потім `cat runs/*/workspace/mcp_calls.jsonl` | Нові рядки з посиланнями на djinni.co з'явилися лише для двох вакансій, із роллю `reviewer`. Аудит сервера показує роль кожного виклику. |
| 2:40–3:00 | `evaluation/results/<stamp>/report.md` | Скільки запитів з 5 пройшло з двома агентами й з одним, і скільки коштував другий агент. |

Команда запуску (запит c5: на найкращу вакансію кандидатка вже відгукувалась):

```bash
uv run python -m jobs_helper.agents.run --seed djinni:851711-junior-manual-qa-engineer "Apply to the 2 best remote manual QA vacancies on Djinni for me."
```

Друга помилка, якщо є час (c2: вакансію закрили):

```bash
uv run python -m jobs_helper.agents.run --closed djinni:853773-junior-manual-qa-engineer-mobile-web "Apply to this vacancy for me: https://djinni.co/jobs/853773-junior-manual-qa-engineer-mobile-web/"
```

Живий пошук на djinni.co (потрібен інтернет; без відгуків):

```bash
uv run python -m jobs_helper.agents.run --snapshot live "Find 3 remote manual QA vacancies on Djinni that fit me. Do not apply."
```

## Що має бути видно (і де)

| Вимога із завдання | Де в запуску |
|---|---|
| Новий рядок у файлі наприкінці запуску | блок `applications.csv` у кінці: нові рядки `A-0002`, `A-0003` з роллю `reviewer` і посиланнями на djinni.co (рядок `A-0001` з роллю `seed` був до запуску) |
| У консолі видно, які інструменти викликались | рядки `→ search_jobs(...)`, `→ get_job(...)`, `→ apply(...)` з назвою агента `[scout]` / `[reviewer]` |
| Одна відмова й вихід із неї | `✗ apply → already_applied` на UAPP.AI: Reviewer не дублює відгук і переходить до запасної вакансії; у c2 `✗ apply → job_closed`: агент нічого не надсилає й пропонує схожі відкриті вакансії |
| Чому писати може лише один агент | тест `test_scout_cannot_write_and_nothing_is_written`: `forbidden`, і файл не створюється |

## Додатково

Показати сервер окремо від агентів, як його бачить будь-який MCP-клієнт:

```bash
AGENT_ROLE=single_agent npx @modelcontextprotocol/inspector uv run python -m jobs_helper.mcp_jobs.server
```

Показати трейс як сторінку:

```bash
uv run python scripts/trace_view.py runs/<run>/trace.jsonl   # пише trace.html поруч
```

## Питання, до яких варто бути готовим

- **Чому два агенти, а не один?** Див. [`ARCHITECTURE.md`](ARCHITECTURE.md), розділ «Чому розподіл саме такий», і висновки в [`../RESULTS.md`](../RESULTS.md).
- **Як ви гарантуєте, що Scout не напише?** Два шари: `apply` немає в його списку інструментів, і сервер перевіряє `AGENT_ROLE`.
- **Агент ходить через MCP чи імпортує функцію?** Через MCP: SDK запускає `python -m jobs_helper.mcp_jobs.server` як stdio-підпроцес. `service.py` агенти не імпортують.
- **Чому знімок, а не живий Djinni?** Щоб 5 запитів і порівняння можна було відтворити: живі вакансії щодня змінюються. Живий режим теж є (`--snapshot live`).
- **`apply` справді відправляє на Djinni?** Ні: він заповнює mock-форму Djinni і записує відгук у трекер. Цього достатньо для «інструмента, що змінює стан». Справжню відправку браузером робить оригінальний `jobs_helper_MCP`.
- **Чим це відрізняється від оригіналу?** Тільки Djinni, рівно 3 інструменти, агенти пишуть лише лист, без генерації CV і LinkedIn. Таблиця в README.
- **Як перевіряли, що результат правильний?** Грейдери читають `applications.csv` і аудит-лог сервера й порівнюють із ручною розміткою вакансій. Самі грейдери покриті тестами в `tests/test_graders.py`.
