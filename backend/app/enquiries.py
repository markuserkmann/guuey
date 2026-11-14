"""Prepare service enquiries from checked findings; no sending connector."""
import hashlib
import json
import re

from app.orchestration.grounding import GroundedAnswer, finding_coverage, plain


def prepare_enquiry(message, result, run_id):
    contract = result.debug.get('research_contract') or {}
    if contract.get('activity') != 'service' or not re.search(r'\b(prepare|draft)\b.*\b(enquiry|inquiry|request|message)\b', message, re.I | re.S):
        return None
    try:
        answer = GroundedAnswer.model_validate_json(result.debug['output'])
    except (KeyError, ValueError, TypeError):
        return None
    candidates = [finding for finding in answer.findings
                      if finding.source_kind == 'service'
                      and finding.source_url in {source['url'] for source in result.sources}
                      and finding_coverage(finding, result.sources)[0]]
    candidates.sort(key=lambda finding: (
        any(check.status == 'not_met' and check.strength == 'hard' for check in finding.checks),
        not finding_coverage(finding, result.sources)[1],
        -sum(check.status == 'met' for check in finding.checks),
    ))
    candidate = candidates[0] if candidates else None
    if candidate is None:
        return None
    requirements = [item['description'] for item in contract.get('requirements', [])]
    unresolved = [check.requirement for check in candidate.checks if check.status != 'met']
    subject = contract.get('subject') or 'your service'
    destination = contract.get('destination')
    request = f'I would like to enquire about {subject}' + (f' in {destination}.' if destination else '.')
    lines = [f'Hello {candidate.name},', '', request,
             '', 'Please confirm whether you can meet these requirements:']
    lines.extend(f'- {requirement}' for requirement in requirements)
    if contract.get('dates'):
        lines.append('Requested dates: ' + ', '.join(contract['dates']))
    if contract.get('timezone'):
        lines.append('Timezone for the requested time: ' + contract['timezone'])
    lines.extend(['', 'Please confirm the total price including any mandatory charges, available appointment or reservation times, '
                  'and cancellation terms. This is an enquiry only; please do not make a chargeable booking.',
                  '', 'Name: [your name]', 'Preferred contact: [your contact details]'])
    text = '\n'.join(lines)
    proposal_id = hashlib.sha256(json.dumps([run_id, message, candidate.source_url, text], ensure_ascii=False).encode()).hexdigest()
    return {'state': 'prepared', 'proposal_id': proposal_id, 'recipient': candidate.name,
            'source_url': candidate.source_url, 'text': text, 'unresolved': unresolved}


def render_enquiry(action):
    return ('\n\n### Prepared enquiry — review before sending\n\nNothing has been sent or booked. '
            'Review the requirements and fill in your contact details.\n\n'
            + '\n'.join('> ' + plain(line) if line else '>' for line in action['text'].splitlines()))
