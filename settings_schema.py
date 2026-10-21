"""Validation for local settings, imported lists and resolve requests."""
import re
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from content_safety import safe_url

PLATFORMS = ('weibo', 'taoguba', 'zsxq', 'weread')

def normalize_id(platform, value):
    value = str(value).strip()
    if platform not in PLATFORMS:
        raise ValueError('不支持的平台')
    patterns = {'weibo': r'(?:m\.weibo\.cn/u/|weibo\.com/u/)(\d+)',
                'taoguba': r'(?:taoguba\.com\.cn|tgb\.cn)/blog/(\d+)',
                'zsxq': r'wx\.zsxq\.com/dweb2/index/group/(\d+)',
                'weread': r'(MP_WXS_\w+)'}
    m = re.search(patterns[platform], value)
    if m:
        value = next(x for x in m.groups() if x)
    valid = r'\d{1,30}' if platform in ('weibo', 'taoguba') else (
        r'\d{1,30}(?::\d{1,30})?' if platform == 'zsxq' else r'MP_WXS_\w{1,150}')
    if not re.fullmatch(valid, value):
        raise ValueError('用户 ID 格式不正确，可粘贴支持的主页链接')
    return value

class User(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: str
    user_id: str
    nickname: str = Field(default='', max_length=200)
    avatar: str = Field(default='', max_length=2000)
    enabled: StrictBool = True

    @field_validator('platform')
    @classmethod
    def platform_valid(cls, v):
        if v not in PLATFORMS:
            raise ValueError('不支持的平台')
        return v

    @field_validator('user_id')
    @classmethod
    def uid_valid(cls, v, info):
        return normalize_id(info.data.get('platform'), v)

    @field_validator('avatar')
    @classmethod
    def avatar_valid(cls, v):
        if v and not safe_url(v):
            raise ValueError('头像地址无效')
        return v

class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    weibo_cookie: str | None = Field(default=None, max_length=32768)
    taoguba_cookie: str | None = Field(default=None, max_length=32768)
    zsxq_token: str | None = Field(default=None, max_length=32768)
    weread_cookie: str | None = Field(default=None, max_length=32768)
    access_password: str | None = Field(default=None, max_length=256)
    poll_interval_sec: StrictInt | None = Field(default=None, ge=30, le=86400)
    users: list[User] | None = Field(default=None, max_length=500)

    @field_validator('weibo_cookie', 'taoguba_cookie', 'zsxq_token', 'weread_cookie')
    @classmethod
    def credential_valid(cls, v):
        if v is not None and any(c in v for c in ('\n', '\r', '\x00')):
            raise ValueError('凭据不能包含换行符')
        return v.strip() if v else v

    @field_validator('users')
    @classmethod
    def unique_users(cls, v):
        if v and len({(x.platform, x.user_id) for x in v}) != len(v):
            raise ValueError('监控对象重复')
        return v

class ResolveRequest(BaseModel):
    platform: str
    user_id: str
