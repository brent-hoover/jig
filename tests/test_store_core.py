# tests/test_store_core.py
import pytest
from jig.store.core import JsonlStore


async def test_load_creates_missing_file_and_parent(tmp_path):
    path = tmp_path / "sub" / "store.jsonl"
    store = JsonlStore(path)
    await store.load()
    assert path.exists()
    assert path.read_text() == ""
