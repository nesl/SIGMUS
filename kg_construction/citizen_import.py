import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager
import pandas as pd
import numpy as np
from kg_construction.ontology_classes import *
from kg_construction.geo_manager import GeoManager

import json
from tqdm import tqdm
import time
import statistics

# Add LLM import
from utilities.util import get_config
from llm.client import OpenAIClient
from kg_construction.email_records import records_from_csv


TABLE_NAME = "citizen"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "citizen"

TOTAL_ARTICLES = 0

def is_nan(obj):
    return True if obj!=obj else False

#  
def pull_into_kg(event_filepath, time_series_manager, kg_manager, geocode_manager, llm_client=None):
    global TOTAL_ARTICLES
    # Open the GKG file
    kg_times = []

    # Iterate through each row in the GKG file
    for row in records_from_csv(event_filepath, "citizen", llm_client, OpenAIClient):

    
        kg_start_t = time.time()

        # Get the timestamp
        timestamp = row["timestamp"]
        # Get the location name
        loc_name = row["location"]

        event_latitude,event_longitude = geocode_manager.geocode_name(loc_name)

        event_name = row["event_name"]
        event_type = row["event_type"]
        event_description = row["description"]
        source_name = "citizen app"
        
        event_info = str({"event_name": event_name, \
                      "event_description": event_description})

        # Now, insert the data into the table
        data_to_insert = (timestamp, event_latitude, event_longitude, event_name \
                        , event_type, event_description)
        
        db_id = time_series_manager.insert_data(data_to_insert)


        # Create the ontology structure
        time_entity = TimeEntity(timestamp, timestamp)
        
        geo_entity_observer = None
        geo_entity_measurement = ReportGeoEntity([event_latitude, event_longitude], "lat/long coordinates")
        modality_obj_list = [Modality("event_info", "", event_filepath, \
                                        event_info, [event_type])] 
        
        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer(source_name, report, geo_entity_observer)
        aggregator = Aggregator(TABLE_NAME, observer)
        
        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator)

        kg_end_t = time.time()
        kg_times.append(kg_end_t - kg_start_t)

        TOTAL_ARTICLES = TOTAL_ARTICLES + 1
    if not kg_times:
        return None, None
    return 0.0, statistics.mean(kg_times)

def pull_by_day_folders(day_folders):

    # Get config
    config_data = get_config()
    save_folder = config_data["save_folder"]

    data_folder = save_folder + "/citizen_data"

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    geocode_manager = GeoManager()
    kg_manager = KGManager(time_series_manager, connect_reports=False)


    all_news_times, all_kg_times = [], []

    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(data_folder, day_folder)

        # Get the gkg files
        data_files = os.listdir(day_folder_path)
        event_files = sorted([x for x in data_files if ".csv" in x])

        # Iterate through each image and location file
        for i,event_file in tqdm(enumerate(event_files), total=len(event_files)):

            data_filepath = os.path.join(day_folder_path, event_file)

            news_times, kg_times = pull_into_kg(data_filepath, time_series_manager, kg_manager, geocode_manager)
            if news_times is None:
                continue   
            all_news_times.append(news_times)
            all_kg_times.append(kg_times)



    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_news_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times


if __name__ == "__main__":

    # Get config
    config_data = get_config()
    save_folder = config_data["save_folder"]

    data_folder = save_folder + "/citizen_data"
    day_folders = os.listdir(data_folder)

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    geocode_manager = GeoManager()
    kg_manager = KGManager(time_series_manager, connect_reports=False, use_vectordb=False)


    all_news_times, all_kg_times = [], []

    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(data_folder, day_folder)

        # Get the gkg files
        data_files = os.listdir(day_folder_path)
        event_files = sorted([x for x in data_files if ".csv" in x])

        # Iterate through each image and location file
        for i,event_file in tqdm(enumerate(event_files), total=len(event_files)):

            data_filepath = os.path.join(day_folder_path, event_file)

            news_times, kg_times = pull_into_kg(data_filepath, time_series_manager, kg_manager, geocode_manager)
            if news_times is None:
                continue   
            all_news_times.append(news_times)
            all_kg_times.append(kg_times)



    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()
