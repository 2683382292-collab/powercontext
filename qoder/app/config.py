"""应用配置。

从进程环境变量读取配置；若 ``qoder/.env`` 存在，则先加载其中的键值（不覆盖已存在的环境变量）。
这里刻意不引入额外依赖，只用标准库解析简单的 ``KEY=VALUE`` 环境文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# qoder 目录（本文件位于 qoder/app/config.py）
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

_DEFAULTS = {
    "POWERCONTEXT_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_TOKEN": "",
    "REVIEW_SCOPE_PREFIX": "qoder-review",
    "REVIEW_HOST": "127.0.0.1",
    "REVIEW_PORT": "8080",
    "REVIEW_SESSION_SIZE": "5",
}


def _load_env_file(path: Path) -> None:
    """把 ``.env`` 中的键值注入 ``os.environ``，已存在的键保持不变。"""

    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    """运行期配置快照。"""

    powercontext_url: str
    powercontext_token: str | None
    scope_prefix: str
    host: str
    port: int
    session_size: int

    @property
    def has_auth(self) -> bool:
        return bool(self.powercontext_token)


def load_settings() -> Settings:
    """读取环境变量并构造 :class:`Settings`。"""

    _load_env_file(ENV_FILE)

    def env(key: str) -> str:
        value = os.environ.get(key)
        if value is None or value == "":
            return _DEFAULTS[key]
        return value

    token = os.environ.get("POWERCONTEXT_TOKEN") or _DEFAULTS["POWERCONTEXT_TOKEN"]
    return Settings(
        powercontext_url=env("POWERCONTEXT_URL").rstrip("/"),
        powercontext_token=token or None,
        scope_prefix=env("REVIEW_SCOPE_PREFIX"),
        host=env("REVIEW_HOST"),
        port=int(env("REVIEW_PORT")),
        session_size=int(env("REVIEW_SESSION_SIZE")),
    )
