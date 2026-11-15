import pytest

from app.orchestration.contracts import Budget, Requirement, ResearchContract
from app.orchestration.interpretation import preserve_request, requested_result_count


REQUEST = 'Find a store in Matinkylä, Espoo, where I can pick up 10 moving boxes and packing tape for under €40.'


def test_pickup_bundle_retains_quantity_budget_and_local_collection():
    task = ResearchContract(activity='product', destination='Matinkylä, Espoo',
        requested_count=10, count_quote='10 moving boxes',
        budget=Budget(amount=40, operator='lte', basis='total'), requirements=[
            Requirement(id='boxes', description='Store sells a quantity of 10 moving boxes'),
            Requirement(id='tape', description='Packing tape'),
        ])
    result = preserve_request(task, REQUEST)
    assert result.requested_count is None
    assert result.count_quote is None
    assert result.budget.amount == 40
    assert result.budget.operator == 'lt'
    assert result.budget.basis == 'total'
    checks = {requirement.id: requirement for requirement in result.requirements}
    assert checks['boxes'].description == 'Store sells a quantity of 10 moving boxes'
    assert checks['tape'].description == 'Packing tape'
    assert checks['pickup'].strength == 'hard'
    assert 'physical store in Matinkylä, Espoo' in checks['pickup'].description
    assert not any(requirement.kind == 'delivery' for requirement in result.requirements)
    assert checks['stock'].strength == 'preference'
    revalidated = ResearchContract.model_validate(result.model_dump())
    assert {r.id: r.model_dump() for r in revalidated.requirements} == {r.id: r.model_dump() for r in result.requirements}


def test_quantity_misclassified_as_result_count_is_recovered_as_a_requirement():
    task = ResearchContract(activity='product', requested_count=10, count_quote='10 moving boxes',
        requirements=[Requirement(id='boxes', description='10 moving boxes')])
    result = preserve_request(task, REQUEST)
    assert result.requested_count is None
    assert any(r.strength == 'hard' and '10 moving boxes' in r.description for r in result.requirements)


@pytest.mark.parametrize('message,expected', [
    (REQUEST, None),
    ('Find 3 stores where I can pick up 10 monitors', (3, '3 stores')),
    ('Find a retailer where I can buy 5 computers', None),
    ('Find 3 laptops I can buy in Finland', (3, '3 laptops')),
    ('Recommend five affordable restaurants in Tallinn', (5, 'five affordable restaurants')),
])
def test_item_quantities_do_not_increase_candidate_count(message, expected):
    assert requested_result_count(message) == expected


@pytest.mark.parametrize('message', [
    'Find paint with delivery to Bristol, no pickup',
    'Find paint for pickup or delivery in Bristol',
    'Find paint for delivery or pickup in Bristol',
    "Find paint with delivery to Bristol, don't collect",
])
def test_optional_or_declined_pickup_does_not_become_mandatory(message):
    task = ResearchContract(activity='product', original_request=message, destination='Bristol')
    assert not any(requirement.id == 'pickup' for requirement in task.requirements)
    assert any(requirement.id == 'delivery' for requirement in task.requirements)


def test_local_pickup_is_not_specific_to_the_reported_products_or_city():
    task = ResearchContract(activity='product', original_request='Find a shop where I can collect paint',
                            destination='Bristol')
    assert 'physical store in Bristol' in next(r.description for r in task.requirements if r.id == 'pickup')
    assert not any(r.kind == 'delivery' for r in task.requirements)


@pytest.mark.parametrize('message', [
    'Find a pickup truck under €40000',
    'Find a pick-up truck for sale in Bristol',
    'Find a collection of books under €40',
    'Find a store with a collection of books',
])
def test_product_names_do_not_require_store_collection(message):
    task = ResearchContract(activity='product', original_request=message, destination='Bristol')
    assert not any(r.id == 'pickup' for r in task.requirements)


def test_delivery_followup_removes_the_generated_pickup_requirement():
    task = ResearchContract(activity='product', original_request='Find paint for pickup in Bristol',
                            destination='Bristol')
    assert any(r.id == 'pickup' for r in task.requirements)
    result = preserve_request(task, 'Actually, find paint for delivery to Bristol')
    assert not any(r.id == 'pickup' for r in result.requirements)
    assert any(r.id == 'delivery' for r in result.requirements)
