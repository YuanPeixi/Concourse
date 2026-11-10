"""Explicit adapter registry and serializable configuration descriptions."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from shared import config, credentials


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScholatOptions(Options):
    course_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9]{1,12}$")]] = Field(default_factory=list, max_length=100)
    include_submitted: StrictBool = True


class CodeforcesOptions(Options):
    notify_registered: StrictBool = True
    notify_unregistered: StrictBool = True


@dataclass(frozen=True)
class Platform:
    name: str
    title: str
    color: str
    module: str
    factory: str
    options: type[Options]
    fields: tuple[dict, ...] = ()
    secret_fields: tuple[str, ...] = ()
    login: bool = False
    cron: str = "0 * * * *"

    def describe(self):
        return {"name": self.name, "title": self.title, "color": self.color,
                "fields": list(self.fields), "login": self.login, "cron": self.cron}

    def create(self):
        kwargs = self.options.model_validate(config.fetcher_kwargs(self.name)).model_dump()
        return getattr(import_module(self.module), self.factory)(**kwargs)


def account_fields():
    return ({"name": "username", "label": "账号", "type": "secret", "secret": True},
            {"name": "password", "label": "密码", "type": "secret", "secret": True})


REGISTRY = {
    "scholat": Platform("scholat", "学者网", "#0067C0", "fetchers.scholat", "ScholatFetcher", ScholatOptions,
        ({"name": "course_ids", "label": "课程 ID", "type": "ids", "help": "打开学者网课程页，从地址中的 courseId 参数复制数字；多个 ID 用逗号分隔。"},
         {"name": "include_submitted", "label": "保留已提交事项", "type": "boolean"}) + account_fields(),
        ("username", "password"), True),
    "itestcloud": Platform("itestcloud", "iTest", "#8764B8", "fetchers.itestcloud", "ItestcloudFetcher", Options,
        account_fields(), ("username", "password"), True),
    "moodle": Platform("moodle", "Moodle", "#B35C00", "fetchers.moodle", "MoodleFetcher", Options,
        ({"name": "calendar_url", "label": "私人日历订阅 URL", "type": "secret", "secret": True,
          "help": "在 Moodle 日历导出页复制包含授权信息的 HTTPS 订阅地址；它按密码加密保存。"},), ("calendar_url",)),
    "codeforces": Platform("codeforces", "Codeforces", "#6B3FA0", "fetchers.codeforces", "CodeforcesFetcher", CodeforcesOptions,
        ({"name": "notify_registered", "label": "包含已报名比赛", "type": "boolean"},
         {"name": "notify_unregistered", "label": "包含未报名比赛", "type": "boolean"})),
}


def configured(name: str) -> bool:
    platform = REGISTRY[name]
    try:
        options = platform.options.model_validate(config.fetcher_kwargs(name))
        if name == "scholat" and not options.course_ids:
            return False
        if platform.secret_fields:
            saved = credentials.load(name) or {}
            if not all(isinstance(saved.get(k), str) and saved[k].strip() for k in platform.secret_fields):
                return False
            if name == "moodle":
                from fetchers.moodle import _validate_url
                _validate_url(saved["calendar_url"])
        return True
    except (ValueError, TypeError):
        return False


def validate_credentials(name: str, value: dict[str, str]):
    fields = set(REGISTRY[name].secret_fields)
    if set(value) != fields or not fields:
        raise ValueError("请填写完整的平台凭据")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 8192 for v in value.values()):
        raise ValueError("凭据不能为空且长度不能超过 8192 字符")
    if name == "moodle":
        from fetchers.moodle import _validate_url
        _validate_url(value["calendar_url"])
