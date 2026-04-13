from parser.models import (
    CategoryBreakdown, CategoryItem, TurnStats, SessionStats, ProjectStats, GlobalStats,
)
from tui.detail import build_rows, build_category_rows, TokenTotals


def make_breakdown(skills=0, tools=0, agents=0, messages=0):
    bd = CategoryBreakdown()
    if skills:
        bd.skills.append(CategoryItem("TestSkill", skills))
    if tools:
        bd.tools.append(CategoryItem("tool1", tools))
    if agents:
        bd.agents.append(CategoryItem("Agent", agents))
    bd.messages_tokens = messages
    return bd


def make_turn(input_t=1000, cache_read=9550, cache_create=500, output=100, breakdown=None):
    if breakdown is None:
        breakdown = make_breakdown(messages=1000)
    return TurnStats(
        turn_number=1, timestamp="2026-01-01T00:00:00Z",
        input_tokens=input_t, cache_read_tokens=cache_read,
        cache_create_tokens=cache_create, output_tokens=output,
        category_breakdown=breakdown,
    )


def test_build_rows_for_turn():
    bd = make_breakdown(skills=200, tools=100, messages=700)
    turn = make_turn(input_t=1000, cache_read=9550, cache_create=500, output=100, breakdown=bd)
    rows, totals = build_rows(turn)
    categories = {r[0]: r for r in rows}
    assert "Skills" in categories
    assert categories["Skills"][1] == 200  # tokens
    assert "Messages" in categories
    assert totals.input_tokens == 1000
    assert totals.cache_read == 9550
    assert totals.output == 100


def test_build_rows_percentages_sum_to_100():
    bd = make_breakdown(skills=300, tools=200, messages=500)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    total_pct = sum(r[2] for r in rows)
    assert abs(total_pct - 100.0) < 1.0  # allow rounding


def test_build_rows_excludes_zero_categories():
    bd = make_breakdown(messages=1000)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    names = [r[0] for r in rows]
    assert "Skills" not in names
    assert "Messages" in names


def test_build_rows_sorted_by_tokens_descending():
    bd = make_breakdown(skills=100, tools=500, messages=400)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows, _ = build_rows(turn)
    tokens = [r[1] for r in rows]
    assert tokens == sorted(tokens, reverse=True)


def test_build_rows_for_session():
    session = SessionStats(session_id="abc", display_name="abc1234", first_timestamp=None)
    session.turns.append(make_turn(input_t=500, cache_read=9550, output=50,
                                   breakdown=make_breakdown(skills=300, messages=200)))
    session.turns.append(make_turn(input_t=700, cache_read=10000, output=80,
                                   breakdown=make_breakdown(tools=400, messages=300)))
    rows, totals = build_rows(session)
    categories = {r[0]: r for r in rows}
    assert categories["Skills"][1] == 300
    assert categories["Tools"][1] == 400
    assert totals.input_tokens == 1200
    assert totals.output == 130


def test_build_rows_for_global():
    g = GlobalStats()
    p = ProjectStats(project_slug="p1", display_name="p1")
    s = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s.turns.append(make_turn(input_t=100, breakdown=make_breakdown(messages=100)))
    p.sessions = [s]
    g.projects = [p]
    rows, totals = build_rows(g)
    assert totals.input_tokens == 100


def test_build_category_rows_shows_items():
    bd = make_breakdown(skills=500, messages=300)
    bd.skills = [
        CategoryItem("BrainstormingSkill", 300),
        CategoryItem("TDDSkill", 200),
    ]
    turn = make_turn(input_t=1000, breakdown=bd)
    rows = build_category_rows(turn, "Skills")
    names = [r[0] for r in rows]
    assert "BrainstormingSkill" in names
    assert "TDDSkill" in names
    tokens = [r[1] for r in rows]
    assert tokens == sorted(tokens, reverse=True)


def test_build_category_rows_empty_category():
    bd = make_breakdown(messages=1000)
    turn = make_turn(input_t=1000, breakdown=bd)
    rows = build_category_rows(turn, "Skills")
    assert rows == []
