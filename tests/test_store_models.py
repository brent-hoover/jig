import pytest
from pydantic import Field
from jig.store.models import StoreModel


class Widget(StoreModel):
    name: str
    count: int = 0


def test_storemodel_assigns_id_by_default():
    w = Widget(name="a")
    assert isinstance(w.id, str)
    assert len(w.id) > 0


def test_storemodel_dumps_id_as_underscore_id():
    w = Widget(name="a", id="fixed")
    assert w.model_dump(by_alias=True)["_id"] == "fixed"


def test_storemodel_roundtrip_via_alias():
    w1 = Widget(name="a", id="x")
    as_dict = w1.model_dump(by_alias=True)
    w2 = Widget.model_validate(as_dict)
    assert w2.id == "x"
    assert w2.name == "a"
