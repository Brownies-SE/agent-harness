import os
from uuid import uuid4

import pytest

from agent_harness.core.agents import LangGraphAgent


@pytest.fixture
def runner():
    return LangGraphAgent


@pytest.fixture
def chat_database():
    url = os.environ.get("HARNESS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set HARNESS_TEST_DATABASE_URL for real PostgreSQL chat tests")
    from agent_harness.services.database import Database

    database = Database(url)
    database.migrate()
    owner = database.owner(f"test-{uuid4()}@localhost")
    yield database, owner
    with database.connect() as connection:
        connection.execute("DELETE FROM agent_chat.users WHERE id = %s", (owner,))
