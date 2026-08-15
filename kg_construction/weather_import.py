import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager

import csv
import gzip
import shutil
from datetime import datetime
from tqdm import tqdm
import numpy as np

from kg_construction.ontology_classes import *
from kg_construction.situations.trends import *

from utilities.util import get_config
import time
import statistics

TABLE_NAME = "weather"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "weather"


def insert_weather_data(data_filepath, timestamp, city_name, \
time_series_manager, kg_manager, station_location):

    # Read the file
    csv_data = []
    with open(data_filepath, "r") as file:
        reader = csv.reader(file)
        for row in reader:
            csv_data.append(row)

    trend_times, kg_times = [],[]
    # Iterate through each row and get the data
    for row in csv_data:
        temp_f = float(row[0])
        description = row[1]
        humidity = float(row[2])
        wind_mps = float(row[3])
        latitude = station_location[0]
        longitude = station_location[1]
        
        # Insert the data into the time series manager
        db_id = time_series_manager.insert_data((int(timestamp), city_name, latitude, longitude, city_name, temp_f, description, humidity, wind_mps))

        # Get trends
        trend_time_s = time.time()
        trend_info = obtain_trends(time_series_manager, timestamp, city_name, time_series_manager.get_modalities_numeric())
        trend_times.append(time.time() - trend_time_s)

        # Create the ontology structure
        kg_time_s = time.time()
        time_entity = TimeEntity(timestamp, timestamp)
        geo_entity_measurement = ReportGeoEntity("", "N/A")
        geo_entity_observer = GeoEntity("", station_location, city_name)
        modality_obj_list = [ # modality_type, blob_ref, file_path, value, situation_list, actor_triple_list
            Modality("temperature (f)", "", "", temp_f, trend_info["data_temp_f"],[]),
            Modality("weather description", "", "", description, [],[]),
            Modality("humidity", "", "", humidity, trend_info["data_humidity"],[]),
            Modality("wind speed (m/s)", "", "", wind_mps, trend_info["data_wind_mps"],[])
            ]
        # modality_obj_list = []
        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer(city_name, report, geo_entity_observer)
        aggregator = Aggregator(TABLE_NAME, observer)

        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator, timestamp)

        kg_times.append(time.time() - kg_time_s)

    return statistics.mean(trend_times), statistics.mean(kg_times)


def pull_into_kg(day_folder_path, time_series_manager, kg_manager, station_data):

    all_trend_times, all_kg_times = [],[]
    # Iterate through each time file in the day folder
    for city_folder in os.listdir(day_folder_path):

        city_folder_path = os.path.join(day_folder_path, city_folder)

        for time_file in tqdm(os.listdir(city_folder_path)):

            data_file_path = os.path.join(city_folder_path, time_file)

            # Get the timestamp
            timestamp = time_file.split(".")[0]

            city_name = city_folder.replace("  ", " ")
            station_location = station_data[city_name]
            trend_time, kg_time = insert_weather_data(data_file_path, timestamp, city_folder, time_series_manager, kg_manager, station_location)
            all_trend_times.append(trend_time)
            all_kg_times.append(kg_time)

    return statistics.mean(all_trend_times), statistics.mean(all_kg_times)

def pull_by_day_folders(day_folders):

    # Get config
    config_data = get_config()
    save_folder = config_data["save_folder"]   

    weather_folder = save_folder + "/weather_data"

    owm_locations_file = config_data["owm_locations"]

    # Also read in the weather data
    station_data = {}
    with open(owm_locations_file, "r") as owm_locations:
        owm_list = owm_locations.readlines()
        for station_line in owm_list:
            station_name = ','.join(station_line.split(",")[:2])
            station_location = station_line.split(",")[2:]
            latitude = float(station_location[0].strip())
            longitude = float(station_location[1].strip())

            station_data[station_name] = [latitude, longitude]


    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)

    all_trend_times, all_kg_times = [],[]
    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        day_folder_path = os.path.join(weather_folder, day_folder)

        # Pull into kg
        trend_time, kg_time = pull_into_kg(day_folder_path, time_series_manager, kg_manager, station_data)
        all_trend_times.append(trend_time)
        all_kg_times.append(kg_time)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_trend_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times

if __name__ == "__main__":
    
    config_data = get_config()
    pulled_data_folder = config_data["save_folder"]
    weather_folder = pulled_data_folder + "/weather_data"

    owm_locations_file = config_data["owm_locations"]
    # Also read in the weather data
    station_data = {}
    with open(owm_locations_file, "r") as owm_locations:
        owm_list = owm_locations.readlines()
        for station_line in owm_list:
            station_name = ','.join(station_line.split(",")[:2])
            station_location = station_line.split(",")[2:]
            latitude = float(station_location[0].strip())
            longitude = float(station_location[1].strip())

            station_data[station_name] = [latitude, longitude]
        

    day_folders = os.listdir(weather_folder)

    # Set up our managers
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager(time_series_manager)

    # For every day folder, obtain incident data and push to db
    for day_folder in sorted(day_folders):

        if day_folder not in ["20250311", "20250312", "20250313"]:
            continue

        day_folder_path = os.path.join(weather_folder, day_folder)
    
        # Get the data file for this folder
        # data_files = [x for x in os.listdir(day_folder_path) if ".txt.gz" in x]

        # Pull into kg
        pull_into_kg(day_folder_path, time_series_manager, kg_manager, station_data)
        # asdf

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    
