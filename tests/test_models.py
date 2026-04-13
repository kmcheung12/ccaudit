from parser.models import (
    CategoryItem, CategoryBreakdown, TurnStats, SessionStats,
    ProjectStats, GlobalStats,
)


def make_breakdown(skills_tokens=0, memory_tokens=0, tools_tokens=0,
                   mcp_tokens=0, agents_tokens=0, messages_tokens=0, other_tokens=0):
    bd = CategoryBreakdown()
    if skills_tokens:
        bd.skills.append(CategoryItem(name="TestSkill", tokens=skills_tokens))
    if memory_tokens:
        bd.memory.append(CategoryItem(name="mem1", tokens=memory_tokens))
    if tools_tokens:
        bd.tools.append(CategoryItem(name="tool1", tokens=tools_tokens))
    if mcp_tokens:
        bd.mcp_tools.append(CategoryItem(name="MCP", tokens=mcp_tokens))
    if agents_tokens:
        bd.agents.append(CategoryItem(name="Agent", tokens=agents_tokens))
    bd.messages_tokens = messages_tokens
    bd.other_tokens = other_tokens
    return bd


def make_turn(input_t=100, cache_read=500, cache_create=200, output=50, breakdown=None):
    if breakdown is None:
        breakdown = make_breakdown(messages_tokens=100)
    return TurnStats(
        turn_number=1,
        timestamp="2026-01-01T00:00:00Z",
        input_tokens=input_t,
        cache_read_tokens=cache_read,
        cache_create_tokens=cache_create,
        output_tokens=output,
        category_breakdown=breakdown,
    )


# CategoryBreakdown tests

def test_breakdown_category_totals():
    bd = make_breakdown(skills_tokens=100, memory_tokens=50, messages_tokens=200)
    totals = bd.category_totals()
    assert totals["Skills"] == 100
    assert totals["Memory"] == 50
    assert totals["Messages"] == 200
    assert totals["Tools"] == 0
    assert totals["MCP"] == 0
    assert totals["Agents"] == 0
    assert totals["Other"] == 0


def test_breakdown_total_attributed_tokens():
    bd = make_breakdown(skills_tokens=100, memory_tokens=50, messages_tokens=200, other_tokens=50)
    assert bd.total_attributed_tokens() == 400


def test_breakdown_includes_mcp():
    bd = make_breakdown(mcp_tokens=300, messages_tokens=100)
    totals = bd.category_totals()
    assert totals["MCP"] == 300
    assert bd.total_attributed_tokens() == 400


# TurnStats tests

def test_turn_aggregates():
    turn = make_turn(input_t=100, cache_read=500, cache_create=200, output=50)
    assert turn.input_tokens == 100
    assert turn.cache_read_tokens == 500
    assert turn.cache_create_tokens == 200
    assert turn.output_tokens == 50


# SessionStats tests

def test_session_aggregates_across_turns():
    session = SessionStats(session_id="abc", display_name="abc1234", first_timestamp=None)
    session.turns.append(make_turn(input_t=100, cache_read=500, cache_create=200, output=50,
                                   breakdown=make_breakdown(messages_tokens=100)))
    session.turns.append(make_turn(input_t=200, cache_read=700, cache_create=300, output=80,
                                   breakdown=make_breakdown(skills_tokens=150, messages_tokens=50)))
    assert session.total_input_tokens == 300
    assert session.total_cache_read_tokens == 1200
    assert session.total_cache_create_tokens == 500
    assert session.total_output_tokens == 130
    totals = session.category_totals()
    assert totals["Skills"] == 150
    assert totals["Messages"] == 150


def test_session_with_no_turns():
    session = SessionStats(session_id="xyz", display_name="xyz1234", first_timestamp=None)
    assert session.total_input_tokens == 0
    assert session.category_totals()["Messages"] == 0


# ProjectStats tests

def test_project_aggregates_across_sessions():
    project = ProjectStats(project_slug="test-proj", display_name="proj")
    s1 = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s1.turns.append(make_turn(input_t=100, breakdown=make_breakdown(messages_tokens=100)))
    s2 = SessionStats(session_id="s2", display_name="s2abc", first_timestamp=None)
    s2.turns.append(make_turn(input_t=200, breakdown=make_breakdown(skills_tokens=200)))
    project.sessions = [s1, s2]
    assert project.total_input_tokens == 300
    totals = project.category_totals()
    assert totals["Skills"] == 200
    assert totals["Messages"] == 100


# GlobalStats tests

def test_global_aggregates_across_projects():
    g = GlobalStats()
    p1 = ProjectStats(project_slug="p1", display_name="p1")
    s1 = SessionStats(session_id="s1", display_name="s1abc", first_timestamp=None)
    s1.turns.append(make_turn(input_t=100, output=10, breakdown=make_breakdown(messages_tokens=100)))
    p1.sessions = [s1]

    p2 = ProjectStats(project_slug="p2", display_name="p2")
    s2 = SessionStats(session_id="s2", display_name="s2abc", first_timestamp=None)
    s2.turns.append(make_turn(input_t=300, output=30, breakdown=make_breakdown(skills_tokens=300)))
    p2.sessions = [s2]

    g.projects = [p1, p2]
    assert g.total_input_tokens == 400
    assert g.total_output_tokens == 40
    totals = g.category_totals()
    assert totals["Skills"] == 300
    assert totals["Messages"] == 100
