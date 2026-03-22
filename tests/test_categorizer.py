from parser.categorizer import categorize, extract_categories


SKILL_BLOCK = """Base directory: /Users/alan/.claude/plugins/cache/superpowers/5.0.5/skills/brainstorming

# Brainstorming Ideas Into Designs

Help turn ideas into fully formed designs.
"""

MEMORY_BLOCK = """---
name: user_role
description: user is a senior engineer
type: user
---

User is a senior engineer working on Python tools.
"""

SYSTEM_REMINDER_BLOCK = """<system-reminder>
Today's date is 2026-03-21.
</system-reminder>"""

FUNCTION_RESULTS_BLOCK = """<function_results>
{"output": "some tool output here"}
</function_results>"""


def test_skill_detected():
    cats = extract_categories(SKILL_BLOCK)
    assert "Skills" in cats
    items = cats["Skills"]
    assert len(items) == 1
    assert items[0].name == "Brainstorming Ideas Into Designs"


def test_memory_detected():
    cats = extract_categories(MEMORY_BLOCK)
    assert "Memory" in cats
    items = cats["Memory"]
    assert len(items) == 1
    assert items[0].name == "user_role"


def test_system_reminder_detected():
    cats = extract_categories(SYSTEM_REMINDER_BLOCK)
    assert "Tools" in cats
    assert len(cats["Tools"]) == 1


def test_function_results_detected():
    cats = extract_categories(FUNCTION_RESULTS_BLOCK)
    assert "Tools" in cats


def test_unmatched_text_goes_to_messages():
    text = "Just a plain user message with no special markers."
    cats = extract_categories(text)
    assert "Messages" in cats
    assert cats["Messages"][0].name == "Messages"


def test_proportional_attribution_sums_to_input_tokens():
    text = SKILL_BLOCK + "\n" + MEMORY_BLOCK + "\n" + "plain message text"
    bd = categorize(text=text, input_tokens=1000, system_prompt_tokens=0)
    total = bd.total_attributed_tokens()
    assert total == 1000


def test_proportional_attribution_with_system_prompt():
    # system prompt tokens are passed in directly, not from text
    text = "short user message"
    bd = categorize(text=text, input_tokens=50, system_prompt_tokens=9550)
    # system prompt should be exactly 9550
    assert bd.system_prompt_tokens == 9550
    # messages gets the proportional share of input_tokens
    assert bd.messages_tokens == 50


def test_empty_text_produces_zero_tokens():
    bd = categorize(text="", input_tokens=100, system_prompt_tokens=0)
    assert bd.total_attributed_tokens() == 100  # all goes to messages (fallback)


def test_multiple_skills_detected():
    two_skills = SKILL_BLOCK + "\n---SEPARATOR---\n" + """Base directory: /Users/alan/.claude/plugins/cache/superpowers/5.0.5/skills/tdd

# Test-Driven Development

Write tests first.
"""
    cats = extract_categories(two_skills)
    assert len(cats["Skills"]) == 2
    names = [i.name for i in cats["Skills"]]
    assert "Brainstorming Ideas Into Designs" in names
    assert "Test-Driven Development" in names


def test_first_match_wins_for_ambiguous_blocks():
    # A block that matches Skills first should not also match Messages
    cats = extract_categories(SKILL_BLOCK)
    messages_items = cats.get("Messages", [])
    # The skill text should not appear in Messages
    for item in messages_items:
        assert "Brainstorming" not in item.name
