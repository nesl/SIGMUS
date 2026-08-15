import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager

import pandas as pd
from zipfile import ZipFile
from datetime import datetime
from tqdm import tqdm
import numpy as np
import time
import statistics
from utilities.util import get_config

from kg_construction.ontology_classes import *

TABLE_NAME = "pem_incidents"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "pem_incidents"


def extract_incident_data(incident_df):

    # Get the timestamp, description, and latlong of the incident, as well as severity and duration
    #  index is 3, 4, 9,10
    selected_columns = incident_df.iloc[:, [3, 4, 9, 10, 18, 19]]
    incident_data = [list(row) for row in selected_columns.itertuples(index=False, name=None)]
    
    date_format = "%m/%d/%Y %H:%M:%S"
    extracted_data = [] # Dictionary of data
    for i,x in enumerate(incident_data):
        date_obj = datetime.strptime(x[0], date_format)
        # Convert to Unix timestamp
        unix_timestamp = int(date_obj.timestamp())

        # IGNORE NAN
        if np.isnan(x[2]) or np.isnan(x[3]):
            continue

        curr_item = {
            "ts": unix_timestamp,
            "description": x[1],
            "lat": x[2],
            "lon": x[3],
            "duration": x[5],
            "severity": x[4]
        }
        extracted_data.append(curr_item)

    return extracted_data
    

def pull_into_kg(extracted_data, time_series_manager, kg_manager):

    kg_times = []
    for i,x in enumerate(extracted_data):

        kg_time_s = time.time()

        timestamp = x["ts"]
        incident_description = x["description"]
        lat = x["lat"]
        lon = x["lon"]
        duration = x["duration"]
        severity = x["severity"]
 
        # Insert the data into the time series manager
        db_id = time_series_manager.insert_data((int(timestamp), lat, lon, incident_description, severity, duration))

        # Create the ontology structure
        time_entity = TimeEntity(timestamp, timestamp)
        geo_entity_measurement = ReportGeoEntity([lat, lon], "")
        geo_entity_reporter = GeoEntity("", [], "")
        modality_obj_list = [
            Modality("description", "", "", incident_description, []),
            Modality("severity", "", "", duration, []),
            Modality("duration", "", "", severity, [])]
        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer("all_incidents", report, geo_entity_reporter)
        aggregator = Aggregator(TABLE_NAME, observer)

        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator)

        kg_times.append(time.time() - kg_time_s)

    # get last 5 rows
    # last_5_rows = time_series_manager.fetch_last_x_rows(5)
    # print(last_5_rows)
    return statistics.mean(kg_times)

# Obtain necessary data
def obtain_node_data_from_incidents(most_recent_folder, data_filepath):

    extraction_folder = most_recent_folder + "/out"
    if not os.path.exists(extraction_folder):
        os.mkdir(extraction_folder)

    # Open the ZIP file
    with ZipFile(data_filepath, 'r') as zip_file:
        # Extract all files
        zip_file.extractall(extraction_folder)
        print("Files extracted to:", extraction_folder)
    
    # Now get the incident file
    incident_file = os.listdir(extraction_folder)

    # We are not opening the detail file - it does not have location info
    incident_file = [x for x in incident_file if "det" not in x][0]
    incident_filepath = os.path.join(extraction_folder, incident_file)

    df = pd.read_csv(incident_filepath, header=None, delimiter=",")
    # Extracted format is a list of {"ts": unix_timestamp, "description": text, "lat": float, "lon": float}
    extracted_data = extract_incident_data(df)

    return extracted_data

def pull_by_day_folders(day_folders):

    config_data = get_config()
    save_folder = config_data["save_folder"]
    pem_incidents = save_folder + "/pem_data_chp_incidents_day"

    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager()

    all_kg_times = []
    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        day_folder_path = os.path.join(pem_incidents, day_folder)

        # Get the data file for this folder
        data_files = [x for x in os.listdir(day_folder_path) if "zip" in x]
        data_filepath = os.path.join(day_folder_path, data_files[0])
        
        # Get a list of extracted tuples
        extracted_data = obtain_node_data_from_incidents(day_folder_path, data_filepath)
        
        # Pull into kg
        kg_time = pull_into_kg(extracted_data, time_series_manager, kg_manager)
        all_kg_times.append(kg_time)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times
    

if __name__ == "__main__":
    
    # For now, just load directly - use config.json in future
    pulled_data_folder = "../pulled_data"
    pem_incidents = pulled_data_folder + "/pem_data_chp_incidents_day"

    day_folders = os.listdir(pem_incidents)
    # day_folders = [pem_incidents + "/" + x for x in day_folders]

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager()

    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        if day_folder != "20250212":
            continue

        day_folder_path = os.path.join(pem_incidents, day_folder)

        

        # Get the data file for this folder
        data_files = [x for x in os.listdir(day_folder_path) if "zip" in x]
        data_filepath = os.path.join(day_folder_path, data_files[0])
        
        # Get a list of extracted tuples
        extracted_data = obtain_node_data_from_incidents(day_folder_path, data_filepath)
        
        # Pull into kg
        pull_into_kg(extracted_data, time_series_manager, kg_manager)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    