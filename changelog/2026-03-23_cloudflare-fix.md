# Исправление обхода Cloudflare Managed Challenge

**Дата:** 2026-03-23
**Файл:** `src/renderer.py`
**Метод:** `DolphinRenderer.fetch_html` + `_is_error_html`

---

## Проблема

При запуске краулера каждый запрос к 2407.pl завершался ошибкой Cloudflare:

```
Detected interstitial/error HTML: marker=challenge-error-text
Rejected error/interstitial page
Dolphin fetch error: Received Cloudflare / 504 / interstitial HTML instead of real page
Failed to reattach to existing ws endpoint: ECONNREFUSED
```

Браузер Dolphin Anty показывал страницу **"Izvršavanje sigurnosne provjere"**
(Cloudflare Managed Challenge / Turnstile) и никогда не проходил её автоматически.

### Корневые причины

| # | Причина | Описание |
|---|---------|----------|
| 1 | `wait_until="domcontentloaded"` | Переход завершался до запуска JS-скрипта Turnstile |
| 2 | Нет начального ожидания | Сразу после `goto()` начинались проверки маркеров, JS ещё не выполнился |
| 3 | CF-цикл проверял только заголовок страницы | Body-маркеры (`challenge-error-text`, `__cf_chl_` и др.) игнорировались — цикл выходил раньше времени |
| 4 | Мало итераций, короткий интервал | 15 × 2 с = 30 с — недостаточно для Turnstile |
| 5 | Нет защиты от краша браузера внутри цикла | `page.title()` / `page.content()` бросали исключение при падении CDP |
| 6 | Reconnect только с `attempt >= 1` | При первом сбое реконнект не выполнялся |
| 7 | Неполный список CF-маркеров | Отсутствовали `__cf_chl_`, `cf_chl_prog`, `cloudflare ray id`, `ddos protection by cloudflare` |

---

## Изменения

### 1. `wait_until="domcontentloaded"` → `"load"`

```python
# До:
self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

# После:
self._page.goto(url, wait_until="load", timeout=60000)
```

**Зачем:** Turnstile — JS-challenge. `domcontentloaded` срабатывает до выполнения JS.
`load` ждёт все ресурсы, давая скрипту время инициализироваться.

---

### 2. Начальное ожидание 12 секунд после `goto()`

```python
# После goto() добавлено:
time.sleep(12)
```

**Зачем:** Даёт Turnstile JS 12 секунд на запуск и попытку авто-решения до начала опроса маркеров.

---

### 3. CF-цикл теперь проверяет и title, и body

```python
# До — только title:
for _ in range(15):
    title = self._page.title()
    low = title.lower()
    if "момент" not in low and "moment" not in low and "checking" not in low:
        break
    time.sleep(2)

# После — title + body, больше итераций и интервал:
for wait_i in range(25):
    try:
        title = self._page.title()
        html_snap = self._page.content()
    except Exception as page_err:
        logger.warning(f"Page read error during CF wait ({wait_i + 1}/25): {page_err}")
        break
    cf_in_title = ("момент" in low_title or "moment" in low_title
                   or "checking" in low_title or "just a moment" in low_title
                   or "verify" in low_title)
    cf_in_body = self._is_error_html(html_snap, url, silent=True)
    if not cf_in_title and not cf_in_body:
        break
    logger.debug(f"Waiting for Cloudflare ({wait_i + 1}/25): title={title!r}")
    time.sleep(6)
```

**Зачем:** Cloudflare Managed Challenge показывает маркеры в body (`challenge-error-text`,
`__cf_chl_` и т.д.) даже когда title уже "нормальный". Без проверки body цикл выходил
немедленно, и страница отвергалась как CF-error.

---

### 4. Параметры ожидания увеличены

| Параметр | До | После |
|----------|-----|-------|
| Итераций | 15 | 25 |
| Пауза между проверками | 2 с | 6 с |
| Суммарный бюджет | 30 с | 150 с |

---

### 5. Защита от краша браузера внутри цикла

```python
try:
    title = self._page.title()
    html_snap = self._page.content()
except Exception as page_err:
    logger.warning(f"Page read error during CF wait ({wait_i + 1}/25): {page_err}")
    break
```

**Зачем:** Если Dolphin закрывает браузер (ECONNREFUSED) во время ожидания, исключение
перехватывается, цикл завершается, управление передаётся в блок reconnect.

---

### 6. Защита при финальном чтении контента

```python
try:
    html = self._page.content()
except Exception as page_err:
    logger.warning(f"Page content read failed after CF wait: {page_err}")
    raise RuntimeError(f"Page content unavailable after CF wait: {page_err}")
```

---

### 7. Reconnect при каждой ошибке (убран `if attempt >= 1`)

```python
# До:
if attempt >= 1:
    try:
        self._disconnect(keep_ws=True, keep_pw_runtime=True)
        ...

# После:
try:
    self._disconnect(keep_ws=True, keep_pw_runtime=True)
    ...
```

**Зачем:** Раньше при первом сбое реконнект не выполнялся. Теперь браузер пересоздаётся
при любой ошибке, что критично когда CDP падает на первой же попытке.

---

### 8. Расширен список CF body-маркеров

Добавлены в модульный список `_CF_BODY_MARKERS`:

```python
"__cf_chl_",
"cf_chl_prog",
"cloudflare ray id",
"ddos protection by cloudflare",
```

Также добавлен параметр `silent=True` в `_is_error_html` чтобы не спамить WARNING
в логах во время цикла ожидания (только финальная проверка логирует предупреждение).

---

## Итоговый временной бюджет на challenge

| Этап | Время |
|------|-------|
| Начальное ожидание после `goto()` | 12 с |
| Максимальное ожидание в цикле (25 × 6 с) | 150 с |
| Финальная пауза перед чтением | 2 с |
| **Итого макс. на одну попытку** | **~164 с** |

---

## Важное замечание

Если Cloudflare по-прежнему блокирует после этих изменений — причина **инфраструктурная**:

- **IP с плохой репутацией** — Cloudflare блокирует IP датацентров; нужен residential proxy
- **Профиль детектируется** — создать новый Dolphin-профиль с другим fingerprint и proxy
- **Rate limiting** — увеличить `delay_min`/`delay_max` при запуске краулера
