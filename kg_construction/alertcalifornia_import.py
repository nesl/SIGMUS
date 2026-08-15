import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager
from kg_construction.situations.image_events import ImageCaptioner

from kg_construction.ontology_classes import *
from utilities.util import get_config
import time
import statistics 
from tqdm import tqdm

TABLE_NAME = "alertcalifornia"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "alertcalifornia"


#  For each camera folder, import it into a DB instance and then organize into a kg
def pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic):
    # Get all images in the camera folder
    data_files = os.listdir(camera_folder_path)
    location_files = sorted([x for x in data_files if "location" in x])
    image_files = sorted([x for x in data_files if ".jpg" in x])

    print(camera_folder_path)

    caption_times = []
    kg_times = []

    # Iterate through each image and location file
    for i,image_file in tqdm(enumerate(image_files)):
        image_file_path = os.path.join(camera_folder_path, image_file)
        # print(image_file_path)
        # print(camera_folder_path)
        # print(location_files[i])
        location_file_path = os.path.join(camera_folder_path, location_files[i])

        # Get the timestamp
        image_timestamp = image_file.split(".")[0]
        
        # Get the location information
        with open(location_file_path, "r") as file:
            location_data = file.read()
            lat_long = location_data.split(",")[:2]
            direction = location_data.split(",")[-1]
        
        # Get the camera/reporter name
        reporter_name = camera_folder_path.split("/")[-1]
        
    
        # First, upload the image to the bucket
        image_blob_ref = blob_manager.upload_file(BUCKET_NAME, image_file_path)

        try:
            # Obtain caption here
            caption_s_time = time.time()
            image_caption = ic.image_caption(image_file_path)
            caption_times.append(time.time() - caption_s_time)

            # Now, insert the data into the table
            db_id = time_series_manager.insert_data((image_timestamp, reporter_name, float(lat_long[0]), \
                float(lat_long[1]), float(direction), image_blob_ref, image_caption))

            kg_s_time = time.time()
            # Create the ontology structure
            time_entity = TimeEntity(image_timestamp, image_timestamp)
            geo_entity_observer = GeoEntity("", lat_long, "")
            geo_entity_measurement = ReportGeoEntity(direction, "camera_direction")
            # geo_entity_measurement = ReportGeoEntity("", "N/A")
            modality_obj_list = [Modality("image caption", image_blob_ref, image_file_path, image_caption, [])]
            report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
            observer = Observer(reporter_name, report, geo_entity_observer)
            aggregator = Aggregator(TABLE_NAME, observer)

            # From the ontology structure, send to neo4j
            kg_manager.insert_aggregator(aggregator)

            kg_times.append(time.time() - kg_s_time)
        except Exception as e:
            print("Error: ", str(e))
            continue
        

    return statistics.mean(caption_times), statistics.mean(kg_times)

def pull_by_day_folders(day_folders):

    config_data = get_config()
    save_folder = config_data["save_folder"]

    image_folder = save_folder + "/alertcalifornia"

    # Set up our managers
    blob_manager = BlobManager()
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager()

    ic = ImageCaptioner()
    all_caption_times = []
    all_kg_times = []
    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(image_folder, day_folder)
        
        # Only focus on the latest day for now
        #  Note - the old pulled data will not have location files

        # Now call function for every camera
        camera_folders = os.listdir(day_folder_path)
        for camera_folder in camera_folders[3:]:
            camera_folder_path = os.path.join(day_folder_path, camera_folder)
            caption_time, kg_time = pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic)
            all_caption_times.append(caption_time)
            all_kg_times.append(kg_time)


    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_caption_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times


if __name__ == "__main__":

    image_folder = "./pulled_data/alertcalifornia"
    day_folders = os.listdir(image_folder)

    # Set up our managers
    blob_manager = BlobManager()
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager()

    ic = ImageCaptioner()

    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(image_folder, day_folder)
        
        # Only focus on the latest day for now
        #  Note - the old pulled data will not have location files

        # Now call function for every camera
        camera_folders = os.listdir(day_folder_path)
        for camera_folder in camera_folders:
            camera_folder_path = os.path.join(day_folder_path, camera_folder)
            pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic)
            

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()
    # Boto3 (for blob manager) uses HTTP so it doesn't need to be explicitly closed