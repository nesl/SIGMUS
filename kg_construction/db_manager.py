import os
import psycopg2
from datetime import datetime
from utilities.util import get_config, get_past_timestamp, generate_ordered_combinations
import re
import boto3
import uuid
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from neo4j import GraphDatabase
# import pyTigerGraph as tg
from kg_construction.ontology_classes import *
from kg_construction.situations.trends import *
from dateutil import parser
import pytz
import string
from dateutil import parser
import copy
import statistics
import json
import time

# Add fuzzy similarity matching
from fuzzywuzzy import fuzz

# LLM stuff
from llm.client import LLMClient, OpenAIClient

# marqo
import marqo

# Currently this is written synchronously - I don't expect a high throughput to be necessary for our frequency of pulling

TELESCOPE_TIMES = ["15 min", "1 hour", "8 hour", "1 day", "1 week"]
TABLE_TEMPLATES = {
    "cctv":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    sensor_name TEXT NOT NULL,
    seaweed_image_ref TEXT NOT NULL,
    caption TEXT NOT NULL
    """,
    "alertcalifornia":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    sensor_name TEXT NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    direction FLOAT NOT NULL,
    seaweed_image_ref TEXT NOT NULL,
    caption TEXT NOT NULL
    """,
    "pem_incidents":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    data_description TEXT NOT NULL,
    data_severity TEXT,
    data_duration FLOAT
    """,
    "pem_station":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    sensor_name INTEGER NOT NULL,
    data_avg_occupancy FLOAT NOT NULL,
    data_avg_speed FLOAT NOT NULL
    """,
    "gdelt_events":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    actor1_name TEXT NOT NULL,
    actor1_type TEXT,
    actor1_type_desc TEXT,
    actor1_geo_name TEXT,
    actor1_latitude FLOAT,
    actor1_longitude FLOAT,
    actor2_name TEXT NOT NULL,
    actor2_type TEXT,
    actor2_type_desc TEXT,
    actor2_geo_name TEXT,
    actor2_latitude FLOAT,
    actor2_longitude FLOAT,
    event_code TEXT,
    event_name TEXT,
    event_description TEXT,
    event_geo_name TEXT,
    event_latitude FLOAT,
    event_longitude FLOAT,
    event_date TEXT,
    original_link TEXT NOT NULL
    """,
    "air_quality":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    sensor_name INTEGER NOT NULL,
    data_pm25 FLOAT NOT NULL
    """,
    "weather":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    sensor_name TEXT NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    location_description TEXT NOT NULL,
    data_temp_f FLOAT NOT NULL,
    data_weather_description TEXT NOT NULL,
    data_humidity FLOAT NOT NULL,
    data_wind_mps FLOAT NOT NULL
    """,
    "citizen":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    data_event_name TEXT NOT NULL,
    data_event_type TEXT NOT NULL,
    data_event_desc TEXT NOT NULL
    """,
    "twitter":"""
    row_id INTEGER,
    time TIMESTAMPTZ NOT NULL,
    time_end TIMESTAMPTZ NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    data_body TEXT NOT NULL,
    data_event_type TEXT NOT NULL
    """
}

# This involves both a SQL database which stores object metadata (each row can store multiple modalities)
class TimeSeriesManager:

    def get_table_template_columns(self, table_template):
        terms = []
        for line in table_template.split("\n"):
            # print(line)
            terms.append(line.strip().split(" ")[0])
        terms = [x for x in terms if x != ""]
        return terms

    def setup_tables(self, table_name, table_template, additional_indexes=[]):
        
        try:
            # Connect to the database
            # conn = psycopg2.connect(**self.db_config)
            cursor = self.conn.cursor()
            
            # Create table if not exists
            CREATE_TABLE_QUERY = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                {table_template}
            );
            """
            cursor.execute(CREATE_TABLE_QUERY)

            # Setup the row_id column
            SETUP_ROW_ID_QUERY = f"""
            CREATE SEQUENCE IF NOT EXISTS id_seq START 1;
            ALTER TABLE {table_name} ALTER COLUMN row_id SET DEFAULT nextval('id_seq');
            """
            cursor.execute(SETUP_ROW_ID_QUERY)

            if additional_indexes:
                for index in additional_indexes:
                    ADD_INDEX_QUERY = f"""
                    CREATE INDEX IF NOT EXISTS idx_{index} ON {table_name} ({index});
                    """
                    cursor.execute(ADD_INDEX_QUERY)
            else:
                # Add index for ID
                ADD_INDEX_QUERY = f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_id ON {table_name} (row_id);
                """
                cursor.execute(ADD_INDEX_QUERY)

            # Commit changes
            self.conn.commit()
            print("Table created (if not exists), converted to hypertable successfully!")

        except Exception as e:
            print("Setup table Error:", e)
        
        finally:
            cursor.close()

    def __init__(self, table_name, table_template, index_names=[]):
        self.config = get_config()
        self.db_config = self.config["postgres_config"]

        # Initialize connection
        self.conn = psycopg2.connect(**self.db_config)
        self.table_name = table_name

        # self.delete_table()

        # Set up the table
        if table_name:
            self.setup_tables(table_name, table_template, index_names)
            
            self.table_template = table_template

        

    def clear_database(self):

        for table in TABLE_TEMPLATES.keys():
            self.delete_table(table)
    
        print("All tables deleted successfully!")

    def close_connection(self):
        self.conn.close()

    def convert_ms_to_timestamp(self, timestamp):

        # Check if unix timestamp
        if len(str(timestamp)) == 10:
            timestamp = datetime.utcfromtimestamp(timestamp)
        else:  # Otherwise, some YYYYMMDDHHMMSS format
            timestamp = parser.parse(str(timestamp))
            timestamp = timestamp.replace(tzinfo=pytz.UTC)

        return timestamp

    # List columns
    def list_columns(self):

        cur = self.conn.cursor()

        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position;
        """, (self.table_name,))

        columns = cur.fetchall()
        return [column[0] for column in columns]
    
    def get_modality_names(self):
        
        column_names = self.list_columns()
        modality_names = []
        for x in column_names:
            if "data_" in x:
                modality_names.append(x)
        return modality_names

    def get_modalities_numeric(self):

        modality_names = self.get_modality_names()

        cur = self.conn.cursor()

        cur.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s
        AND column_name = ANY(%s)
        AND data_type NOT IN ('text')
        ORDER BY ordinal_position;
        """, (self.table_name, modality_names))

        columns = cur.fetchall()
        return [column[0] for column in columns]
        

    # Function to fetch the last k rows
    def fetch_last_x_rows(self, k, observer_name = None):
        try:
            # Connect to the database
            cursor = self.conn.cursor()
            
            # Query to get the last x rows
            query = f"""
            SELECT * 
            FROM {self.table_name}
            ORDER BY time DESC
            LIMIT {k};
            """
            cursor.execute(query)

            if observer_name:  # If we filter by observer
                query = f"""
                SELECT * 
                FROM {self.table_name}
                WHERE sensor_name = '{observer_name}'
                ORDER BY time DESC
                LIMIT {k};
                """

            cursor.execute(query)
            
            rows = cursor.fetchall()
            
            # Print the results
            return rows
            
        except Exception as e:
            print("Error:", e)
        
        finally:
            cursor.close()
    
    # Function to fetch based on a range of time for a sensor
    def fetch_time_range(self, start_time, end_time, sensor_name, column_names = []):

        try:
            # Connect to the database
            cursor = self.conn.cursor()
            chosen_columns = ', '.join(column_names)
            
            
            # Query to get the last x rows
            query = f"""
            SELECT *
            FROM {self.table_name}
            WHERE time >= %s AND time <= %s AND sensor_name = %s
            ORDER BY time;
            """

            if column_names:
                # Query to get the last x rows
                query = f"""
                SELECT {chosen_columns} 
                FROM {self.table_name}
                WHERE time >= %s AND time <= %s AND sensor_name = %s
                ORDER BY time;
                """
            
            cursor.execute(query, (start_time, end_time, sensor_name))
            rows = cursor.fetchall()
            
            # Print the results
            return rows
            
        except Exception as e:
            print("Error:", e)
        
        finally:
            cursor.close()
    
    # Function to fetch based on a range of time for a sensor
    def fetch_closest_time(self, target_time, sensor_name):

        try:
            # Connect to the database
            cursor = self.conn.cursor()
            
            # Query to get the last x rows
            query = f"""
            SELECT *
            FROM {self.table_name}
            WHERE sensor_name = %s
            ORDER BY time - %s 
            LIMIT 1;
            """
            
            cursor.execute(query, (sensor_name, target_time))
            rows = cursor.fetchall()
            
            # Print the results
            return rows
            
        except Exception as e:
            print("Error:", e)
        
        finally:
            cursor.close()

    # Function to delete the table
    def delete_table(self, table_name):
        
        if not table_name:
            table_name = self.table_name
        
        cur = self.conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {table_name}")
        self.conn.commit()
        cur.close()

    # Function to insert the data
    def insert_data(self, data_tuple):

        # print(data_tuple)

        table_columns = self.get_table_template_columns(self.table_template)
        if table_columns[0] == "row_id":
            table_columns = table_columns[1:]

        INSERT_QUERY = f"""
        INSERT INTO {self.table_name} ({", ".join(table_columns)}) 
        VALUES ({", ".join(["%s"] * len(table_columns))})
        RETURNING row_id;
        """

        # print(INSERT_QUERY)
        try:
            # Connect to the database
            cursor = self.conn.cursor()

            # First element is always timestamp
            data_tuple = (self.convert_ms_to_timestamp(data_tuple[0]), *data_tuple[1:])
            
            # print(data_tuple)   
            # Execute the insert query
            cursor.execute(INSERT_QUERY, data_tuple)

            insert_id = cursor.fetchone()[0]

            # Commit the transaction
            self.conn.commit()
            # print("Time series data inserted successfully!")

        except Exception as e:
            print("Error:", e)
        
        finally:
            
            cursor.close()
            return insert_id
    
    # Function to insert the data
    def insert_geo_data(self, data_tuple):

        table_columns = self.get_table_template_columns(self.table_template)
        if table_columns[0] == "row_id":
            table_columns = table_columns[1:]

        INSERT_QUERY = f"""
        INSERT INTO {self.table_name} ({", ".join(table_columns)}) 
        VALUES ({", ".join(["%s"] * len(table_columns))})
        RETURNING row_id;
        """

        # print(INSERT_QUERY)
        try:
            # Connect to the database
            cursor = self.conn.cursor()

            # Execute the insert query
            cursor.execute(INSERT_QUERY, data_tuple)

            insert_id = cursor.fetchone()[0]

            # Commit the transaction
            self.conn.commit()
            # print("Time series data inserted successfully!")

        except Exception as e:
            print("Error:", e)
        
        finally:
            
            cursor.close()
            return insert_id
    
    # Function to retrieve data
    def execute_query(self, query):

        try:
            # Connect to the database
            cursor = self.conn.cursor()
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            # Print the results
            return rows
            
        except Exception as e:
            print("Error:", e)
        
        finally:
            cursor.close()

class BlobManager:

    def __init__(self):
        self.config = get_config()
        self.seaweedfs_config = self.config["seaweedfs_config"]
        # self.bucket_name = table_name
        # Seawed fs boto interface
        self.s3 = boto3.client('s3',
            endpoint_url=self.seaweedfs_config["endpoint"],  # SeaweedFS S3 endpoint
            aws_access_key_id='', 
            aws_secret_access_key='',  
            region_name='')
        
        # self.s3.create_bucket(Bucket=self.bucket_name)

    def clear_database(self):
        
        for table in TABLE_TEMPLATES.keys():
            self.clear_bucket(table)


    def clear_bucket(self, bucket_name):
        try:
            objects_to_delete = self.s3.list_objects_v2(Bucket=bucket_name)
        
            if 'Contents' in objects_to_delete:
                # Create a list of objects to delete
                delete_objects = [{'Key': obj['Key']} for obj in objects_to_delete['Contents']]

                # Batch delete objects
                delete_response = self.s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': delete_objects}
                )
                print(f"Deleted {len(delete_objects)} objects from bucket {bucket_name}.")
            else:
                print("No objects found in " + str(bucket_name))

        except (NoCredentialsError, PartialCredentialsError):
            print("Credentials not provided or incomplete.")
        except Exception as e:
            print(f"Error deleting objects from {bucket_name}: {e}")

    def generate_unique_id(self):
        return str(uuid.uuid4())  # Generate a UUID as the unique file key

    def upload_file(self, bucket_name, file_path):

        return ""

        object_id = self.generate_unique_id()
        try:
            with open(file_path, 'rb') as file:
                self.s3.upload_fileobj(file, bucket_name, object_id)
            # print(f"File uploaded successfully: {object_id}")
        except NoCredentialsError:
            print("Credentials not available")
        except Exception as e:
            print(f"Error uploading file: {e}")
        finally:
            return object_id

    # Download a file from SeaweedFS (as if it were S3)
    # def download_file(self, bucket_name, object_name, download_path):
    #     try:
    #         with open(download_path, 'wb') as file:
    #             self.s3.download_fileobj(bucket_name, object_name, file)
    #         print(f"File downloaded successfully: {download_path}")
    #     except NoCredentialsError:
    #         print("Credentials not available")
    #     except Exception as e:
    #         print(f"Error downloading file: {e}")

    def get_file_size(self, bucket_name, object_id):
        try:
            # Use head_object to get metadata of the file
            response = self.s3.head_object(Bucket=bucket_name, Key=object_id)
            file_size = response['ContentLength']
            print(f"File size: {file_size} bytes")
            return file_size
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                print(f"Error: Object with key '{object_id}' not found.")
            else:
                print(f"Error: {e}")
        except NoCredentialsError:
            print("Credentials not available")


# class KGManager:

#     def __init__(self):

#         self.config = get_config("../config.json")
#         self.graph_config = self.config["tigergraph_config"]
#         uri = self.graph_config["uri"]
#         username = self.graph_config["username"]
#         password = self.graph_config["password"]

#         self.conn = tg.TigerGraphConnection(host=uri, \
#             graphname="MyGraph",
#             username=username, password=password)

#         self.conn.gsql("CREATE GRAPH MyGraph ()")

#     def clear_database(self):
#         self.conn.gsql("DROP ALL")
#         print("Database reset.")

#     def insert_aggregator(self, aggregator):

#         schema_query = """
#         CREATE VERTEX Person (PRIMARY_ID id STRING, name STRING) WITH STATS="OUTDEGREE_BY_EDGETYPE"
#         CREATE EDGE Knows (FROM Person, TO Person)
#         """
#         self.conn.gsql(schema_query)

#         data = {
#             "vertices": {
#                 "Person": {
#                     "Alice": {"name": "Alice"},
#                     "Bob": {"name": "Bob"}
#                 }
#             },
#             "edges": {
#                 "Knows": {
#                     "Alice": {"Bob": {}}
#                 }
#             }
#         }

#         self.conn.upsertData(data)



class vectorstoreManager:

    def __init__(self, index_name="incidents"):

        self.config = get_config()
        self.marqo_config = self.config["marqo_config"]
        uri = self.marqo_config["uri"]
        self.mq_client = marqo.Client(url=uri)
        self.index_name = index_name

        index_config = {
            "textPreprocessing":{
                "splitLength": 5,
                "splitOverlap": 0,
                "splitMethod": "sentence"
            },
            "model":"hf/e5-base-v2"
        }

        
        # Create the index if it doesn't exist
        if not self.index_exists(index_name):
            self.mq_client.create_index(index_name,settings_dict=index_config)
            print(f"Index {index_name} created.")
        
            

    def obtain_similar_docs(self, query, top_k=5):

        # Search for similar documents
        results = self.mq_client.index(self.index_name).search(
            q=query,
            limit=top_k
        )

        return results
    
    def insert_incident(self, incident_id, incident_label, incident_text):

        # Add the incident data to the index
        results = self.mq_client.index(self.index_name).add_documents([
            {"id": incident_id, "label": incident_label, "text": incident_text}
        ], tensor_fields=["label"])

        print("Adding incident: " + str(incident_label))

        return results
        
        
    def test_insertion(self):

        # List loaded models
        # print(self.mq_client.get_loaded_models())
        # print(self.mq_client.index(self.index_name).get_stats())

        # Add some test documents
        results = self.mq_client.index(self.index_name).add_documents([
            {"id": "1", "label": "2024 Venice Shooting","text": "This is a test document."},
            {"id": "2", "label": "2023 Happy Feet Movie Remake", "text": "This is another test document."},
            {"id": "3", "label": "2024 Barker Dam Crash", "text": "This is a third test document."}
        ], tensor_fields=[])
        # , tensor_fields=["label"]

        self.list_top_k_docs()

    def index_exists(self,index_name):
        index_list = self.mq_client.get_indexes()["results"]
        index_list = [x["indexName"] for x in index_list]
        return index_name in index_list

    def clear_database(self):

        indexes = self.mq_client.get_indexes()["results"]
        index_list = [x["indexName"] for x in indexes]
        for index_name in index_list:
            self.mq_client.delete_index(index_name)
            print(f"Deleted {index_name}")

    def list_top_k_docs(self, top_k=100):
        results = self.mq_client.index(self.index_name).search(
            q="Give me all test documents"
        , limit=top_k)
        for doc in results['hits']:
            print(doc)



class KGManager:

    def __init__(self, time_series_manager=None, connect_reports=True, use_vectordb=False):

        self.config = get_config()
        self.neo4j_config = self.config["neo4j_config"]
        uri = self.neo4j_config["uri"]
        username = self.neo4j_config["username"]
        password = self.neo4j_config["password"]
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

        # Have a corresponding timeseries manager
        self.ts_manager = time_series_manager

        # Obtain merging prompts
        with open("llm/prompts/align_nodes.json", "r") as f:
            self.align_actor_prompt = json.load(f)

        # Obtain incident linking prompts
        with open("llm/prompts/incident_linking.json", "r") as f:
            self.incident_linking_prompt = json.load(f)

        # LLM client
        # llm_uri = self.config["llm_host"]["uri"]
        # self.llm_client = LLMClient(llm_uri)
        self.llm_client = OpenAIClient()

        # initialize vectordb
        if use_vectordb:
            self.vector_db = vectorstoreManager("incidents")

        # Keep track of different times
        self.link_incident_times = []
        self.link_actor_times = []
        self.link_modality_times = []

        self.connect_reports = connect_reports



    def clear_database(self):
        query = "MATCH (n) DETACH DELETE n"  # Deletes all nodes and relationships

        with self.driver.session() as session:
            session.run(query)
            print("All nodes and relationships have been deleted.")
        
        # Also clear atached vectordb
        self.vector_db.clear_database()

    def close_driver(self):
        self.driver.close()

    # Insert geo entity for observer
    def insert_geo_entity(self, ent_id, geo_entity, entity_type):

        # Also ignore if entity is empty
        if not geo_entity.address and not geo_entity.coordinates:
            return None
        # And again, ignore if nan or description is unknown
        if all([x != x for x in geo_entity.coordinates]) or geo_entity.description.lower() == "unknown":
            return None
        
        address = geo_entity.address
        coordinates = geo_entity.coordinates
        description = geo_entity.description

        query = f"""
        MATCH (ent:{entity_type}) WHERE elementId(ent) = $ent_id
        MERGE (geo:GeoEntity {{address: $address, coordinates: $coordinates, description:$description}})
        MERGE (ent)-[:LOCATED_AT]->(geo)
        RETURN elementId(geo) as geo_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                entity_type=entity_type,
                ent_id = ent_id,
                address=address,
                coordinates=coordinates,
                description=description
            )
            geo_id = result.single()["geo_id"]
        
        return geo_id

    # Insert the geo entity for a report
    def insert_geo_report(self, rep_id, geo_entity):

        # If report is empty, ignore
        if not geo_entity.value:
            return None

        geo_val = geo_entity.value
        geo_description = geo_entity.geo_type_description

        query = """
        MATCH (rep:Report) WHERE elementId(rep) = $rep_id
        MERGE (geo:GeoEntity {geo_description: $geo_description, value:$geo_val})
        CREATE (rep)-[:OCCURRED_AT]->(geo)
        RETURN elementId(geo) as geo_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                rep_id=rep_id,
                geo_description=geo_description,
                geo_val=geo_val
            )
            geo_id = result.single()["geo_id"]
        return geo_id

    # Insert the time entity
    def insert_time_action(self, act_id, time_entity):
        start_time = time_entity.startTime
        end_time = time_entity.endTime

        # OBTAIN NATURAL LANGUAGE LABELS
        start_obj = parser.parse(str(start_time))
        end_obj = parser.parse(str(end_time))
        start_time_str = start_obj.strftime("%B %d, %Y at %I:%M:%S %p")
        end_time_str = end_obj.strftime("%B %d, %Y at %I:%M:%S %p")

        query = """
        MATCH (ev:Action) WHERE elementId(ev) = $act_id
        MERGE (tim:TimeEntity {start_time: $st, end_time:$et, start_time_str: $st_str, end_time_str: $et_str})
        CREATE (ev)-[:CAPTURE_TIME]->(tim)
        RETURN elementId(tim) as time_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                act_id=act_id,
                st=start_time,
                et=end_time,
                st_str=start_time_str,
                et_str=end_time_str
            )
            time_id = result.single()["time_id"]
        
        return time_id

    # Insert the time entity
    def insert_time_report(self, rep_id, time_entity):
        start_time = time_entity.startTime
        end_time = time_entity.endTime

        # Sometimes the time will be unix, which can't be directly parsed

        # OBTAIN NATURAL LANGUAGE LABELS
        start_time_str, end_time_str = "",""
        try:
            start_obj = parser.parse(str(start_time))
            end_obj = parser.parse(str(end_time))
            start_time_str = start_obj.strftime("%B %d, %Y at %I:%M:%S %p")
            end_time_str = end_obj.strftime("%B %d, %Y at %I:%M:%S %p")
        except Exception as e:
            # Error, probably means parse error as unix timestamp
            start_obj = datetime.utcfromtimestamp(start_time)
            end_obj = datetime.utcfromtimestamp(end_time)
            start_time_str = start_obj.strftime("%B %d, %Y at %I:%M:%S %p")
            end_time_str = end_obj.strftime("%B %d, %Y at %I:%M:%S %p")

        query = """
        MATCH (rep:Report) WHERE elementId(rep) = $rep_id
        MERGE (tim:TimeEntity {start_time: $st, end_time:$et, start_time_str: $st_str, end_time_str: $et_str})
        CREATE (rep)-[:CAPTURE_TIME]->(tim)
        RETURN elementId(tim) as time_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                rep_id=rep_id,
                st=start_time,
                et=end_time,
                st_str=start_time_str,
                et_str=end_time_str
            )
            time_id = result.single()["time_id"]
        
        return time_id

    # Compares fuzzy match similarity
    def fuzzy_match(self, str1, str2, delimiter=" "):
        # Tokenize by splitting on whitespace and lowercasing
        tokens1 = list(str1.lower().split(delimiter))
        tokens2 = list(str2.lower().split(delimiter))
        
        target_len = max(len(tokens1), len(tokens2))
        shorter_longer = (tokens1, tokens2) if len(tokens1) < len(tokens2) else (tokens2, tokens1)
        shorter_tokens = shorter_longer[0]
        longer_tokens = shorter_longer[1]

        # If we have different sizes, we need to create permutations
        shorter_combos = [shorter_tokens]
        if len(shorter_tokens) != len(longer_tokens):
            shorter_combos = generate_ordered_combinations(shorter_tokens, target_len)

        # Compare each pair
        overall_scores = []
        for compare_tokens in shorter_combos:
            scores = []
            for t1, t2 in zip(longer_tokens, compare_tokens):
                # If one token is missing, apply a penalty score (e.g., 0 for empty string comparison)
                if t1 == "" or t2 == "":
                    scores.append(100)  # missing a name is fine
                else:
                    scores.append(fuzz.ratio(t1, t2))
            overall_scores.append(statistics.mean(scores))
            
        return max(overall_scores)


    def get_top_similar_nodes(self, target_attr_val, node_list, attribute_name, top_k=1):

        for x in node_list:
            current_attr_val = x["attributes"][attribute_name]
            fuzzy_similarity = self.fuzzy_match(target_attr_val, current_attr_val)

            x["similarity"] = fuzzy_similarity
        
        # Sort by most similar
        node_list = sorted(node_list, key=lambda x:x["similarity"])
        return node_list[-top_k:]

    def obtain_node_properties(self, node):
        out_str = []
        
        for key, value in node.items():
            out_str.append(f"{key}: {value}")
        return "\n".join(out_str)
    
    # Format actor context
    def format_actor_context(self,node_attributes, node_location, other_contexts):
        
        node_attribute_str = self.obtain_node_properties(node_attributes)
        node_location_str = self.obtain_node_properties(node_location)

        # Iterate through all context
        context_strs = []
        for context_section in other_contexts:
            other_actor_str = self.obtain_node_properties(context_section[0])
            action_str = self.obtain_node_properties(context_section[1])
            situation_str = context_section[2]
            geo_str = self.obtain_node_properties(context_section[3])

            context_section_str = f"\nThis candidate actor is associated with the following other actor: \n{other_actor_str}\n, The association between these two actors is this relationship: {action_str}\n, This relationship took place at: {geo_str}\n, This overall relationship involved: {situation_str}\n"
            context_strs.append(context_section_str)
        full_context_str = "\n".join(context_strs)

        message_str = f"This candidate actor has the following name and other info:\n {node_attribute_str}.  This actor is located at: \n{node_location_str}\n\nIt is also connected to the following actors, actions, and situations:\n{full_context_str}"

        return message_str

    # Query actor nodes for more info
    def query_actor_context_neo4j(self, similar_nodes):

        messages = []
        node_ids = {}
        # Iterate through each node
        for i,node in enumerate(similar_nodes):
            node_id = node["id"]
            node_attributes = node["attributes"]
            node_ids[i] = (node_id, node_attributes)

            # Node location
            node_location = None
            query_located_at = f"""
            MATCH (a)-[:LOCATED_AT]->(out)
            WHERE elementId(a) = $object_id
            RETURN out
            """
            with self.driver.session() as session:
                result = session.run(query_located_at, object_id=node_id)
                connected_node = result.single()  # Get the first result
                if connected_node:
                    node_location = connected_node['out']
            
            # Associated Actors and Actions
            other_contexts = None
            query_actors = f"""
            MATCH (actor:Actor)-[:INVOLVED_IN]->(action:Action)<-[:INVOLVED_IN]-(other_actor:Actor)
            MATCH (action)-[:INFERRED_FROM]->(data:Data)
            MATCH (action)-[:LOCATED_AT]->(geo:GeoEntity)
            WHERE elementId(actor) = $target_actor_id AND elementId(other_actor) <> $target_actor_id
            RETURN other_actor, action, data, geo
            """
            with self.driver.session() as session:
                result = session.run(query_actors, target_actor_id=node_id)
                other_contexts = [(record['other_actor'], record['action'], record['data']['situation'],record['geo']) for record in result]

            # This actor doesn't have a location or any other context
            if not node_location or not other_contexts:
                return "", node_ids
            

            message_content = self.format_actor_context(node_attributes, node_location, other_contexts)
            messages.append(message_content)

        candidate_actors = ["Candidate " + str(i) + "\n\n" + x for i,x in enumerate(messages)]
        candidate_actors = "\n\n".join(candidate_actors)

        return candidate_actors, node_ids
            
                
            
    # Query actor context for an ontology object
    def query_actor_context_ont(self, node):
        
        actor_attribute_str = f"""This actor has the following name and other info:\n Name: {node.name}, Actor Type Description {node.actor_type_desc}.  \n\nThis actor is located at: \nAddress:{node.geo_info.address}\nLatitude/Longitude Coordinates:{node.geo_info.coordinates}\nLocation Description:{node.geo_info.description}\n\n"""

        associated_actors = [node.parent.actor_triple_list[0][0], node.parent.actor_triple_list[0][2]]

        # only select the actor which is not equal
        # print(node.name)
        # print([x.name for x in associated_actors])
        associated_actor = [x for x in associated_actors if x.name != node.name][0]

        associated_action = node.parent.actor_triple_list[0][1]
        associated_situation = node.parent.hasSituation
        associated_action_geo = associated_action.geo_info

        actor_str = f"This actor is associated with the following other actor: \nName: {associated_actor.name}, Actor Type Description {associated_actor.actor_type_desc}.\n"
        action_str = f"The association between these two actors is this relationship: \nAction Code: {associated_action.action_code}, Action Name: {associated_action.action_name}, Action Description: {associated_action.action_description}.\n"
        situation_str = f"This overall relationship involved: {associated_situation}.\n"
        geo_str = f"This relationship took place at: \nAddress:{associated_action_geo.address}\nLatitude/Longitude Coordinates:{associated_action_geo.coordinates}\nLocation Description:{associated_action_geo.description}\n"

        message_str = actor_attribute_str + actor_str + action_str + situation_str + geo_str

        return message_str


    def update_actor_node(self, node, chosen_node, update_name):


        chosen_node_id, chosen_node_attributes = chosen_node
        
        # node.name = chosen_node_attributes["name"]
        # node.actor_type = chosen_node_attributes["actor_type"]
        # node.actor_type_desc = chosen_node_attributes["actor_type_desc"]

        chosen_name = node.name if update_name else chosen_node_attributes["name"] 
        chosen_actor_type = chosen_node_attributes["actor_type"] + "," + node.actor_type
        chosen_actor_type_desc = chosen_node_attributes["actor_type_desc"] + "," + node.actor_type_desc

        # If we update, we need to overwrite all data.  Otherwise we just append to the existing information (like actor type, actor type description)
        query = """
        MATCH (actor:Actor) 
        WHERE elementId(actor) = $actor_id
        SET actor.name = $new_name,
            actor.actor_type = $new_actor_type,
            actor.actor_type_desc = $new_actor_type_desc
        RETURN elementId(actor) as actor_id
        """
        with self.driver.session() as session:
            result = session.run(query, actor_id=chosen_node_id, new_name=chosen_name, new_actor_type=chosen_actor_type, new_actor_type_desc=chosen_actor_type_desc)
            updated_actor = result.single()

            return updated_actor['actor_id'] if updated_actor else None
        


    # Check other nodes of same type, and order them by jaccard similarity
    def merge_similar_nodes(self, node, node_type):
        

        merge_occurred = False
        query = f"""
        MATCH (n:{node_type})
        RETURN elementId(n) AS id, properties(n) AS attrs
        """
        with self.driver.session() as session:
            result = session.run(query)
            node_list = [{"id": record["id"], "attributes": record["attrs"]} for record in result]

        if node_type == "Actor":

            most_similar_nodes = self.get_top_similar_nodes(node.name, node_list, "name")


            # Query the nodes
            neo4j_context, node_ids = self.query_actor_context_neo4j(most_similar_nodes)


            if neo4j_context: # There's actually context for this node
                target_context = self.query_actor_context_ont(node)

                # Now, prompt the LLM
                prompt = self.align_actor_prompt["choose_actors"] + "\n\n" + target_context + "\n\n" + neo4j_context + "\n\n" + self.align_actor_prompt["choose_actors_format"]


                output, thoughts = self.llm_client.send_message_to_llm_single(prompt)

                # Get the chosen node
                chosen_id = output.split("<ACTOR>")[1].split("</ACTOR>")[0]
                chosen_id = int(chosen_id)


                # We have to update
                if chosen_id > -1:
                    merge_occurred = True
                    chosen_node = node_ids[chosen_id]
                    update_name = True if "<UPDATE_NAME>" in output else False
                    actor_id = self.update_actor_node(node, chosen_node, update_name)
                    return merge_occurred, actor_id

        return merge_occurred, None


    def create_or_merge_actor(self, actor):

        # Do some sanitization
        def sanitize_value(value):
            return "Unknown" if value != value else value
        
        name = sanitize_value(actor.name)
        actor_type = sanitize_value(actor.actor_type)
        actor_type_desc = actor.actor_type_desc

        actor_merge_start = time.time()
        merge_occurred = False
        if name != "Unknown":
            merge_occurred, actor_id = self.merge_similar_nodes(actor, "Actor")
        actor_merge_end = time.time()
        self.link_actor_times.append(actor_merge_end - actor_merge_start)

        # If we merged, do not create a new node, otherwise just create a new node
        if merge_occurred:
            return actor_id
        else:
            query = """
            MERGE (actor:Actor {name: $name, actor_type: $actor_type, actor_type_desc: $actor_type_desc})
            RETURN elementId(actor) AS actor_id
            """
            with self.driver.session() as session:
                result = session.run(query, name=name, actor_type=actor_type, actor_type_desc=actor_type_desc)
                actor = result.single()
                return actor['actor_id']  # Return the element ID of the actor node
        
    
    def create_or_merge_action(self, data_id, actor1_id, actor2_id, action_code, action_name, action_description):

        query = """
        MATCH (act1:Actor) WHERE elementId(act1) = $actor1_id
        MATCH (act2:Actor) WHERE elementId(act2) = $actor2_id
        MATCH (dat:Data) WHERE elementId(dat) = $data_id
        CREATE (ev:Action {action_code: $action_code, action_name: $action_name, action_description: $action_description})
        
        CREATE (act1)-[:REL_TYPE]->(act2)
        CREATE (act1)-[:INVOLVED_IN]->(ev)
        CREATE (act2)-[:INVOLVED_IN]->(ev)
        CREATE (ev)-[:INFERRED_FROM]->(dat)
        RETURN elementId(ev) AS ev_id
        """.replace("REL_TYPE", action_name)
        with self.driver.session() as session:
            result = session.run(query, 
                                 actor1_id=actor1_id, 
                                 actor2_id=actor2_id, 
                                 data_id=data_id,
                                 action_code=action_code, 
                                 action_name=action_name, 
                                 action_description=action_description)
            action = result.single()
            return action['ev_id']  # Return the element ID of the Action node
            

    # Insert the actor triple list
    def insert_actor_triple(self, data_id, actor1, action, actor2):

        
        # Also, remove punctuation from the action name
        # translator = str.maketrans('', '', string.punctuation)
        # action_name = action_name.translate(translator)
        # Remove other weird characters
        action_name = ''.join([x for x in action.action_name if (x.isalnum() or x==" ")])
        action_name = action_name.replace(" ", "_").upper()

        actor1_id = self.create_or_merge_actor(actor1)

        actor2_id = self.create_or_merge_actor(actor2)

        ev_id = self.create_or_merge_action(data_id, actor1_id, actor2_id, action.action_code, action_name, action.action_description)


        # Insert the associated geo information
        self.insert_geo_entity(actor1_id, actor1.geo_info, entity_type="Actor")
        self.insert_geo_entity(ev_id, action.geo_info, entity_type="Action")
        self.insert_geo_entity(actor2_id, actor2.geo_info, entity_type="Actor")
        self.insert_time_action(ev_id, action.time_entity)

        # query = f"""
        # MATCH (dat:Data) WHERE elementId(dat) = $data_id
        # MERGE (act1:Actor {{name:$actor1_name, actor_type:$actor1_type, actor_type_desc:$actor1_type_desc}})
        # CREATE (ev:Action {{action_code:$action_code, action_name:$action_name, action_description:$action_description}})
        # MERGE (act2:Actor {{name:$actor2_name, actor_type:$actor2_type, actor_type_desc:$actor2_type_desc}})
        # CREATE (act1)-[:{action_name}]->(act2)
        # CREATE (act1)-[:INVOLVED_IN]->(ev)
        # CREATE (act2)-[:INVOLVED_IN]->(ev)
        # CREATE (ev)-[:INFERRED_FROM]->(dat)
        # RETURN elementId(act1) AS a1_id, elementId(act2) AS a2_id, elementId(ev) AS ev_id
        # """

        

        
        # with self.driver.session() as session:
        #     result = session.run(
        #         query,
        #         data_id=data_id,
        #         actor1_name=sanitize_value(actor1.name),
        #         actor1_type=sanitize_value(actor1.actor_type),
        #         actor1_type_desc=actor1.actor_type_desc,
        #         action_code=action.action_code,
        #         action_name = action_name,
        #         action_description = action.action_description,
        #         actor2_name=sanitize_value(actor2.name),
        #         actor2_type=sanitize_value(actor2.actor_type),
        #         actor2_type_desc=actor2.actor_type_desc,
        #     )
        #     result = result.single()

        

    # Insert modality information
    def insert_modality(self, rep_id, modality_data):

        modality_type = modality_data.modality_type
        blob_ref = modality_data.hasBlob
        filepath_ref = modality_data.filePath
        modality_val = modality_data.value
        situation_info = modality_data.hasSituation
        actor_triple_list = modality_data.actor_triple_list


        # Convert the situation info into a string (dict right now)
        situation_info = str(situation_info)

        query = """
        MATCH (rep:Report) WHERE elementId(rep) = $rep_id
        CREATE (dat:Data {data_type: $dt, data_val:$dv, blob_ref:$br, filepath_ref:$fr, situation:$si})
        CREATE (rep)-[:HAS_DATA]->(dat)
        RETURN elementId(dat) as data_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                rep_id=rep_id,
                dt=modality_type,
                dv=modality_val,
                br=blob_ref,
                fr=filepath_ref,
                si=situation_info
            )
            data_id = result.single()["data_id"]

        # Iterate through the actor triples
        for actor_trip in actor_triple_list:
            try:
                self.insert_actor_triple(data_id, actor_trip[0], actor_trip[1], actor_trip[2])
            except Exception as e:
                print("DB manager insert modality: " + str(e))
                continue  # Just continue...
        
        return data_id
    
    
    def insert_incident_node(self, rep_id, incident):

        # Incident data
        incident_label = incident.label
        incident_text = incident.context

        query = """
        MATCH (rep:Report) WHERE elementId(rep) = $rep_id
        MERGE (inc:Incident {label: $label})
        CREATE (rep)-[:HAS_LABEL]->(inc)
        RETURN elementId(inc) as inc_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                rep_id=rep_id,
                label=incident_label
            )
            inc_id = result.single()["inc_id"]

        # Also add directly to the vector store
        self.vector_db.insert_incident(inc_id, incident_label, incident_text)

        return inc_id
    

    def add_incident_relation(self, rep_id, result, incident,reason_text,  isparent):

        # Add the incident
        inc_id = self.insert_incident_node(rep_id, incident)

        # Get incident id of result
        result_id = result["id"]

        query = ""
        if isparent:
            # Add the relationship
            query = """
            MATCH (inc:Incident) WHERE elementId(inc) = $inc_id
            MATCH (inc2:Incident) WHERE elementId(inc2) = $result_id
            CREATE (c:LLM_CONTEXT {reason: $reason})
            CREATE (inc)-[:IS_PART_OF]->(c)
            CREATE (c)-[:IS_PART_OF]->(inc2)
            """
        else:
            # Add the relationship
            query = """
            MATCH (inc:Incident) WHERE elementId(inc) = $inc_id
            MATCH (inc2:Incident) WHERE elementId(inc2) = $result_id
            CREATE(c:LLM_CONTEXT {reason:$reason})
            CREATE (inc2)-[:IS_PART_OF]->(c)
            CREATE (c)-[:IS_PART_OF]->(inc)
            """

        with self.driver.session() as session:
            result = session.run(
                query,
                inc_id=inc_id,
                result_id =result_id,
                reason=reason_text
            )

    def merge_incidents(self, rep_id, result):

        # Get incident id of result
        result_id = result["id"]

        # In the case of merging, we just add the relation
        query = """
        MATCH (inc:Incident) WHERE elementId(inc) = $inc_id
        MATCH (rep:Report) WHERE elementId(rep) = $rep_id
        CREATE (rep)-[:HAS_LABEL]->(inc)
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                inc_id=result_id,
                rep_id=rep_id
            )


    # Insert the incident
    def link_incidents(self, rep_id, incident):

        # Incident data
        incident_label = incident.label
        incident_text = incident.context


        # Don't forget to merge the incident if similar incidents already exist

        # Check if this incident already exists
        results = self.vector_db.obtain_similar_docs(incident_label)
        result_hits = results["hits"]



        # If there's even results
        if result_hits:

            results_text = str(["\nCandidate " + str(i) + "\n" + str(x) for i,x in enumerate(result_hits)])
            
            prompt = self.incident_linking_prompt["match_incidents"] + "\n\nincident label: " + incident_label + "\nincident text: " + incident_text + "\n\n Candidates: \n" + results_text + self.incident_linking_prompt["match_incidents_format"]

            output, thoughts = self.llm_client.send_message_to_llm_single(prompt)
            
            # Split the different incidents
            incident_text = output.split("<LIST>")[1].split("</LIST>")[0]
            if "," in incident_text:
                incident_outputs = incident_text.split("<NEXT>")
            else:
                incident_outputs = [incident_text]

            # Always put the "SAME" first
            incident_outputs = sorted(incident_outputs, key=lambda s: "<SAME>" not in s)

            for inc_output in incident_outputs:

                print(inc_output)

                # Parse the output
                chosen_id = int(inc_output.split("<INCIDENT>")[1].split("</INCIDENT>")[0])
                if chosen_id > -1:  # We have a relation

                    chosen_incident_data = result_hits[chosen_id]
                    if "<SAME>" in inc_output:
                        self.merge_incidents(rep_id, chosen_incident_data)
                        incident.label = chosen_incident_data['label']
                        
                    elif "<PARENT>" in inc_output:
                        
                        # Get the reasoning
                        reason_text = ""
                        if "WHY" in inc_output:
                            reason_text = inc_output.split("<WHY>")[1].split("</WHY>")[0]

                        self.add_incident_relation(rep_id, chosen_incident_data, incident, reason_text, isparent=True)
                    elif "<CHILD>" in inc_output:

                        # Get the reasoning
                        reason_text = ""
                        if "WHY" in inc_output:
                            reason_text = inc_output.split("<WHY>")[1].split("</WHY>")[0]

                        self.add_incident_relation(rep_id, chosen_incident_data, incident, reason_text, isparent=False)

            else:
                self.insert_incident_node(rep_id, incident)

        else:  # No result, then add to graph
            self.insert_incident_node(rep_id, incident)


    # Insert the report
    def insert_report(self, obs_id, report):

        db_id = report.db_id
        modality_list = report.hasModality
        report_geo_entity = report.reportLocation
        
        report_time_entity = report.captureTime
        incident = report.hasIncident

        query = """
        MATCH (obs:Observer) WHERE elementId(obs) = $obs_id
        CREATE (rep:Report {db_id: $db_id})
        CREATE (obs)-[:HAS_REPORT]->(rep)
        RETURN elementId(rep) as rep_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                obs_id=obs_id,
                db_id=db_id
            )
            rep_id = result.single()["rep_id"]
        
        # Attach the geo_entity if it exists
        if report_geo_entity:
            self.insert_geo_report(rep_id, report_geo_entity)

        # Attach the time_entity
        self.insert_time_report(rep_id, report_time_entity)

        # Attach the modalities
        for modality_data in modality_list:
            self.insert_modality(rep_id, modality_data)

        # Attach the incident
        if incident:
            incident_link_start = time.time()
            self.link_incidents(rep_id, incident)
            incident_link_end = time.time()
            self.link_incident_times.append(incident_link_end - incident_link_start)

        # also, check if there are connections across modalities
        # if self.connect_reports:
        #     reason_start_t = time.time()
        #     self.qclient.reason_on_report(rep_id, self.llm_client)
        #     print(time.time() - reason_start_t)


    # Keep a list of telescoping timestamps in the KG for each observer
    def telescope_reports(self, observer_id, observer_name, current_ts, max_reports=5):

        # First, we delete all reports for this observer (not efficient but rn just protoyping) if greater than X
        query = """
        MATCH (n)-[:HAS_REPORT]->(r) 
        WHERE elementId(n) = $obs_id
        RETURN COUNT(r) AS report_count
        """
        with self.driver.session() as session:
            result = session.run(
                query,
                obs_id=observer_id,
            )
            report_count = result.single()["report_count"]
        
        if report_count > max_reports:
            # Get all connected reports.  Delete them as necessary
            delete_query = """
            MATCH (a)-[:HAS_REPORT]->(firstLayer)
            WHERE elementID(a) = $obs_id
            WITH COLLECT(firstLayer) AS to_delete
            MATCH (n) WHERE ANY(x IN to_delete WHERE (x)-[*]->(n) OR x = n)
            DETACH DELETE n;
            """
            with self.driver.session() as session:
                result = session.run(
                    delete_query,
                    obs_id=observer_id,
                )
        
        # Now, we need to obtain and re-construct the KG.
        # Right now we take the 5 most recent reports and also add time
        nodes_to_recreate = self.ts_manager.fetch_last_x_rows(5, observer_name)
        selected_row_ids = [x[0] for x in nodes_to_recreate]  # Keep track of the db_ids which are already used (no duplicates)
        # Make sure we 
        for time_delta in TELESCOPE_TIMES[::-1]:
            
            current_ts_converted = self.ts_manager.convert_ms_to_timestamp(current_ts)
            past_timestamp = get_past_timestamp(time_delta, current_ts_converted)

            closest_row = self.ts_manager.fetch_closest_time(past_timestamp, \
                             observer_name)
            closest_row_id = closest_row[0][0]
            if closest_row_id not in selected_row_ids:
                selected_row_ids.append(selected_row_ids)
                nodes_to_recreate.append(closest_row)
            
        self.create_kg_nodes(nodes_to_recreate)


    # Create the observer
    #  Telescope its reports as necessary
    def insert_observer(self, agg_id, observer, current_ts=None):

        observer_name = observer.name
        observer_geo_entity = observer.locatedAt
        report = observer.hasReport

        query = """
        MATCH (agg:Aggregator) WHERE elementId(agg) = $agg_id
        MERGE (obs:Observer {name: $name})
        MERGE (agg)-[:HAS_OBSERVER]->(obs)
        RETURN elementId(obs) as obs_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                agg_id=agg_id,
                name=observer_name
            )
            obs_id = result.single()["obs_id"]
        
        # Now insert the geo observer
        if observer_geo_entity:
            self.insert_geo_entity(obs_id, observer_geo_entity, entity_type="Observer")

        # If we connect a ts manager
        # if self.ts_manager and current_ts:
        #     self.telescope_reports(obs_id, observer_name, current_ts)

        # Insert the report
        self.insert_report(obs_id, report)


    def insert_aggregator(self, aggregator, current_ts=None):
        
        aggregator_name = aggregator.name
        observer = aggregator.hasObserver

        query = """
        MERGE (agg:Aggregator {name: $name})
        RETURN elementId(agg) AS agg_id
        """

        with self.driver.session() as session:
            result = session.run(
                query,
                name=aggregator_name
            )
            agg_id = result.single()["agg_id"]
            
        # Use the result to create the observer
        self.insert_observer(agg_id, observer, current_ts)


        self.close_driver()

    def get_values_from_chosen_columns(self, db_row, column_names, names_of_interest):

        values_of_interest = []
        for name in names_of_interest:
            db_value = db_row[column_names.index(name)]
            values_of_interest.append(db_value)
        return values_of_interest


    # Actor(actor1_name, actor1_type, actor1_type_desc, actor1_geo_name, [actor1_latitude, actor1_longitude]
    # ActorAction(event_code, event_name, event_description, event_geo_name, [event_latitude, event_longitude], event_date),
    def create_actor_action_entities(self, db_row, column_names, remaining_names):
        
        actor1_columns = ["actor1_name", "actor1_type", "actor1_type_desc", \
                          "actor1_geo_name","actor1_latitude","actor1_longitude"]
        for c_name in actor1_columns:
            remaining_names.remove(c_name)

        actor1_values = self.get_values_from_chosen_columns(db_row, column_names, actor1_columns)
        # Note - lat long values have to be put together
        actor1_values = actor1_values[:-2] + [[actor1_values[-2], actor1_values[-1]]]
        actor1_entity = Actor(*actor1_values)


        actor2_columns = ["actor2_name", "actor2_type", "actor2_type_desc", \
            "actor2_geo_name", "actor2_latitude","actor2_longitude"]
        for c_name in actor2_columns:
            remaining_names.remove(c_name)

        actor2_values = self.get_values_from_chosen_columns(db_row, column_names, actor2_columns)
        actor2_values = actor2_values[:-2] + [[actor2_values[-2], actor2_values[-1]]]
        actor2_entity = Actor(*actor2_values)


        action_columns = ["event_code","event_name", "event_description", \
                          "event_geo_name", "event_latitude", "event_longitude", "event_date"]
        for c_name in action_columns:
            remaining_names.remove(c_name)

        action_values = self.get_values_from_chosen_columns(db_row, column_names, action_columns)
        action_values = action_values[:-3] + [[action_values[-3], action_values[-2]]] + [action_values[-1]]
        action_entity = ActorAction(*action_values)

        return actor1_entity, actor2_entity, action_entity

    
            
    # Create modality information   
    # [Modality("link", "", event_filepath, source_url, event_classifications, actor_triple_list)]
    def create_modality_entities(self, db_row, column_names, \
                                 remaining_names, actor_list, timestamp, observer_name):
        
        # timestamp converted into utc time
        timestamp = timestamp.astimezone(pytz.utc)
        timestamp = timestamp.strftime("%Y%m%d%H%M%S")


        # Get the trend info
        trend_info = obtain_trends(self.ts_manager, timestamp, observer_name, self.ts_manager.get_modalities_numeric())

        # Modalities 
        modality_entities = []
        for modality_name in self.ts_manager.get_modality_names():
            modality_i = column_names.index(modality_name)
            modality_measurement = db_row[modality_i]

            situation_data = []
            if modality_name in trend_info:
                situation_data = trend_info[modality_name]
            modality_entity = Modality(modality_name, "", "", modality_measurement, situation_data, actor_list)
            
            modality_entities.append(modality_entity)

        return modality_entities


    def create_kg_node(self, db_row, column_names):
        
        remaining_names = copy.deepcopy(column_names)

        # Find time, create time entity
        time_entity = None
        timestamp = None
        if "time" in column_names:
            st_i = column_names.index("time")
            remaining_names.remove("time")

            if "time_end" in column_names:
                et_i = column_names.index("time_end")
                time_entity = TimeEntity(db_row[st_i], db_row[et_i])
                remaining_names.remove("time_end")
            else: # End time not found
                time_entity = TimeEntity(db_row[st_i], db_row[st_i]) # set as same
            
            timestamp = db_row[st_i]

        # Find location, create location entity
        geo_entity_measurement, geo_entity_observer = None, None
        if "latitude" in column_names and "longitude" in column_names:
            lat_i, long_i = column_names.index("latitude"), column_names.index("longitude")
            remaining_names.remove("latitude")
            remaining_names.remove("longitude")

            if "location_description" in column_names:
                loc_desc_i = column_names.index("location_description")
                remaining_names.remove("location_description")
                geo_entity_observer = GeoEntity("", [db_row[lat_i], db_row[long_i]], db_row[loc_desc_i])
            else:
                geo_entity_observer = GeoEntity("", [db_row[lat_i], db_row[long_i]], db_row[loc_desc_i])

            
        # Get actors
        actor_triple_list = []
        if "actor1_name" in column_names: # All actors should have info
            actor1_entity, actor2_entity, action_entity = \
                self.create_actor_action_entities(db_row, column_names, remaining_names)
            actor_triple_list = [actor1_entity, action_entity, actor2_entity]
        
        # Get observer name
        observer_index = column_names.index("sensor_name")
        observer_name = db_row[observer_index]
        remaining_names.remove("sensor_name")

        # Get the modality data
        modality_entities = self.create_modality_entities(db_row, column_names, remaining_names, actor_triple_list, timestamp, observer_name)

        # Create report
        row_i = column_names.index("row_id")
        db_id = db_row[row_i]
        report = Report(time_entity, geo_entity_measurement, db_id, modality_entities)

        # Create observer and aggregator
        observer = Observer(observer_name, report, geo_entity_observer)
        aggregator = Aggregator(self.ts_manager.table_name, observer)

        self.insert_aggregator(aggregator)

        # actor_triple_list = [(
        #     Actor(actor1_name, actor1_type, actor1_type_desc, actor1_geo_name, [actor1_latitude, actor1_longitude]),
        #     ActorAction(event_code, event_name, event_description, event_geo_name, [event_latitude, event_longitude], event_date),
        #     Actor(actor2_name, actor2_type, actor2_type_desc, actor2_geo_name, [actor2_latitude, actor2_longitude]),
        # )]

        # # Create the ontology structure
        # time_entity = TimeEntity(timestamp, timestamp)

        # geo_entity_measurement = ReportGeoEntity("", "N/A")
        # geo_entity_observer = GeoEntity("", station_location, city_name)
        

        # geo_entity_observer = None
        # geo_entity_measurement = None
        # modality_obj_list = [Modality("link", "", event_filepath, source_url, event_classifications, actor_triple_list)]
        # report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        # observer = Observer(news_source, report, geo_entity_observer)
        # aggregator = Aggregator(TABLE_NAME, observer)

        # # From the ontology structure, send to neo4j
        # kg_manager.insert_aggregator(aggregator)
        


    # Create a KG node from time series db row
    def create_kg_nodes(self, db_rows):

        column_names = self.ts_manager.list_columns()
        
        for db_row in db_rows:
            # print("Creating db row")
            self.create_kg_node(db_row, column_names)

        

        
if __name__ == "__main__":

    # table_name = "cctv_test"
    # cctv_manager = TimeSeriesManager(table_name, TABLE_TEMPLATES["cctv"])
    # cctv_manager.insert_data((2024112903005, "sensor_1", "uuid here"))
    # print(cctv_manager.fetch_last_x_rows(5))

    # blob_manager = BlobManager()
    # bucket_name = "cctv-test"  # note - buckets have naming constraints - only have lower case characters, numbers, and hyphens
    # file_key = blob_manager.upload_file(bucket_name, "../pulled_data/cctv/20241129/I-5 : (33) SB Route 5 at Route 134/20241129003005.jpg")
    # print(file_key)
    # file_size = blob_manager.get_file_size(bucket_name, file_key)
    # print(file_size)


    # # Test the  manager
    neo4j_manager = KGManager()

    neo4j_manager.vector_db.test_insertion()
    asdf
    
    # node_list = [{'id': '4:a3342a9c-f23b-4fb9-9474-15bd4ea7fe69:7', 'attributes': {'actor_type': 'GOV', 'name': 'Rebecca Marodi', 'actor_type_desc': 'Government (primary role code'}, 'similarity': 78.33333333333333}, {'id': '4:a3342a9c-f23b-4fb9-9474-15bd4ea7fe69:5', 'attributes': {'actor_type': 'OPP', 'actor_type_desc': 'Opposition (primary role code)', 'name': 'Yolanda Olenjniczak'}, 'similarity': 98.33333333333333}]

    # my_manager.query_actor_context_neo4j(node_list)

    # Should be child
    geo_entity_obs = GeoEntity("123 Main St", "40.7128,-74.0060", "New York")
    time_entity = TimeEntity(1700000000, 1700003600)
    incident = Incident("2025 New York Power Outage", "Major flooding causes significant power outages across New York")
    report = Report(time_entity, None, "iiii3", [], incident)
    observer = Observer("Observer3", report, geo_entity_obs)
    aggregator = Aggregator("Aggregator1", observer)

    # Insert into Neo4j
    neo4j_manager.insert_aggregator(aggregator)

    # Should be added as parent of previous
    geo_entity_obs = GeoEntity("123 Main St", "40.7128,-74.0060", "New York")
    time_entity = TimeEntity(1700000000, 1700003600)
    incident = Incident("2025 New York Floods", "20 people drowned in these floods")
    report = Report(time_entity, None, "iiii", [], incident)
    observer = Observer("Observer1", report, geo_entity_obs)
    aggregator = Aggregator("Aggregator1", observer)

    # Insert into Neo4j
    neo4j_manager.insert_aggregator(aggregator)

    # Should be child
    geo_entity_obs = GeoEntity("123 Main St", "40.7128,-74.0060", "New York")
    time_entity = TimeEntity(1700000000, 1700003600)
    incident = Incident("2025 New York Street Cleanup", "Volunteer groups form to drain streets after major flooding and repairing buildings")
    report = Report(time_entity, None, "iiii2", [], incident)
    observer = Observer("Observer2", report, geo_entity_obs)
    aggregator = Aggregator("Aggregator1", observer)

    # Insert into Neo4j
    neo4j_manager.insert_aggregator(aggregator)

    # # Close connection
    neo4j_manager.close_driver()



#### Original query

# query = """
#         MERGE (agg:Aggregator {name: $agg_name})

#         MERGE (obs:Observer {name: $obs_name})
#         MERGE (agg)-[:HAS_OBSERVER]->(obs)

#         MERGE (geoobs:GeoEntity {address: $obs_address, coordinates: $obs_coordinates, description: $obs_geo_desc})
#         MERGE (obs)-[:LOCATED_AT]->(geoobs)

#         CREATE (rep:Report)
#         MERGE (georep:GeoEntity {value: $rep_geo_value, geo_type: $rep_geo_type_desc})
#         MERGE (rep)-[:REPORT_LOCATION]->(georep)
#         MERGE (obs)-[:HAS_REPORT]->(rep)

#         MERGE (time:TimeEntity {startTime: $rep_start_time, endTime: $rep_end_time})
#         MERGE (rep)-[:CAPTURE_TIME]->(time)

#         FOREACH (modality IN $modality_list |
#             CREATE (m:Modality {type: modality.type})
#             MERGE (rep)-[:HAS_MODALITY]->(m)
#             MERGE (m)-[:HAS_DATA]->(:Data {blob_ref: modality.blob_ref, db_id: modality.db_id, filePath: modality.filePath})
#         )
#         """
