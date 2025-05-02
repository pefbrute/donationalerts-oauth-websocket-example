# Здесь будет документация по проблеме с дубликацией сообщений

# Проблема: Дублирование сообщений о донатах во фронтенде

## Описание проблемы

Во фронтенд-интерфейсе (Donation Monitor) одно и то же сообщение о донате могло отображаться несколько раз подряд, создавая впечатление дублирования.

## Анализ причины

При анализе логов бэкенда (в частности, сообщений от `socketio.client` и `engineio.client` в `backend/donationalerts_listener.py`) было обнаружено, что бэкенд получал одно и то же событие `donation` (с одинаковым `id`) от сервиса DonationAlerts несколько раз в течение короткого промежутка времени.

Пример лога:
```log
INFO:engineio.client:Received packet MESSAGE data 2["donation","{\"id\":164399607,...}"]
INFO:socketio.client:Received event "donation" [/]
INFO:engineio.client:Received packet MESSAGE data 2["donation","{\"id\":164399607,...}"]
INFO:socketio.client:Received event "donation" [/]
```

Поскольку бэкенд обрабатывал каждое полученное событие независимо (`_process_and_broadcast`), он отправлял обработанный результат во фронтенд несколько раз для одного и того же исходного доната. Фронтенд корректно отображал все полученные сообщения.

Источник дублирования находится на стороне сервиса DonationAlerts, который по какой-то причине (возможно, из-за сетевых проблем, внутренних механизмов повторной отправки или особенностей тестовых оповещений) отправляет одно и то же событие несколько раз.

## Реализованное решение

Для устранения этой проблемы была внедрена логика дедупликации в обработчике событий `handle_donation_event` внутри класса `DonationAlertsListener` (`backend/donationalerts_listener.py`).

**Механизм дедупликации:**

1.  **Отслеживание ID:** Бэкенд теперь хранит идентификаторы (`id`) недавно обработанных донатов и время их получения.
    *   Используется `collections.deque` (`_recent_donation_ids`) для хранения последних N идентификаторов (ограничено `RECENT_DONATION_IDS_MAX_SIZE`).
    *   Используется словарь (`_recent_donation_timestamps`) для хранения временных меток `{donation_id: timestamp}`.
2.  **Проверка при получении:** Когда приходит новое событие `donation`:
    *   Извлекается его `id`.
    *   Проверяется, есть ли этот `id` в словаре `_recent_donation_timestamps`.
    *   **Если `id` существует и временная метка получения этого `id` находится в пределах заданного окна (`RECENT_DONATION_IDS_MAX_AGE`), событие считается дубликатом.** В этом случае обработка прекращается, а в лог записывается предупреждение.
    *   Если `id` новый или его временная метка старше заданного окна, временная метка обновляется (или добавляется), `id` добавляется в `deque`, и событие передается на дальнейшую обработку (`_process_and_broadcast`).

**Ключевые фрагменты кода в `backend/donationalerts_listener.py`:**

```python
# Настройки дедупликации
RECENT_DONATION_IDS_MAX_AGE = timedelta(seconds=10) # Как долго помнить ID
RECENT_DONATION_IDS_MAX_SIZE = 100 # Макс. кол-во ID для хранения

# ... в __init__ ...
self._recent_donation_ids = deque(maxlen=RECENT_DONATION_IDS_MAX_SIZE)
self._recent_donation_timestamps = {}

# ... в handle_donation_event ...
donation_id = donation_data.get('id')
if donation_id:
    now = datetime.now()
    if donation_id in self._recent_donation_timestamps:
        # Проверка временного окна
        if now - self._recent_donation_timestamps[donation_id] < RECENT_DONATION_IDS_MAX_AGE:
             logging.warning(f"Duplicate donation event received for ID {donation_id}...")
             return # Пропустить обработку дубликата
        else:
            # ID старый, удаляем перед обновлением
            del self._recent_donation_timestamps[donation_id]

    # Добавить/Обновить ID и временную метку
    self._recent_donation_timestamps[donation_id] = now
    self._recent_donation_ids.append(donation_id)
else:
     logging.warning("Received donation event without an 'id'...")

# ... (дальнейшая обработка, если не дубликат) ...
```

## Конфигурация

Поведение дедупликации можно настроить с помощью констант в `backend/donationalerts_listener.py`:

*   `RECENT_DONATION_IDS_MAX_AGE`: Определяет временное окно (в `timedelta`), в течение которого повторное получение доната с тем же `id` будет считаться дубликатом.
*   `RECENT_DONATION_IDS_MAX_SIZE`: Определяет максимальное количество последних `id` донатов, которые будут храниться для проверки.

Это решение гарантирует, что даже если DonationAlerts отправит одно и то же событие несколько раз, оно будет обработано и отправлено во фронтенд только один раз.