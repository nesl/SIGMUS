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
from llm.client import LLMClient, OpenAIClient

# Add news parsing
from newspaper import Article

TABLE_NAME = "gdelt_events"
TABLE_TEMPLATE = TABLE_TEMPLATES[TABLE_NAME]
BUCKET_NAME = "gdelt_events"

TOTAL_ARTICLES = 0

def is_nan(obj):
    return True if obj!=obj else False

FIRE_NEWS_URLS = ['https://www.wired.com/story/maga-blaming-dei-california-wildfires-conspiracy/', 'https://www.foxbusiness.com/lifestyle/los-angeles-area-college-campuses-shut-down-wildfires-spread',
'https://www.jckonline.com/editorial-article/los-angeles-area-wildfires/', 'https://twitchy.com/eric-v/2025/01/08/high-winds-and-lack-of-water-hamper-firefighters-in-california-n2406309', 'https://www.hellomagazine.com/celebrities/739933/major-hollywood-events-canceled-due-to-safety-concerns-in-la-following-wildfires/','https://www.iclarified.com/96050/apple-to-donate-to-support-los-angeles-fire-recovery-efforts', 'https://www.staradvertiser.com/2025/01/09/breaking-news/jamie-lee-curtis-pledges-1m-to-start-l-a-wildfire-fund/', 'https://rock937online.com/whats-happening-on-the-ground-in-california-as-crews-battle-fires-in-los-angeles-county/', 'https://www.housingwire.com/articles/la-fires-upend-rental-market-as-residents-scramble-for-housing/', 'https://www.crookwellgazette.com.au/story/8862309/two-killed-as-thousands-flee-los-angeles-wildfires/?cs=14264', 'https://angelusnews.com/local/la-catholics/parishes-la-fire-shelters/', 'https://www.foxnews.com/media/la-times-owner-blames-mayor-cutting-fire-department-budget-ahead-wildfires-competence-matters', 'https://www.latimes.com/environment/story/2025-01-09/climate-whiplash-study-california-fires', 'https://www.yahoo.com/news/palisades-fire-causes-significant-damage-001157528.html', 'https://kval.com/news/local/oregon-department-of-forestry-sends-70-firefighters-to-fight-california-wildfires', 'https://www.boredpanda.com/henry-winkler-arson-theory-on-los-angeles-fire/', 'https://www.rnz.co.nz/news/world/538528/movie-tv-stars-flee-homes-as-los-angeles-wildfires-burn', 'https://nypost.com/2025/01/08/entertainment/tom-hanks-son-chets-childhood-neighborhood-burnt-down-in-palisades-fire/', 'https://www.foxnews.com/media/la-residents-recall-harrowing-escape-from-wildfires-homes-businesses-go-up-flames-like-war-zone', 'https://www.cairnspost.com.au/entertainment/celebrity-life/thousands-flee-celebritypacked-enclave-as-wildfires-rapidly-spread-through-pacific-palisades-in-la/news-story/6e87dfad641a34958cd0570e8ff0eba2?nk=1db1d39b2e840ed9cf41b8fb5ec73a34-1736326901', 'https://timesofindia.indiatimes.com/world/us/how-santa-ana-winds-made-los-angeles-wildfires-so-dangerous/articleshow/117055158.cms', 'https://www.theblaze.com/news/fire-chief-dei-wildfire-lafd', 'https://www.vox.com/climate/394005/palisades-eaton-wildfire-los-angeles-santa-ana-winds-california-explainer', 'https://www.nbcmiami.com/news/local/people-impacted-california-palisades-eaton-wildfires/3510499/', 'https://uk.news.yahoo.com/live/los-angeles-wildfires-live-updates-5-killed-palisades-and-eaton-fires-spread-sunset-fire-erupts-in-hollywood-hills-141555082.html','https://tribune.com.pk/story/2520994/five-killed-thousands-displaced-as-eaton-fire-burns-10600-acres-in-la']
FIRE_NEWS_URLS = ['https://www.kcra.com/article/eaton-fire-altadena-los-angeles-january-14/63422185']

# Remove comments
def remove_json_comments(json_str):
    
    start_idx = 0
    found_comment_idx = json_str.find("//")
    kept_substrings = []
    while found_comment_idx != -1:
        kept_substrings.append(json_str[start_idx:found_comment_idx])

        # Find the end of the comment
        end_of_comment = json_str.find("\"", found_comment_idx)
        if end_of_comment == -1:
            found_comment_idx = -1
            kept_substrings.append("}")
        start_idx = end_of_comment
        found_comment_idx = json_str.find("//", start_idx)
    
    # Add remaining substring
    kept_substrings.append(json_str[start_idx:])
    filtered_json = "".join(kept_substrings)
    
    return filtered_json

# Filter out the llm response
def filter_llm_actor_responses(llm_response):

    # print(llm_response)

    # get the json response
    json_response = llm_response
    if "[" in llm_response and "]" in llm_response:
        json_response = llm_response.split("[")[1].split("]")[0]
    elif "{" in llm_response and "}" in llm_response:
        json_response = '{' + llm_response.split("{")[1].split("}")[0] + '}'
    else:
        return None
    json_response = json_response.replace("\'", "\"")

    json_response = remove_json_comments(json_response)

    # convert to json objects
    try:
        json_objects = json.loads('[' + json_response + ']')
    except Exception as e:
        print("Error:", e)
        return None
    return json_objects

# Filter out the event repsonse
def filter_llm_event_responses(llm_response):

    event_classifications = []
    if "[" in llm_response and "]" in llm_response:
        event_classifications = llm_response.split("[")[1].split("]")[0]
        event_classifications = event_classifications.replace('\'', "")
        event_classifications = event_classifications.replace('\"', "")
        event_classifications = event_classifications.split(",")
        event_classifications = [x.strip() for x in event_classifications]

    # Now look for incidents
    incident_name = None
    if "<incident>" in llm_response and "</incident>" in llm_response:
        incident_name = llm_response.split("<incident>")[1].split("</incident>")[0]
        if incident_name == "no incident":
            incident_name = None

    # Get incident explanation, if any
    incident_summary = None
    if "<summary>" in llm_response and "</summary>" in llm_response:
        incident_summary = llm_response.split("<summary>")[1].split("</summary>")[0]
        if incident_name == "no incident":
            incident_summary = None  # No summary if no incident

    return event_classifications, incident_name, incident_summary

# Prompt the LLM for a given entry:
def prompt_on_news(news_url, llm_client, llm_prompt, cameo_definitions, actor_types, cap_terms, event_date):

    if os.environ.get("SIGMUS_ALLOW_LEGACY_LOCAL_ENRICHMENT") != "1":
        raise RuntimeError(
            "SIGMUS-local article download and enrichment is disabled by default. "
            "Use shared Urban Observations annotations, or explicitly set "
            "SIGMUS_ALLOW_LEGACY_LOCAL_ENRICHMENT=1 for compatibility."
        )

    intermediate_save_folder = "intermediate"
    if not os.path.exists(intermediate_save_folder):
        os.mkdir(intermediate_save_folder)
    # Create a folder called intermediate which saves the prompt output
    news_save_folder = os.path.join(intermediate_save_folder, "news")
    if not os.path.exists(news_save_folder):
        os.mkdir(news_save_folder)
    # news save filepath
    if ".com" in news_url:
        save_filepath = news_url.split(".com")[1][1:]
    else:
        save_filepath = news_url
    save_filepath = save_filepath.replace("/","_") + ".json"
    
    print(save_filepath)

    save_filepath = os.path.join(news_save_folder, save_filepath)

    # If the path exists, get the data
    if os.path.exists(save_filepath):
        with open(save_filepath, "r") as json_file:
            json_dict = json.load(json_file)
        return json_dict["actor_output"], json_dict["event_output"], json_dict["incident_name"], json_dict["incident_summary"]

    try:
        # Obtain news
        article = Article(news_url)
        article.download()
        article.parse()

        article_date = event_date
        if article.publish_date:
            article_date = article.publish_date.strftime("%Y-%m-%d %H:%M:%S")

        news_text = "Article date: " + article_date + \
            "\n Article text: " + article.text

        llm_start = llm_prompt["obtain_actors"]
        llm_end = llm_prompt["formatting_actors"]
        full_prompt = str(llm_start) \
            + "\nAction Codes: \n" + str(cameo_definitions) + "\nActor Codes:\n"\
                + str(actor_types["role_type"])  + "\n\nI will now provide the news text.  \n\nNews text: \n" + news_text +  str(llm_end)


    except Exception as e:
        print("Error:", e)
        return None
    # print(full_prompt)
    
    actor_output, thoughts = llm_client.send_message_to_llm_single(str(full_prompt))

    actor_output = filter_llm_actor_responses(actor_output)
    if actor_output == None:  # Error in json parsing, so LLM gave badly formatted output
        return None

    # Also prompt for the event itself
    llm_start = llm_prompt["obtain_events"]
    llm_end = llm_prompt["formatting_events"]
    full_prompt = str(llm_start) + cap_terms + "\n\nI will now provide the news text.  \n\nNews text: \n" + news_text + str(llm_end)
    
    event_output, thoughts = llm_client.send_message_to_llm_single(str(full_prompt))
    
    
    event_output, incident_name, incident_summary = filter_llm_event_responses(event_output)

    json_dict = {
        "event_output": event_output,
        "incident_name": incident_name,
        "actor_output": actor_output,
        "incident_summary": incident_summary
    }

    # Save this json data to file
    with open(save_filepath, "w") as json_file:
        json.dump(json_dict, json_file)


    return actor_output, event_output, incident_name, incident_summary

# Clean up response
# TODO: actually clean up the response
def clean_response(response):

    # Response fields do not exist
    if "entity1_name" not in response or "entity2_name" not in response:
        return None
    if "entity1_code" not in response or "entity2_code" not in response:
        return None
    if "entity1_location" not in response or "entity2_location" not in response:
        return None
    if "action_location" not in response:
        return None
    if "action_code" not in response:
        return None

    return response


#  
def pull_into_kg(event_filepath, time_series_manager, kg_manager, \
                 cameo_definitions, actor_types, cap_terms, llm_client, llm_prompt, geocode_manager):
    global TOTAL_ARTICLES
    # Open the GKG file
    df = pd.read_csv(event_filepath)

    error_cases = 0

    unique_articles = []

    news_times = []
    kg_times = []

    # Iterate through each row in the GKG file
    for index, row in df.iterrows():

        # Get the timestamp (when GDELT added it)
        timestamp = row.iloc[59]

        # Also get the timestamp of the event itself.
        #  GDELT has this field but only at the day resolution, so we should
        #  rely on LLMs to extract this.
        event_date = str(row.iloc[1]) + "000000"

        # Get the source URL
        source_url = row.iloc[60]

        # if source_url not in FIRE_NEWS_URLS:
        #     continue # Skip if not fire related in some way
        # DELETE THIS ABOVE LATER

        news_source = source_url.split("/")[2]

        # Test with custom url
        source_url = 'https://www.kcra.com/article/eaton-fire-altadena-los-angeles-january-14/63422185'
        # source_url = "https://gvwire.com/2025/03/24/wife-of-slain-california-fire-captain-is-arrested-in-mexico-on-suspicion-of-murder/"
        # source_url = "https://www.yahoo.com/news/rebecca-marodi-murder-wife-accused-152739043.html?guccounter=1"

        # If we've visited this news article, skip it
        if source_url in unique_articles:
            continue
        unique_articles.append(source_url)

        # Prompt the LLM for the news
        news_start_t = time.time()
        prompt_response = prompt_on_news(source_url, llm_client, llm_prompt, cameo_definitions, actor_types, cap_terms, event_date)
        if prompt_response:
            actor_responses, event_classifications, incident_name, incident_summary = prompt_response
        else:
            error_cases += 1
            continue
        news_end_t = time.time()
        news_times.append(news_end_t - news_start_t)

        # Failed to download article, then move on
        if actor_responses == None:
            error_cases += 1
            continue
        

        kg_start_t = time.time()
        for response in actor_responses:

            # Clean up the response if necessary
            response = clean_response(response)
            if response == None:
                continue


            # Get the actor name, type, and geo information
            actor1_name = response["entity1_name"]
            actor1_type = response["entity1_code"]
            
            actor1_geo_name = response["entity1_location"]
            actor1_latitude, actor1_longitude = np.nan, np.nan
            if actor1_geo_name.lower() != "unknown" and actor1_geo_name != "":
                actor1_latitude,actor1_longitude = geocode_manager.geocode_name(actor1_geo_name)

            # Get actor type description
            actor1_type_desc = "Unknown"
            if actor1_type in actor_types["role_type"]:
                actor1_type_desc = actor_types["role_type"][actor1_type]


            # Get the actor name, type, and geo information
            actor2_name = response["entity2_name"]
            actor2_type = response["entity2_code"]
            
            actor2_geo_name = response["entity2_location"]
            actor2_latitude, actor2_longitude = np.nan, np.nan
            if actor2_geo_name.lower() != "unknown" and actor2_geo_name != "":
                actor2_latitude,actor2_longitude = geocode_manager.geocode_name(actor2_geo_name)

            # Get actor type description
            actor2_type_desc = "Unknown"
            if actor2_type in actor_types["role_type"]:
                actor2_type_desc = actor_types["role_type"][actor2_type]
            


            # Get the event information and its geo data
            event_code = response["action_code"]
            event_name = ""
            event_description = ""
            if event_code not in cameo_definitions:
                event_code = str(event_code).zfill(4)
            if event_code in cameo_definitions:
                event_name = cameo_definitions[event_code]["name"]
                event_description = cameo_definitions[event_code]["description"]
            else:  # If the action is not found, continue
                event_name = "Unknown"

            event_geo_name = response["action_location"]
            event_latitude,event_longitude = geocode_manager.geocode_name(event_geo_name)
            # If the response has a time
            if response["action_time"].lower() != "unknown" and response["action_time"] != "":
                try:
                    pd.to_datetime(response["action_time"])
                    event_date = response["action_time"]
                except:  # If we get an error (e.g. not a date), pass
                    pass

            # Ignore cases where the action location is unknown
            if event_geo_name.lower() == "unknown":
                continue

            # Now, insert the data into the table
            data_to_insert = (timestamp, actor1_name, actor1_type, actor1_type_desc \
                            , actor1_geo_name,\
                actor1_latitude, actor1_longitude, actor2_name, actor2_type, actor2_type_desc, \
                actor2_geo_name, actor2_latitude, actor2_longitude,\
                event_code, event_name, event_description,\
                event_geo_name, event_latitude, event_longitude, event_date, source_url)
            
            db_id = time_series_manager.insert_data(data_to_insert)

            actor_triple_list = [(
                Actor(actor1_name, actor1_type, actor1_type_desc, actor1_geo_name, [actor1_latitude, actor1_longitude]),
                ActorAction(event_code, event_name, event_description, event_geo_name, [event_latitude, event_longitude], event_date),
                Actor(actor2_name, actor2_type, actor2_type_desc, actor2_geo_name, [actor2_latitude, actor2_longitude]),
            )]

            # Create the ontology structure
            time_entity = TimeEntity(timestamp, timestamp)
            
            geo_entity_observer = None
            geo_entity_measurement = None
            modality_obj_list = [Modality("link", "", event_filepath, \
                                          source_url, event_classifications, actor_triple_list)]
            incident = Incident(incident_name, incident_summary) if incident_name else None
            report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list, incident)
            observer = Observer(news_source, report, geo_entity_observer)
            aggregator = Aggregator(TABLE_NAME, observer)
            

            # From the ontology structure, send to neo4j
            kg_manager.insert_aggregator(aggregator)
        kg_end_t = time.time()
        kg_times.append(kg_end_t - kg_start_t)

        TOTAL_ARTICLES = TOTAL_ARTICLES + 1
        print("Error cases: ", error_cases)

        asdf

    if not news_times:
        return None, None
    return statistics.mean(news_times), statistics.mean(kg_times)

def pull_by_day_folders(day_folders):

    # Get config
    config_data = get_config()
    save_folder = config_data["save_folder"]

    image_folder = save_folder + "/gkg"

    # Set up our managers
    blob_manager = BlobManager()
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    geocode_manager = GeoManager()
    kg_manager = KGManager(time_series_manager, connect_reports=False)

    # Open our json file
    with open("kg_construction/gdelt/gdelt_cameo.json", "r") as f:
        cameo_definitions = json.load(f)
    # Open the actor file
    with open("kg_construction/gdelt/gdelt_actor.json", "r") as f:
        actor_types = json.load(f)
    # Open the cap terms
    with open("kg_construction/gdelt/cap_terms.txt", "r") as f:
        cap_terms = f.read()


    # Open our config and get LLM API and google geocoding key
    # llm_uri = config_data["llm_host"]["uri"]
    # llm_client = LLMClient(llm_uri)
    llm_client = OpenAIClient()

    # Obtain LLM prompts
    with open("llm/prompts/gdelt_events.json", "r") as f:
        llm_prompt = json.load(f)

    all_news_times, all_kg_times = [], []

    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(image_folder, day_folder)

        # Get the gkg files
        data_files = os.listdir(day_folder_path)
        event_files = sorted([x for x in data_files if ".export" in x])

        # Iterate through each image and location file
        for i,event_file in tqdm(enumerate(event_files), total=len(event_files)):

            data_filepath = os.path.join(day_folder_path, event_file)

            news_times, kg_times = pull_into_kg(data_filepath, time_series_manager, kg_manager, \
                         cameo_definitions, actor_types, cap_terms, llm_client, llm_prompt, geocode_manager)
            if not news_times:
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

    image_folder = save_folder + "/gkg"
    day_folders = os.listdir(image_folder)

    # Set up our managers
    blob_manager = BlobManager()
    time_series_manager = TimeSeriesManager(TABLE_NAME, TABLE_TEMPLATE)
    geocode_manager = GeoManager()
    kg_manager = KGManager(time_series_manager)

    # Open our json file
    with open("kg_construction/gdelt/gdelt_cameo.json", "r") as f:
        cameo_definitions = json.load(f)
    # Open the actor file
    with open("kg_construction/gdelt/gdelt_actor.json", "r") as f:
        actor_types = json.load(f)
    # Open the cap terms
    with open("kg_construction/gdelt/cap_terms.txt", "r") as f:
        cap_terms = f.read()


    # Open our config and get LLM API and google geocoding key
    # llm_uri = config_data["llm_host"]["uri"]
    # llm_client = LLMClient(llm_uri)
    llm_client = OpenAIClient()

    # Obtain LLM prompts
    with open("llm/prompts/gdelt_events.json", "r") as f:
        llm_prompt = json.load(f)

    # Iterate through each day folder
    for day_folder in day_folders:
        day_folder_path = os.path.join(image_folder, day_folder)

        # Get the gkg files
        data_files = os.listdir(day_folder_path)
        event_files = sorted([x for x in data_files if ".export" in x])

        # Iterate through each image and location file
        for i,event_file in tqdm(enumerate(event_files), total=len(event_files)):

            data_filepath = os.path.join(day_folder_path, event_file)

            if TOTAL_ARTICLES > 2:  # Skip once we get more than 10 articles
                continue

            pull_into_kg(data_filepath, time_series_manager, kg_manager, \
                         cameo_definitions, actor_types, cap_terms, llm_client, llm_prompt, geocode_manager)


    # Close managers
    kg_manager.close_driver()
    time_series_manager.close_connection()
    # Boto3 (for blob manager) uses HTTP so it doesn't need to be explicitly closed


# Old version using gdelt files directly

# def pull_into_kg(event_filepath, time_series_manager, kg_manager, \
#                  cameo_definitions, actor_types, llm_client, llm_prompt):
#     print(event_filepath)
#     global TOTAL_ARTICLES
#     # Open the GKG file
#     df = pd.read_csv(event_filepath)

#     unique_articles = []

#     # Iterate through each row in the GKG file
#     for index, row in df.iterrows():

#         # Get the timestamp (when GDELT added it)
#         timestamp = row.iloc[59]

#         # Get the source URL
#         source_url = row.iloc[60]
#         news_source = source_url.split("/")[2]

#         # If we've visited this news article, skip it
#         if source_url in unique_articles:
#             continue
#         unique_articles.append(source_url)

#         # Prompt the LLM for the news
#         llm_responses = prompt_on_news(source_url, llm_client, llm_prompt)

#         for response in llm_responses:

#             # Get the actor name, type, and geo information
#             actor1_name = row.iloc[6]
#             actor1_type = row.iloc[12]
#             actor1_geo_name = row.iloc[36]  # this is the format of state, country or city,state, country
#             actor1_latitude = row.iloc[40]
#             actor1_longitude = row.iloc[41]

#             # Get actor type description
#             actor1_type_desc = ""
#             if actor1_type in actor_types["role_type"]:
#                 actor1_type_desc = actor_types["role_type"][actor1_type]
#             elif actor1_type in actor_types["geo_type"]:
#                 actor1_type_desc = actor_types["geo_type"][actor1_type]

#             actor2_name = row.iloc[16]
#             actor2_type = row.iloc[22] 
#             actor2_geo_name = row.iloc[44]
#             actor2_latitude = row.iloc[48]
#             actor2_longitude = row.iloc[49]

#             # Get actor type description
#             actor2_type_desc = ""
#             if actor2_type in actor_types["role_type"]:
#                 actor2_type_desc = actor_types["role_type"][actor2_type]
#             elif actor2_type in actor_types["geo_type"]:
#                 actor2_type_desc = actor_types["geo_type"][actor2_type]

#             # Get the event information and its geo data
#             event_code = str(row.iloc[26]).zfill(3)
#             event_name = ""
#             event_description = ""
#             if event_code in cameo_definitions:
#                 event_name = cameo_definitions[event_code]["name"]
#                 event_description = cameo_definitions[event_code]["description"]

#             event_geo_name = row.iloc[52]
#             event_latitude = row.iloc[56]
#             event_longitude = row.iloc[57]

#             # Ignore empty cases
#             if is_nan(actor1_name) or is_nan(actor2_name):
#                 continue

#             # Now, insert the data into the table
#             data_to_insert = (timestamp, actor1_name, actor1_type, actor1_type_desc \
#                             , actor1_geo_name,\
#                 actor1_latitude, actor1_longitude, actor2_name, actor2_type, actor2_type_desc, \
#                 actor2_geo_name, actor2_latitude, actor2_longitude,\
#                 event_code, event_name, event_description,\
#                 event_geo_name, event_latitude, event_longitude, source_url)
#             print(len(data_to_insert))
#             db_id = time_series_manager.insert_data(data_to_insert)

#             actor_triple_list = [(
#                 Actor(actor1_name, actor1_type, actor1_type_desc, actor1_geo_name, [actor1_latitude, actor1_longitude]),
#                 ActorAction(event_code, event_name, event_description, event_geo_name, [event_latitude, event_longitude]),
#                 Actor(actor2_name, actor2_type, actor2_type_desc, actor2_geo_name, [actor2_latitude, actor2_longitude]),
#             )]

#             # Create the ontology structure
#             time_entity = TimeEntity(timestamp, timestamp)
#             geo_entity_observer = GeoEntity("", [], "")
#             geo_entity_measurement = ReportGeoEntity("", "N/A")
#             modality_obj_list = [Modality("link", "", event_filepath, source_url, [], actor_triple_list)]
#             report = Report(time_entity, geo_entity_measurement, db_id, modality_obj_list)
#             observer = Observer(news_source, report, geo_entity_observer)
#             aggregator = Aggregator(TABLE_NAME, observer)

#             # From the ontology structure, send to neo4j
#             kg_manager.insert_aggregator(aggregator)
        
#         TOTAL_ARTICLES = TOTAL_ARTICLES + 1
