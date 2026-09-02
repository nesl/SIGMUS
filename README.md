# SIGMUS

SIGMUS stores enriched urban observations in a time-series database and a
multimodal knowledge graph, then provides an LLM-assisted question-and-answer
interface over both.

SIGMUS does not collect or enrich raw observations. Those responsibilities
belong to [Urban Observations](../urban-observations), which produces the shared
`observations.jsonl` stream consumed here.

```text
Urban Observations receiver and enrichment
                  │
                  ▼
          observations.jsonl
                  │
                  ▼
        SIGMUS ingestion worker
           ┌──────┴──────┐
           ▼             ▼
      TimescaleDB       Neo4j ◄──► Marqo
           └──────┬──────┘
                  ▼
             MCP Q/A tools
                  ▼
             sigmus-ask
```

The accompanying paper is available at [`docs/sigmus.pdf`](docs/sigmus.pdf).

## Containers

`./docker-start up` builds one SIGMUS application image and starts these five
containers:

| Container | Purpose | Persistent data |
|---|---|---|
| `timescaledb` | Stores observations and numeric time-series measurements. | `timescale-data` volume |
| `neo4j` | Stores reports, incidents, actors, places, and graph relationships. Its Browser UI is exposed only on localhost. | `neo4j-data` volume |
| `marqo` | Finds similar incidents before the LLM decides whether graph records should be linked. | `marqo-data` volume |
| `ingestion` | Follows the enriched JSONL stream and idempotently writes each observation to TimescaleDB and Neo4j. | `ingestion-state` volume |
| `mcp` | Exposes bounded, read-only TimescaleDB and Neo4j query tools for Q/A. | None |

The Urban Observations receiver and enrichment services run separately, usually
on the machine that runs SIGMUS or IncidentLens. They are deliberately not
duplicated in this repository.

## Quick start

### 1. Check out both repositories

The repositories must be siblings because the SIGMUS image installs the shared
data model directly from Urban Observations:

```text
<parent>/
├── urban-observations/
└── sigmus/
```

Requirements are Docker with the Compose plugin and enough disk and memory for
the database images. The Marqo image is approximately 7.7 GB compressed and
15 GB unpacked because it includes Vespa and CUDA/ML dependencies.

### 2. Start Urban Observations processing

Configure Urban Observations first, then start its receiver and enrichment
services:

```bash
cd ../urban-observations
./docker-start processing-up
```

Its durable output is normally
`processing-output/observations.jsonl`. Collection can run on another machine;
SIGMUS only needs access to the resulting stream file.

### 3. Configure SIGMUS

```bash
cd ../sigmus
cp config.example.json config.json
chmod 600 config.json
```

Edit `config.json`. At minimum, replace the PostgreSQL and Neo4j passwords, set
the OpenAI API key, and point `enriched_stream_path` to the JSONL file produced
by Urban Observations. Relative paths are resolved from the SIGMUS repository.

Important settings:

| Setting | Purpose |
|---|---|
| `enriched_stream_path` | Shared enriched JSONL input followed by the ingestion worker. |
| `postgres_config` | TimescaleDB database name, user, password, and host-side connection settings. |
| `neo4j_config` | Neo4j URI, user, and password. |
| `marqo_config.uri` | Marqo endpoint; the launcher rewrites it for the Docker network. |
| `openai.api` | OpenAI key used for graph-context linking and the Q/A client. |
| `openai.model` | Model used for graph linking. |
| `openai.qa_model` | Model used by `sigmus-ask`. |
| `mcp_port` | Localhost port for the MCP server; default `8006`. |
| `neo4j_browser_port` | Localhost port for Neo4j Browser; default `7474`. |

The remaining paths and API settings support legacy source-specific imports.
They are not used to collect new data. `config.json` and the generated
`.runtime/` directory are ignored by Git; no `.env` file is required.

### 4. Start SIGMUS

```bash
./docker-start up
./docker-start ps
```

The launcher validates `config.json`, creates a Docker-network version of it in
`.runtime/`, builds the shared application image, and starts the complete stack.
It does not modify `compose.yaml`.

Neo4j Browser is available at <http://localhost:7474>. The MCP endpoint defaults
to <http://localhost:8006/mcp>. Both bind only to localhost.

Common operations are:

```bash
./docker-start logs -f
./docker-start restart
./docker-start stop
./docker-start start
./docker-start down
```

`down` removes containers and the network but preserves the named database
volumes. Do not add `--volumes` unless permanent deletion is intended.

## Ask questions

The MCP container performs deterministic, read-only queries and contains no
LLM. The local `sigmus-ask` client lets the configured OpenAI model choose from
those tools and synthesize an answer with stable observation IDs.

Install the lightweight command-line client in a virtual environment:

The Docker launcher does not create or use this virtual environment; it is only
for running `sigmus-ask` and development commands directly on the host.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ../urban-observations
pip install -r requirements.txt
pip install --no-deps --no-build-isolation -e .
```

Then ask a question:

```bash
sigmus-ask "Was a fire corroborated by multiple sources near downtown Los Angeles?"
```

Use `--mcp-url` to connect to an authorized remote MCP service. The available
tools are `search_reports`, `get_report`, `find_related_reports`,
`search_incidents`, `query_measurements`, and `aggregate_measurements`. Arbitrary
SQL, Cypher, and Python execution are not exposed.

A safe multisource example is provided in `qa/fixtures/multisource.jsonl`, with
suggested questions in `qa/fixtures/questions.md`.

## Ingestion behavior

The ingestion worker follows appended complete JSONL records and saves its byte
offset only after both database writes succeed. Restarting the worker resumes
from that offset. Observation IDs are upserted, so replaying a record does not
duplicate its TimescaleDB row or core Neo4j report.

Shared enrichment has already extracted events, entities, relationships,
locations, effects, and incident annotations. SIGMUS adds only graph-dependent
reasoning: actor identity merging, incident linking, and cross-modality report
links. Optional LLM or Marqo failures do not prevent the base observation from
being stored.

For a one-time import instead of following the stream:

```bash
python -m kg_construction.cli ingest-stream \
  --input ../urban-observations/processing-output/observations.jsonl
```

## Legacy source imports

The source-specific importers remain for historical data that predates the
shared stream. Do not run them alongside enriched-stream ingestion for the same
records.

```bash
python -m kg_construction.cli validate-config
python -m kg_construction.cli list-sources
python -m kg_construction.cli list-dates --source weather
python -m kg_construction.cli import --source weather --latest
python -m kg_construction.cli import --source air --date 20260814
```

Legacy source names are `air`, `alertcalifornia`, `cctv`, `citizen`, `gdelt`,
`pems-incidents`, `pems-stations`, `twitter`, and `weather`. These commands write
to the databases and some may call paid APIs. Start with one known date and
inspect the result before importing a range. Local legacy LLM/VLM enrichment is
disabled unless `SIGMUS_ALLOW_LEGACY_LOCAL_ENRICHMENT=1` is explicitly set.

## Troubleshooting

- If `config.json` is missing, copy `config.example.json` and edit it.
- If the image build cannot find Urban Observations, confirm that the sibling
  directory is named `urban-observations`.
- If ingestion is unhealthy, verify that the parent directory of
  `enriched_stream_path` exists and inspect `./docker-start logs ingestion`.
- If Neo4j authentication fails after changing its password, the existing
  volume still has the original password. Restore that password or deliberately
  recreate the volume.
- If Marqo is slow to download or start, verify available disk space and memory.

## Development

The test suite does not require running databases:

```bash
python -m pytest -q
```

Before submitting a change, also validate the launcher and Compose file:

```bash
./docker-start --help
./docker-start config
```

The second command requires a valid local `config.json` and checks the rendered
Compose configuration without printing secrets.
