import os
from kg_construction.db_manager import TABLE_TEMPLATES
from kg_construction.db_manager import TimeSeriesManager, BlobManager, KGManager
from kg_construction.situations.image_events import ImageCaptioner

from kg_construction.ontology_classes import *

from utilities.util import get_config
from tqdm import tqdm
import time
import statistics

TABLE_NAME = "cctv"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "cctv"

# fire_locations = ["I-10 : (81) Overland Ave On-Ramp", "I-210 : (464) Vernon Ave On-Ramp", "I-5 : (577) Lakewood Blvd", "I-10 : (87) Western Ave On-Ramp"]
# fire_locations = []

# Get lat long from a dictionary
#  There seems to be a KML file that we can reference...
def get_cam_locations(locations_filepath):

    with open(locations_filepath, "r") as f:
        cam_location_text = f.read()
    
    # Split by placemark
    cam_split = cam_location_text.split("<Placemark>")[1:]

    cam_loc_dict = {}
    for cam_split_xml in cam_split:
        cam_name = cam_split_xml.split("<name>")[1].split("</name>")[0]
        coordinates = cam_split_xml.split("<coordinates>")[1].split("</coordinates>")[0]
        
        latitude = coordinates.split(",")[1]
        longitude = coordinates.split(",")[0]
        
        cam_loc_dict[cam_name] = [float(latitude), float(longitude)]
    
    return cam_loc_dict

def retrieve_lat_long(cam_name, cam_loc):
    return cam_locations[cam_name]


#  For each camera folder, import it into a DB instance and then organize into a kg
def pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic, cam_location_dict):
    # Get all images in the camera folder
    data_files = os.listdir(camera_folder_path)
    image_files = sorted([x for x in data_files if ".jpg" in x])

    print(camera_folder_path)

    cam_location_filepath = camera_folder_path + "/location.txt"

    caption_timing = []
    kg_timing = []

    # Iterate through each image and location file
    for i,image_file in enumerate(image_files):
        image_file_path = os.path.join(camera_folder_path, image_file)

        # Get the timestamp
        image_timestamp = image_file.split(".")[0]
        
        # Get the camera/reporter name
        reporter_name = camera_folder_path.split("/")[-1]
        
        cam_location = "Unknown"
        if reporter_name in cam_location_dict:
            cam_location = cam_location_dict[reporter_name]

        # First, upload the image to the bucket
        image_blob_ref = ""#blob_manager.upload_file(BUCKET_NAME, image_file_path)

        # try:
        # Obtain caption here
        ic_start_t = time.time()
        image_caption = ic.image_caption(image_file_path)
        ic_end_t = time.time()
        

        # Now, insert the data into the table
        db_id = time_series_manager.insert_data((image_timestamp, reporter_name, image_blob_ref, image_caption))

        kg_start_t = time.time()
        # Create the ontology structure
        time_entity = TimeEntity(image_timestamp, image_timestamp)
        geo_entity_observer = GeoEntity("", cam_location, "")
        geo_entity_measurement = ReportGeoEntity("", "N/A")

        modality_obj_list = [Modality("image caption", image_blob_ref, image_file_path, image_caption, [])]

        report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
        observer = Observer(reporter_name, report, geo_entity_observer)
        aggregator = Aggregator(TABLE_NAME, observer)

        # From the ontology structure, send to neo4j
        kg_manager.insert_aggregator(aggregator)

        kg_end_t = time.time()

        # Add time info 
        caption_timing.append(ic_end_t - ic_start_t)
        kg_timing.append(kg_end_t - kg_start_t)   
        # except Exception as e:
        #     print(e)
        #     continue # Ignore errors, just do this for timing

        # give information about 

    
    # Calculate the mean timing
    return 0.0, 0.0 # statistics.mean(caption_timing), statistics.mean(kg_timing)



def pull_by_day_folders(day_folders):

    # Get config
    config_data = get_config()
    save_folder = config_data["save_folder"]
    # Get camera location file
    cam_locations_filepath = get_config()["cctv_locations"]
    cam_location_dict = get_cam_locations(cam_locations_filepath)

    image_folder = save_folder + "/cctv"
    # day_folders = os.listdir(image_folder)



    # Set up our managers
    blob_manager = BlobManager()
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    kg_manager = KGManager()

    ic = ImageCaptioner()

    all_cap_times, all_kg_times = [],[]
    # Iterate through each day folder
    for day_folder in tqdm(day_folders):
        day_folder_path = os.path.join(image_folder, day_folder)

        # Now call function for every camera
        camera_folders = os.listdir(day_folder_path)
        for camera_folder in tqdm(camera_folders):

            camera_folder_path = os.path.join(day_folder_path, camera_folder)
            cap_time, kg_time = pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic, cam_location_dict)

            all_cap_times.append(cap_time)
            all_kg_times.append(kg_time)

    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()

    return all_cap_times, all_kg_times, kg_manager.link_incident_times, kg_manager.link_actor_times



if __name__ == "__main__":

    pull_by_day_folders(["20250605"])

    # Get config
    # config_data = get_config()
    # save_folder = config_data["save_folder"]

    # image_folder = save_folder + "/cctv"
    # day_folders = os.listdir(image_folder)

    # # Set up our managers
    # blob_manager = BlobManager()
    # time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    # kg_manager = KGManager()

    # ic = ImageCaptioner()

    # # Iterate through each day folder
    # for day_folder in tqdm(day_folders):
    #     day_folder_path = os.path.join(image_folder, day_folder)

    #     # Now call function for every camera
    #     camera_folders = os.listdir(day_folder_path)
    #     for camera_folder in camera_folders:
    #         camera_folder_path = os.path.join(day_folder_path, camera_folder)
    #         pull_into_kg(camera_folder_path, blob_manager, time_series_manager, kg_manager, ic)

    # # Close managers
    # kg_manager.close_driver()
    # time_series_manager.close_connection()
    # Boto3 (for blob manager) uses HTTP so it doesn't need to be explicitly closed