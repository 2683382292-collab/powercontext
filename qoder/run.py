"""复习助手启动入口。

用法（在 qoder 目录下）::

    python run.py            # 或：uv run python run.py

启动后访问终端提示的地址即可打开网页前端。
"""

from __future__ import annotations

import uvicorn

from app.config import load_settings
from app.main import app


def main() -> None:
    cfg = load_settings()
    banner = (
        "\n=== 个性化复习助手 ===\n"
        f"  网页前端 : http://{cfg.host}:{cfg.port}/\n"
        f"  API 文档 : http://{cfg.host}:{cfg.port}/docs\n"
        f"  PowerContext: {cfg.powercontext_url}"
        f"{'（已配置令牌）' if cfg.has_auth else '（无鉴权）'}\n"
        "  请确保 PowerContext Server 已在上面地址启动。\n"
    )
    print(banner)  # noqa: T201 - 启动横幅面向终端用户
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
