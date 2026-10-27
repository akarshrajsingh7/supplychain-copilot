from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Provider = Literal["ollama", "openai", "anthropic", "google"]


class GraphQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=2000)
    provider: Provider
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    view: Literal["before", "current"] = "current"
    selected_entity: str | None = Field(default=None, max_length=120)


class ModelAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=16000)
    citations: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("answer")
    @classmethod
    def nonempty_answer(cls, value):
        if not value.strip():
            raise ValueError("Empty answer")
        return value


class GraphSource(BaseModel):
    id: str
    label: str
    kind: str
    entity_id: str | None = None
    data: dict


class GraphAnswer(BaseModel):
    answer: str
    sources: list[GraphSource]
    warnings: list[str]
    provider: Provider
    model: str
    view: Literal["before", "current"]
    run_id: str
    incident_status: str
    as_of: str
