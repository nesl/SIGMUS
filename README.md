# SIGMUS

SIGMUS turns raw, time-partitioned urban observations into a multimodal
knowledge graph. It imports traffic, weather, air-quality, camera, news, and
alert data; stores structured measurements in PostgreSQL; and represents
reports, actors, events, locations, and their relationships in Neo4j.

You do not need to read the accompanying paper to install or run the software.
The paper is available at [`docs/sigmus.pdf`](docs/sigmus.pdf) for background on
the system design and experiments.

## What the system does

An import processes one source and one or more `YYYYMMDD` data directories:

```text
Urban Observations files
        │
        ▼
source-specific parser ──► PostgreSQL measurement row
        │
        ├── image source ──► VLM caption
        ├── textual source ─► OpenAI extraction
        └── place name ─────► Google geocoding
        │
        ▼
Neo4j report/event/actor/location graph
```

SIGMUS reads observations from an external directory. It does not download the
source data itself; use Urban Observations or provide data with the documented
directory layout below.

## Requirements

- Python 3.10 or newer
- PostgreSQL 14 or newer
- Neo4j 5
- An OpenAI API key
- A Google Maps Platform key with Geocoding API access for GDELT, X, and
  Citizen imports
- An OpenAI-compatible vision-language-model endpoint for CCTV and
  ALERTCalifornia imports when `.caption` files are not already present
- Raw Urban Observations data

Marqo and SeaweedFS settings remain in the configuration for optional vector
and blob-storage workflows. They are not required for the basic import commands
shown below.

## 1. Install SIGMUS

```bash
git clone <repository-url> sigmus
cd sigmus
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install --no-deps --no-build-isolation -e .
```

The final editable installation makes `kg_construction`, `llm`, and `utilities`
importable as packages. Run the commands below from the repository root because
some prompt and metadata resources are intentionally addressed relative to it.

## 2. Start PostgreSQL and Neo4j

The following Docker commands provide a minimal local development setup. Choose
strong local passwords and use the same values in `.env`.

```bash
docker run --name sigmus-postgres \
  -e POSTGRES_DB=raw_data \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD='<postgres-password>' \
  -p 5432:5432 -d postgres:16

docker run --name sigmus-neo4j \
  -e NEO4J_AUTH='neo4j/<neo4j-password>' \
  -p 7474:7474 -p 7687:7687 -d neo4j:5
```

Wait until both containers are healthy enough to accept connections:

```bash
docker logs sigmus-postgres
docker logs sigmus-neo4j
```

Neo4j Browser is available at <http://localhost:7474>. PostgreSQL tables and
Neo4j graph structures are created as importers need them; there is no separate
schema-migration command.

For existing services, create a PostgreSQL database matching
`postgres_config.dbname` and give the configured user permission to create
tables, sequences, and indexes. The configured Neo4j user must be able to create
and update nodes, relationships, constraints, and indexes.

## 3. Configure paths and credentials

```bash
cp config.example.json config.json
cp .env.example .env
chmod 600 config.json .env
```

Edit `.env` and replace every `/absolute/path/...` and credential placeholder.
The OpenAI and Google Maps keys belong in this root-level `.env` file—not in
`config.json` and not in the Python source. For example:

```dotenv
SIGMUS_PULLED_DATA_ROOT=/mnt/urban-data/raw
SIGMUS_CCTV_LOCATIONS=/absolute/path/to/sigmus/resources/cctv.kml
SIGMUS_WEATHER_LOCATIONS=/absolute/path/to/sigmus/resources/owm_locations.txt
POSTGRES_PASSWORD=choose_a_postgres_password
NEO4J_PASSWORD=choose_a_neo4j_password
OPENAI_API_KEY=your_openai_api_key
GOOGLE_PLACES_API_KEY=your_google_maps_platform_key
```

`OPENAI_API_KEY` is used for text extraction. `GOOGLE_PLACES_API_KEY` must be a
Google Maps Platform key for a project with the Geocoding API enabled; it is
used to resolve locations in GDELT, X, and Citizen reports. The corresponding
entries in `config.json` should remain `${OPENAI_API_KEY}` and
`${GOOGLE_PLACES_API_KEY}` so SIGMUS reads the values from the environment.

Load the environment whenever you open a new shell:

```bash
set -a
. ./.env
set +a
export URBAN_SYSTEM_CONFIG="$PWD/config.json"
```

`URBAN_SYSTEM_CONFIG` may point to a configuration elsewhere. If it is unset,
SIGMUS reads `./config.json`. Exact `${VARIABLE}` values in the JSON are loaded
from the environment; a missing variable causes an immediate error naming it.
Neither `.env` nor `config.json` is tracked by Git.

## 4. Prepare the observation directory

`SIGMUS_PULLED_DATA_ROOT` must contain source/date partitions. Only the source
being imported needs to be present:

```text
<save_folder>/
├── air_data/YYYYMMDD/
├── alertcalifornia/YYYYMMDD/<camera>/
├── citizen_data/YYYYMMDD/
├── cctv/YYYYMMDD/<camera>/
├── gkg/YYYYMMDD/
├── pem_data_chp_incidents_day/YYYYMMDD/
├── pem_data_station_5min/YYYYMMDD/
├── twitter_data/YYYYMMDD/
└── weather_data/YYYYMMDD/<location>/
```

Validate configuration and paths before writing to either database:

```bash
python -m kg_construction.cli validate-config
```

This command checks configuration structure and local files only. It does not
contact APIs or modify PostgreSQL or Neo4j. If a required environment variable
was not loaded, it exits with an error naming that variable.

## 5. Import observations

List supported sources:

```bash
python -m kg_construction.cli list-sources
```

List the available dates for one source:

```bash
python -m kg_construction.cli list-dates --source weather
```

Import the newest available day:

```bash
python -m kg_construction.cli import --source weather --latest
```

Import an explicit day or several days:

```bash
python -m kg_construction.cli import --source air --date 20260814

python -m kg_construction.cli import \
  --source pems-stations \
  --date 20260813 \
  --date 20260814
```

Supported source names are:

| CLI source | Input directory | Additional service or resource |
|---|---|---|
| `air` | `air_data` | OpenAI; PurpleAir inventory for related queries |
| `alertcalifornia` | `alertcalifornia` | VLM unless captions already exist |
| `cctv` | `cctv` | `resources/cctv.kml`; VLM unless captions exist |
| `citizen` | `citizen_data` | OpenAI and Google geocoding |
| `gdelt` | `gkg` | OpenAI, Google geocoding, and news-page access |
| `pems-incidents` | `pem_data_chp_incidents_day` | No additional API |
| `pems-stations` | `pem_data_station_5min` | `kg_construction/pem_7_stations.txt` |
| `twitter` | `twitter_data` | OpenAI and Google geocoding |
| `weather` | `weather_data` | `resources/owm_locations.txt` |

Imports are not dry runs: they create PostgreSQL rows and Neo4j graph data and
may call paid APIs. Start with one known date, inspect both databases, and then
import a larger range. Importing the same day repeatedly may create duplicate
records; the CLI does not currently maintain an ingestion ledger.

## Optional services

### Vision-language model

Image importers call an OpenAI-compatible chat-completions endpoint configured
by `vlm_host.uri` and request the model name `NVILA-15B`. Start a compatible
server at that URI or pre-populate a `.caption` file beside each image. When a
caption file exists, SIGMUS reads it without calling the VLM.

### Marqo

Vector-backed incident workflows use the URI in `marqo_config`:

```bash
docker run --name sigmus-marqo -p 8882:8882 -d marqoai/marqo:latest
```

Marqo requires considerably more memory than the database containers.

## Destructive reset

The following module permanently removes SIGMUS data from configured databases
and prompts for confirmation:

```bash
python -m kg_construction.clear_all_databases
```

Do not use it as an installation or health-check command. Back up the databases
before intentionally resetting them.

## Troubleshooting

- `Required configuration environment variable is not set`: load `.env` with
  `set -a; . ./.env; set +a` before running SIGMUS.
- `save_folder is not a directory`: use an absolute mounted path for
  `SIGMUS_PULLED_DATA_ROOT`.
- PostgreSQL connection errors: confirm the container is running, the `raw_data`
  database exists, and `postgres_config` matches its published port.
- Neo4j authentication errors: confirm `NEO4J_PASSWORD` matches `NEO4J_AUTH` and
  that the Bolt URI is `bolt://localhost:7687` for the example container.
- Image import connection errors: start the configured VLM or provide caption
  sidecar files.
- A source date is rejected before import: run `list-dates` and verify that the
  directory name uses exactly eight digits (`YYYYMMDD`).
- GDELT imports fetch linked news pages; inaccessible or changed publisher pages
  can prevent individual articles from being enriched.

## Tests

The test suite does not require running databases:

```bash
python -m pytest -q
```
