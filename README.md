# SIGMUS

SIGMUS stores enriched urban observations in TimescaleDB and Neo4j, links
related graph records with Marqo and an LLM, and provides a Q/A interface over
the stored data.

SIGMUS does not collect raw data or perform observation-level enrichment. The
sibling `urban-observation-processing` repository owns replay, the shared data
model, receiving, and enrichment. `urban-observations` owns collection.

```text
Urban Observation Processing
            │
            ▼
    observations.jsonl
            │
            ▼
  SIGMUS ingestion worker
      ┌─────┴─────┐
      ▼           ▼
 TimescaleDB    Neo4j ◄──► Marqo
      └─────┬─────┘
            ▼
       MCP Q/A tools
            ▼
       sigmus-ask
```

The accompanying paper is available at [`docs/sigmus.pdf`](docs/sigmus.pdf).
The implemented Neo4j model and its extension guide are documented in
[`docs/ontology.md`](docs/ontology.md).

## Repository structure

```text
database_storage/   Database config, TimescaleDB, Neo4j, Marqo, and ingestion
qa/                 MCP query server and the sigmus-ask client
docs/               Paper and implemented ontology documentation
compose.yaml        Five-service deployment
docker-start        Configuration-aware Compose wrapper
```

There are no source-specific importers in SIGMUS. Historical and current raw
files must first pass through shared replay and processing to
produce the shared enriched stream.

## Containers

`./docker-start up` builds one application image and starts five containers:

| Container | Purpose | Persistent data |
|---|---|---|
| `timescaledb` | Stores complete observations and numeric time-series measurements. | `timescale-data` |
| `neo4j` | Stores reports, incidents, actors, places, and their relationships. | `neo4j-data` |
| `marqo` | Retrieves similar incident candidates for bounded graph-linking decisions. | `marqo-data` |
| `ingestion` | Follows enriched JSONL and idempotently writes TimescaleDB and Neo4j; vector linking is best-effort while Marqo starts. | `ingestion-state` |
| `mcp` | Exposes bounded, read-only database tools for Q/A. | None |

Marqo is approximately 7.7 GB compressed and 15 GB unpacked because its image
includes Vespa and CUDA/ML dependencies.

SIGMUS itself needs **one routine command**, `./docker-start up`, which starts
all five containers. Asking a question is an additional optional host Python
command. On a new checkout, configuration and startup use three commands
(`cp`, `chmod`, and `./docker-start up`); the Q/A virtual environment has its
own one-time installation block below.

## Installation

Check out both repositories as siblings because the SIGMUS image installs the
shared model directly from Urban Observation Processing:

```text
<parent>/
├── urban-observation-processing/
└── sigmus/
```

Start the receiver and enrichment services on the analysis machine:

```bash
cd ../urban-observation-processing
./docker-start up
```

Then configure SIGMUS:

```bash
cd ../sigmus
cp config.example.json config.json
chmod 600 config.json
```

Replace every `replace_me` value and point `enriched_stream_path` to the JSONL
file produced by Urban Observation Processing. `config.json` is ignored by Git and no
`.env` file is required.

| Setting | Purpose |
|---|---|
| `enriched_stream_path` | Enriched JSONL followed by the ingestion worker. |
| `postgres_config` | TimescaleDB database name and credentials. |
| `neo4j_config` | Neo4j connection and credentials. |
| `marqo_config.uri` | Marqo endpoint; rewritten automatically inside Docker. |
| `openai.api` | Key used for graph linking and the host-side Q/A client. |
| `openai.model` | Model used for actor, incident, and cross-modality linking. |
| `openai.qa_model` | Model used by `sigmus-ask`. |
| `mcp_port` | Local MCP port; default `8006`. |
| `timezone` | IANA timezone used for human-readable Q/A report times; canonical storage remains UTC. | `America/Los_Angeles` |
| `neo4j_browser_port` | Local Neo4j Browser port; default `7474`. |
| `neo4j_bolt_port` | Local Neo4j Bolt port used by Browser; default `7687`. |

Start the stack:

```bash
./docker-start up
./docker-start ps
```

Neo4j Browser defaults to <http://localhost:7474>, its connection URL is
`neo4j://localhost:7687`, and MCP defaults to <http://localhost:8006/mcp>.
All three ports bind only to localhost.

This localhost-only demo stack permits six-character Neo4j passwords (Neo4j's
normal default minimum is eight). Changing `neo4j_config.password` after Neo4j
has initialized does not change
the password stored in its existing named volume. Change it through Neo4j
Browser before updating `config.json`, or recreate the development volume with
`./docker-start down --volumes` when its contents are disposable.

```bash
./docker-start logs -f
./docker-start restart
./docker-start stop
./docker-start start
./docker-start down
```

`down` preserves the named database volumes unless `--volumes` is explicitly
added.

## Ingestion

The ingestion worker follows appended complete JSONL records. It advances its
durable byte offset only after both database writes succeed, so interrupted
records are retried. Observation IDs are upserted and can safely be replayed.
Synthetic observations require no alternate importer after shared enrichment.
SIGMUS and IncidentLens keep independent offset files, so both can follow one
`observations.jsonl` without consuming or interfering with each other's records.

Shared processing has already produced event, entity, relation, location,
effect, incident, anomaly, and summary annotations. SIGMUS adds only reasoning
that depends on existing graph context:

The complete annotation object is retained as JSON on the connected
`Data.annotations` property. `Report` represents the report itself and retains
only its identifying, temporal, spatial, summary, and event projection.
The related `GeoEntity` projects the resolved location name as `name`, along
with its coordinates, geocoding provider, and provider place ID.

- actor identity merging;
- incident identity and hierarchy linking; and
- cross-modality corroboration links.

Only real GDELT (`gdelt`) and synthetic news (`news`) reports may originate an
`Incident` node. The shared `incidents` annotation remains a generic candidate
list used by IncidentLens. News enrichment additionally supplies event-specific
labels in `news_incidents`; SIGMUS uses only that field to construct incidents.
Marqo and the LLM then decide whether each news incident is new, the same as an
existing incident, or part of an incident hierarchy. Other sources retain their
candidate annotations in TimescaleDB but do not create graph incidents. They
connect to news evidence through cross-modality corroboration.

Neo4j remains authoritative if optional LLM or Marqo linking fails.

For a one-time import instead of following the stream:

```bash
python -m database_storage.cli ingest-stream \
  --input ../urban-observation-processing/processing-output/observations.jsonl
```

The equivalent installed command is `sigmus-ingest`.

## Ask questions

There is one Q/A workflow:

```text
sigmus-ask → OpenAI API → MCP container → TimescaleDB / Neo4j
```

The MCP container contains no LLM and exposes only bounded, read-only tools.
The `sigmus-ask` client runs in a host-side Python environment and lets the
configured model call those tools.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ../urban-observation-processing
pip install -r requirements.txt
pip install --no-deps --no-build-isolation -e .

sigmus-ask "Was a fire corroborated by multiple sources near downtown Los Angeles?"
```

Use `--mcp-url` when the MCP service is on another authorized machine. Available
tools are `search_reports`, `get_report`, `find_related_reports`,
`search_incidents`, `query_measurements`, and `aggregate_measurements`.
Arbitrary SQL, Cypher, and Python execution are not exposed.

The safe example in `qa/fixtures/multisource.jsonl` has suggested questions in
`qa/fixtures/questions.md`.

## Troubleshooting

- If `config.json` is missing, copy `config.example.json` and edit it.
- If the image cannot find the shared package, confirm the sibling directory is
  named `urban-observation-processing`.
- If ingestion is unhealthy, verify `enriched_stream_path` and inspect
  `./docker-start logs ingestion`.
- If `sigmus-ask` reports an HTTP connection error, check `./docker-start ps`;
  the `mcp` service must be running and healthy on port 8006.
- A Neo4j volume retains the password used at its first initialization.
- Marqo requires substantial disk space and memory and may take time to start.

## Development

Tests are retained in Git for regression checking but excluded from the
production image:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
./docker-start --help
./docker-start config
```

The final command requires a valid ignored `config.json` and validates the
rendered Compose configuration without printing secrets.
