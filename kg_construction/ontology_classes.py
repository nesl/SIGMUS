import os


# Important note - since we are 'submitting' a single observation at a time, we do not need to have multiple observers.
# Another important note - this is designed for 'submitting', so the analysis of 'incidents' really only happens after the submission process, so we don't
#   have a relation between modality and incident


class Aggregator:

    def __init__(self, name, observer):
        
        self.name = name  # Aggregator name
        self.hasObserver = observer  # observer object


class Observer:

    def __init__(self, name, report, geo_entity):
        self.name = name  # String
        self.hasReport = report
        self.locatedAt = geo_entity  # String, describes the location (possibly with a type?)

class Report:

    def __init__(self, time_entity, rep_geo_entity, db_id, modality_obj_list, incident=None):
        self.captureTime = time_entity
        self.reportLocation = rep_geo_entity
        self.hasModality = modality_obj_list # This is a list of modality objects
        self.db_id = db_id  # String - represents entry in the time series DB
        self.hasIncident = incident # Incident object

class Modality:

    def __init__(self, modality_type, blob_ref, file_path, value, situation_list, actor_triple_list=[]):
        self.modality_type = modality_type  # String
        self.hasBlob = blob_ref  # String - represents the entry in the blob storage
        self.value = value
        self.filePath = file_path
        self.hasSituation = situation_list  # A python list of dicts
        self.actor_triple_list = actor_triple_list # Pair of actors
        self.set_actor_action_references(actor_triple_list) # Set references to actors and actions
    
    # Set refernce to actors and actions
    def set_actor_action_references(self, actor_triple_list):
        for tup in actor_triple_list:
            for x in tup:
                x.set_parent(self)
            

class Actor:
    def __init__(self, name, actor_type, actor_type_desc, geo_name, latlong_coords):
        self.name = name
        self.actor_type = actor_type
        self.actor_type_desc = actor_type_desc
        self.geo_info = GeoEntity("", latlong_coords, geo_name)
        self.parent = None
    
    def set_parent(self, ref):
        self.parent = ref

class ActorAction:
    def __init__(self, action_code, action_name, action_description, geo_name, \
                 latlong_coords, action_time):
        self.action_code = action_code
        self.action_name = action_name
        self.time_entity = TimeEntity(action_time, action_time)
        self.action_description = action_description
        self.geo_info = GeoEntity("", latlong_coords, geo_name)
    
    def set_parent(self, ref):
        self.parent = ref


# class Situation:

#     def __init__(self, data):
#         self.hasData = data


class Incident:

    def __init__(self, label, news_text):
        self.label = label
        self.context = news_text

class TimeEntity:

    def __init__(self, start_time, end_time):
        self.startTime = start_time  # Integer for start timestamp
        self.endTime = end_time  # Integer for end timestamp    

class GeoEntity:

    def __init__(self, address, coordinates, place_description):
        self.address = address # String for mail address
        self.coordinates = coordinates # latlong coordinates for a point or region
        self.description = place_description # String obtained from a map server like Google places API

class ReportGeoEntity:

    def __init__(self, value, geo_type_description):
        self.value = value
        self.geo_type_description = geo_type_description


