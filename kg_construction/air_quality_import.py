import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager

import pandas as pd
import gzip
import shutil
from datetime import datetime
from tqdm import tqdm
import numpy as np
import time

from utilities.util import get_config
from utilities.util import *
import statistics

from kg_construction.ontology_classes import *
from kg_construction.situations.trends import *

TABLE_NAME = "air_quality"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "air_quality"
TREND_COLUMNS = ["data_pm25"]


def insert_air_data(air_filepath, timestamp, time_series_manager, kg_manager):

    # Read the file
    df = pd.read_csv(air_filepath)

    # Latlong of interest
    # event_lat = 33.96477552086272
    # event_long = -118.32043354818204

    # distances = []
    trend_times = []
    kg_times = []

    # Iterate through each row and get the data
    for index, row in df.iterrows():
        sensor_id = int(row.iloc[0])
        lat = float(row.iloc[1])
        lon = float(row.iloc[2])
        pm25 = float(row.iloc[3])

        # if sensor_id not in [54101]:
        #     continue

        # Insert the data into the time series manager
        db_id = time_series_manager.insert_data((int(timestamp), lat, lon, sensor_id, pm25))

        # Trend info
        trend_time_s = time.time()
        trend_info = obtain_trends(time_series_manager, timestamp, sensor_id, TREND_COLUMNS)
        trend_times.append(time.time() - trend_time_s)

        kg_time_s = time.time()
        # Create the ontology structure
        time_entity = TimeEntity(timestamp, timestamp)
        geo_entity_measurement = ReportGeoEntity("", "N/A")
        geo_entity_observer = GeoEntity("", [lat, lon], "")
        modality_obj_list = [Modality("pm25", "", "", pm25, trend_info["data_pm25"], [])]
        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer(sensor_id, report, geo_entity_observer)
        aggregator = Aggregator(TABLE_NAME, observer)

        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator)
        kg_times.append(time.time() - kg_time_s)
    
    return statistics.mean(trend_times), statistics.mean(kg_times)
    



def pull_into_kg(day_folder_path, time_series_manager, kg_manager):

    all_trend_times = []
    all_kg_times = []
    # Iterate through each time file in the day folder
    for filename in tqdm(os.listdir(day_folder_path)):
        if ".csv" not in filename:
            continue

        # Get the timestamp
        timestamp = filename.split(".")[0]

        air_filepath = os.path.join(day_folder_path, filename)

        
        trend_time, kg_time = insert_air_data(air_filepath, timestamp, time_series_manager, kg_manager)
        all_trend_times.append(trend_time)
        all_kg_times.append(kg_time)

    return statistics.mean(all_trend_times), statistics.mean(all_kg_times)


def pull_by_day_folders(day_folders):

    # For now, just load directly - use config.json in future
    config_data = get_config()
    save_folder = config_data["save_folder"]
    air_quality_folder = save_folder + "/air_data"

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)

    all_kg_times = []
    all_trend_times = []
    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        day_folder_path = os.path.join(air_quality_folder, day_folder)
    
        # Get the data file for this folder
        # data_files = [x for x in os.listdir(day_folder_path) if ".txt.gz" in x]


        # Pull into kg
        print("Pulling for day ", day_folder)
        trend_time, kg_time = pull_into_kg(day_folder_path, time_series_manager, kg_manager)
        all_trend_times.append(trend_time)
        all_kg_times.append(kg_time)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_trend_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times


if __name__ == "__main__":
    
    pulled_data_folder = get_config()["save_folder"]
    air_quality_folder = pulled_data_folder + "/air_data"

    day_folders = os.listdir(air_quality_folder)

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)

    all_kg_times = []
    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        if day_folder not in ["20250308", "20250309"]:
            continue

        day_folder_path = os.path.join(air_quality_folder, day_folder)
    
        # Get the data file for this folder
        # data_files = [x for x in os.listdir(day_folder_path) if ".txt.gz" in x]


        # Pull into kg
        print("Pulling for day ", day_folder)
        kg_time = pull_into_kg(day_folder_path, time_series_manager, kg_manager)
        all_kg_times.append(kg_time)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    
