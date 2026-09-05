from unittest.mock import Mock

import pytest

from database_storage.vector import MarqoStore


def store_with_response(response):
    index = Mock()
    index.add_documents.return_value = response
    client = Mock()
    client.get_indexes.return_value = {"results": [{"indexName": "incidents"}]}
    client.index.return_value = index
    return MarqoStore(client=client)


def test_index_incident_returns_successful_response():
    response = {"errors": False, "items": [{"status": 200, "_id": "one"}]}

    assert store_with_response(response).index_incident("one", "fire", "smoke") == response


def test_index_incident_raises_when_marqo_rejects_document():
    store = store_with_response({
        "errors": True,
        "items": [{"status": 400, "message": "vector store is out of space"}],
    })

    with pytest.raises(RuntimeError, match="vector store is out of space"):
        store.index_incident("one", "fire", "smoke")
