from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import Header, HTTPException

from ..config import settings

log = logging.getLogger(__name__)


def admin_required() -> bool:
    """是否启用了后台鉴权。ADMIN_TOKEN 为空时不拦（方便自用与本地开发）。"""
    return bool(settings.admin_token)


def check_admin_token(token: Optional[str]) -> bool:
    if not admin_required():
        return True
    if not token:
        return False
    # 定长比较，避免时序侧信道
    return secrets.compare_digest(token, settings.admin_token)


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """后台专属接口的守卫。

    前台（用户端）不应该看到代理凭据、定价配置、系统信息、全量日志这些东西，
    所以这些接口统一挂这个依赖。ADMIN_TOKEN 没设置时不生效，
    但控制台会在界面上明确提示「后台未设密码」。
    """
    if not check_admin_token(x_admin_token):
        raise HTTPException(
            status_code=401,
            detail="需要管理员令牌：请在后台页面输入 ADMIN_TOKEN（请求头 X-Admin-Token）",
        )
