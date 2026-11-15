from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrowserModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class FormChoice(BrowserModel):
    field: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)
    source: Literal['assumed', 'user'] = 'assumed'


class InputField(BrowserModel):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    label: str = Field(min_length=1, max_length=200)
    kind: Literal['text', 'email', 'choice', 'password', 'otp', 'magic_link']
    choices: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def validate_choices(self):
        if (self.kind == 'choice') != bool(self.choices):
            raise ValueError('Choice fields must have choices; other fields must not')
        return self


class InputPayload(BrowserModel):
    reason: str = Field(min_length=1, max_length=1000)
    fields: list[InputField] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def unique_fields(self):
        if len({field.id for field in self.fields}) != len(self.fields):
            raise ValueError('Input field IDs must be unique')
        return self


class ApprovalPayload(BrowserModel):
    summary: str = Field(min_length=1, max_length=4000)
    button_label: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=8192)
    site: str = Field(min_length=1, max_length=253)


class TakeoverPayload(BrowserModel):
    reason: str = Field(min_length=1, max_length=1000)


class BrowserResponse(BrowserModel):
    decision: Literal['approve', 'decline'] | None = None
    values: dict[str, Annotated[str, Field(max_length=12000)]] | None = Field(default=None, max_length=6, repr=False)
    done: bool | None = None


class CookieImportRequest(BrowserModel):
    site_url: str = Field(min_length=1, max_length=8192)
    cookies: str = Field(min_length=1, max_length=1_048_576, repr=False)


class ActionResolution(BrowserModel):
    decision: Literal['confirmed', 'not_completed']
    evidence: str = Field(min_length=1, max_length=2000)
