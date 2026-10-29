import pytest

from agent_harness.agents import LangGraphAgent


@pytest.fixture
def runner():
    return LangGraphAgent
