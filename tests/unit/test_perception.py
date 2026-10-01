"""Perception mapping tests independent of a live browser."""

from agentic_tester.browser.perception import PerceptionEngine


def test_map_to_element_refs_assigns_ids_and_emits_llm_tree() -> None:
    engine = PerceptionEngine()
    refs, tree = engine.map_to_element_refs(
        {
            "role": "document",
            "name": "Demo",
            "children": [
                {
                    "role": "navigation",
                    "name": "Main",
                    "children": [
                        {
                            "role": "link",
                            "name": "Home",
                            "locator_value": "#home",
                        }
                    ],
                },
                {
                    "role": "textbox",
                    "name": "Project name",
                    "locator_value": "#project-name",
                    "value": "Demo",
                },
                {
                    "role": "button",
                    "name": "Save",
                    "locator_value": "#save",
                    "is_enabled": False,
                },
            ],
            "aria_snapshot": '- navigation "Main"',
        }
    )

    assert [ref.id for ref in refs] == ["el_0", "el_1", "el_2"]
    assert refs[1].value == "Demo"
    assert refs[2].is_enabled is False
    assert '- link "Home" [el_0]' in tree
    assert '- textbox "Project name" [el_1]' in tree
    assert '# - navigation "Main"' in tree


def test_reset_counter_restarts_agent_owned_ids() -> None:
    engine = PerceptionEngine()
    snapshot = {
        "role": "document",
        "children": [{"role": "button", "name": "Save", "locator_value": "#save"}],
    }

    first, _ = engine.map_to_element_refs(snapshot)
    engine.reset_counter()
    second, _ = engine.map_to_element_refs(snapshot)

    assert first[0].id == "el_0"
    assert second[0].id == "el_0"
