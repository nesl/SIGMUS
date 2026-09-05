"""Neo4j projection and graph-context linking for enriched observations."""

from __future__ import annotations

import json

from fuzzywuzzy import fuzz
from neo4j import GraphDatabase

from .config import get_config
from .reasoning import GraphReasoner
from .vector import MarqoStore


NEWS_SOURCES = frozenset({"gdelt", "news"})


def originates_incidents(source: object) -> bool:
    """Only news reports may introduce incident nodes into the SIGMUS graph."""
    return str(source or "").strip().lower() in NEWS_SOURCES


class Neo4jStore:
    def __init__(self, *, driver=None, reasoner=None, vector_store=None,
                 enable_reasoning: bool = True):
        settings = get_config()["neo4j_config"]
        self.driver = driver or GraphDatabase.driver(
            settings["uri"], auth=(settings["username"], settings["password"])
        )
        self.enable_reasoning = enable_reasoning
        self.reasoner = reasoner or (GraphReasoner() if enable_reasoning else None)
        self.vector_store = vector_store
        if enable_reasoning and vector_store is None:
            try:
                self.vector_store = MarqoStore()
            except Exception as exc:
                # Marqo improves candidate selection but is not authoritative.
                # Keep ingestion available while its heavyweight service starts
                # or when the optional vector index is temporarily unavailable.
                print(f"Marqo unavailable; continuing without vector linking: {exc}")
                self.vector_store = None

    @staticmethod
    def _name(item) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or item.get("label") or "").strip()
        return str(item or "").strip()

    @staticmethod
    def _json_object(text: str) -> dict:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response did not contain a JSON object")
        value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("LLM response JSON was not an object")
        return value

    def insert_observation(self, record: dict) -> str:
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        summary = str(record.get("summary") or event.get("description") or "")
        incidents = [self._name(item) for item in record.get("incidents", [])]
        incidents = [name for name in incidents if name]
        news_incidents = [self._name(item) for item in record.get("news_incidents", [])]
        news_incidents = [name for name in news_incidents if name]
        # Compatibility for enriched v1 news: its event name was the only
        # event-specific value. Never fall back to generic incident candidates.
        if not news_incidents and originates_incidents(record.get("source")):
            legacy_event_name = self._name(event.get("name"))
            if legacy_event_name:
                news_incidents = [legacy_event_name]
        annotations = {key: record.get(key) for key in (
            "event", "summary", "entities", "relations", "effects", "incidents", "news_incidents",
            "anomaly", "enrichment",
        )}
        location = record.get("location") if isinstance(record.get("location"), dict) else {}
        location_name = str(location.get("formatted_address") or location.get("text") or "").strip() or None
        anomaly = record.get("anomaly") if isinstance(record.get("anomaly"), dict) else {}
        with self.driver.session() as session:
            result = session.run("""
                MERGE (agg:Aggregator {name: $source})
                MERGE (obs:Observer {name: $sensor, source: $source})
                MERGE (agg)-[:HAS_OBSERVER]->(obs)
                MERGE (rep:Report {observation_id: $observation_id})
                SET rep.time=datetime($time),
                    rep.end_time=CASE WHEN $end_time IS NULL THEN NULL ELSE datetime($end_time) END,
                    rep.latitude=$latitude,
                    rep.longitude=$longitude, rep.summary=$summary, rep.source=$source,
                    rep.event_name=$event_name, rep.event_type=$event_type,
                    rep.incident_names=$confirmed_incident_names,
                    rep.candidate_incident_names=$candidate_incident_names
                MERGE (obs)-[:HAS_REPORT]->(rep)
                MERGE (dat:Data {observation_id: $observation_id})
                SET dat.data_val=$data_json, dat.annotations=$annotations_json,
                    dat.filepath=$filepath, dat.raw=$raw_json, dat.files=$files_json,
                    dat.anomaly_score=$anomaly_score,
                    dat.is_anomaly=$is_anomaly,
                    dat.enrichment_status=$enrichment_status
                MERGE (rep)-[:HAS_DATA]->(dat)
                MERGE (tim:TimeEntity {observation_id: $observation_id})
                SET tim.start_time=$time, tim.end_time=coalesce($end_time, $time)
                MERGE (rep)-[:CAPTURE_TIME]->(tim)
                FOREACH (_ IN CASE WHEN $latitude IS NULL OR $longitude IS NULL THEN [] ELSE [1] END |
                  MERGE (geo:GeoEntity {observation_id: $observation_id})
                  SET geo.latitude=$latitude, geo.longitude=$longitude,
                      geo.name=$location_name, geo.provider=$location_provider,
                      geo.provider_place_id=$location_provider_place_id
                  MERGE (rep)-[:OCCURRED_AT]->(geo))
                RETURN elementId(rep) AS report_id
            """, source=record["source"], sensor=str(record["sensor"]),
                observation_id=record["id"], time=record["time"],
                end_time=record.get("end_time"), latitude=record.get("latitude"),
                longitude=record.get("longitude"), location_name=location_name,
                location_provider=location.get("provider"),
                location_provider_place_id=location.get("provider_place_id"), summary=summary,
                event_name=event.get("name"), event_type=event.get("type"),
                confirmed_incident_names=news_incidents if originates_incidents(record.get("source")) else [],
                candidate_incident_names=incidents,
                data_json=json.dumps(record.get("data") or {}, ensure_ascii=False),
                annotations_json=json.dumps(annotations, ensure_ascii=False),
                filepath=record.get("filepath"),
                raw_json=json.dumps(record.get("raw") or {}, ensure_ascii=False),
                files_json=json.dumps([item.to_dict() for item in record.get("files") or ()],
                                      ensure_ascii=False),
                anomaly_score=anomaly.get("score"), is_anomaly=anomaly.get("is_anomaly"),
                enrichment_status=(record.get("enrichment") or {}).get("status"))
            report_id = result.single()["report_id"]

        actor_ids = {}
        for entity in record.get("entities", []):
            if not isinstance(entity, dict) or not entity.get("name"):
                continue
            actor_id = self._create_or_merge_actor(entity)
            actor_ids[str(entity["name"])] = actor_id
            with self.driver.session() as session:
                session.run("""
                    MATCH (rep:Report), (actor:Actor)
                    WHERE elementId(rep)=$report_id AND elementId(actor)=$actor_id
                    MERGE (rep)-[:MENTIONS]->(actor)
                """, report_id=report_id, actor_id=actor_id)

        for relation in record.get("relations", []):
            if not isinstance(relation, dict):
                continue
            subject = actor_ids.get(str(relation.get("subject")))
            object_id = actor_ids.get(str(relation.get("object")))
            if not subject or not object_id:
                continue
            with self.driver.session() as session:
                session.run("""
                    MATCH (a:Actor), (b:Actor)
                    WHERE elementId(a)=$subject AND elementId(b)=$object
                    MERGE (rel:EntityRelation {
                      observation_id:$observation_id, subject_id:$subject,
                      object_id:$object, predicate:$predicate})
                    MERGE (a)-[:SUBJECT_OF]->(rel)
                    MERGE (rel)-[:OBJECT_OF]->(b)
                """, subject=subject, object=object_id, observation_id=record["id"],
                    predicate=str(relation.get("predicate") or "related_to"))

        # In the SIGMUS ontology, an Incident is introduced by a news report.
        # Other modalities retain their possible-incident annotations for
        # TimescaleDB and may corroborate news through report relationships,
        # but they must not originate graph Incident nodes.
        if originates_incidents(record.get("source")):
            for label in news_incidents:
                self._link_incident(report_id, label, summary)

        enrichment = record.get("enrichment") or {}
        semantic = bool(summary or event or incidents or news_incidents or record.get("effects"))
        if (self.enable_reasoning and semantic
                and enrichment.get("status") != "skipped_by_anomaly"):
            self._link_cross_modality(report_id, record)
        return report_id

    def _create_or_merge_actor(self, entity: dict) -> str:
        name = str(entity.get("name") or "Unknown")
        actor_type = str(entity.get("type") or "Unknown")
        description = str(entity.get("description") or "")
        location = entity.get("location") if isinstance(entity.get("location"), dict) else {}
        candidates = []
        with self.driver.session() as session:
            rows = session.run("""
                MATCH (actor:Actor)
                RETURN elementId(actor) AS id, actor.name AS name,
                       actor.actor_type AS actor_type,
                       actor.actor_type_desc AS description
                LIMIT 200
            """)
            candidates = [dict(row) for row in rows if row.get("name")]

        candidates.sort(key=lambda item: fuzz.ratio(name.lower(), item["name"].lower()), reverse=True)
        candidates = candidates[:5]
        if self.enable_reasoning and self.reasoner and candidates:
            prompt = (
                "Determine whether this actor is the same real-world entity as one candidate. "
                "Return only JSON: {\"candidate_index\":-1,\"update_name\":false," 
                "\"reason\":\"...\"}. Use -1 when uncertain.\n\n"
                f"Incoming: {json.dumps({'name': name, 'type': actor_type, 'description': description, 'location': location})}\n"
                f"Candidates: {json.dumps(candidates, default=str)}"
            )
            try:
                decision = self._json_object(self.reasoner.ask(prompt))
                index = int(decision.get("candidate_index", -1))
                if 0 <= index < len(candidates):
                    candidate = candidates[index]
                    chosen_name = name if decision.get("update_name") else candidate["name"]
                    with self.driver.session() as session:
                        session.run("""
                            MATCH (actor:Actor) WHERE elementId(actor)=$id
                            SET actor.name=$name, actor.actor_type=$type,
                                actor.actor_type_desc=$description
                        """, id=candidate["id"], name=chosen_name, type=actor_type,
                            description=description)
                    return candidate["id"]
            except Exception as exc:
                print(f"Actor merge skipped after reasoning error: {exc}")

        with self.driver.session() as session:
            result = session.run("""
                MERGE (actor:Actor {name:$name, actor_type:$type,
                                    actor_type_desc:$description})
                RETURN elementId(actor) AS actor_id
            """, name=name, type=actor_type, description=description)
            return result.single()["actor_id"]

    def _insert_incident(self, report_id: str, label: str, context: str) -> str:
        with self.driver.session() as session:
            result = session.run("""
                MATCH (rep:Report) WHERE elementId(rep)=$report_id
                MERGE (inc:Incident {label:$label})
                MERGE (rep)-[:HAS_LABEL]->(inc)
                RETURN elementId(inc) AS incident_id
            """, report_id=report_id, label=label)
            incident_id = result.single()["incident_id"]
        if self.vector_store:
            try:
                self.vector_store.index_incident(incident_id, label, context)
            except Exception as exc:
                print(f"Incident vector indexing failed: {exc}")
        return incident_id

    def _link_incident(self, report_id: str, label: str, context: str):
        if not self.enable_reasoning or not self.reasoner or not self.vector_store:
            return self._insert_incident(report_id, label, context)
        try:
            hits = self.vector_store.similar_incidents(label)
        except Exception as exc:
            print(f"Incident vector lookup failed; creating incident: {exc}")
            return self._insert_incident(report_id, label, context)
        if not hits:
            return self._insert_incident(report_id, label, context)
        candidates = [
            {"candidate_index": index, "label": hit.get("label"), "text": hit.get("text")}
            for index, hit in enumerate(hits)
        ]
        prompt = (
            "Compare the incoming incident with these candidates. Return only JSON: "
            "{\"matches\":[{\"candidate_index\":0,\"relationship\":\"same|parent|child\"," 
            "\"reason\":\"...\"}]}. Return an empty matches list when uncertain.\n\n"
            f"Incoming: {json.dumps({'label': label, 'text': context})}\n"
            f"Candidates: {json.dumps(candidates, default=str)}"
        )
        try:
            decisions = self._json_object(self.reasoner.ask(prompt)).get("matches") or []
        except Exception as exc:
            print(f"Incident linking skipped after reasoning error: {exc}")
            decisions = []
        for decision in decisions:
            try:
                index = int(decision.get("candidate_index", -1))
            except (AttributeError, TypeError, ValueError):
                continue
            if not 0 <= index < len(hits):
                continue
            hit = hits[index]
            existing_id = hit.get("neo4j_id") or hit.get("id") or hit.get("_id")
            if not existing_id:
                continue
            relationship = str(decision.get("relationship") or "").lower()
            if relationship == "same":
                with self.driver.session() as session:
                    session.run("""
                        MATCH (inc:Incident), (rep:Report)
                        WHERE elementId(inc)=$incident_id AND elementId(rep)=$report_id
                        MERGE (rep)-[:HAS_LABEL]->(inc)
                    """, incident_id=str(existing_id), report_id=report_id)
                return str(existing_id)
            if relationship in {"parent", "child"}:
                new_id = self._insert_incident(report_id, label, context)
                parent, child = ((str(existing_id), new_id) if relationship == "parent"
                                 else (new_id, str(existing_id)))
                with self.driver.session() as session:
                    session.run("""
                        MATCH (parent:Incident), (child:Incident)
                        WHERE elementId(parent)=$parent AND elementId(child)=$child
                        MERGE (child)-[:IS_PART_OF]->(ctx:LLM_CONTEXT {
                          source_incident_id:$child, target_incident_id:$parent,
                          kind:'incident_hierarchy'})
                        SET ctx.reason=$reason
                        MERGE (ctx)-[:IS_PART_OF]->(parent)
                    """, parent=parent, child=child,
                        reason=str(decision.get("reason") or ""))
                return new_id
        return self._insert_incident(report_id, label, context)

    def _link_cross_modality(self, report_id: str, record: dict, limit: int = 6):
        if (not self.reasoner or record.get("latitude") is None
                or record.get("longitude") is None):
            return
        with self.driver.session() as session:
            rows = session.run("""
                MATCH (candidate:Report)
                WHERE elementId(candidate) <> $report_id
                  AND candidate.observation_id IS NOT NULL
                  AND candidate.source <> $source
                  AND ($source IN $news_sources OR candidate.source IN $news_sources)
                  AND candidate.latitude IS NOT NULL AND candidate.longitude IS NOT NULL
                  AND abs(datetime(candidate.time).epochSeconds - datetime($time).epochSeconds)
                      <= $max_time_seconds
                  AND point.distance(
                        point({latitude:candidate.latitude, longitude:candidate.longitude}),
                        point({latitude:$latitude, longitude:$longitude})) <= $max_distance_meters
                RETURN elementId(candidate) AS id,
                       candidate.observation_id AS observation_id,
                       candidate.time AS time, candidate.summary AS summary,
                       candidate.event_type AS event_type,
                       candidate.incident_names AS incidents,
                       candidate.candidate_incident_names AS candidate_incidents,
                       candidate.latitude AS latitude,
                       candidate.longitude AS longitude,
                       abs(datetime(candidate.time).epochSeconds - datetime($time).epochSeconds)
                         AS time_delta_seconds,
                       point.distance(
                         point({latitude:candidate.latitude, longitude:candidate.longitude}),
                         point({latitude:$latitude, longitude:$longitude})) AS distance_meters
                ORDER BY time_delta_seconds, distance_meters LIMIT $limit
            """, report_id=report_id, source=record["source"], time=record["time"],
                latitude=record["latitude"], longitude=record["longitude"],
                news_sources=sorted(NEWS_SOURCES), max_time_seconds=1800,
                max_distance_meters=20000, limit=limit)
            candidates = [dict(row) for row in rows]
        if not candidates:
            return
        incoming = {key: record.get(key) for key in (
            "id", "source", "time", "latitude", "longitude", "summary",
            "event", "incidents", "news_incidents", "effects",
        )}
        prompt = (
            "A news report is the only authoritative incident source. Choose at most two sensor "
            "reports that materially corroborate the same specific news event. An anomaly label, "
            "nearby location, or similar time alone is insufficient. Return only JSON: "
            "{\"links\":[{\"candidate_id\":\"Neo4j element id\",\"confidence\":0.0," 
            "\"reason\":\"...\"}]}. Exclude weak or topical matches.\n\n"
            f"Incoming: {json.dumps(incoming, default=str)}\n"
            f"Candidates: {json.dumps(candidates, default=str)}"
        )
        try:
            links = self._json_object(self.reasoner.ask(prompt)).get("links") or []
        except Exception as exc:
            print(f"Cross-modality linking skipped after reasoning error: {exc}")
            return
        allowed = {str(item["id"]) for item in candidates}
        accepted = 0
        candidate_by_id = {str(item["id"]): item for item in candidates}
        for link in links:
            try:
                candidate_id = str(link["candidate_id"])
                confidence = float(link.get("confidence", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if candidate_id not in allowed or confidence < 0.6:
                continue
            candidate = candidate_by_id[candidate_id]
            with self.driver.session() as session:
                session.run("""
                    MATCH (a:Report), (b:Report)
                    WHERE elementId(a)=$report_id AND elementId(b)=$candidate_id
                    MERGE (ctx:LLM_CONTEXT {
                      source_observation_id:$observation_id,
                      target_element_id:$candidate_id, kind:'cross_modality'})
                    SET ctx.reason=$reason, ctx.confidence=$confidence,
                        ctx.distance_meters=$distance_meters,
                        ctx.time_delta_seconds=$time_delta_seconds
                    MERGE (a)-[:CORROBORATES]->(ctx)
                    MERGE (ctx)-[:CORROBORATES]->(b)
                """, report_id=report_id, candidate_id=candidate_id,
                    observation_id=record["id"], reason=str(link.get("reason") or ""),
                    confidence=confidence, distance_meters=candidate["distance_meters"],
                    time_delta_seconds=candidate["time_delta_seconds"])
            accepted += 1
            if accepted >= 2:
                break

    def close(self):
        self.driver.close()
