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

## Repository structure

```text
database_storage/   Database config, TimescaleDB, Neo4j, Marqo, and ingestion
qa/                 MCP query server and the sigmus-ask client
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
| `ingestion` | Follows enriched JSONL and idempotently writes TimescaleDB and Neo4j. | `ingestion-state` |
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
| `neo4j_browser_port` | Local Neo4j Browser port; default `7474`. |

Start the stack:

```bash
./docker-start up
./docker-start ps
```

Neo4j Browser defaults to <http://localhost:7474> and MCP to
<http://localhost:8006/mcp>. Both bind only to localhost.

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

- actor identity merging;
- incident identity and hierarchy linking; and
- cross-modality corroboration links.

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
