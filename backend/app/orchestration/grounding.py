import re
import json
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import quote as quote_url, urlsplit
from unicodedata import combining, normalize

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.passages import find_passage, visible, citation_passages
from app.research import live_source, source_domain, comparison_source, category_source
from app.source_quality import source_class, task_kind, QUALITY_RANK, property_page
from app.source_cache import PersistentCache, delivery_source_current


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(min_length=1)
    passage_id: str | None = Field(default=None, description="Supplied P_... reference identifying this quote's exact source context.")


class RequirementCheck(BaseModel):
    requirement: str
    requirement_id: str = ""
    status: Literal["met", "not_met", "unknown"]
    statement: str
    reason: str = ""
    basis: Literal["confirmed", "reported", "conflicting", "none"] = "none"
    observed: str | None = None
    strength: Literal["hard", "preference"] = "hard"
    scope: Literal["primary", "related"] = "primary"
    related_entity: str | None = Field(default=None, description="For a related requirement, name the actual related entity, such as the hotel near a destination. Include this name in the statement and supporting passage.")
    value: str = ""
    source_label: str = ""
    refinement_excluded: bool = False
    evidence: list[Evidence] = Field(description="Required. For met/not_met, provide the exact source_url and a verbatim quote proving this requirement for this finding. Use [] only for unknown.")


class Finding(BaseModel):
    _identity_alias: str | None = PrivateAttr(default=None)
    name: str
    description: str = Field(default="", description="One concise factual description of this option, supported by description_evidence.")
    description_evidence: list[Evidence] = Field(default_factory=list, max_length=4)
    assessment: str = Field(default="", description="Brief practical judgment about why to consider this option; do not introduce unsupported facts.")
    assessment_evidence: list[Evidence] = Field(default_factory=list)
    source_url: str | None = Field(default=None, description="Exact supplied URL establishing the identity of this candidate, even when its requirements are unknown.")
    source_kind: Literal["product", "service", "manufacturer", "review", "comparison", "other"] = "other"
    condition: Literal["new", "outlet", "used", "refurbished"] | None = None
    checks: list[RequirementCheck] = Field(min_length=1)


class KnowledgePoint(BaseModel):
    statement: str
    evidence: list[Evidence] = Field(default_factory=list)
    basis: Literal["confirmed", "reported"] = "confirmed"
    source_label: str = ""
    observed: str | None = None


class KnowledgeAnswer(BaseModel):
    verdict: str = ""
    points: list[KnowledgePoint] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    draft: str | None = None
    caveats: list[str] = Field(default_factory=list)
    limited_evidence: bool = False


class GroundedAnswer(BaseModel):
    response: str = Field(default="", description="Conversational answer for explanations or research questions that do not call for comparing candidates. Cite supplied URLs for current facts. Leave empty when findings are used.")
    requested_count: int | None = Field(default=None, ge=1, le=100, description="Number of items explicitly requested by the user, or null.")
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    knowledge: KnowledgeAnswer | None = None
    alignment_rejections: list[dict] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def bounded_findings(cls, findings: list[Finding]) -> list[Finding]:
        # Enforce this locally: maxItems on the outer nested schema exceeds Gemini's schema budget.
        if len(findings) > 30:
            raise ValueError("Return at most 30 findings")
        return findings


class LLMCheck(BaseModel):
    requirement_id: str
    status: Literal["met", "not_met", "unknown"]
    value: str = Field(default="", description="Extracted value only, at most twelve words; preserve units and qualifiers.")
    evidence: list[str] = Field(default_factory=list, description="Exact supplied P_... passage IDs.")
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def legacy_payload(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("value"):
                data["value"] = data.get("statement", "")
            data["evidence"] = [json.dumps(item) if isinstance(item, dict) else item
                                for item in data.get("evidence", [])]
        return data


class LLMFinding(BaseModel):
    name: str
    source_url: str | None = None
    source_kind: Literal["product", "service", "manufacturer", "review", "comparison", "other"] = "other"
    checks: list[LLMCheck] = Field(default_factory=list)


class LLMKnowledgePoint(BaseModel):
    statement: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def legacy_evidence(cls, items):
        return [json.dumps(item) if isinstance(item, dict) else item for item in items]


class LLMKnowledgeAnswer(BaseModel):
    verdict: str = ""
    points: list[LLMKnowledgePoint] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    draft: str | None = None
    caveats: list[str] = Field(default_factory=list)


class LLMAnswer(BaseModel):
    findings: list[LLMFinding] = Field(default_factory=list)
    knowledge: LLMKnowledgeAnswer | None = None
    limitations: list[str] = Field(default_factory=list)

    _legacy_response: str = PrivateAttr(default="")

    @model_validator(mode="wrap")
    @classmethod
    def legacy_response(cls, data, handler):
        result = handler(data)
        if isinstance(data, dict):
            result._legacy_response = data.get("response", "")
        return result


def expand_llm_answer(answer: LLMAnswer, sources: list[dict], contract: ResearchContract | None) -> GroundedAnswer:
    if isinstance(answer, GroundedAnswer):
        return answer
    passages = {identifier: (source["url"], quote) for source in sources
                for identifier, (quote, _) in citation_passages(source).items()}

    def expand_evidence(references):
        evidence = []
        for reference in references:
            if reference in passages:
                url, quote = passages[reference]
                evidence.append(Evidence(source_url=url, quote=quote, passage_id=reference))
            elif reference.startswith("{"):
                try:
                    evidence.append(Evidence.model_validate_json(reference))
                except ValueError:
                    pass
        return evidence

    requirements = {r.id: r for r in contract.requirements} if contract else {}
    findings = []
    for item in answer.findings[:30]:
        checks = []
        for check in item.checks:
            requirement = requirements.get(check.requirement_id)
            value = check.value.strip()
            too_long = len(value.split()) > 12
            evidence = expand_evidence(check.evidence)
            related = None
            if requirement and requirement.scope == "related":
                # The extracted hotel value includes its name; the app recovers a sourced heading.
                for name, _ in accommodation_headings([s for s in sources if s["url"] in {e.source_url for e in evidence}]):
                    if passage_present(name, value):
                        related = name
                        break
                if not related:
                    related = re.split(r"\s+(?:is|in|located|at|for)\s+|[,;]", value, maxsplit=1, flags=re.I)[0] or None
            checks.append(RequirementCheck(requirement_id=check.requirement_id,
                requirement=requirement.description if requirement else check.requirement_id,
                status="unknown" if too_long else check.status, value="" if too_long else value,
                basis="reported" if check.status == "unknown" and value and evidence and not too_long else "none",
                statement="" if too_long else value, evidence=[] if too_long else evidence,
                related_entity=related, scope=requirement.scope if requirement else "primary",
                strength=requirement.strength if requirement else "hard",
                reason="Extracted value exceeds twelve words." if too_long else check.reason))
        findings.append(Finding(name=item.name, source_url=item.source_url, source_kind=item.source_kind, checks=checks or [
            RequirementCheck(requirement="Identity", status="unknown", statement="", evidence=[])]))
    knowledge = None
    if answer.knowledge:
        knowledge = KnowledgeAnswer(**answer.knowledge.model_dump(exclude={"points"}), points=[
            KnowledgePoint(statement=p.statement, evidence=expand_evidence(p.evidence)) for p in answer.knowledge.points])
    return GroundedAnswer(findings=findings, knowledge=knowledge, response=answer._legacy_response,
                          limitations=answer.limitations)


def plain(text: str) -> str:
    text = re.sub(r"\[S(?:\d+|_[a-f0-9]+)\]", "", text)
    text = " ".join(text.split())
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def passage_present(quote: str, text: str) -> bool:
    return find_passage(quote, text) is not None


def matching_evidence(evidence: list[Evidence], sources: list[dict]) -> list[Evidence]:
    texts = {source["url"]: source["text"] for source in sources if source_class(source) not in {"seo", "off_target"}}
    return [item for item in evidence if item.source_url in texts
            and find_passage(item.quote, texts[item.source_url], passage_id=item.passage_id, source_url=item.source_url)]


def destination_evidence(item: Evidence, source: dict, destination: str) -> bool:
    match = find_passage(item.quote, source["text"], passage_id=item.passage_id, source_url=item.source_url)
    context = visible(match.context if match else item.quote)
    if destination.casefold() in {"finland", "suomi", "finnland"}:
        if re.search(r"\b(?:finland|finnland|suomi|suomeen|suomessa|suomen|finlande)\b", context):
            return True
        pickup = re.search(r"\b(?:pickup|pick up|collection|nouto|noudettavissa|noutaa)\b", context)
        city = re.search(r"\b(?:helsinki|helsingissä|helsingistä|espoo|espoossa|espoosta|vantaa|vantaalla|"
                         r"tampere|tampereella|turku|turussa|oulu|oulussa)\b", context)
        return bool(pickup and city)
    return bool(re.search(r"(?<!\w)" + re.escape(destination.casefold()) + r"(?!\w)", context))


def current_stock_evidence(item: Evidence, source: dict) -> bool:
    match = find_passage(item.quote, source["text"], passage_id=item.passage_id, source_url=item.source_url)
    context = match.context if match else item.quote
    return explicit_stock(context) and not re.search(r"ei (?:äskettäin|hiljattain) vahvistettu|not recently (?:confirmed|verified)|"
                         r"stock (?:status )?(?:unconfirmed|unverified)|availability (?:unconfirmed|unverified)", context, re.I)


def explicit_stock(text: str) -> bool:
    return bool(re.search(r"\b(?:in stock|out of stock|sold out|varastossa|ei varastossa|loppuunmyyty|"
                          r"saatavilla|in warehouse|stock status|currently unavailable)\b", text, re.I))


def detected_condition(url: str, title: str) -> str:
    text = url + " " + title
    for condition, pattern in (("refurbished", r"refurbished|kunnostettu|remanufactured"),
                               ("used", r"käytetty|kaytetty|\bused\b|recommerce|second.hand"),
                               ("outlet", r"outlet|\bdemo\b|b[ -]grade|open.box")):
        if re.search(pattern, text, re.I):
            return condition
    return "new"


def alignment_ok(value: str, evidence: list[Evidence], sources: list[dict], *, proper_names=True) -> bool:
    if not value or not evidence:
        return False
    context = "\n".join(evidence_context(item, sources) for item in evidence)
    def folded(text):
        return re.sub(r"\s+", " ", visible(text)).replace(",", ".")
    haystack = folded(context)
    # Match numeric tokens rather than substrings (7 is not supported by 17).
    for number in re.findall(r"\d+(?:[.,]\d+)*", value):
        token = number.replace(",", ".")
        if not re.search(r"(?<!\d)(?<!\d\.)" + re.escape(token) + r"(?!\d|\.\d)", haystack):
            # Retailers often render superscript cents or thousands with a space.
            if Decimal(token) not in {amount for currency in ("EUR", "USD", "GBP")
                                      for amount in monetary_values(context, currency)}:
                return False
    ignored = {"EUR", "USD", "GBP", "GB", "TB", "RAM", "SSD", "HDD", "DDR4", "DDR5", "VAT",
               "Current", "Price", "From", "Adult", "Public", "Includes", "Located", "Available",
               "In", "Not", "The", "It", "Open", "Book", "Delivery", "Finnish", "Swedish", "Nordic"}
    for word in re.findall(r"(?<!\w)[A-ZÀ-ÖØ-Þ][\wÀ-ÿ’-]{2,}", value) if proper_names else []:
        if word in ignored:
            continue
        if not place_present(word, context):
            return False
    return True


def viable_check_evidence(finding, check, sources, contract, requirement):
    by_url = {s["url"]: s for s in sources}
    evidence = [e for e in matching_evidence(check.evidence, sources)
                if source_class(by_url[e.source_url], contract) not in {"seo", "off_target"}]
    if requirement.scope == "related" and requirement.description.startswith("Named hotel") and check.status != "not_met":
        primary_identity = [e for e in evidence if source_class(by_url[e.source_url], contract) == "official"
                    and (property_page(e.source_url) or (check.related_entity and
                         re.sub(r"\W", "", source_domain(e.source_url).split(".")[-2]) in
                         re.sub(r"\W", "", visible(check.related_entity))))]
        if not primary_identity:
            return []
    if requirement.scope == "primary" and re.search(r"budget|affordable|cheap", requirement.description, re.I):
        # A country-wide sentence listing several destinations cannot rate one of them.
        evidence = [e for e in evidence if any(place_present(finding.name, sentence) and re.search(
            re.escape(visible(finding.name)) + r"(?:[’']s)?\s+(?:\w+\s+){0,3}(?:is|are|offers?|has|provides?|costs?|can|remains?)\b", visible(sentence))
            for sentence in re.split(r"(?<=[.!?])\s+", e.quote))]
    return evidence


def evidence_context(item: Evidence, sources: list[dict]) -> str:
    source = next((source for source in sources if source["url"] == item.source_url), None)
    if not source:
        return ""
    match = find_passage(item.quote, source["text"], passage_id=item.passage_id, source_url=item.source_url)
    return source.get("title", "") + "\n" + (match.context if match else "")


def place_present(place: str, text: str) -> bool:
    def folded(value):
        return "".join(character for character in normalize("NFKD", visible(value)) if not combining(character))
    return bool(re.search(r"(?<!\w)" + re.escape(folded(place)) + r"(?!\w)", folded(text)))


def accommodation_headings(sources: list[dict]):
    for source in sources:
        for match in re.finditer(r"^#{1,6}\s+(.+)$", source["text"], re.M):
            name = match.group(1).strip()
            if re.search(r"\b(?:hotel|hotell|hostel|guesthouse|apartment|apartments|inn|resort|lodge)\b", name, re.I):
                yield name, Evidence(source_url=source["url"], quote=name)


def infer_related_entity(check: RequirementCheck, sources: list[dict]) -> None:
    if check.scope != "related" or check.related_entity or not check.statement:
        return
    cited_urls = {item.source_url for item in check.evidence}
    names = {visible(name): name for name, _ in accommodation_headings([source for source in sources if source["url"] in cited_urls])
             if passage_present(name, check.statement)}
    if len(names) == 1:
        check.related_entity = next(iter(names.values()))


def district_bridge(finding: Finding, sources: list[dict]) -> tuple[str, Evidence] | None:
    match = re.fullmatch(r"(.+?)\s+(?:old town|city cent(?:re|er))", finding.name, re.I)
    if not match:
        return None
    city = match.group(1)
    for source in sources:
        if source["url"] != finding.source_url:
            continue
        for paragraph in re.split(r"\n\s*\n", source["text"]):
            location_text = re.sub(re.escape(visible(finding.name)), "", visible(paragraph))
            if paragraph.lstrip().startswith("#") or not place_present(city, location_text):
                continue
            if re.search(r"\b(?:from|away|outside|kilometres?|kilometers?|miles?|km)\b", location_text):
                continue
            passage = find_passage(paragraph.strip(), source["text"])
            if passage and passage_present(finding.name, passage.context):
                return city, Evidence(source_url=source["url"], quote=paragraph.strip())
    return None


def candidate_evidence(finding: Finding, check: RequirementCheck, sources: list[dict], kind: str = "fact") -> list[Evidence]:
    matched = matching_evidence(check.evidence, sources)
    entity = check.related_entity if check.scope == "related" else finding.name
    by_url = {source["url"]: source for source in sources}

    def matching_subject(item):
        source = by_url[item.source_url]
        passage = find_passage(item.quote, source["text"], passage_id=item.passage_id, source_url=item.source_url)
        local = "\n".join([*(passage.headings if passage else ()), item.quote])
        if re.search(r"related products|you may also like|recommended products|other (?:models|products)|similar products", local, re.I):
            return False
        if not entity or passage_present(entity, item.quote):
            return True
        family = re.match(r"(laptop|hotel|sauna|venue|product|model)\s+(\w+)", visible(entity))
        if family:
            names = re.findall(r"\b" + family[1] + r"\s+(\w+)", visible(local))
            if names and any(name != family[2] for name in names):
                return False
        brands = {"acer", "asus", "dell", "lenovo", "hp", "apple", "microsoft", "samsung", "msi"}
        identity_brands = set(re.findall(r"\w+", visible(entity))) & brands
        quoted_brands = set(re.findall(r"\w+", visible(item.quote))) & brands
        return not (identity_brands and quoted_brands and not identity_brands.intersection(quoted_brands))

    matched = [item for item in matched if matching_subject(item)]
    def matching_row(item):
        context = evidence_context(item, sources)
        rows = [line for line in context.splitlines() if "|" in line]
        if len(rows) < 2:
            return True
        header = visible(rows[-2].strip().strip("|").split("|")[0])
        if header not in {"name", "hotel", "model", "product", "candidate", "venue", "destination", "city", "option"}:
            return True
        row_name = rows[-1].strip().strip("|").split("|")[0].strip()
        if check.scope == "related" and header in {"destination", "city"}:
            return place_present(finding.name, row_name)
        return bool(entity and passage_present(entity, row_name))
    matched = [item for item in matched if matching_row(item)]
    if check.scope == "related":
        def hotel_name_present(text):
            if not check.related_entity:
                return False
            core = re.sub(r"^(?:hotel|hotell)\s+", "", check.related_entity, flags=re.I)
            return passage_present(check.related_entity, text) or (core != check.related_entity and len(core) >= 4
                                                                  and passage_present(core, text))

        if not check.related_entity or not hotel_name_present(check.statement):
            return []
        place_names = [finding.name]
        core_name = re.sub(r"\s+(?:National Park|Island)$", "", finding.name, flags=re.I)
        if core_name != finding.name and any(place_present(finding.name, source["text"]) for source in sources):
            place_names.append(core_name)
        cited_urls = {item.source_url for item in matched}
        individual_hotel_pages = set()
        for source in sources:
            if source["url"] not in cited_urls:
                continue
            for passage_id, (quote, context) in citation_passages(source).items():
                if hotel_name_present(context) and any(place_present(name, context) for name in place_names):
                    headings = [name for name, _ in accommodation_headings([source])]
                    if headings and all(hotel_name_present(name) for name in headings):
                        individual_hotel_pages.add(source["url"])
                    item = Evidence(source_url=source["url"], quote=quote, passage_id=passage_id)
                    if matching_row(item) and item not in matched:
                        matched.append(item)
                    break
        matched = [item for item in matched if hotel_name_present(evidence_context(item, sources))
                   or item.source_url in individual_hotel_pages]
        if any(place_present(name, evidence_context(item, sources)) for name in place_names for item in matched):
            return matched
        bridge = district_bridge(finding, sources)
        if bridge and any(place_present(bridge[0], evidence_context(item, sources)) for item in matched):
            return [*matched, bridge[1]]
        return []
    name_words = re.findall(r"\w+", visible(finding.name))

    def operator_title_identity(item):
        title = visible(by_url[item.source_url].get("title", ""))
        return (source_domain(item.source_url) == source_domain(finding.source_url or "")
                and len(name_words) > 1
                and all(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", title) for word in name_words))

    return [item for item in matched
            if passage_present(finding.name, evidence_context(item, sources))
            or operator_title_identity(item)
            or (item.source_url == finding.source_url and finding._identity_alias
                and passage_present(finding._identity_alias, evidence_context(item, sources)))
            or (kind == "delivery" and source_domain(item.source_url) == source_domain(finding.source_url or ""))]


def monetary_values(text: str, currency: str) -> set[Decimal]:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    token = "(?:" + re.escape(currency) + "|" + re.escape(symbols.get(currency, currency)) + ")"
    # Some retailer extracts put superscript cents after a space: 679 90€.
    number = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)*"
    display_number = number + r"(?:[ \u00a0\u202f]\d{2}(?!\d))?"
    amounts = re.findall(token + r"\s*(" + number + r")|(" + display_number + r")\s*" + token, text, re.I)
    free = r"(?:free(?: of charge)?|complimentary|maksuton|ilmainen|tasuta)"
    explicit_free = re.search(
        r"(?:^|[·|;:\n])\s*" + free + r"\s*(?=$|[·|;.\n])|"
        r"\b(?:admission|entry|entrance|tickets?|participation|registration|price|cost)\s*(?:is|are|:)?\s*" + free + r"\b|"
        r"\bfree\s+(?:admission|entry|entrance|tickets?|participation|registration)\b", text, re.I)
    values = {Decimal(0)} if explicit_free else set()
    for before, after in amounts:
        raw = (before or after).strip()
        if not re.search(r"[.,]", raw):
            raw = re.sub(r"(?<=\d)[ \u00a0\u202f](\d{2})$", r".\1", raw)
        raw = re.sub(r"\s", "", raw)
        if "," in raw and "." in raw:
            decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
            raw = raw.replace("." if decimal_mark == "," else ",", "").replace(decimal_mark, ".")
        elif "," in raw or "." in raw:
            mark = "," if "," in raw else "."
            parts = raw.split(mark)
            raw = "".join(parts) if len(parts[-1]) == 3 else "".join(parts[:-1]) + "." + parts[-1]
        try:
            values.add(Decimal(raw))
        except InvalidOperation:
            continue
    return values


def recover_hotel_locations(answer: GroundedAnswer, sources: list[dict]) -> list[dict]:
    recovered = []
    for finding in answer.findings:
        places = [finding.name]
        core = re.sub(r"\s+(?:National Park|Island)$", "", finding.name, flags=re.I)
        if core != finding.name and any(place_present(finding.name, source["text"]) for source in sources):
            places.append(core)
        for check in finding.checks:
            if check.status != "unknown" or check.scope != "related" or not check.related_entity:
                continue
            if not check.requirement.startswith(("Named hotel for this destination", "Named hotel located in this destination")):
                continue
            if named_hotel_alternative(check.related_entity) and any(
                    passage_present(check.related_entity, name) for name, _ in accommodation_headings(sources)):
                continue
            if hotel_alternative_evidence(check.related_entity, sources):
                continue
            for source in sources:
                for passage_id, (quote, context) in citation_passages(source).items():
                    if not any(place_present(place, context) for place in places):
                        continue
                    proposed = check.model_copy(deep=True)
                    sentence = re.split(r"(?<=[.!?])\s+", quote, maxsplit=1)[0]
                    proposed.statement = (sentence if passage_present(check.related_entity, sentence)
                                          and any(place_present(place, sentence) for place in places)
                                          else f"{check.related_entity} is listed for {finding.name}.")
                    proposed.evidence = [Evidence(source_url=source["url"], quote=quote, passage_id=passage_id)]
                    evidence = candidate_evidence(finding, proposed, sources)
                    if not evidence:
                        continue
                    check.status, check.statement, check.reason = "met", proposed.statement, ""
                    check.evidence = evidence
                    recovered.append({"candidate": finding.name, "requirement_id": check.requirement_id,
                                      "source_url": source["url"], "review_required": True})
                    break
                if check.status == "met":
                    break
    return recovered


def recover_destination_locations(answer: GroundedAnswer, sources: list[dict]) -> list[dict]:
    recovered = []
    for finding in answer.findings:
        for check in finding.checks:
            if check.status != "unknown" or check.scope != "primary" or not check.requirement.startswith("Located in "):
                continue
            destination = check.requirement.removeprefix("Located in ")
            for source in sources:
                for passage_id, (quote, context) in citation_passages(source).items():
                    sentence = direct_location_sentence(finding.name, destination, quote)
                    if not sentence:
                        continue
                    check.statement = sentence
                    check.evidence = [Evidence(source_url=source["url"], quote=quote, passage_id=passage_id)]
                    check.status, check.reason = "met", ""
                    recovered.append({"candidate": finding.name, "requirement_id": check.requirement_id,
                                      "source_url": source["url"], "review_required": True})
                    break
                if check.status == "met":
                    break
    return recovered


def recover_budget_features(answer: GroundedAnswer, sources: list[dict], *, replace_assertions: bool = False) -> list[dict]:
    recovered = []
    budget_words = r"\b(?:budget|affordable|cheap|cheaper|cheapest|low.cost|economical)\b"
    qualitative = r"\b(?:affordable|cheap|cheaper|cheapest|low.cost|budget.friendly|economical)\b"
    cost_less = r"\b(?:costs?|prices?|rates?)\b[^.!?]{0,100}\bless\b"
    positive = r"\bfree\b|" + qualitative + "|" + cost_less
    negative = r"\b(?:not|never|no longer|isn't|aren't|wasn't|weren't|no free)\b"
    amenities = r"\b(?:wi[ -]?fi|internet|parking|toiletries|towels?|shampoo|shower gel)\b"
    activities = r"\b(?:admission|entry|walking|walks?|sightseeing|museums?|trails?|parks?|beaches?|attractions?|enter|explore|visit)\b"
    cost_subject = r"(?:rooms?|rates?|stays?|accommodation|lodging|hotels?|cafes?|cafés?|food|meals?|travel|activities)"
    rate_label = r"^[\s/|:–—-]*(?:cheapest|lowest|best)\s+(?:available\s+)?(?:rates?|prices?|deals?)(?:\s+per\s+(?:night|stay|room))?[\s.!/|:–—-]*$"

    def names_other_lodging(finding, sentence, context, source):
        selected = {visible(check.related_entity) for check in finding.checks
                    if check.scope == "related" and check.related_entity}
        if not selected:
            return False
        names = {name for name, _ in accommodation_headings([source])}
        for label, url in re.findall(r"\[([^\]]+)\]\(<?([^\s)>]+)>?\)", context):
            if re.search(r"/(?:hotel|hostel|guesthouse|accommodation|property)/", url, re.I):
                names.add(label)
        return any(visible(name) not in selected and passage_present(name, sentence) for name in names)

    def bound_context(finding, check, sentence, context):
        entity = check.related_entity if check.scope == "related" else finding.name
        if passage_present(entity, sentence):
            return True
        headers = [visible(part) for part in context.split("\n\n")[:-1] if part.strip()]
        entity_header = next((header for header in headers if header == visible(entity)
                              or (check.scope == "primary" and re.fullmatch(re.escape(visible(entity))
                                  + r"(?:\s+city)?(?:\s*[:–—-]\s*.+)?", header))), None)
        if entity_header is None:
            return False
        if check.scope == "related":
            following = headers[headers.index(entity_header) + 1:]
            return all(header in {"amenities", "facilities", "overview", "description", "about", "good to know",
                                  "rooms", "services", "location", "reviews", "property amenities"} for header in following)
        return True

    for finding in answer.findings:
        for check in finding.checks:
            if check.status not in ({"unknown", "met"} if replace_assertions else {"unknown"}) or not re.search(budget_words, check.requirement, re.I):
                continue
            if "conflict" in check.reason.casefold():
                continue
            if any(monetary_values(check.requirement, currency) for currency in ("EUR", "USD", "GBP")):
                continue
            if check.scope == "related" and not check.related_entity:
                continue
            cited_urls = {item.source_url for item in check.evidence}
            if replace_assertions:
                check.status, check.statement, check.evidence = "unknown", "", []
                check.reason = "No candidate-specific, non-monetary budget statement was established."
            ordered_sources = sorted(sources, key=lambda source: source["url"] not in cited_urls)
            for source in ordered_sources:
                headings = {visible(match.group(1)) for match in re.finditer(r"^#{1,6}\s+(.+)$", source["text"], re.M)}
                for passage_id, (quote, context) in citation_passages(source).items():
                    if visible(quote) in headings:
                        continue
                    for sentence in re.split(r"(?<=[.!?])\s+", quote):
                        if (sentence.endswith("?") or not re.search(positive, sentence, re.I)
                                or re.search(negative, sentence, re.I)
                                or any(monetary_values(sentence, currency) for currency in ("EUR", "USD", "GBP"))):
                            continue
                        if check.scope == "related" and re.fullmatch(rate_label, sentence, re.I):
                            continue
                        if check.scope == "primary" and names_other_lodging(finding, sentence, context, source):
                            continue
                        has_quality = bool(re.search(qualitative + "|" + cost_less, sentence, re.I))
                        if check.scope == "related" and not has_quality:
                            continue
                        if check.scope == "primary" and not re.search(
                                r"\b(?:is|are|offers?|has|have|costs?|provides?|can|should|serve[sd]?|include[sd]?|dominate[sd]?|saves?|makes?|means?)\b", sentence, re.I):
                            continue
                        if check.scope == "primary" and not has_quality and not re.search(activities, sentence, re.I):
                            continue
                        if re.search(amenities, sentence, re.I) and not re.search(qualitative + r"\s+(?:\w+\s+){0,2}" + cost_subject, sentence, re.I):
                            continue
                        if not bound_context(finding, check, sentence, context):
                            continue
                        proposed = check.model_copy(deep=True)
                        entity = check.related_entity or finding.name
                        proposed.statement = sentence if passage_present(entity, sentence) else f"{entity}: {sentence}"
                        proposed.evidence = [Evidence(source_url=source["url"], quote=sentence, passage_id=passage_id)]
                        evidence = candidate_evidence(finding, proposed, sources)
                        if not evidence:
                            continue
                        check.status, check.statement, check.reason = "met", proposed.statement, ""
                        check.evidence = evidence
                        recovered.append({"candidate": finding.name, "requirement_id": check.requirement_id,
                                          "source_url": source["url"], "review_required": True})
                        break
                    if check.status == "met":
                        break
                if check.status == "met":
                    break
    return recovered


def labelled_current_price(text: str, currency: str) -> set[Decimal] | None:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    token = "(?:" + re.escape(currency) + "|" + re.escape(symbols.get(currency, currency)) + ")"
    number = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)*"
    display_number = number + r"(?:[ \u00a0\u202f]\d{2}(?!\d))?"
    money = "(?:" + token + r"\s*" + number + "|" + display_number + r"\s*" + token + ")"
    current_label = r"\b(?:price\s+now|now|current(?:\s+price)?|sale(?:\s+price)?|offer(?:\s+price)?|nyt|tarjoushinta|price(?=\s*[:=]))"
    previous_label = r"\b(?:regular(?:\s+price)?|was|previous(?:\s+price)?|original(?:\s+price)?|normal(?:\s+price)?|norm(?:aali)?(?:hinta)?|ennen|ovh)"
    def labelled_values(label):
        return set().union(*(monetary_values(match.group(1), currency) for match in re.finditer(
            label + r"\s*[:=]?\s*(" + money + ")", text, re.I)))
    current, previous = labelled_values(current_label), labelled_values(previous_label)
    for match in re.finditer("(" + money + r")\s*\(\s*" + previous_label + r"\s*[:=]?\s*" + money + r"\s*\)", text, re.I):
        current.update(monetary_values(match.group(1), currency))
    excluded = excluded_price_values(text, currency)
    current -= excluded
    if len(current) == 1 and (previous or excluded) and monetary_values(text, currency) <= current | previous | excluded:
        return current
    valid = monetary_values(text, currency) - previous - excluded
    if len(valid) == 1 and excluded:
        return valid
    return None


def excluded_price_values(text: str, currency: str) -> set[Decimal]:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    token = "(?:" + re.escape(currency) + "|" + re.escape(symbols.get(currency, currency)) + ")"
    number = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)*"
    money = token + r"\s*" + number + "|" + number + r"\s*" + token
    excluded = set()
    for line in text.splitlines():
        matches = list(re.finditer(money, line, re.I))
        for index, match in enumerate(matches):
            before = line[matches[index - 1].end() if index else 0:match.start()][-45:]
            after = line[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(line)][:45]
            if (re.search(r"^[\s(,:;]*?/\s*(?:kk|mo)\b|^[\s(,:;]*(?:per month|monthly|kk.erä|instalments?|installments?|alv\.?\s*0\s*%|excl\.?\s*VAT|without VAT|veroton)", after, re.I)
                    or re.search(r"(?:kk.erä|monthly instalment|alv\.?\s*0\s*%|veroton|excl\.?\s*VAT)[\s():=]*$", before, re.I)):
                excluded.update(monetary_values(match[0], currency))
    return excluded


def monetary_range(text: str, currency: str) -> bool:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    token = "(?:" + re.escape(currency) + "|" + re.escape(symbols.get(currency, currency)) + ")"
    number = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)*"
    prefix = token + r"\s*" + number + r"\s*[–—-]\s*(?:" + token + r"\s*)?" + number
    suffix = number + r"\s*(?:" + token + r"\s*)?[–—-]\s*" + number + r"\s*" + token
    middle = number + r"\s*" + token + r"\s*[–—-]\s*(?:" + token + r"\s*)?" + number
    return bool(re.search(prefix + "|" + suffix + "|" + middle, text, re.I))


def apply_budget(check: RequirementCheck, sources: list[dict], budget, *, item_price: bool = False) -> None:
    if check.status == "unknown" or budget.operator == "approx":
        return
    evidence = matching_evidence(check.evidence, sources)
    text = "\n".join(item.quote for item in evidence)
    currency = budget.currency.upper()
    values = labelled_current_price(check.statement, currency) or (monetary_values(check.statement, currency) - excluded_price_values(check.statement, currency))
    quoted_values = labelled_current_price(text, currency) or (monetary_values(text, currency) - excluded_price_values(text, currency))
    if len(quoted_values) > 1:
        check.status, check.statement = "unknown", ""
        check.reason = "Conflicting or ambiguous prices require a matching date, configuration and price basis."
        return
    qualifiers = r"\b(?:from|starting(?: at)?|alkaen|ab)\s*(?:[€$£]|\d)"
    context = "\n".join(evidence_context(item, sources) for item in evidence)
    current_context = labelled_current_price(context, currency)
    excluded_context = excluded_price_values(context, currency)
    basis_words = re.findall(r"\w+", (budget.basis or "").casefold())
    basis_words = [word for word in basis_words if word not in {"per", "a", "an", "the"}]
    if item_price and basis_words == ['total']:
        basis_words = []
    basis_present = all(re.search(r"\b" + re.escape(word) + r"s?\b", visible(context)) for word in basis_words)
    if (len(values) != 1 or values != quoted_values or (current_context and values != current_context)
            or monetary_range(text + " " + check.statement, currency)
            or (values & excluded_context and not current_context)
            or re.search(qualifiers, context + " " + check.statement, re.I) or not basis_present):
        check.status, check.statement = "unknown", ""
        check.reason = "No unambiguous price in the requested currency and price basis."
        return
    price = next(iter(values))
    amount = Decimal(str(budget.amount))
    check.status = "met" if (price < amount if budget.operator == "lt" else price <= amount) else "not_met"
    check.reason = "" if check.status == "met" else "The quoted price does not satisfy the budget boundary."


def canonical_candidate_name(finding: Finding, contract: ResearchContract, sources: list[dict]) -> str:
    finding._identity_alias = None
    if not contract.shopping:
        compound = re.fullmatch(r"(.+?)\s*\(\s*Hotel\s*:\s*(.+?)\s*\)", finding.name, re.I)
        if compound:
            primary, hotel = compound.groups()
            related_ids = {requirement.id for requirement in contract.requirements if requirement.scope == "related"}
            explicit_relation = any(check.requirement_id in related_ids and check.related_entity
                                    and visible(check.related_entity) == visible(hotel) for check in finding.checks)
            sourced_primary = any(source["url"] == finding.source_url and passage_present(primary, source.get("title", "") + "\n" + source["text"])
                                  for source in sources)
            if explicit_relation and sourced_primary:
                finding.name = primary.strip()
    if contract.shopping or not contract.subject:
        return finding.name
    descriptors = set(re.findall(r"\w+", visible(contract.subject)))
    requested_words = set(re.findall(r"\w+", visible(contract.original_request)))
    descriptors.update(requested_words.intersection({"adult", "public", "session", "ticket", "admission", "entry", "visit"}))
    source = next((source for source in sources if source["url"] == finding.source_url), None)
    if not source:
        return finding.name
    name = visible(finding.name)
    for entity in sorted(contract.exact_entities, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(visible(entity)) + r"(?!\w)"
        if not re.search(pattern, name):
            continue
        remainder = re.sub(pattern, "", name, count=1)
        if not set(re.findall(r"\w+", remainder)).issubset(descriptors):
            continue
        direct = passage_present(entity, source.get("title", "") + "\n" + source["text"])
        if contract.destination:
            location = r"(?<!\w)" + re.escape(visible(contract.destination)) + r"(?!\w)"
            alias = re.sub(location, "", visible(entity)).strip(" ,–-()")
            if alias and alias != visible(entity) and passage_present(alias, source.get("title", "")) and passage_present(contract.destination, source["text"]):
                finding._identity_alias = alias
        if direct or finding._identity_alias:
            return entity
    return finding.name


def identity_source(finding: Finding, source: dict) -> bool:
    if source["url"] != finding.source_url:
        return False
    text = source.get("title", "") + "\n" + source["text"]
    return bool(passage_present(finding.name, text) or (finding._identity_alias and passage_present(finding._identity_alias, text)))


def named_hotel_alternative(name: str):
    if re.search(r"(?<!s)hotell?", name, re.I):
        return None
    return re.search(r"(hostell?|guest\s?house|külalistemaja|motell?)", name, re.I)


def hotel_alternative_evidence(name: str, sources: list[dict]) -> tuple[str, Evidence] | None:
    entity = re.escape(visible(name))
    named_alternative = named_hotel_alternative(name)
    alternative = r"(hostel|guesthouse|motel|motell)"
    pattern = re.compile(r"(?<!\w)" + entity + r"\s+(?:is\s+(?:a|an)\s+)?" + alternative + r"\b|"
                         r"\b" + alternative + r"\s+" + entity + r"(?!\w)")
    for source in sources:
        for passage_id, (quote, _) in citation_passages(source).items():
            if named_alternative and (passage_present(name, quote) or passage_present(name, source.get("title", ""))):
                return named_alternative.group(1).lower(), Evidence(
                    source_url=source["url"], quote=quote, passage_id=passage_id)
            match = pattern.search(visible(quote))
            if match:
                return next(group for group in match.groups() if group), Evidence(
                    source_url=source["url"], quote=quote, passage_id=passage_id)
    return None


def direct_location_sentence(candidate: str, destination: str, quote: str) -> str | None:
    entity, place = re.escape(visible(candidate)), re.escape(visible(destination))
    relation = re.compile(
        r"(?<!\w)" + entity + r"(?:\s*,(?:\s*\w+\s*,)?\s*|\s+(?:is\s+)?(?:located\s+|situated\s+)?in\s+(?:\w+\s+){0,2})" + place + r"(?!\w)|"
        r"(?<!\w)" + entity + r"\s*[:–—-]\s*" + place + r"[’']s\b|"
        r"(?<!\w)" + place + r"[’']s\s+" + entity + r"(?!\w)")
    for sentence in re.split(r"(?<=[.!?])\s+", quote):
        if relation.search(visible(sentence)):
            return sentence
    return None


def report_check(check: RequirementCheck, sources: list[dict], fallback: str, *, preserve_failure=False) -> None:
    evidence = matching_evidence(check.evidence, sources)
    if not (preserve_failure and check.status == "not_met"):
        check.status = "unknown"
    if not evidence or not check.statement:
        check.basis, check.statement, check.evidence = "none", "", []
        check.reason = fallback
        return
    by_url = {source['url']: source for source in sources}
    source = by_url[evidence[0].source_url]
    check.evidence, check.basis = evidence, "reported"
    check.observed = source.get('crawled_at') or source.get('observed_at') or source.get('retrieved_at')
    check.reason = ("cached page" if source.get('retrieval') != 'live' or not live_source(source) else "third-party listing")
    if check.observed:
        check.reason += ", crawled " + check.observed[:10] if source.get('crawled_at') else ", observed " + check.observed[:10]


def apply_contract(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract | None) -> GroundedAnswer:
    if not contract:
        contract = ResearchContract(requested_count=answer.requested_count)
        kinds = {finding.source_kind for finding in answer.findings}
        if "product" in kinds:
            contract.activity, contract.shopping = "product", True
        elif "service" in kinds:
            contract.activity = "service"
    answer = answer.model_copy(deep=True)
    answer.requested_count = contract.requested_count
    if contract.activity in {"product", "service"} and not answer.findings:
        answer.response = ""
    by_url = {source["url"]: source for source in sources}
    for finding in answer.findings:
        if contract.subject or contract.shopping:
            finding.name = canonical_candidate_name(finding, contract, sources)
        if contract.shopping:
            product_source = by_url.get(finding.source_url, {})
            finding.condition = detected_condition(finding.source_url or "", product_source.get("title", finding.name))
        if contract.shopping and (comparison_source(finding.source_url or "") or category_source(finding.source_url or "")):
            finding.source_kind = "comparison"
        if contract.activity == "service" and finding.source_kind == "product":
            finding.source_kind = "service"
        if contract.activity in {"product", "service"}:
            # Offer facts must use the same checked fields as eligibility.
            finding.description = finding.assessment = ""
            finding.description_evidence = finding.assessment_evidence = []
        requirements = contract.requirements
        if not requirements:
            requirements = []
            for index, check in enumerate(finding.checks):
                check.requirement_id = check.requirement_id or f"fact_{index}"
                kind = next((kind for kind in ("price", "stock", "delivery", "availability") if kind in check.requirement_id.casefold()), "fact")
                requirements.append(Requirement(id=check.requirement_id, description=check.requirement, kind=kind,
                                                strength=check.strength, scope=check.scope))
        checks = {check.requirement_id: check for check in finding.checks}
        for requirement in requirements:
            if requirement.id not in checks:
                matches = [check for check in finding.checks if not check.requirement_id
                           and visible(check.requirement) == visible(requirement.description)]
                if len(matches) == 1:
                    matches[0].requirement_id = requirement.id
                    checks[requirement.id] = matches[0]
        required = []
        for requirement in requirements:
            check = checks.get(requirement.id)
            if check is None:
                check = RequirementCheck(requirement_id=requirement.id, requirement=requirement.description,
                                         status="unknown", statement="", evidence=[], reason="No supporting passage was provided.")
            check.requirement = requirement.description
            check.strength, check.scope = requirement.strength, requirement.scope
            infer_related_entity(check, sources)
            type_mismatch = False
            if check.scope == "related" and check.related_entity and requirement.description.startswith("Named hotel"):
                alternative = named_hotel_alternative(check.related_entity)
                cited = {item.source_url for item in check.evidence}
                typed = hotel_alternative_evidence(check.related_entity, [source for source in sources if source["url"] in cited])
                if typed:
                    type_mismatch = True
                    check.status = "not_met"
                    check.statement = f"{check.related_entity} is a {typed[0]} alternative."
                    check.reason = "The source explicitly identifies this property as another accommodation type."
                    check.evidence = [typed[1]]
                if alternative:
                    identity = [item for name, item in accommodation_headings(sources)
                                if passage_present(check.related_entity, name)]
                    if identity:
                        type_mismatch = True
                        check.status = "not_met"
                        check.statement = f"{check.related_entity} is a {alternative.group(1).lower()} alternative."
                        check.reason = "The requested hotel type is not established by this accommodation."
                        check.evidence = identity[:1]
            duplicates = [candidate for candidate in finding.checks if candidate.requirement_id == requirement.id]
            price_values = set().union(*(monetary_values(item.quote, contract.budget.currency if contract.budget else "EUR")
                                        for candidate in duplicates for item in matching_evidence(candidate.evidence, sources)))
            conflicting_prices = requirement.kind == "price" and len(duplicates) > 1 and len(price_values) > 1
            if conflicting_prices or len({candidate.status for candidate in duplicates if candidate.status != "unknown"}) > 1:
                check.evidence = list({(item.source_url, item.quote, item.passage_id): item
                                       for candidate in duplicates for item in candidate.evidence}.values())
                check.status = "unknown"
                check.basis = "conflicting"
                check.reason = "Conflicting evidence for this requirement."
            if (check.status != "unknown" or check.basis in {"reported", "conflicting"}) and not type_mismatch:
                check.evidence = candidate_evidence(finding, check, sources, requirement.kind)
                if not check.evidence:
                    check.status, check.statement = "unknown", ""
                    check.basis = "none"
                    check.reason = "No matching passage establishes this fact for the exact candidate or named related entity."
            if check.status != "unknown" or check.basis in {"reported", "conflicting"}:
                check.evidence = viable_check_evidence(finding, check, sources, contract, requirement)
                if not check.evidence:
                    check.status, check.statement, check.basis = "unknown", "", "none"
                    check.reason = "No viable primary or candidate-specific source establishes this fact."
            if (check.status != "unknown" or check.basis == "reported") and not type_mismatch:
                aligned = alignment_ok(check.value or check.statement, check.evidence, sources, proper_names=bool(check.value))
                if not aligned:
                    rejection = {"candidate": finding.name, "requirement_id": requirement.id,
                                 "value": check.value or check.statement}
                    if rejection not in answer.alignment_rejections:
                        answer.alignment_rejections.append(rejection)
                    check.status, check.statement, check.evidence, check.basis = "unknown", "", [], "none"
                    check.reason = "Extracted value adds a number or name absent from its cited passage."
            if (check.status == "met" and check.scope == "primary" and requirement.description.startswith("Located in ")
                    and check.statement.startswith(finding.name + ":")
                    and not any(direct_location_sentence(finding.name, requirement.description.removeprefix("Located in "), item.quote)
                                for item in check.evidence)):
                check.status, check.statement, check.evidence = "unknown", "", []
                check.reason = "The recovered passage does not directly locate this candidate."
            monetary_claims = {currency: monetary_values(check.statement, currency) for currency in ("EUR", "USD", "GBP")}
            dynamic_context = " ".join([requirement.description, contract.subject or "", contract.objective])
            dynamic_money = any(monetary_claims.values()) and (requirement.scope == "related" or contract.activity in {"product", "service"}
                or requirement.kind == "price" or bool(re.search(r"\b(?:price|cost|affordable|cheap|hotel|lodging|accommodation|travel|holiday|trip|food|meal|room|ticket|admission)\b", dynamic_context, re.I)))
            if check.status != "unknown" and (dynamic_money or ((contract.activity == "service" or requirement.scope == "related") and requirement.kind in {"price", "availability"})):
                evidence = [e for e in matching_evidence(check.evidence, sources)
                            if live_source(by_url[e.source_url])]
                if dynamic_money:
                    current_quotes = "\n".join(item.quote for item in evidence)
                    if any(values and not values.issubset(monetary_values(current_quotes, currency))
                           for currency, values in monetary_claims.items()):
                        evidence = []
                wrong_offer = contract.activity == "service" and requirement.scope == "primary" and requirement.kind in {"price", "availability"} and finding.source_kind != "service"
                if wrong_offer or not evidence:
                    report_check(check, sources, "No current venue or booking evidence for this check.")
                else:
                    check.evidence = evidence
            if check.status != "unknown" and contract.shopping and requirement.kind in {"price", "stock", "availability", "delivery"}:
                product = by_url.get(finding.source_url, {})
                evidence = matching_evidence(check.evidence, sources)
                if requirement.kind in {"price", "stock", "availability"}:
                    evidence = [e for e in evidence if e.source_url == finding.source_url
                                and live_source(product)]
                    if requirement.kind in {"stock", "availability"}:
                        evidence = [e for e in evidence if current_stock_evidence(e, product)]
                else:
                    evidence = [e for e in evidence if source_domain(e.source_url) == source_domain(finding.source_url or "")
                                and delivery_source_current(by_url[e.source_url])]
                    if contract.destination:
                        evidence = [e for e in evidence if destination_evidence(e, by_url[e.source_url], contract.destination)]
                # Reviews/comparison pages and search snippets cannot establish a purchasable offer.
                if finding.source_kind != "product" or not evidence:
                    report_check(check, sources, "No matching current retailer evidence for this check.")
                else:
                    check.evidence = evidence
            if contract.budget and requirement.id == contract.budget.requirement_id:
                apply_budget(check, sources, contract.budget, item_price=contract.shopping)
            opening_hours = re.search(r"\b(?:opening|closing|business hours|hours (?:on|for)|open (?:on|until|from)|aukiolo\w*)\b", requirement.description, re.I)
            if check.status != "unknown" and opening_hours:
                fresh = [e for e in check.evidence if live_source(by_url[e.source_url])]
                if fresh:
                    check.evidence = fresh
                else:
                    report_check(check, sources, "Opening hours require a current operator observation.")
            if check.status != "unknown" and check.evidence:
                primary = [e for e in check.evidence if source_class(by_url[e.source_url], contract) in {"official", "retailer"}]
                if task_kind(contract) in {"legal", "benefits"}:
                    primary = [e for e in primary if source_class(by_url[e.source_url], contract) == "official"]
                if not primary or check.scope == "related" and len(primary) != len(check.evidence):
                    report_check(check, sources, "No primary evidence for this fact.", preserve_failure=True)
                    check.source_label = "via " + source_domain(check.evidence[0].source_url) if check.evidence else ""
                elif primary:
                    check.evidence = primary
            if check.evidence:
                first = by_url[check.evidence[0].source_url]
                check.observed = first.get("crawled_at") or first.get("observed_at") or first.get("retrieved_at")
                if check.basis == "reported" and not check.source_label:
                    check.source_label = "via " + source_domain(first["url"])
            if contract.shopping and requirement.kind == "stock":
                domain = source_domain(finding.source_url or "")
                domain_sources = [s for s in sources if source_domain(s["url"]) == domain]
                stock_seen = any(explicit_stock(s.get("text", "")) for s in domain_sources)
                stock_cache = PersistentCache("stock_domains")
                if stock_seen:
                    stock_cache.put(domain, True, 7 * 86400)
                check.refinement_excluded = not stock_seen and not stock_cache.get(domain)
                if check.status == "unknown" and check.refinement_excluded:
                    check.reason = "Stock shown at checkout"
            if check.status != "unknown" and check.basis != "reported":
                check.basis = "confirmed"
            elif check.basis == "reported" and (not check.statement or not matching_evidence(check.evidence, sources)):
                check.basis, check.statement, check.evidence = "none", "", []
            elif check.basis == 'reported':
                report_check(check, sources, 'No supporting passage was provided.', preserve_failure=True)
            elif check.basis not in {"reported", "conflicting"}:
                check.basis = "conflicting" if check.evidence and "conflict" in check.reason.casefold() else "none"
            required.append(check)
        finding.checks = required or finding.checks
        if not required:
            for check in finding.checks:
                if check.status != "unknown":
                    check.evidence = candidate_evidence(finding, check, sources)
                    if not check.evidence:
                        check.status, check.statement = "unknown", ""
                        check.reason = "No matching passage establishes this fact for the exact candidate."
    if contract.shopping:
        answer.findings = [finding for finding in answer.findings if finding.source_kind == "product"]
        def rank(finding):
            return (
            any(c.status == "not_met" and c.strength == "hard" for c in finding.checks),
            finding.condition != "new",
            -sum(c.status == "met" and c.strength == "hard" and bool(matching_evidence(c.evidence, sources)) for c in finding.checks),
            min((QUALITY_RANK[source_class(by_url[e.source_url], contract)] for c in finding.checks for e in c.evidence if e.source_url in by_url), default=5),
            -sum(c.status == "met" and c.strength == "preference" for c in finding.checks),
            -sum(c.basis == "reported" and bool(matching_evidence(c.evidence, sources)) for c in finding.checks),
            )
        answer.findings.sort(key=rank)
        # Keep verified options first, spreading equally supported candidates across sellers.
        ranked = []
        for score in sorted({rank(finding) for finding in answer.findings}):
            groups = {}
            for finding in answer.findings:
                if rank(finding) == score:
                    groups.setdefault(source_domain(finding.source_url or ""), []).append(finding)
            while any(groups.values()):
                for group in groups.values():
                    if group:
                        ranked.append(group.pop(0))
        domains = {source_domain(f.source_url or "") for f in ranked if not any(c.status == "not_met" and c.strength == "hard" for c in f.checks)}
        cap = 1 if len(domains) >= (contract.requested_count or 3) else 2
        counts = {}
        answer.findings = []
        for finding in ranked:
            domain = source_domain(finding.source_url or "")
            brand = domain.split(".")[-2] if "." in domain else domain
            named_seller = bool(brand and re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", contract.original_request, re.I))
            if counts.get(domain, 0) >= cap and not named_seller:
                continue
            counts[domain] = counts.get(domain, 0) + 1
            answer.findings.append(finding)
        if contract.requested_count:
            answer.findings = answer.findings[:contract.requested_count]
    else:
        answer.findings.sort(key=lambda f: (any(c.status == "not_met" and c.strength == "hard" for c in f.checks),
            -sum(c.status == "met" and c.strength == "hard" for c in f.checks),
            -sum(c.status == "met" and c.strength == "preference" for c in f.checks)))
    return answer


def finding_coverage(finding: Finding, sources: list[dict]) -> tuple[bool, bool]:
    supported_checks = [check.status for check in finding.checks if check.strength == "hard"
                        and check.status != "unknown" and matching_evidence(check.evidence, sources)]
    identity = any(identity_source(finding, source) for source in sources)
    named_evidence = any(passage_present(finding.name, evidence_context(item, sources))
                         for item in matching_evidence([*finding.description_evidence,
                             *[item for check in finding.checks for item in check.evidence]], sources))
    retained = bool(identity or named_evidence)
    complete = bool(retained and finding.checks and len(supported_checks) == sum(check.strength == "hard" for check in finding.checks)
                    and all(status == "met" for status in supported_checks))
    return retained, complete


def answer_coverage(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract | None = None) -> dict:
    answer = apply_contract(answer, sources, contract)
    coverage = [finding_coverage(finding, sources) for finding in answer.findings]
    return {"retained": sum(retained for retained, _ in coverage),
            "verified": sum(complete for _, complete in coverage),
            "requested": answer.requested_count,
            "offer_domains": len({source_domain(f.source_url) for f in answer.findings if f.source_kind in {"product", "service"} and f.source_url}),
            "supported_checks": sum(check.status == "met" and bool(matching_evidence(check.evidence, sources)) for f in answer.findings for check in f.checks),
            "supported_hard_checks": sum(check.status == "met" and check.strength == "hard" and bool(matching_evidence(check.evidence, sources)) for f in answer.findings for check in f.checks),
            "gaps": [{"name": f.name, "source_url": f.source_url, "source_kind": f.source_kind,
                      "requirements": [c.requirement for c in f.checks if c.strength == "hard" and not c.refinement_excluded and (c.status != "met" or not matching_evidence(c.evidence, sources))]}
                     for f in answer.findings if not finding_coverage(f, sources)[1]]}


def links(evidence: list[Evidence]) -> str:
    urls = dict.fromkeys(item.source_url for item in evidence)
    return " ".join("[Source](<" + quote_url(url, safe=":/?=&%#@+;,-._~") + ">)" for url in urls)


def sufficient_reported_answer(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract) -> bool:
    answer = apply_contract(answer, sources, contract)
    requirements = {r.id: r for r in contract.requirements}
    sufficient = 0
    for finding in answer.findings:
        if not finding_coverage(finding, sources)[0]:
            continue
        hard = [c for c in finding.checks if c.strength == 'hard']
        if hard and all(matching_evidence(c.evidence, sources) and
                       (c.status == 'met' or c.status == 'unknown' and c.basis == 'reported'
                        and requirements[c.requirement_id].kind in {'stock', 'availability', 'delivery'}) for c in hard):
            sufficient += 1
    return sufficient >= (contract.requested_count or 1)


def check_text(check: RequirementCheck, sources: list[dict]) -> str:
    evidence = matching_evidence(check.evidence, sources)
    if check.basis == "reported" and evidence and check.statement:
        details = check.source_label or check.reason or "secondary source"
        if check.observed:
            details += ", as of " + check.observed[:10]
        failure = "Does not meet requirement. " if check.status == "not_met" and check.strength == "hard" else ""
        return "Reported: " + failure + plain(check.statement) + " (" + plain(details) + ") " + links(evidence) + ". Confirm before buying or booking."
    if evidence and (check.basis == "conflicting" or "conflict" in check.reason.casefold()):
        details = " vs ".join(dict.fromkeys(item.quote for item in evidence)) if len(evidence) > 1 else check.statement or evidence[0].quote
        return "Conflicting: " + plain(details) + " " + links(evidence) + ". Confirm with the provider."
    if check.status == "unknown" or not evidence:
        return ""
    label = ""
    if check.status == "not_met":
        label = "**Preference not met**. " if check.strength == "preference" else "**Does not meet requirement**. "
    statement = plain(check.statement)
    for value in re.findall(r'https?://[^\s<>"\']+', check.statement):
        url = value.rstrip('.,;:!?)]}')
        if any(url in evidence_context(item, sources) for item in evidence):
            target = quote_url(url, safe=":/?=&%#@+;,-._~")
            statement = statement.replace(plain(url), f"[{plain(url)}](<{target}>)")
    volatile = bool(monetary_values(check.statement, "EUR") or re.search(r"stock|opening|hours|availability|price", check.requirement, re.I))
    observed = " (as of " + plain(check.observed[:10]) + ")" if volatile and check.observed else ""
    return label + statement + observed + " " + links(evidence)


def comparison_table(findings: list[Finding], sources: list[dict]) -> str:
    hide_type = all(any(check.requirement_id == "requested_type" and check.status == "met"
                        and matching_evidence(check.evidence, sources) for check in finding.checks) for finding in findings)
    columns = dict.fromkeys(check.requirement for finding in findings for check in finding.checks
                            if check_text(check, sources) and not (hide_type and check.requirement_id == "requested_type"))
    headings = ["Option", *[plain(label) for label in columns]]
    rows = ["| " + " | ".join(headings) + " |", "| " + " | ".join("---" for _ in headings) + " |"]
    for finding in findings:
        name = plain(finding.name)
        if finding.condition:
            name += " (" + finding.condition + ")"
        if any(identity_source(finding, source) for source in sources):
            name += " " + links([Evidence(source_url=finding.source_url, quote=finding.name)])
        checks = {check.requirement: check for check in finding.checks}
        cells = [check_text(checks[label], sources) if label in checks else "" for label in columns]
        rows.append("| " + " | ".join([name, *cells]) + " |")
    return "\n".join(rows)


def confirmation_line(finding: Finding, sources: list[dict], omit=()) -> str:
    missing = [check for check in finding.checks if check.strength == "hard" and check.status != "not_met"
               and (check.status != "met" or not matching_evidence(check.evidence, sources))
               and check.requirement not in omit]
    if not missing:
        return ""
    labels = ["stock shown at checkout" if c.refinement_excluded and "stock" in c.requirement_id
              else c.requirement for c in missing]
    link = links([Evidence(source_url=finding.source_url, quote=finding.name)]) if any(identity_source(finding, s) for s in sources) else ""
    return "**To confirm for " + plain(finding.name) + ":** " + plain("; ".join(dict.fromkeys(labels))) + ". " + link


def render_knowledge(knowledge: KnowledgeAnswer, sources: list[dict], contract=None) -> str:
    if not any(matching_evidence(point.evidence, sources) for point in knowledge.points):
        return ""
    sections = []
    if knowledge.verdict:
        sections.append(plain(knowledge.verdict))
    lookup = task_kind(contract) == "business"
    table = ["| Field | Information | Source |", "| --- | --- | --- |"] if lookup else []
    for point in knowledge.points:
        evidence = matching_evidence(point.evidence, sources)
        if evidence:
            label = ""
            if point.basis == "reported":
                detail = point.source_label or "secondary source"
                if point.observed:
                    detail += ", as of " + point.observed[:10]
                label = "Reported (" + plain(detail) + "): "
            if lookup:
                fields = [("Business ID", r"business id|y-tunnus|\b\d{7}-\d\b"),
                          ("Registered address", r"registered address"), ("Line of business", r"line of business"),
                          ("CEO", r"\bCEO\b|chief executive")]
                field = next((name for name, pattern in fields if re.search(pattern, point.statement, re.I)), "Detail")
                table.append("| " + field + " | " + label + plain(point.statement) + " | " + links(evidence) + " |")
            else:
                sections.append("- " + label + plain(point.statement) + " " + links(evidence))
    if lookup:
        sections.append("\n".join(table))
    if knowledge.limited_evidence:
        sections.append("The available evidence supports only part of the answer.")
    if knowledge.steps:
        sections.append("What to do next:\n\n" + "\n".join(f"{i}. {plain(step)}" for i, step in enumerate(knowledge.steps, 1)))
    else:
        sections.append("Next step: open the cited source for the details that apply to your situation.")
    if knowledge.draft:
        sections.append("Draft (not sent):\n\n" + "\n".join("> " + plain(line) for line in knowledge.draft.splitlines()))
    if knowledge.caveats:
        sections.append("\n\n".join(plain(caveat) for caveat in knowledge.caveats))
    return "\n\n".join(sections)


def render_answer(answer: GroundedAnswer, sources: list[dict], contract: ResearchContract | None = None) -> tuple[str, list[str]]:
    answer = apply_contract(answer, sources, contract)
    sections = []
    issues = []
    retained = 0
    retained_findings = []
    for finding in answer.findings:
        rows = []
        description_evidence = matching_evidence(finding.description_evidence, sources)
        if finding.description and description_evidence:
            rows.append(plain(finding.description) + " " + links(description_evidence))
        elif finding.description:
            issues.append(f"{finding.name}: no matching evidence for description")
        for check in finding.checks:
            evidence = matching_evidence(check.evidence, sources)
            text = check_text(check, sources)
            if text:
                rows.append(f"- {plain(check.requirement)}: {text}")
            elif check.status != "unknown":
                issues.append(f"{finding.name}: no matching evidence for {check.requirement}")
        keep, complete = finding_coverage(finding, sources)
        if keep:
            retained += 1
            retained_findings.append(finding)
            assessment_evidence = matching_evidence(finding.assessment_evidence, sources)
            if complete and finding.assessment and assessment_evidence:
                rows.append("Assessment: " + plain(finding.assessment) + " " + links(assessment_evidence))
            if finding.source_url and any(identity_source(finding, source) for source in sources):
                rows.append(links([Evidence(source_url=finding.source_url, quote=finding.name)]))
            label = "" if complete else " (option to consider)"
            if any(check.status == "not_met" and check.strength == "hard" and matching_evidence(check.evidence, sources) for check in finding.checks):
                label = " (does not meet all requirements)"
            if finding.condition:
                label = " (" + finding.condition + ")" + label
            confirm = confirmation_line(finding, sources)
            if confirm:
                rows.append(confirm)
            sections.append(f"### {retained}. {plain(finding.name)}{label}\n\n" + "\n\n".join(rows))
        else:
            issues.append(f"{finding.name}: omitted because no checks had matching evidence")
        if answer.requested_count and retained >= answer.requested_count:
            break
    if retained_findings and contract and contract.answer_format == "table":
        sections = [comparison_table(retained_findings, sources)]
        common = set.intersection(*[{c.requirement for c in f.checks if c.strength == "hard" and c.status != "not_met"
                                   and not check_text(c, sources)} for f in retained_findings])
        if common:
            common_labels = ["stock shown at checkout" if any(c.requirement == label and c.refinement_excluded
                            for f in retained_findings for c in f.checks) else label for label in sorted(common)]
            sections.append("**To confirm for all options:** " + plain("; ".join(common_labels)) + ". " + " ".join(
                links([Evidence(source_url=f.source_url, quote=f.name)]) for f in retained_findings if any(identity_source(f, s) for s in sources)))
        sections.extend(line for f in retained_findings if (line := confirmation_line(f, sources, common)))
    if retained_findings:
        best = next((f for f in retained_findings if not any(c.status == "not_met" and c.strength == "hard" for c in f.checks)
                     and any(c.status == "met" and matching_evidence(c.evidence, sources) for c in f.checks)), None)
        if best:
            fact = next(c for c in best.checks if c.status == "met" and matching_evidence(c.evidence, sources))
            sections.insert(0, "Best pick: " + plain(best.name) + " because " + plain(fact.statement.rstrip(".!")) + ". " + links(fact.evidence))
        sections.append("Next step: open the provider link and confirm the outstanding details before buying or booking.")
    if not sections:
        if answer.knowledge and not answer.findings:
            text = render_knowledge(answer.knowledge, sources, contract)
            if text:
                sections.append(text)
        if not sections and answer.response and not answer.findings:
            sections.append(answer.response)
        elif not sections and sources:
            knowledge_task = bool(contract and contract.activity == "research" and not contract.requested_count)
            sections.append("I could not confirm the requested facts from the available evidence. These sources may help resolve the remaining questions."
                            if knowledge_task else "I couldn't verify matching options yet. These provider links may help confirm suitability.")
            viable_sources = sorted([s for s in sources if source_class(s, contract) not in {"seo", "off_target"}],
                                    key=lambda s: QUALITY_RANK[source_class(s, contract)])
            for source in viable_sources[:answer.requested_count or 5]:
                url = source["url"].replace("(", "%28").replace(")", "%29")
                sections.append(f"- [{plain(source.get('title') or source['url'])}](<{url}>)")
            issues.append("No validated findings; showing retrieved leads instead")
        elif not sections:
            sections.append("I couldn't retrieve usable pages for this request. Try a broader search or share a listing to check.")
    # Completeness is determined by checked evidence, not generated summary prose.
    limitations = []
    if issues or any(not finding_coverage(finding, sources)[1] for finding in answer.findings):
        checks = [c for f in retained_findings for c in f.checks]
        confirmed = sum(c.basis == 'confirmed' and bool(matching_evidence(c.evidence, sources)) for c in checks)
        reported = sum(c.basis == 'reported' and bool(matching_evidence(c.evidence, sources)) for c in checks)
        conflicting = sum((c.basis == 'conflicting' or 'conflict' in c.reason.casefold()) and bool(matching_evidence(c.evidence, sources)) for c in checks)
        limitations.append(f"{confirmed} confirmed, {reported} reported, {conflicting} conflicting.")
    if answer.requested_count and retained and retained < answer.requested_count:
        limitations.append(f"Found {retained} sourced options of the {answer.requested_count} requested.")
    if limitations:
        sections.append("Evidence limits:\n\n" + "\n".join(f"- {plain(item)}" for item in dict.fromkeys(limitations)))
    return "\n\n".join(sections), issues
