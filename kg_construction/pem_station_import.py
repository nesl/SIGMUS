import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager

import pandas as pd
import gzip
import shutil
from datetime import datetime
from tqdm import tqdm
import numpy as np

from utilities.util import get_config
import time
import statistics

from kg_construction.ontology_classes import *
from kg_construction.situations.trends import *

TABLE_NAME = "pem_station"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "pem_station"
TREND_COLUMNS = ["data_avg_occupancy", "data_avg_speed"]


def extract_station_data(station_df, station_metadata):

    # Get timestamp (0), station id (1), avg occupancy (11), avg speed (12)
    selected_columns = station_df.iloc[:, [0, 1, 10, 11]]

    station_data = [list(row) for row in selected_columns.itertuples(index=False, name=None)]
    
    date_format = "%m/%d/%Y %H:%M:%S"
    extracted_data = [] # Dictionary of data
    for i,x in enumerate(station_data):
        date_obj = datetime.strptime(x[0], date_format)
        # Convert to Unix timestamp
        unix_timestamp = int(date_obj.timestamp())

        # If the occupancy or speed is nan, then set them to -1 (supported in Neo4j)
        occupancy, speed = x[2], x[3]
        if np.isnan(x[2]):
            occupancy = -1
        if np.isnan(x[3]):
            speed = -1

        # Get the latlon - if the mapping doens't exist, it means the location is invalid
        if x[1] in station_metadata:
            latlon = station_metadata[x[1]]
            curr_item = {
                "ts": unix_timestamp,
                "avg_occupancy": occupancy,
                "avg_speed": speed,
                "lat": latlon[0],
                "lon": latlon[1],
                "station_id": x[1]
            }
            extracted_data.append(curr_item)
        # print(curr_item)
    return extracted_data


def pull_into_kg(extracted_data, time_series_manager, kg_manager):

    # DELETE LATER
    limited_stations = {}

    kg_times = []
    trend_times = []
    for i,x in tqdm(enumerate(extracted_data)):

        timestamp = x["ts"]
        avg_occupancy = x["avg_occupancy"]
        avg_speed = x["avg_speed"]
        lat = x["lat"]
        lon = x["lon"]
        station_id = x["station_id"]

        # if avg_speed > 40 or avg_speed < 0:
        #     continue  # Skip non-anamalous data
        


        # Insert the data into the time series manager
        
        # Skip if this is not part of our stations fo interest
        if station_id not in limited_stations and len(limited_stations.keys()) >= 200:
            continue
        elif station_id in limited_stations and limited_stations[station_id] < 20:
            limited_stations[station_id] += 1
        elif station_id not in limited_stations:
            limited_stations[station_id] = 1
        else:  # In all other cases, skip, especially if exceed 20 values
            continue

        db_id = time_series_manager.insert_data((int(timestamp), lat, lon, station_id, avg_occupancy, avg_speed))

        # Call trends
        # This creates a dict of modalities where each entry is modality_name:[list of measurements]
        trend_time_s = time.time()
        trend_info = obtain_trends(time_series_manager, timestamp, station_id, TREND_COLUMNS)
        trend_times.append(time.time() - trend_time_s)

        kg_start_s = time.time()
        # Create the ontology structure
        time_entity = TimeEntity(timestamp, timestamp)
        geo_entity_measurement = ReportGeoEntity("", "N/A")
        geo_entity_observer = GeoEntity("", [lat, lon], "")
        modality_obj_list = [Modality("occupancy", "", "", avg_occupancy, trend_info["data_avg_occupancy"], []), \
        Modality("speed", "", "", avg_speed, trend_info["data_avg_speed"],[])]
        
        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer(station_id, report, geo_entity_observer)
        aggregator = Aggregator(TABLE_NAME, observer)

        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator)
        kg_times.append(time.time() - kg_start_s)
    
    return statistics.mean(trend_times), statistics.mean(kg_times)
    
    

def obtain_node_data_from_station(station_metadata, data_filepath, day_folder_path):

    # Unzip the data filepath into a out folder
    extraction_folder = day_folder_path + "/out"
    if not os.path.exists(extraction_folder):
        os.mkdir(extraction_folder)

    # Unzip the data filepath into the extraction folder
    with gzip.open(data_filepath, 'rb') as f_in:
        shutil.copyfileobj(f_in, open(extraction_folder + "/data.txt", 'wb'))

    df = pd.read_csv(extraction_folder + "/data.txt", header=None, delimiter=",")
    # Get station data
    extracted_data = extract_station_data(df, station_metadata)
    return extracted_data

# Get station metadata, which maps station IDs to latlon
def get_station_metadata(metadata_filepath):

    df = pd.read_csv(metadata_filepath, delimiter="\t")
    selected_columns = df.iloc[:, [0, 8,9]]
    
    station_data = {row[0]:[row[1], row[2]] for row in selected_columns.itertuples(index=False, name=None) if not np.isnan(row[1]) or not np.isnan(row[2])}

    return station_data


def pull_by_day_folders(day_folders):

    # For now, just load directly - use config.json in future
    config_data = get_config()
    save_folder = config_data["save_folder"]
    pem_station = save_folder + "/pem_data_station_5min"
    station_metadata_filepath = "./kg_construction/pem_7_stations.txt"

    station_metadata = get_station_metadata(station_metadata_filepath)

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)  # Our graph also updates depending on the time series manager

    all_trend_times, all_kg_times = [],[]
    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        day_folder_path = os.path.join(pem_station, day_folder)
    
        # Get the data file for this folder
        data_files = [x for x in os.listdir(day_folder_path) if ".txt.gz" in x]
        data_filepath = os.path.join(day_folder_path, data_files[0])
        
        # Get a list of extracted tuples
        extracted_data = obtain_node_data_from_station(station_metadata, data_filepath, day_folder_path)

        # Pull into kg
        trend_time, kg_time = pull_into_kg(extracted_data, time_series_manager, kg_manager)
        all_trend_times.append(trend_time)
        all_kg_times.append(kg_time)    

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()
    return all_trend_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times
    

if __name__ == "__main__":
    
    pulled_data_folder = get_config()["save_folder"]
    pem_station = pulled_data_folder + "/pem_data_station_5min"
    station_metadata_filepath = "./kg_construction/pem_7_stations.txt"

    day_folders = os.listdir(pem_station)
    station_metadata = get_station_metadata(station_metadata_filepath)
    # day_folders = [pem_incidents + "/" + x for x in day_folders]

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)  # Our graph also updates depending on the time series manager

    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):


        day_folder_path = os.path.join(pem_station, day_folder)
    
        # Get the data file for this folder
        data_files = [x for x in os.listdir(day_folder_path) if ".txt.gz" in x]
        data_filepath = os.path.join(day_folder_path, data_files[0])
        
        # Get a list of extracted tuples
        extracted_data = obtain_node_data_from_station(station_metadata, data_filepath, day_folder_path)

        # Pull into kg
        pull_into_kg(extracted_data, time_series_manager, kg_manager)
        

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    
