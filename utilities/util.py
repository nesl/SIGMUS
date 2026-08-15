
import json
from datetime import datetime, timedelta
import pytz
import math
import os


import itertools

def generate_ordered_combinations(tokens, target_length):
    """
    Generates ordered combinations of tokens padded with empty strings
    to match the target length, while maintaining the order of the original tokens.
    
    Args:
    - tokens: List of tokens (strings)
    - target_length: The length to pad the permutation to
    
    Returns:
    - A list of ordered combinations with empty string padding
    """
    # Calculate how many empty strings need to be added
    num_empty_strings = target_length - len(tokens)
    
    if num_empty_strings < 0:
        raise ValueError("Target length must be greater than or equal to the length of tokens")
    
    # Create a list of empty strings
    empty_tokens = [''] * num_empty_strings
    
    # Combine the original tokens and empty tokens
    padded_tokens = tokens + empty_tokens
    
    # Generate all combinations of positions for the empty strings
    result = []
    for positions in itertools.combinations(range(target_length), num_empty_strings):
        # Create a new list to hold the ordered combination
        perm = []
        token_index = 0
        empty_index = 0
        for i in range(target_length):
            if i in positions:
                perm.append(empty_tokens[empty_index])  # Insert an empty string
                empty_index += 1
            else:
                perm.append(tokens[token_index])  # Insert an original token
                token_index += 1
        result.append(perm)
    
    return result

# Haversine formula to calculate the distance between two lat/long points
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in kilometers
    
    # Convert degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Differences between coordinates
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    # Haversine formula
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    # Distance in kilometers
    distance = R * c
    return distance

# Open up the config file
def _expand_config_environment(value):
    """Resolve exact ``${NAME}`` values without placing secrets in JSON files."""
    if isinstance(value, dict):
        return {key: _expand_config_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_config_environment(item) for item in value]
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        name = value[2:-1]
        if not name:
            raise ValueError("Empty environment-variable placeholder in configuration")
        try:
            return os.environ[name]
        except KeyError as exc:
            raise RuntimeError(f"Required configuration environment variable is not set: {name}") from exc
    return value


def get_config(filepath=None):
    """Load configuration from an explicit path, env-selected path, or ./config.json.

    ``URBAN_SYSTEM_CONFIG`` selects a file without relying on the current working
    directory. Any scalar JSON value written exactly as ``${ENV_NAME}`` is
    replaced with that environment variable and fails clearly when it is absent.
    """
    resolved = filepath or os.environ.get("URBAN_SYSTEM_CONFIG", "./config.json")
    with open(resolved, "rb") as file:
        json_data = json.load(file)
    return _expand_config_environment(json_data)


# Function to obtain data for the last X amount of time
def get_past_timestamp(time_interval, current_tz_timestamp):
    
    # Parse the time interval input
    time_value, time_unit = time_interval.split()
    time_value = int(time_value)

    # Create a timedelta based on the time_unit
    if time_unit.lower() == "min" or time_unit.lower() == "minute" or time_unit.lower() == "minutes":
        delta = timedelta(minutes=time_value)
    elif time_unit.lower() == "hour" or time_unit.lower() == "hours":
        delta = timedelta(hours=time_value)
    elif time_unit.lower() == "day" or time_unit.lower() == "days":
        delta = timedelta(days=time_value)
    elif time_unit.lower() == "week" or time_unit.lower() == "weeks":
        delta = timedelta(weeks=time_value)
    else:
        raise ValueError("Invalid time unit. Please use one of 'min', 'hour', 'day', 'week'.")

    # Subtract the timedelta from the current timestamp
    past_timestamp = current_tz_timestamp - delta
    return past_timestamp
