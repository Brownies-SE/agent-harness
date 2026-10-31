from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict


def parse_uuid(value):
    if type(value) is str:
        return UUID(value)
    return value


def parse_datetime(value):
    if type(value) is str:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


WireUUID = Annotated[UUID, BeforeValidator(parse_uuid)]
WireDatetime = Annotated[datetime, BeforeValidator(parse_datetime)]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
