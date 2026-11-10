"""Only fixed, non-sensitive error messages cross logs, storage or API boundaries."""
import asyncio
from urllib.error import URLError
import httpx

MESSAGES = {
    "login": "登录已失效，请更新凭据或打开辅助登录。",
    "verification": "需要人工验证，请主动打开辅助登录完成验证。",
    "network": "网络请求失败或超时，请检查网络后重试。",
    "parse": "平台返回的数据无法解析，请稍后重试或更新客户端。",
    "configuration": "平台配置不完整或无效，请检查配置。",
    "unknown": "抓取失败，请重试；持续失败时请更新客户端。",
}


class LoginRequired(RuntimeError):
    pass


class VerificationRequired(LoginRequired):
    pass


class NetworkFailure(RuntimeError):
    pass


def classify(error):
    if isinstance(error, VerificationRequired):
        return "verification"
    if isinstance(error, LoginRequired):
        return "login"
    if isinstance(error, (NetworkFailure, asyncio.TimeoutError, httpx.TransportError, URLError)):
        return "network"
    if isinstance(error, httpx.HTTPStatusError):
        return "login" if error.response.status_code in (401, 403) else "network"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return "parse"
    return "unknown"


def stored_kind(message):
    return next((kind for kind, text in MESSAGES.items() if text == message), None)
