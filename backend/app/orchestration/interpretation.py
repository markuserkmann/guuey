import re
from datetime import UTC, datetime

from app.orchestration.contracts import Budget, ResearchContract, SearchQuery, Requirement


RESULT_NOUNS = (r"laptops?|computers?|phones?|tablets?|headphones?|monitors?|products?|offers?|"
                r"hotels?|restaurants?|cafes?|saunas?|providers?|services?|destinations?|spots?|"
                r"places?|cities|towns|stores?|shops?|retailers?|suppliers?|options?|alternatives?|recommendations?|courses?|books?|games?")


def result_subjects(text: str) -> set[str]:
    return {match[0].casefold().removesuffix("s") for match in re.finditer(r"\b(?:" + RESULT_NOUNS + r")\b", text, re.I)}


def purchase_quantity_quote(quote: str, request: str) -> bool:
    for match in re.finditer(re.escape(quote), request, re.I):
        if re.search(r"\b(?:pick[ -]?up|collect|purchase|buy|selling|stocking)\s+(?:\w+\s+){0,3}$",
                     request[:match.start()], re.I):
            return True
    return False


def requested_result_count(text: str) -> tuple[int, str] | None:
    words = {word: index for index, word in enumerate(
        ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"), 1)}
    count = r"(?:100|[1-9]\d?|" + "|".join(words) + r")"
    matches = [match for match in re.finditer(r"\b(" + count + r")\s+(?:[\w-]+\s+){0,4}?(?:" + RESULT_NOUNS + r")\b", text, re.I)
               if not purchase_quantity_quote(match[0], text)]
    if len(matches) != 1:
        return None
    match = matches[0]
    value = words.get(match[1].casefold()) or int(match[1])
    # A hardware capacity, duration or party size must never become the result count.
    if re.search(r"\b(?:GB|TB|adults?|children|people|persons?|guests?|nights?|days?|weeks?|hours?|minutes?)\b", match[0], re.I):
        return None
    return value, match[0]


def preserve_request(contract: ResearchContract, original: str) -> ResearchContract:
    contract = contract.model_copy(deep=True)
    specification_checks = [requirement for requirement in contract.requirements
                            if requirement.scope == "primary" and re.search(
                                r"\b(?:RAM|SSD|HDD|CPU|GPU|processor|storage|memory|operating system|model|SKU)\b",
                                requirement.description, re.I)]
    requirements = []
    for requirement in contract.requirements:
        description = requirement.description.strip().rstrip(".")
        if requirement.scope == "primary" and requirement.kind == "fact":
            if re.fullmatch(r"(?:(?:seller|retailer|product|purchase)\s+(?:links?|URLs?)"
                            r"(?:\s+(?:provided|included|available|shown))?|(?:links?|URLs?)\s+to\s+(?:the\s+)?(?:seller|retailer|product))",
                            description, re.I):
                continue
            if re.fullmatch(r"(?:exact|full)\s+(?:product\s+)?configuration(?:\s+details)?"
                            r"(?:\s+(?:provided|included|shown|specified))?", description, re.I):
                if specification_checks:
                    continue
                requirement.description = "Exact model or SKU and configuration of this offer"
        requirements.append(requirement)
    contract.requirements = requirements
    destination_shortlist = bool(contract.subject and re.fullmatch(
        r'(?:holiday|vacation|travel)\s+(?:spots?|destinations?)|places?\s+to\s+visit', contract.subject, re.I))
    if destination_shortlist and not re.search(r'\b(?:book|reserve|reservation)\b', original, re.I):
        contract.activity, contract.shopping = 'research', False
        for requirement in contract.requirements:
            if requirement.scope == 'primary' and requirement.description.startswith('Located in or provides service in '):
                requirement.description = 'Located in ' + (contract.destination or '')
    contract.original_request = original
    contract.request_time = contract.request_time or datetime.now(UTC).isoformat()
    if not contract.objective:
        contract.objective = original
    if re.search(r"\btable\b", original, re.I):
        contract.answer_format = "table"
    result_count = requested_result_count(original)
    quantity_quote = contract.count_quote if contract.count_quote and purchase_quantity_quote(contract.count_quote, original) else None
    if quantity_quote:
        if not any(contract.count_quote.casefold() in requirement.description.casefold()
                   for requirement in contract.requirements):
            identifier = 'purchase_quantity'
            while any(requirement.id == identifier for requirement in contract.requirements):
                identifier += '_item'
            contract.requirements.append(Requirement(id=identifier, description='Quantity to purchase: ' + contract.count_quote))
    if result_count:
        contract.requested_count, contract.count_quote = result_count
    elif quantity_quote:
        contract.requested_count = contract.count_quote = None
    hotels_requested = re.search(r"\bhotels?\b", original, re.I) and not re.search(
        r"\b(?:hostels?|guesthouses?|accommodation|apartments?)\b", original, re.I)
    if hotels_requested:
        for requirement in contract.requirements:
            if requirement.scope == "related" and re.search(r"\b(?:hotel|accommodation)\b", requirement.description, re.I):
                requirement.description = "Named hotel located in this destination" if re.search(
                    r"\b(?:inside|within|in the heart of|in the old town)\b", original, re.I
                ) else "Named hotel for this destination, with its location or proximity stated"
                requirement.strength = "hard"
                break
        if any(r.scope == 'related' and r.description.startswith('Named hotel') for r in contract.requirements):
            contract.requirements = [r for r in contract.requirements if not (r.scope == 'related' and re.fullmatch(
                r'(?:hotel\s+)?location(?:\s+details)?\s+(?:provided|included|shown)', r.description, re.I))]
        if re.search(r"\bbudget\b", original, re.I) and not contract.budget and not any(
            r.id == "hotel_value" or (r.scope == "related" and re.search(
                r"\b(?:budget|economical|affordable|cheap|low.cost)\b", r.description, re.I))
            for r in contract.requirements
        ):
            contract.requirements.append(Requirement(id="hotel_value", description="Budget-friendly hotel features or positioning",
                                                     strength="preference", scope="related"))
    if contract.subject and any(r.scope == "related" for r in contract.requirements):
        parts = re.split(r"\s+(?:and|with)\s+", contract.subject, maxsplit=1, flags=re.I)
        if len(parts) == 2 and re.search(r"\bhotels?\b", parts[1], re.I) and re.search(
            r"\b(?:destinations?|spots?|places?|cities|towns)\b", parts[0], re.I
        ):
            contract.subject = parts[0]
            contract.requirements = [r for r in contract.requirements if r.id != "requested_type"]
    if contract.anchor and contract.anchor.casefold() in {entity.casefold() for entity in contract.exact_entities} and not re.search(
        r"\b(?:near|nearby|within|close to|walking distance|lähellä)\b", original, re.I
    ):
        contract.anchor = None
    if contract.clarification_question and re.search(r"\b(?:date|dates|when|check.in)\b", contract.clarification_question, re.I):
        if not re.search(r"\b(?:book|reserve|schedule|make a reservation)\b", original, re.I):
            contract.clarification_question = None
    # Pin unambiguous numeric bounds to the user's wording, not the search paraphrase.
    amounts = list(re.finditer(
        r"\b(under|below|less than|at most|up to|no more than|around|about|approximately)\s*"
        r"(?:(€|\$|£|EUR|USD|GBP)\s*(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(EUR|USD|GBP|euros?|dollars?|pounds?))\b(?![.,]\d)",
        original, re.I,
    ))
    # A currency-free amount is only a budget when the surrounding wording says so.
    bare_amounts = list(re.finditer(
        r"\b(?:price|budget|cost)\s*(?:is|of|:|=)?\s*"
        r"(under|below|less than|at most|up to|no more than|around|about|approximately)\s*"
        r"(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)(?![\w.,])", original, re.I))
    declared_budgets = list(re.finditer(
        r"\bbudget\s*(?:(?:is|of|now|to|:|=)\s*)*"
        r"(?:(€|\$|£|EUR|USD|GBP)\s*)?(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
        r"(?:\s*(EUR|USD|GBP|euros?|dollars?|pounds?))?(?![\w.,])", original, re.I))
    if len(amounts) > 1 and re.search(r"\b(?:actually|instead|now|change|raise|increase|lower|reduce|new budget)\b",
                                                    original[amounts[-2].end():amounts[-1].start()], re.I):
        amounts = amounts[-1:]
    if len(amounts) == 1 or (not amounts and len(bare_amounts) == 1) or (not amounts and not bare_amounts and len(declared_budgets) == 1):
        if amounts:
            qualifier, prefix, before, after, suffix = amounts[0].groups()
        elif bare_amounts:
            qualifier, before = bare_amounts[0].groups()
            prefix = after = suffix = None
        else:
            prefix, before, suffix = declared_budgets[0].groups()
            qualifier, after = "up to", None
        currency = (prefix or suffix or (contract.budget.currency if contract.budget else "EUR")).upper()
        currency = {"€": "EUR", "$": "USD", "£": "GBP", "EURO": "EUR", "EUROS": "EUR",
                    "DOLLAR": "USD", "DOLLARS": "USD", "POUND": "GBP", "POUNDS": "GBP"}.get(currency, currency)
        operator = "lt" if qualifier.lower() in {"under", "below", "less than"} else "approx" if qualifier.lower() in {"around", "about", "approximately"} else "lte"
        existing = contract.budget
        price = next((r for r in contract.requirements if r.kind == "price"), None)
        number = before or after
        if "," in number and "." in number:
            decimal = "," if number.rfind(",") > number.rfind(".") else "."
            number = number.replace("." if decimal == "," else ",", "").replace(decimal, ".")
        elif "," in number or "." in number:
            separator = "," if "," in number else "."
            parts = number.split(separator)
            number = "".join(parts) if len(parts[-1]) == 3 else "".join(parts[:-1]) + "." + parts[-1]
        contract.budget = Budget(amount=float(number), currency=currency,
            operator=operator, basis=existing.basis if existing else None,
            requirement_id=existing.requirement_id if existing else price.id if price else "price")
    if not contract.budget and re.search(r"\bbudget\b", original, re.I) and not re.search(
        r"\b(?:prices?|rates?|costs?)\b", original, re.I
    ):
        for requirement in contract.requirements:
            vague_budget = bool(re.search(r'\bon\s+(?:a|an)\s+budget\b', original, re.I)
                                and not re.search(r'\b(?:must|only|required)\b', original, re.I))
            if (requirement.kind == "price" or vague_budget and requirement.kind in {'fact', 'type'}) and re.search(
                r"\b(?:budget|economical|affordable|cheap|low.cost)\b", requirement.description, re.I
            ):
                requirement.kind, requirement.strength = "fact", "preference"
    validated = ResearchContract.model_validate(contract.model_dump())
    order = {requirement.id: index for index, requirement in enumerate(contract.requirements)}
    validated.requirements.sort(key=lambda requirement: order.get(requirement.id, len(order)))
    return validated


def permitted_filters(query: SearchQuery, original: str) -> SearchQuery:
    query = query.model_copy(deep=True)
    quote = query.filter_quote or ""
    authorized = bool(quote and quote.casefold() in original.casefold())
    if not authorized:
        query.include_domains = []
        query.exclude_domains = []
        query.start_published_date = query.end_published_date = query.category = None
    else:
        include = bool(re.search(r"\b(?:only|exclusively|restrict|limited to)\b|\bsite:", quote, re.I))
        query.include_domains = [d for d in query.include_domains if include and re.search(
            r"(?<![\w.-])" + re.escape(d) + r"(?![\w.-])", quote, re.I)]
        query.exclude_domains = [d for d in query.exclude_domains if re.search(
            r"(?:exclude|excluding|do not use|don't use|avoid results from)\s+(?:(?:results|sources) from\s+)?" + re.escape(d), quote, re.I)]
        publication_window = bool(re.search(r"\b(?:published|publication|articles?|news|papers?|reports?)\b", quote, re.I)
                                  and re.search(r"\b(?:between|since|after|before|last|past|from|during|in)\b", quote, re.I)
                                  and re.search(r"\d|\b(?:week|month|year|days|yesterday|today)\b", quote, re.I))
        if not publication_window:
            query.start_published_date = query.end_published_date = None
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", quote)
        if dates:
            for field in ("start_published_date", "end_published_date"):
                if getattr(query, field) and getattr(query, field)[:10] not in dates:
                    setattr(query, field, None)
        category_words = {"news": r"\bnews\b", "publication": r"\b(?:publications?|research papers?|scientific papers?|journal articles?)\b",
                          "company": r"\bcompan(?:y|ies)\b", "people": r"\b(?:people|person|profiles?)\b",
                          "personal site": r"\bpersonal (?:sites?|websites?)\b", "financial report": r"\bfinancial reports?\b"}
        if query.category and not re.search(category_words[query.category], quote, re.I):
            query.category = None
    if query.category in {"company", "people"}:
        query.start_published_date = query.end_published_date = None
    if query.category == "people":
        query.exclude_domains = []
    for field in ("include_domains", "exclude_domains"):
        setattr(query, field, [domain for domain in getattr(query, field)
                             if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", domain, re.I)])
    for field in ("start_published_date", "end_published_date"):
        if getattr(query, field):
            try:
                datetime.fromisoformat(getattr(query, field).replace("Z", "+00:00"))
            except ValueError:
                setattr(query, field, None)
    if query.start_published_date and query.end_published_date and query.start_published_date[:10] > query.end_published_date[:10]:
        query.start_published_date = query.end_published_date = None
    return query


def query_key(query: SearchQuery) -> str:
    data = query.model_dump(exclude={"objective", "gap", "filter_quote"})
    data["query"] = " ".join(query.query.casefold().split())
    for key in ("include_domains", "exclude_domains"):
        data[key] = sorted(data[key])
    import json
    return json.dumps(data, sort_keys=True, ensure_ascii=False)
