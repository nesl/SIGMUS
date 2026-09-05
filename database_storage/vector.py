"""Marqo incident index used to bound graph-linking candidates."""

from __future__ import annotations

import marqo

from .config import get_config


class MarqoStore:
    def __init__(self, index_name: str = "incidents", client=None):
        self.client = client or marqo.Client(url=get_config()["marqo_config"]["uri"])
        self.index_name = index_name
        indexes = {item["indexName"] for item in self.client.get_indexes()["results"]}
        if index_name not in indexes:
            self.client.create_index(index_name, settings_dict={
                "textPreprocessing": {
                    "splitLength": 5, "splitOverlap": 0, "splitMethod": "sentence"
                },
                "model": "hf/e5-base-v2",
            })

    def similar_incidents(self, query: str, limit: int = 5) -> list[dict]:
        return self.client.index(self.index_name).search(q=query, limit=limit).get("hits", [])

    def index_incident(self, incident_id: str, label: str, text: str):
        result = self.client.index(self.index_name).add_documents([{
            "_id": str(incident_id), "neo4j_id": str(incident_id),
            "label": label, "text": text,
        }], tensor_fields=["label"])
        if result.get("errors"):
            failures = [
                item for item in result.get("items", [])
                if int(item.get("status", 500)) >= 400
            ]
            detail = failures[0].get("message", "unknown Marqo error") if failures else result
            raise RuntimeError(f"Marqo rejected incident {incident_id}: {detail}")
        return result
