import pytest

from app.core.errors import PromptVariableError
from app.prompts.template import extract_variables, render


def test_extract_variables_in_order_without_duplicates():
    template = "Hi {{ name }}, welcome to {{company}}. Bye {{name}}."
    assert extract_variables(template) == ["name", "company"]


def test_extract_ignores_things_that_are_not_variables():
    assert extract_variables("{{ 1abc }} {{ a-b }} { x } {{}} {{ ok_1 }}") == ["ok_1"]


def test_render_fills_values_and_ignores_extras():
    text = render("Hi {{ name }} ({{ age }})", {"name": "Ada", "age": 36, "unused": "x"})
    assert text == "Hi Ada (36)"


def test_render_leaves_non_variable_braces_alone():
    assert render('JSON: {"a": 1} {{ x }}', {"x": "ok"}) == 'JSON: {"a": 1} ok'


def test_render_missing_variable_names_all_missing():
    with pytest.raises(PromptVariableError, match="company, region"):
        render("{{ company }} {{ region }} {{ name }}", {"name": "x"})
