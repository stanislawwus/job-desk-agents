# Job Desk Agents

Міні-проєкт з Generative AI: два агенти + свій MCP-сервер. Шукаємо вакансії на Djinni, переробляємо CV під вакансію і подаємо відгук.

- **Scout** шукає вакансії, пише лист рекрутеру і переробляє CV. Відгукуватись не може.
- **Reviewer** перевіряє лист і CV, щоб там не було вигаданих фактів, і тільки він викликає `apply`.

![Схема](docs/architecture.png)

## MCP-сервер `jobs`

- `search_jobs` — пошук вакансій на Djinni
- `get_job` — повний опис вакансії
- `apply` — подає відгук (лист + перероблене CV) і пише рядок у `workspace/applications.csv`

Якщо в переробленому CV з'являються технології чи цифри, яких немає в оригінальному CV, `apply` повертає помилку `unverified_facts`. Інші помилки: `already_applied`, `job_closed`, `forbidden` (Scout не може відгукуватись) тощо.

Вакансії справжні (знімок Djinni від 17.10.2026), кандидатка вигадана. На сам Djinni нічого не відправляється.

## Як запустити

Потрібні `uv` і залогінений Claude Code.

```bash
uv sync
uv run pytest -q
uv run python -m jobs_helper.agents.run "Apply to the 2 best remote manual QA vacancies on Djinni for me."
```

Те саме одним агентом: додати `--mode single`. Трейс зберігається в `runs/<run>/trace.jsonl`.

## Оцінка

```bash
uv run python -m evaluation.run_eval
```

5 запитів, кожен по 3 рази для двох агентів і для одного. Результат: **15/15 і 15/15**. Другий агент дорожчий приблизно в 2,3 раза, але він прибирав з CV факти, яких немає в оригіналі, а один агент один раз це пропустив. Детальніше в [RESULTS.md](RESULTS.md).

Архітектура — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), сценарій демо — [docs/DEMO.md](docs/DEMO.md).
