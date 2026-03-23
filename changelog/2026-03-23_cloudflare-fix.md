# Исправление обхода Cloudflare Managed Challenge

**Дата:** 2026-03-23
**Файл:** `src/renderer.py`
**Метод:** `DolphinRenderer.fetch_html`

---

## Проблема

При запуске краулера каждый запрос к 2407.pl завершался ошибкой Cloudflare:

```
Detected interstitial/error HTML: marker=challenge-error-text
Rejected error/interstitial page
Dolphin fetch error: Received Cloudflare / 504 / interstitial HTML instead of real page
Failed to reattach to existing ws endpoint: ECONNREFUSED
```

Браузер Dolphin Anty показывал страницу **"Izvršavanje sigurnosne provjere"** (Cloudflare Managed Challenge / Turnstile) и зависал на ней бесконечно.

### Корневые причины

| # | Причина | Описание |
|---|---------|----------|
| 1 | `wait_until="domcontentloaded"` | Переход завершался до запуска JS-скрипта Turnstile |
| 2 | Нет начального ожидания | Сразу после `goto()` начинались проверки маркеров, JS ещё не успевал выполниться |
| 3 | Короткий интервал опроса | 3 секунды между проверками — мало для Turnstile |
| 4 | Мало итераций опроса | 20 итераций × 3 с = 60 с — недостаточно |
| 5 | Reconnect только с `attempt >= 1` | При первом сбое реконнект не выполнялся |
| 6 | Нет защиты от краша браузера внутри цикла | `page.title()` / `page.content()` внутри цикла ожидания бросали исключение при падении CDP-соединения, ломая логику retry |

---

## Изменения

### 1. `wait_until="domcontentloaded"` → `"load"`

```python
# До:
self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

# После:
self._page.goto(url, wait_until="load", timeout=60000)
```

**Зачем:** `domcontentloaded` срабатывает до выполнения JS. Turnstile — это JS-challenge, ему нужно время для инициализации. `load` ждёт завершения загрузки всех ресурсов.

---

### 2. Добавлено начальное ожидание 12 секунд

```python
# После goto() добавлено:
time.sleep(12)
```

**Зачем:** Даёт Turnstile JS время запуститься и попытаться авто-решить проверку до начала опроса маркеров.

---

### 3. Увеличены параметры цикла опроса

```python
# До:
for wait_i in range(20):
    ...
    time.sleep(3)

# После:
for wait_i in range(25):
    ...
    time.sleep(6)
```

**Зачем:** 25 × 6 с = 150 с суммарного ожидания вместо 60 с. Даёт больше времени на авто-решение Turnstile.

---

### 4. Защита от краша браузера внутри цикла

```python
# До:
title = self._page.title()
html_snap = self._page.content()

# После:
try:
    title = self._page.title()
    html_snap = self._page.content()
except Exception as page_err:
    logger.warning(f"Page read error during CF wait ({wait_i}): {page_err}")
    break
```

**Зачем:** Если Dolphin закрывает браузер во время ожидания (ECONNREFUSED), исключение теперь перехватывается, цикл выходит, и управление передаётся в блок reconnect.

---

### 5. Защита при финальном чтении контента

```python
# До:
html = self._page.content()

# После:
try:
    html = self._page.content()
except Exception as page_err:
    logger.warning(f"Page content read failed after CF wait: {page_err}")
    raise Exception(f"Page content unavailable after CF wait: {page_err}")
```

---

### 6. Reconnect при каждой ошибке (исправлен баг)

```python
# До:
if attempt >= 1:
    try:
        self._disconnect()
        self._connect()
    ...

# После:
try:
    self._disconnect()
    self._connect()
...
```

**Зачем:** Раньше при первом сбое (attempt=0) реконнект не выполнялся. Теперь браузер пересоздаётся при любой ошибке, что важно когда CDP-соединение падает.

---

## Итоговый временной бюджет на challenge

| Этап | Время |
|------|-------|
| Начальное ожидание после `goto()` | 12 с |
| Максимальное ожидание в цикле | 25 × 6 = 150 с |
| Финальная пауза перед чтением | 2 с |
| **Итого макс. на одну попытку** | **~164 с** |

---

## Важное замечание

Если Cloudflare Managed Challenge по-прежнему не проходит после этих изменений, причина скорее всего **инфраструктурная**, а не в коде:

- **IP с плохой репутацией** — Cloudflare блокирует IP датацентров; нужен residential proxy
- **Флаг автоматизации** — Dolphin профиль детектируется; создать новый профиль с другим fingerprint
- **Rate limiting** — слишком частые запросы; увеличить `delay_min`/`delay_max` в настройках
