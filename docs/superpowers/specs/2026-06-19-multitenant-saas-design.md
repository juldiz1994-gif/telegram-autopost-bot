# Multi-Tenant SaaS Design — Telegram Autopost Bot

**Date:** 2026-06-19  
**Status:** Approved

---

## Обзор

Қазіргі single-tenant ботты (бір канал, бір ниша, бір admin) толыққанды SaaS платформасына айналдыру. Әрбір клиент өз нишасы бойынша қазақ тілінде посттар алады, өз каналына/тобына жариялайды, жеке ботта модерациялайды.

**Бизнес модель:**
- 5 күн тегін сынақ мерзімі
- 990 тг/ай (Kaspi аударым + қолмен растау)
- Күніне 1 немесе 2 рет пост (клиент таңдайды)
- Фото міндетті (нишаға сай Gemini генерациясы)

---

## 1. Дерекқор өзгерістері

### Жаңа кестелер

```sql
CREATE TABLE users (
    id BIGINT PRIMARY KEY,              -- Telegram user_id
    username TEXT,                       -- @username (болмауы мүмкін)
    full_name TEXT,
    niche TEXT NOT NULL,                 -- "Психология"
    channel_id BIGINT NOT NULL,         -- -1001234567890
    channel_title TEXT,                  -- "Менің каналым"
    post_frequency INTEGER DEFAULT 2,   -- 1 немесе 2
    publish_times TEXT DEFAULT '10:00,18:00',  -- CSV форматы
    status TEXT DEFAULT 'trial',        -- trial | active | expired | blocked
    trial_ends_at TIMESTAMP NOT NULL,
    subscription_ends_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    amount INTEGER DEFAULT 990,
    status TEXT DEFAULT 'pending',      -- pending | confirmed | rejected
    check_file_id TEXT,                 -- Telegram file_id чек суреті
    created_at TIMESTAMP DEFAULT NOW(),
    confirmed_at TIMESTAMP,
    confirmed_by BIGINT                 -- super-admin Telegram ID
);
```

### Өзгертілетін кестелер

```sql
-- content_plan кестесіне user_id қосу
ALTER TABLE content_plan ADD COLUMN user_id BIGINT REFERENCES users(id);

-- posts кестесіне user_id қосу
ALTER TABLE posts ADD COLUMN user_id BIGINT REFERENCES users(id);
```

---

## 2. Тіркелу ағыны (Onboarding FSM)

```
Күйлер:
OnboardingState.waiting_niche
OnboardingState.waiting_channel
OnboardingState.waiting_frequency
OnboardingState.waiting_channel_confirm
```

### Толық ағын

```
Клиент /start жазады
  ↓
Бот: "Каналыңның немесе тобыңның тақырыбы не?
      Мысалы: Психология, Фитнес, Бизнес, Тамақ..."
  ↓
Клиент: "Психология"
  ↓
Бот: "Енді @{bot_username}-ді каналыңа немесе тобыңа
      АДМИН ретінде қос. Қосқаннан кейін мен өзім
      автоматты табамын!"
      [Нұсқаулық суреті немесе GIF]
  ↓
(Клиент ботты каналға админ ретінде қосады)
  ↓
Бот my_chat_member оқиғасын қабылдайды → chat_id алады
  ↓
Бот клиентке жібереді:
  "✅ Таптым! «Психология блогы» каналы.
   Осы ма?"
   [✅ Иә, осы] [❌ Жоқ, басқасы]
  ↓
[Иә] → Бот: "Күніне неше рет пост жарияланатын?"
              [1 рет] [2 рет]
  ↓
Клиент таңдайды
  ↓
Бот: "🎉 Тіркеу аяқталды!

  📋 Ниша: Психология
  📢 Канал: «Психология блогы»
  📅 Постар: күніне 2 рет (10:00 және 18:00)
  ⏳ Тегін мерзім: 5 күн ({end_date}-ге дейін)

  Контент-жоспар жасалуда... ⏳"
  ↓
Автоматты: апталық жоспар + посттар генерацияланады
```

**Ескерту:** Клиент "Жоқ, басқасы" десе → бот күтеді, клиент басқа каналға ботты қосады → бот жаңа каналды ұстап алады.

---

## 3. Per-User Контент Генерациясы

### Жоспар жасау
- Тіркелу аяқталғанда автоматты `generate_weekly_plan(niche, user_id)` шақырылады
- `content_plan` жазбасына `user_id` сақталады
- Жоспар сол пайдаланушының нишасына арналған

### Пост генерациясы
- `generate_post(topic, format_type, niche)` — нише параметрі пайдаланушының нишасынан алынады
- Image prompt нишаға сай: "Психология" → тыныштық, ми, адам эмоциясы иллюстрациялары
- Фото міндетті — сурет жоқ болса пост модерацияға бармайды, retry жасалады

### Кесте (APScheduler)
- Тіркелу кезінде әрбір пайдаланушыға жеке job қосылады:
  ```python
  scheduler.add_job(
      check_and_publish_for_user,
      CronTrigger(hour=10, minute=0, timezone="Asia/Almaty"),
      id=f"publish_{user_id}_1000",
      args=[user_id]
  )
  ```
- Статус `active` немесе `trial` болса ғана жұмыс істейді
- `expired` немесе `blocked` болса job тоқтатылады

---

## 4. Per-User Модерация

Пост жасалғанда → клиенттің жеке чатына жіберіледі (super-admin-ге емес):

```
[Пост суреті]

[Пост мәтіні — 500-1500 таңба]

📋 Формат: tips
📌 Тақырып: Стресс менеджменті
📅 Жоспарланған: Дүйсенбі 10:00
🆔 Пост ID: 42

✅ Бекіту   🔄 Қайта жаз   ✏️ Өңдеу   ❌ Қабылдамау
```

- **✅ Бекіту** → `status = approved`, кестеде жариялауды күтеді
- **🔄 Қайта жаз** → Gemini жаңа пост жасайды, клиентке қайта жібереді
- **✏️ Өңдеу** → клиент нені өзгерту керектігін жазады → AI өңдейді
- **❌ Қабылдамау** → `status = rejected`, жарияланбайды

---

## 5. Төлем ағыны

### Сынақ мерзімі аяқталғанда

```
(trial_ends_at жеткенде scheduler іске қосады)
  ↓
Клиентке хабарлама:
  "⏰ Тегін мерзімің аяқталды!

  Жалғастыру үшін 990 тг аудар:
  📱 Kaspi: +7XXXXXXXXXX (Атыңыз)

  Аударымнан кейін осы ботқа чек суретін жібер.
  Растаудан кейін 30 күнге автоматты белсендіріледі."
  ↓
Клиент чек суретін жібереді
  ↓
Super-adminге хабарлама:
  "💳 Жаңа төлем!
   👤 [Аты] (@username) — Психология
   📅 Тіркелген: 2026-06-14

   [Чек суреті]

   ✅ Растау   ❌ Қабылдамау"
  ↓
Super-admin [✅ Растау] басады
  ↓
Клиентке: "✅ Төлем расталды! 30 күн белсендірілді."
Бот жалғасады
```

### Ескерту хабарламалары
- Мерзім біткенге **3 күн** қалғанда клиентке ескерту
- Мерзім өткеннен кейін **3 күн** ішінде төленбесе → `status = expired`, бот тоқтайды

---

## 6. Super-Admin Панелі (Telegram командалары)

Тек `TELEGRAM_ADMIN_ID`-ге ие пайдаланушыға қолжетімді.

| Команда | Сипаттама |
|---------|-----------|
| `/admin` | Басты мәзір — батырмалармен |
| `/users` | Барлық клиенттер тізімі (статус, ниша, мерзім) |
| `/user <id>` | Бір клиент толық ақпараты |
| `/confirm <user_id>` | Төлемді растау → 30 күн қосу |
| `/reject <user_id>` | Төлемді қабылдамау + клиентке хабарлама |
| `/block <user_id>` | Бұғаттау (бот тоқтайды) |
| `/unblock <user_id>` | Бұғаттауды алу |
| `/stats` | Жалпы статистика |
| `/extend <user_id> <days>` | Қолмен күн қосу |

### `/admin` мәзірі

```
👑 Super-Admin Панелі

👥 Жалпы клиенттер: 12
✅ Активті: 8
⏳ Сынақта: 3
❌ Мерзімі өткен: 1

[👥 Клиенттер] [📊 Статистика]
[💳 Төлемдер]  [⚙️ Параметрлер]
```

### `/stats` шығысы

```
📊 Статистика

👥 Клиенттер: 12 барлығы
  ✅ Активті: 8
  ⏳ Сынақта: 3
  🚫 Мерзімі өткен: 1
  ⛔ Бұғатталған: 0

📝 Посттар (барлық клиенттер):
  📢 Жарияланған: 156
  ✅ Бекітілген: 12
  ⏳ Қарауда: 8
  ❌ Қабылданбаған: 23

💰 Табыс: ~7 920 тг/ай
```

---

## 7. Файлдық құрылым (өзгерістер)

```
.
├── main.py                  — өзгерілмейді
├── config.py                — KASPI_PHONE env var қосылады
├── database.py              — users, payments кестелері + user_id барлық сұраныстарда
├── prompts.py               — өзгерілмейді (нише динамикалы беріледі)
├── content_planner.py       — user_id параметрі қосылады
├── post_generator.py        — user_id параметрі қосылады
├── image_generator.py       — өзгерілмейді
├── publisher.py             — channel_id динамикалы (users кестесінен)
├── scheduler.py             — per-user jobs басқару
├── moderator_bot.py         — толық қайта жазылады (multi-tenant)
│
├── handlers/                — ЖАҢА папка
│   ├── onboarding.py        — тіркелу FSM (/start, нише, канал, жиілік)
│   ├── moderation.py        — клиент модерациясы (approve/reject/edit/redo)
│   ├── admin.py             — super-admin командалары
│   └── payments.py          — чек қабылдау, растау callbacks
│
└── services/
    ├── subscription.py      — trial/expired тексеру, ескерту жіберу
    └── user_scheduler.py    — per-user APScheduler job басқару
```

---

## 8. Env Vars өзгерістері

```env
# Бар айнымалылар өзгерілмейді
GEMINI_API_KEY=...
DATABASE_URL=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL_ID=...      # Артық болады (users кестесінен алынады)
TELEGRAM_ADMIN_ID=...        # Super-admin ID — қалады
CONTENT_NICHE=...            # Артық болады (users кестесінен алынады)
PUBLISH_TIMES=...            # Default мән ретінде қалады

# ЖАҢА
KASPI_PHONE=+77001234567     # Төлем үшін телефон нөмірі
TRIAL_DAYS=5                 # Сынақ мерзімі күн санымен
```

---

## 9. Кілтті шектеулер мен ескертулер

1. **Бот каналда admin болуы міндетті** — `can_post_messages` рұқсаты керек
2. **`my_chat_member` handler** — бот каналға қосылған сәтте пайдаланушының onboarding күйін тексереді, тек `waiting_channel` күйінде болса ғана өңдейді
3. **Бір пайдаланушы — бір канал** — бірнеше канал қосу бірінші версияда жоқ
4. **Gemini квотасы** — көп пайдаланушы болғанда image generation квотасы жетпеуі мүмкін; image retry логикасы қазірдің өзінде бар
5. **APScheduler** — бот рестарт болғанда барлық user job-тарды қайта қосу керек (`on_startup` кезінде)

---

## 10. Іске асыру кезеңдері

1. **Дерекқор** — жаңа кестелер + migration
2. **Onboarding FSM** — тіркелу ағыны
3. **my_chat_member handler** — канал автоматты тану
4. **Per-user контент генерациясы** — content_planner, post_generator, scheduler
5. **Per-user модерация** — клиент өз постарын бекітеді
6. **Төлем ағыны** — чек жіберу, super-admin растауы
7. **Super-admin панелі** — /admin командалары
8. **Subscription сервисі** — trial бітіру, ескерту, expired блоктау
