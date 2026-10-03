"""The two pointer arms, and the claim that they differ.

The benchmark compares four arms, so the code has to make them genuinely
distinct: asking for a pointer and demanding one are different requests, and
if the schema did not change between them the mandatory arm would be a copy
of the optional one while the results were reported as a comparison.
"""
from simulation.actions.actions import ActionType, action_schema
from simulation.agents.prompts import PromptBuilder
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role


def _view() -> AgentView:
    return AgentView(
        agent_id="aaron",
        game_id="t",
        round_number=2,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={},
        alive_players=["aaron", "tom"],
        eliminated_players=[],
        public_transcript=[],
        private_conversations=[],
    )


def test_no_pointer_requested_by_default() -> None:
    text = PromptBuilder().build_user(_view(), ActionType.PUBLIC_MESSAGE, [])
    assert "gist" not in text


def test_pointer_requested_when_asked() -> None:
    builder = PromptBuilder(want_gist=True)
    text = builder.build_user(_view(), ActionType.PUBLIC_MESSAGE, [])
    assert "gist" in text
    # The instruction has to say what a pointer is, not just name the field.
    assert "one short line" in text


def test_optional_arm_leaves_the_field_out_of_required() -> None:
    schema = action_schema(
        ActionType.PUBLIC_MESSAGE, want_gist=True, gist_required=False
    ).model_json_schema()
    assert "gist" in schema["properties"]
    assert "gist" not in schema["required"]


def test_mandatory_arm_requires_the_field() -> None:
    """The difference between the arms lives in this list and nowhere else."""
    schema = action_schema(
        ActionType.PUBLIC_MESSAGE, want_gist=True, gist_required=True
    ).model_json_schema()
    assert "gist" in schema["required"]


def test_pointers_are_only_asked_of_actions_with_content() -> None:
    """A vote has no message to point at, so it is not asked for one."""
    schema = action_schema(
        ActionType.VOTE, want_gist=True, gist_required=True
    ).model_json_schema()
    assert "gist" not in schema["required"]


def test_closed_choice_action_still_pins_its_enum_with_a_pointer() -> None:
    """The end vote keeps its enum; adding a pointer must not free the prose."""
    schema = action_schema(
        ActionType.END_VOTE, want_gist=True, gist_required=True
    ).model_json_schema()
    assert "gist" in schema["required"]
    assert schema["properties"]["content"]["enum"] == ["end", "banish"]


def test_gist_defaults_to_none() -> None:
    """A reply without a pointer is still a valid action, not a failure."""
    from simulation.actions.actions import Action

    action = Action(action=ActionType.PUBLIC_MESSAGE, actor_id="aaron",
                    content="hello")
    assert action.gist is None