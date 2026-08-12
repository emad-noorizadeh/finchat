"""Shared fixtures.

`temp_db` gives store-level tests a real (temporary) SQLite database.
Note: app.agents.template_store imports `engine` and `get_session_context`
BY VALUE at module import — patching app.database alone would not redirect
it, so the fixture patches the template_store module attributes directly.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine

    # Ensure the model is registered on SQLModel.metadata before create_all.
    import app.models.sub_agent_template  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _ctx():
        with Session(engine) as s:
            yield s

    import app.agents.template_store as store

    monkeypatch.setattr(store, "engine", engine)
    monkeypatch.setattr(store, "get_session_context", _ctx)
    yield engine
    engine.dispose()
