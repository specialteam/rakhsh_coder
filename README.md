# Rakhsh — دستیار کدنویسی

یک ایجنت هوش مصنوعی **شخصی و خودآموز** در سبک [Hermes Agent](https://hermes-agent.nousresearch.com/docs)، ساخته‌شده با **LangChain** و **LangGraph** و با **رابط وب**. روی کامپیوتر یا سرور خودتان اجرا می‌شود، دستور اجرا می‌کند، فایل می‌خواند و می‌نویسد، در وب جستجو می‌کند و از کارهایی که انجام داده «حافظه» و «مهارت» می‌سازد.

## قابلیت‌ها

| قابلیت | توضیح |
|---|---|
| مدل‌های متعدد | Anthropic Claude، OpenRouter، OpenAI و هر سرویس سازگار با OpenAI (با `base_url`)، Ollama (لوکال). در رابط وب برای هر گفتگو انتخاب می‌شود. |
| ابزارها | ترمینال (PowerShell روی ویندوز، bash روی لینوکس)، خواندن/نوشتن/ویرایش فایل، اجرای پایتون، جستجوی وب، خواندن صفحه. |
| تأیید انسانی | پیش از دستورهای خطرناک (یا همیشه، یا هرگز) از شما اجازه می‌گیرد؛ می‌توانید دستور را ویرایش کنید. |
| حافظهٔ پایدار | دو فایل کوچک و سقف‌دار (`MEMORY.md`، `USER.md`) که در ابتدای هر گفتگو به ایجنت داده می‌شوند. |
| مهارت‌های خودآموز | روش‌های کارآمد به شکل `SKILL.md` ذخیره می‌شوند؛ یک «مرورگر» پس‌زمینه بعد از کارهای سنگین تصمیم می‌گیرد چیزی ذخیره شود یا نه. |
| جستجوی گفتگوهای قبلی | جستجوی متنی (FTS5) روی همهٔ گفتگوها، چه توسط شما چه توسط ایجنت. |
| ساب‌ایجنت | تا ۳ کار مستقل را موازی به دستیارهای جداگانه می‌سپارد و فقط نتیجه برمی‌گردد. |
| ماندگاری | گفتگوها با checkpoint ‏SQLite ذخیره می‌شوند؛ حتی تأیید نیمه‌تمام بعد از بستن صفحه یا ری‌استارت سرور برمی‌گردد. |

آگاهانه **خارج از نسخهٔ اول** است: کارهای زمان‌بندی‌شده (cron)، ربات تلگرام و MCP.

## نصب و اجرا

نیاز: Python 3.11+ و [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run dastyar init     # ساخت ~/.dastyar با config.yaml و .env نمونه
```

کلید API را در `~/.dastyar/.env` بگذارید (در ویندوز: `C:\Users\<شما>\.dastyar\.env`):

```
ANTHROPIC_API_KEY=sk-ant-...
# یا OPENROUTER_API_KEY / OPENAI_API_KEY
```

```bash
uv run dastyar serve
```

سپس `http://localhost:8765` را در مرورگر باز کنید. بدون هیچ کلیدی هم می‌توانید رابط را امتحان کنید:

```bash
uv run dastyar serve --fake    # مدل آزمایشی؛ مثلاً بنویسید: !sh echo hello
```

> اگر `files.pythonhosted.org` از شبکهٔ شما بسته است، `uv` را با یک آینهٔ PyPI اجرا کنید، مثلاً `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/ uv sync`. (آینه شخص ثالث است؛ فقط در صورت اعتماد استفاده کنید.) این تنظیم عمداً داخل `pyproject.toml` نیست.

## پیکربندی

فایل `~/.dastyar/config.yaml` (نمونه: [config.example.yaml](config.example.yaml)):

- `models`: پروفایل‌ها. هر مدل `provider` (`anthropic` | `openai` | `openrouter` | `ollama`)، `model`، `api_key_env` و در صورت نیاز `base_url` دارد. برای هر endpoint سازگار با OpenAI از `provider: openai` و `base_url` استفاده کنید.
- `default_model` و `cheap_model`: مدل گفتگوهای جدید، و مدل ارزان‌تر برای عنوان‌گذاری، خلاصه‌سازی و مرور پس‌زمینه (اگر کلیدش نباشد از مدل گفتگو استفاده می‌شود).
- `approval_mode`: `dangerous` (پیش‌فرض) | `always` | `never` — از رابط هم قابل تغییر است.
- `reviewer`: کنترل مرور پس‌زمینه (`min_tool_calls`، `every_n_turns`).
- `search.provider`: `ddgs` (بدون کلید) | `tavily` | `searxng`.
- `HTTPS_PROXY` در `.env` برای دسترسی به API از پشت پروکسی.

دادهٔ شما در `~/.dastyar` (یا `DASTYAR_HOME`): `memories/`، `skills/`، `workspace/`، `sessions.db`، `state.db`.

## امنیت — لطفاً بخوانید

این برنامه به یک مدل زبانی اجازهٔ اجرای دستور روی ماشین شما را می‌دهد. محتوای صفحه‌های وب و فایل‌ها می‌تواند سعی کند مدل را فریب دهد (prompt injection).

- **پیش‌فرض فقط روی `127.0.0.1` گوش می‌دهد.** اگر `host` غیرلوکال باشد، بدون `DASTYAR_TOKEN` سرور بالا نمی‌آید. توکن: `uv run dastyar token`.
- Host و Origin هر درخواست بررسی می‌شود (جلوگیری از DNS rebinding و ربودن WebSocket از صفحه‌های دیگر)؛ هدرهای `X-Frame-Options` و CSP سخت‌گیرانه فعال است و رابط هیچ کتابخانهٔ خارجی ندارد.
- حالت `dangerous` فقط یک **تشخیص ساده** است (حذف بازگشتی، نصب بسته، `git push --force`، نوشتن بیرون از workspace و…)، نه مرز امنیتی. برای کار با محتوای ناشناس از `always` استفاده کنید. حالت `never` فقط برای محیط‌های ایزوله است.
- کلیدهای API و توکن به پردازه‌های فرزند (دستورهایی که ایجنت اجرا می‌کند) داده نمی‌شوند و ابزارهای فایل به `~/.dastyar/.env` دسترسی ندارند. ولی ایجنت همچنان با دسترسی کاربر شما کار می‌کند؛ برای سرور از یک کاربر کم‌دسترسی یا کانتینر استفاده کنید.
- ساب‌ایجنت‌ها نمی‌توانند تأیید بگیرند؛ کار پرریسک را رد می‌کنند و به ایجنت اصلی گزارش می‌دهند.

## استقرار روی سرور لینوکس

همان کد بدون تغییر اجرا می‌شود. دو راه:

**Docker** (ساده و ایزوله — ایجنت فقط داخل کانتینر دسترسی دارد):

```bash
cp .env.example .env     # ANTHROPIC_API_KEY و DASTYAR_TOKEN را پر کنید
docker compose up -d --build
```

**systemd**: نمونه در [deploy/dastyar.service](deploy/dastyar.service). برنامه را روی `127.0.0.1` نگه دارید و از **SSH tunnel** (`ssh -L 8765:127.0.0.1:8765 server`) یا یک reverse proxy با HTTPS (مثلاً Caddy: `agent.example.com { reverse_proxy 127.0.0.1:8765 }`) استفاده کنید. پشت proxy حتماً `DASTYAR_TOKEN` بگذارید.

## معماری

```
مرورگر ──WebSocket──► FastAPI ──► create_agent (LangGraph)
                         │           ├─ middleware: پرامپت پویا + حافظه/مهارت، تأیید انسانی (interrupt)،
                         │           │              خلاصه‌سازی، برنامه‌ریزی (todo)، سقف ابزار
                         │           ├─ tools: terminal, files, python, web, memory, skills,
                         │           │         session_search, delegate_task (ساب‌ایجنت)
                         │           └─ checkpointer: AsyncSqliteSaver (state.db)
                         ├─ sessions.db  (تاریخچهٔ رابط + FTS5)
                         └─ reviewer پس‌زمینه ──► فقط ابزارهای memory و skill_manage
```

ساختار کد: `src/dastyar/{agent,tools,store,env,server,web}`.

## توسعه و تست

```bash
uv run pytest            # همهٔ تست‌ها بدون شبکه و بدون کلید (مدل جعلی)
uv run ruff check src
```

تست‌ها شامل جریان کامل ایجنت (تأیید/رد/ویرایش، ساب‌ایجنت، مرور پس‌زمینه)، WebSocket، امنیت و ابزارها هستند. فایل `.env` و `uv.lock` عمداً در git نیستند.
