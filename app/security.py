"""登录节流、会话开启与生产环境安全检查。"""

from __future__ import annotations

import math
import threading
import time

from .config import settings

DEFAULT_SESSION_SECRET = "dev-only-change-me-please-32-chars"
MIN_SECRET_LENGTH = 32


class AttemptWindow:
    """滑动窗口计数：同一键在窗口内的失败次数达到上限后返回等待秒数。"""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        events = [stamp for stamp in self._events.get(key, []) if stamp > cutoff]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        with self._lock:
            events = self._prune(key, moment)
            if len(events) < self.limit:
                return 0
            return max(1, math.ceil(events[0] + self.window_seconds - moment))

    def record(self, key: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            events = self._prune(key, moment)
            events.append(moment)
            self._events[key] = events

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


class LoginThrottle:
    """同时限制“IP + 账号”和“单 IP”的登录失败次数。"""

    def __init__(
        self,
        *,
        per_user: int = 5,
        per_ip: int = 20,
        window_seconds: int = 900,
    ) -> None:
        self.users = AttemptWindow(per_user, window_seconds)
        self.ips = AttemptWindow(per_ip, window_seconds)

    @staticmethod
    def _user_key(ip: str, username: str) -> str:
        return f"{ip}|{username.strip().lower()}"

    def retry_after(
        self, ip: str, username: str, *, now: float | None = None
    ) -> int:
        return max(
            self.users.retry_after(self._user_key(ip, username), now=now),
            self.ips.retry_after(ip, now=now),
        )

    def record_failure(
        self, ip: str, username: str, *, now: float | None = None
    ) -> None:
        self.users.record(self._user_key(ip, username), now=now)
        self.ips.record(ip, now=now)

    def reset_user(self, ip: str, username: str) -> None:
        self.users.reset(self._user_key(ip, username))

    def reset(self) -> None:
        self.users.reset()
        self.ips.reset()


def session_expired(
    session: dict, max_age_seconds: int, *, now: float | None = None
) -> bool:
    issued = session.get("issued_at")
    if not isinstance(issued, int):
        return True
    moment = time.time() if now is None else now
    return moment - issued > max_age_seconds


def ensure_production_settings(config) -> None:
    if config.is_dev:
        return
    secret = config.session_secret or ""
    if secret == DEFAULT_SESSION_SECRET or len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            "生产环境必须通过 SESSION_SECRET 配置至少 32 字符的随机密钥，"
            "不能使用仓库里的开发默认值"
        )


login_throttle = LoginThrottle(
    per_user=settings.login_max_attempts,
    per_ip=settings.login_ip_max_attempts,
    window_seconds=settings.login_window_seconds,
)
