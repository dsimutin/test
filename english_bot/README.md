# English Flashcards Bot

Telegram-бот для изучения английских слов и неправильных глаголов.
Источник данных — Google Sheets. Карточки рендерятся как PNG 1080×1350
через HTML + Playwright. Прогресс ученика хранится в SQLite.

## Стек

- Python 3.11+
- aiogram 3.x (async)
- aiosqlite — прогресс
- gspread — Google Sheets
- Jinja2 + Playwright (headless Chromium) — рендер PNG-карточек

## Архитектура

```
bot/
  config.py              — env vars → typed config
  main.py                — entry point (run_polling)
  handlers/              — только Telegram-логика (роутеры + FSM)
      start.py, sync.py, stats.py
      vocabulary.py, verbs.py, quiz.py
      _session.py        — общая логика показа карточек / прохождения теста
      states.py          — FSM states
  keyboards/             — фабрики клавиатур (reply + inline)
  services/              — бизнес-логика
      sheets.py          — sync from Google Sheets
      selection.py       — выбор N карточек по приоритету
      card_renderer.py   — HTML+CSS → PNG (Playwright)
      quiz.py            — построение вопросов
      progress.py        — обновление статуса в БД
  storage/
      db.py              — SQLite (init + CRUD)
      models.py          — dataclass-модели
  templates/             — Jinja2 HTML для карточек
  static/                — card.css
data/
  english_bot.db
  cache/                 — PNG-кеш карточек
```

## Запуск на macOS

```bash
# 1) клонировать
git clone <repo> && cd english_bot

# 2) виртуальное окружение
python3.11 -m venv .venv
source .venv/bin/activate

# 3) зависимости
pip install -r requirements.txt
playwright install chromium

# 4) Google Sheets
#    — создать service account в Google Cloud
#    — включить Sheets API
#    — скачать credentials.json в корень проекта
#    — поделиться таблицей с email service account
#    — взять spreadsheet ID из URL

# 5) env
cp .env.example .env
# заполнить BOT_TOKEN, SPREADSHEET_ID, GOOGLE_CREDENTIALS_FILE

# 6) запуск
python -m bot.main
```

## Структура Google Sheets

Файл должен содержать **два листа** с этими именами:

### Лист `vocabulary`

| id | lesson | word     | transcription | translation | example                         | highlight | accent  | level | active |
|----|--------|----------|---------------|-------------|---------------------------------|-----------|---------|-------|--------|
| 1  | 2      | young    | /jʌŋ/         | молодой     | She looks very young.           | young     | #2F7D5B | A1    | TRUE   |
| 2  | 2      | improve  | /ɪmˈpruːv/    | улучшать    | I want to improve my speaking.  | improve   | #2F6FD6 | A2    | TRUE   |

### Лист `irregular_verbs`

| id | lesson | infinitive | past_simple | past_participle | translation | example                  | highlight | accent  | active |
|----|--------|------------|-------------|-----------------|-------------|--------------------------|-----------|---------|--------|
| 1  | 2      | drink      | drank       | drunk           | пить        | Yesterday I drank coffee.| drank     | #D77A22 | TRUE   |
| 2  | 2      | go         | went        | gone            | идти        | Yesterday I went home.   | went      | #2F7D5B | TRUE   |

Колонки:
- `id` — уникальный (используется как PK). Не меняй между синками.
- `active` — TRUE/FALSE: если FALSE, слово не показывается ученику.
- `accent` — hex-цвет, акцент карточки. Если пусто — дефолт.
- `highlight` — слово, которое выделяется в `example` (для verbs обычно
  совпадает с `past_simple`).
- `lesson` — используется только для сортировки.

## SQL-схема

См. `bot/storage/db.py` константу `SCHEMA`. Таблицы:

- `users` — ученики
- `vocabulary`, `irregular_verbs` — кеш из Google Sheets
- `vocabulary_progress`, `verb_progress` — прогресс
- `sessions` — каждая тренировка

`status` ∈ `new | learning | known | repeat`

## Учительская панель

Прогресс **полностью изолирован между учениками** (PK `(telegram_id, word_id)`).
Можешь тестировать со второго Telegram-аккаунта — твой собственный
прогресс не затронется.

### Меню преподавателя (расширенное)

```
[ 📚 Учить слова  ] [ ⚡ Учить глаголы ]
[ 📊 Моя статистика ] [ 👥 Ученики    ]
[ 🔄 Синхронизация ] [ 📋 Экспорт CSV ]
```

- **👥 Ученики** — интерактивный список с инлайн-навигацией.
  Тап по ученику → детальный отчёт (последние 5 сессий, слова и
  глаголы с ошибками, разбивка ошибок Past Simple / Past Participle).
  Пагинация по 10 учеников на страницу.
- **🔄 Синхронизация** — ручной sync с Google Sheets (если что-то
  добавил и хочешь увидеть прямо сейчас).
- **📋 Экспорт CSV** — присылает файл со всем прогрессом всех
  учеников. Открывается в Excel / Google Sheets (разделитель `;`,
  UTF-8 BOM).

### Команды (slash)

- `/myid` — узнать свой Telegram ID и username (работает у всех)
- `/students` — то же что кнопка 👥
- `/student <id>` — детально по ученику
- `/export` — то же что кнопка 📋

### Настройка

1. Напиши боту `/myid` — получишь свой ID и username.
2. В Railway → Variables добавь любое:
   - `TEACHER_IDS=11111,22222` — по числовым ID, или
   - `TEACHER_USERNAMES=mrgrief,annateacher` — по username (без `@`)
3. Redeploy. Теперь у тебя расширенное меню, у учеников — обычное.

## Автоматический sync

База обновляется из Google Sheets:
- **при старте** бота (на каждый деплой)
- **каждые 30 минут** в фоне

Ученикам ничего нажимать не надо — добавил слова в таблицу → в течение
30 минут они появятся у всех учеников.

## Persistence

SQLite-файл лежит в `/app/data/english_bot.db`. Чтобы прогресс пережил
redeploy на Railway — примонтируй Volume на `/app/data` (Settings →
Volumes → Mount path `/app/data`).

## Логика подачи карточек

1. Учим слова: 10 карточек → mini-test → результат
2. Учим глаголы: 5 карточек → mini-test → результат
3. Mini-test — 3 типа вопросов, по 4 варианта ответа
4. Ошибки переводят слово в статус `repeat` — попадёт в следующую тренировку

Приоритет подбора:
1. `repeat`
2. `learning`
3. `new` (без прогресса)
4. Самые старые по `last_seen_at`

## Тестирование рендера карточки локально

```python
import asyncio
from bot.config import load_config
from bot.services import card_renderer
from bot.storage.models import VocabularyItem

async def main():
    cfg = load_config()
    await card_renderer.startup()
    item = VocabularyItem(
        id="demo", lesson=1, word="young",
        transcription="/jʌŋ/", translation="молодой",
        example="She looks very young.", highlight="young",
        accent="#2F7D5B", level="A1", active=True,
    )
    p = await card_renderer.render_vocabulary_card(item, "01", cfg)
    print("PNG saved:", p)
    await card_renderer.shutdown()

asyncio.run(main())
```

## Деплой на Railway (через Docker)

В проекте есть `Dockerfile` на базе официального образа Playwright —
Chromium и все системные зависимости уже внутри, никаких проблем с
`libasound2` на Ubuntu 24.04.

1. В Railway создай **новый сервис** (или поменяй существующий) и подключи
   этот репо.
2. **Root Directory**: `english_bot` (Settings → Source).
3. Railway автоматически найдёт `Dockerfile` и `railway.toml`.
4. В **Variables** добавь:
   - `BOT_TOKEN`
   - `SPREADSHEET_ID`
   - `GOOGLE_CREDENTIALS_JSON` — вставь весь JSON service-account целиком
5. (Опционально) добавь **Volume** на `/app/data` чтобы SQLite и кеш
   карточек выживали между деплоями.
6. **Replicas = 1** (иначе два бота будут драться за `getUpdates`).
7. Если у тебя уже крутится старый бот в этом же репо — поставь его на
   паузу, иначе два бота с одним токеном конфликтуют.

## Локальный Docker

```bash
docker build -t english-bot ./english_bot
docker run --rm \
  -e BOT_TOKEN=xxx -e SPREADSHEET_ID=xxx \
  -e GOOGLE_CREDENTIALS_JSON="$(cat credentials.json)" \
  -v $(pwd)/data:/app/data \
  english-bot
```
