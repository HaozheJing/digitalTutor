"""Host-only configuration, separate from model tool arguments."""
import json
from pathlib import Path
from pydantic import Field, model_validator
from .domain import Strict


class Config(Strict):
    database: str = 'state/backend-stage1/demo.sqlite'
    service_start: int = Field(default=8, ge=0, le=23)
    service_end: int = Field(default=21, ge=1, le=24)
    channel: str = 'mock_wechat'

    @model_validator(mode='after')
    def check(self):
        if self.channel != 'mock_wechat' or self.service_start >= self.service_end:
            raise ValueError('Only mock_wechat and a valid daytime window are supported')
        return self

    @classmethod
    def load(cls, path=None):
        return cls.model_validate(json.loads(Path(path).read_text(encoding='utf-8'))) if path else cls()
