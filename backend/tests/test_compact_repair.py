from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import Requirement, ResearchContract
from app.orchestration.grounding import GroundedAnswer, apply_contract
from app.orchestration.passages import citation_passages
from app.orchestration.repairs import repair_findings


def test_compact_repair_can_correct_embellished_candidate_name_without_full_prompt():
    source = {'url': 'https://seller.fi/product/a', 'title': 'Laptop A',
              'text': '# Laptop A\n\n16 GB RAM.\n\n' + 'Irrelevant text.\n\n' * 20000}
    passage_id = next(i for i, (q, _) in citation_passages(source).items() if '16 gb' in q)
    task = ResearchContract(activity='product', requirements=[Requirement(id='ram', description='16 GB RAM')])
    proposed = GroundedAnswer.model_validate({'findings': [{'name': 'Laptop A (16 GB RAM)', 'source_kind': 'product',
        'source_url': source['url'], 'checks': [{'requirement_id': 'ram', 'requirement': 'RAM', 'status': 'met',
        'statement': '16 GB RAM', 'evidence': [{'source_url': source['url'], 'quote': '16 GB RAM.'}]}]}]})
    checked = apply_contract(proposed, [source], task)
    async def model(messages, info):
        prompt = messages[0].parts[-1].content
        assert len(prompt) < 85000
        assert source['url'] in prompt
        replacement = proposed.findings[0].model_copy(deep=True)
        replacement.name = 'Laptop A'
        replacement.checks[0].evidence[0].quote = passage_id
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'repairs': [
            {'original_name': proposed.findings[0].name, 'finding': replacement.model_dump()}]})])
    output, usage, trace = repair_findings(checked, proposed, ['Laptop A (16 GB RAM): ram'], [source], task, FunctionModel(model))
    assert usage.requests == 1 and trace['input_chars'] < 85000
    assert output.findings[0].name == 'Laptop A'
    final = apply_contract(output, [source], task)
    assert next(c for c in final.findings[0].checks if c.requirement_id == 'ram').status == 'met'
