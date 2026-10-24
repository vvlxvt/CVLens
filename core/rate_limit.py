"""Простой rate limiter на основе токен-бакета для защиты LLM-затратных эндпоинтов.

Хранит счётчики в памяти (dict). Для single-server развёртывания этого
достаточно. Для multi-server нужно заменить на Redis-backed хранилище.

Использование:
    from core.rate_limit RateLimiter

    limiter = RateLimiter(max_requests=10, window_seconds=60)

    @app.post("/heavy-endpoint", dependencies=[Depends(limiter.check)])
    def heavy_endpoint(): ...
"""

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request


class RateLimiter:
    """Token-bucket rate limiter на базе скользящего окна.

    Каждый ключ (IP или API токен) имеет своё окно. Если запросов в окне
    больше max_requests — возвращается HTTP 429.
    """

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _key(self, request: Request) -> str:
        # Предпочитаем API токен, иначе IP клиента
        auth = request.headers.get("authorization", "")
        if auth:
            return f"token:{auth}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    def check(self, request: Request) -> None:
        """Вызывает HTTP 429, если лимит исчерпан."""
        now = time.time()
        key = self._key(request)

        with self._lock:
            # Удаляем устаревшие метки за пределами окна
            cutoff = now - self.window_seconds
            self._timestamps[key] = [
                ts for ts in self._timestamps[key] if ts > cutoff
            ]

            if len(self._timestamps[key]) >= self.max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Rate limit exceeded: {self.max_requests} requests "
                        f"per {self.window_seconds}s. Try again later."
                    ),
                    headers={"Retry-After": str(self.window_seconds)},
                )

            self._timestamps[key].append(now)


# Экземпляры для разных категорий эндпоинтов
llm_rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
read_rate_limiter = RateLimiter(max_requests=60, window_seconds=60)