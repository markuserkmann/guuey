from typing import Literal
import re

from pydantic import BaseModel, Field, field_validator, model_validator


def type_subject(value: str) -> str:
    value = re.sub(r"\b(?:adult|adults|child|children|men'?s|women'?s|red|blue|black|white|silver)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:sessions?|tickets?|admissions?)$", "", value, flags=re.I)
    value = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:inch|inches|cm|mm)\s+", "", value, flags=re.I)
    return " ".join(value.split())


def pickup_requested(request: str) -> bool:
    noun = r"(?:pick[ -]?up(?!\s+trucks?\b)|collection(?!\s+of\b))"
    pickup = r"(?:" + noun + r"|collect)"
    intent = (r"\b(?:pick[ -]up(?!\s+trucks?\b)|collect)\b|"
              r"\b(?:for|with|offers?|offering|supports?|in[ -]store|store|local)\s+" + noun + r"\b|"
              r"\b" + noun + r"\s+(?:in|at|from|only|available|required|or|locations?|points?)\b")
    if not re.search(intent, request, re.I):
        return False
    if re.search(r"\b(?:no|not|don't|don’t|without|avoid)\s+(?:using\s+)?" + pickup + r"\b", request, re.I):
        return False
    return not re.search(r"\b(?:" + pickup + r"\s+or\s+(?:delivery|shipping)|"
                         r"(?:delivery|shipping)\s+or\s+" + pickup + r")\b", request, re.I)


class Requirement(BaseModel):
    id: str
    description: str
    kind: Literal["fact", "type", "location", "booking", "availability", "price", "stock", "delivery"] = "fact"
    strength: Literal["hard", "preference"] = "hard"
    scope: Literal["primary", "related"] = Field(default="primary", description="Related checks concern a named associated entity, e.g. a hotel for a destination, not the destination itself.")


class Budget(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    operator: Literal["lt", "lte", "approx"] = Field(description="Under/below is lt; at most/up to is lte; around is approx.")
    basis: str | None = Field(default=None, description="Per night, per person, total, etc. Only when specified.")
    requirement_id: str = "price"


class ResearchContract(BaseModel):
    original_request: str = ""
    objective: str = ""
    request_time: str | None = None
    shopping: bool = False
    activity: Literal["product", "service", "research"] | None = Field(default=None, description="Product purchases, service/venue hire and reservations, or general research.")
    subject: str | None = Field(default=None, description="Requested item/service type, preserving defining features such as floating sauna. Audience, size and colour belong in variant notes, not the type check.")
    variant_notes: list[str] = Field(default_factory=list, description="User-requested audience or price variant (e.g. adult admission), separate from service type.")
    anchor: str | None = Field(default=None, description="Specific landmark the user explicitly wants to be near, or null. A purchase country/city belongs in destination, never anchor.")
    booking_required: bool = False
    destination: str | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    jurisdiction: str | None = None
    dates: list[str] = Field(default_factory=list, description="Exact requested dates/windows; preserve unresolved relative wording when timezone is unknown.")
    timezone: str | None = Field(default=None, description="User-supplied IANA timezone or null; do not assume UTC is the user's timezone.")
    budget: Budget | None = None
    exact_entities: list[str] = Field(default_factory=list, description="Requested named entities, exact model numbers or versions, without inventing them.")
    exclusions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list, description="Missing information relevant to the task; missing travel dates prevent a dated room quote, not hotel research.")
    clarification_question: str | None = Field(default=None, description="One focused question only if the ambiguity prevents useful progress. Otherwise null.")
    answer_format: Literal["prose", "table"] = "prose"
    artifact: Literal["answer", "comparison", "checklist", "message_draft", "plan", "form_draft"] = "answer"
    requested_count: int | None = Field(default=None, ge=1, le=100, description="Number of recommended options explicitly requested, never quantities to purchase, people, nights, hours or hardware specs.")
    count_quote: str | None = Field(default=None, description="Exact words including the counted noun, e.g. '3 laptops', from the original request. Null if no result count was requested.")
    requirements: list[Requirement] = Field(default_factory=list, description="Additional atomic constraints only. Subject, anchor, destination and booking_required generate their own checks: do not repeat those here. For a service with no extra budget, date or accessibility constraints, return [].")

    @field_validator("anchor", "destination", "subject", "jurisdiction", "timezone",
                     "clarification_question", "count_quote", mode="before")
    @classmethod
    def empty_optional_text(cls, value):
        if isinstance(value, str) and value.strip().casefold() in {"", "null", "none", "n/a"}:
            return None
        return value

    @model_validator(mode="after")
    def purchase_requirements(self):
        if self.clarification_question and not self.clarification_question.strip().endswith('?'):
            self.clarification_question = None
        self.requirements = [r for r in self.requirements if r.id not in {"table_format", "answer_format", "output_format"}]
        if self.count_quote:
            count_phrase = re.sub(r"^(?:top|best)\s+", "", self.count_quote.strip(), flags=re.I)
            location = r"(?:\s+in\s+" + re.escape(self.destination) + r")?" if self.destination else ""
            parts = count_phrase.split(maxsplit=1)
            count_pattern = re.escape(count_phrase)
            if len(parts) == 2:
                count_pattern = re.escape(parts[0]) + r"\s+(?:(?:distinct|different|unique|budget|affordable|cheap)\s+)*" + re.escape(parts[1])
            count_only = r"(?:top\s+|best\s+)?" + count_pattern + location + r"[.!]?"
            self.requirements = [r for r in self.requirements if not re.fullmatch(count_only, r.description.strip(), re.I)]
        self.requirements = [r for r in self.requirements if not (
            re.search(r'^(?:prepare|draft|write|produce|create)\b.*\b(?:draft|message|checklist|form)\b', r.description, re.I)
            and not re.search(r'\d', r.description))]
        if self.anchor and self.destination and self.anchor.casefold().strip() == self.destination.casefold().strip():
            self.anchor = None
        if not self.anchor:
            self.requirements = [r for r in self.requirements if r.id != "proximity"]
        if self.activity is None:
            self.activity = "product" if self.shopping else "research"
        self.shopping = self.activity == "product"
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique")
        if self.shopping:
            # Stock and delivery are distinct: an in-stock offer may not ship to the buyer.
            self.requirements = [r for r in self.requirements
                                 if r.kind not in {"stock", "delivery"} and r.id not in {"stock", "delivery"}
                                 and not (r.id == "pickup" and r.description.startswith("The requested items can be picked up"))
                                 and not (self.destination and self.destination.casefold() in r.description.casefold()
                                          and re.search(r"\b(available|availability|shipping|delivery|purchase|buy)\b", r.description, re.I))]
            stock_requested = not self.original_request or bool(re.search(r"\b(?:stock|in stock|availability|available now)\b", self.original_request, re.I))
            self.requirements.append(Requirement(id="stock", kind="stock", description="Currently in stock for this exact configuration",
                                                 strength="hard" if stock_requested else "preference"))
            if pickup_requested(self.original_request):
                self.requirements = [r for r in self.requirements if r.id != "pickup"]
                location = f" at a physical store in {self.destination}" if self.destination else " at a physical store"
                self.requirements.append(Requirement(id="pickup", description=f"The requested items can be picked up{location}; delivery alone does not satisfy this requirement"))
            elif self.destination:
                self.requirements.append(Requirement(id="delivery", kind="delivery", description=f"Available to buy in or deliver to {self.destination}"))
        elif self.activity == "service":
            self.requirements = [r for r in self.requirements if r.kind not in {"stock", "delivery"}]
            if self.booking_required and not any(r.kind == "booking" for r in self.requirements):
                self.requirements = [r for r in self.requirements if r.id != "booking"]
                self.requirements.append(Requirement(id="booking", kind="booking", description="Offered for booking or hire"))
            if self.destination and not any(r.kind == "location" and r.id != "proximity" for r in self.requirements):
                self.requirements = [r for r in self.requirements if r.id != "location"]
                self.requirements.append(Requirement(id="location", kind="location", description=f"Located in or provides service in {self.destination}"))
        recommendation_label = self.activity == "research" and self.subject and re.fullmatch(
            r"(?:holiday|vacation|travel)\s+(?:spots?|destinations?)|places?\s+to\s+visit", self.subject, re.I)
        if recommendation_label:
            self.requirements = [r for r in self.requirements if r.id != "requested_type"]
            for requirement in list(self.requirements):
                if (self.destination and requirement.scope == "primary" and requirement.kind == "location"
                        and re.search(r"\b(?:budget|affordable|cheap|low.cost)\b", requirement.description, re.I)):
                    requirement.description = "Budget-friendly features or positioning for this destination"
                    requirement.kind, requirement.strength = "fact", "preference"
                    if not any(r.scope == "primary" and r.strength == "preference"
                               and re.search(r"\b(?:budget|affordable|cheap|low.cost)\b", r.description, re.I)
                               for r in self.requirements):
                        identifier = "destination_value"
                        while any(r.id == identifier for r in self.requirements):
                            identifier += "_preference"
                        self.requirements.append(Requirement(id=identifier, description="Budget-friendly features or positioning for this destination",
                                                             strength="preference"))
            # A destination country bounds discovery; it is not another output fact.
            self.requirements = [r for r in self.requirements if not (r.kind == "location" and r.scope == "primary"
                and self.destination and self.destination.casefold() in r.description.casefold() and r.id != "proximity")]
        if self.subject and not recommendation_label and not any(r.kind == "type" for r in self.requirements):
            self.requirements = [r for r in self.requirements if r.id not in {"type", "requested_type"}]
            self.requirements.append(Requirement(id="requested_type", kind="type", description=f"Matches the requested type: {type_subject(self.subject)}"))
        if self.subject:
            for audience in re.findall(r"\b(?:adult|child|children)\b", self.subject, re.I):
                note = audience.casefold() + " price variant"
                if note not in self.variant_notes:
                    self.variant_notes.append(note)
            for qualifier in re.findall(r"\b(?:red|blue|black|white|silver|\d+(?:[.,]\d+)?\s*(?:inch|inches|cm|mm))\b", self.subject, re.I):
                if any(qualifier.casefold() in r.description.casefold() for r in self.requirements if r.kind != "type"):
                    continue
                identifier = "variant_" + re.sub(r"\W+", "_", qualifier.casefold()).strip("_")
                if not any(r.id == identifier for r in self.requirements):
                    self.requirements.append(Requirement(id=identifier, description="Requested variant: " + qualifier))
        for requirement in self.requirements:
            if requirement.kind == "type" and requirement.scope == "primary":
                requirement.description = type_subject(requirement.description)
        if self.anchor and not any(r.id == "proximity" for r in self.requirements):
            self.requirements.append(Requirement(id="proximity", kind="location", description=f"Near {self.anchor}, supported by relative-location or route/distance evidence; a shared city alone does not establish proximity"))
        if self.budget:
            budget = self.budget
            requirement = next((r for r in self.requirements if r.id == budget.requirement_id), None)
            description = f"Price {'under' if budget.operator == 'lt' else 'at most' if budget.operator == 'lte' else 'around'} {budget.amount:g} {budget.currency}"
            if budget.basis:
                description += f" ({budget.basis})"
            if requirement is None:
                requirement = Requirement(id=budget.requirement_id, description=description, kind="price")
                self.requirements.append(requirement)
            requirement.kind = "price"
            requirement.description = description
            requirement.strength = "preference" if budget.operator == "approx" else "hard"
        for index, exclusion in enumerate(self.exclusions):
            identifier = f"exclude_{index + 1}"
            if not any(r.id == identifier for r in self.requirements):
                self.requirements.append(Requirement(id=identifier, description=f"Exclude: {exclusion}"))
        return self


class SearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=2000)
    gap: str | None = Field(default=None, description="Specific unresolved fact this query will establish; null only on discovery.")
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    start_published_date: str | None = None
    end_published_date: str | None = None
    category: Literal["news", "company", "people", "personal site", "financial report", "publication"] | None = None
    search_type: Literal["auto", "lite"] = "auto"
    filter_quote: str | None = Field(default=None, description="Exact user words authorizing domain/date/category restrictions. Null when using no hard filters.")


class SearchPlan(BaseModel):
    contract: ResearchContract
    queries: list[SearchQuery] = Field(max_length=4, description="Up to four distinct evidence needs; empty if no useful search remains.")
