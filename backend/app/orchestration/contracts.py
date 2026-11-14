from typing import Literal
import re

from pydantic import BaseModel, Field, model_validator


class Requirement(BaseModel):
    id: str
    description: str
    kind: Literal["fact", "price", "stock", "delivery"] = "fact"


class ResearchContract(BaseModel):
    shopping: bool = False
    destination: str | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    requested_count: int | None = Field(default=None, ge=1, le=100)
    requirements: list[Requirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def purchase_requirements(self):
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique")
        if self.shopping:
            # Stock and delivery are distinct: an in-stock offer may not ship to the buyer.
            self.requirements = [r for r in self.requirements
                                 if r.kind not in {"stock", "delivery"} and r.id not in {"stock", "delivery"}
                                 and not (self.destination and self.destination.casefold() in r.description.casefold()
                                          and re.search(r"\b(available|availability|shipping|delivery|purchase|buy)\b", r.description, re.I))]
            self.requirements.append(Requirement(id="stock", kind="stock", description="Currently in stock for this exact configuration"))
            if self.destination:
                self.requirements.append(Requirement(id="delivery", kind="delivery", description=f"Available to buy in or deliver to {self.destination}"))
        return self


class SearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=2000)


class SearchPlan(BaseModel):
    contract: ResearchContract
    queries: list[SearchQuery] = Field(min_length=1, max_length=4)
