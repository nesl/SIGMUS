import os
import psycopg2
from datetime import datetime
from utilities.util import get_config
import re
import uuid
from dateutil import parser
import pytz
import string
import numpy as np

# Add geocoding
import googlemaps

# Creating a custom geo class because I suspect we'll need some geo-specific ops

GEO_TEMPLATES = {
    "geocode":"""
    row_id INTEGER,
    location_name TEXT NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL
    """
}

# This involves both a SQL database which stores object metadata (each row can store multiple modalities)
class GeoManager:

    def get_table_template_columns(self, table_template):
        terms = []
        for line in table_template.split("\n"):
            # print(line)
            terms.append(line.strip().split(" ")[0])
        terms = [x for x in terms if x != ""]
        return terms
    
    # Load or call geolocation
    def geocode_name(self, location_name):

        if location_name.lower() == "unknown":
            return np.nan, np.nan
        
        # Check if the location is already in the database
        location_exists, lat, long = self.query_match_name(location_name)
        if location_exists:
            return lat, long
        else:
            geocode_result = self.gmaps_client.geocode(location_name)
            if geocode_result:
                location = geocode_result[0]["geometry"]["location"]
                data_to_insert = (location_name, location["lat"], location["lng"])
                self.insert_data(data_to_insert)

                return location["lat"], location["lng"]
        
        return np.nan, np.nan



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

    def __init__(self):
        self.config = get_config()
        self.db_config = self.config["postgres_config"]

        # Initialize connection
        self.conn = psycopg2.connect(**self.db_config)
        self.table_name = "geocode"

        # Obtain geocoding api
        config_data = get_config()
        google_key = config_data["google_places_key"]["key"]
        self.gmaps_client = googlemaps.Client(key=google_key)

        self.setup_tables("geocode", GEO_TEMPLATES["geocode"], ["location_name"])
        
        self.table_template = GEO_TEMPLATES["geocode"]

        

    def clear_database(self):

        for table in GEO_TEMPLATES.keys():
            self.delete_table(table)
    
        print("All tables deleted successfully!")

    def close_connection(self):
        self.conn.close()

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

    # Function to fetch the last k rows
    def fetch_last_x_rows(self, k):
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
        

    # Function to match a name in the db
    def query_match_name(self, location_name):

        query = "SELECT * FROM geocode WHERE location_name = %s"
        results = self.execute_query(query, (location_name,))
        # Obtain the lat long
        if results:
            return True, results[0][2], results[0][3]
        else:
            return False, None, None

    # Function to retrieve data
    def execute_query(self, query, params=None):
        try:
            cursor = self.conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            rows = cursor.fetchall()
            return rows

        except Exception as e:
            print("Error:", e)

        finally:
            cursor.close()