import os
from datetime import datetime, timedelta
import pytz
import pandas as pd
import numpy as np
from utilities.util import *

# Various trends to calculate
def calculate_trends(values):
    # Calculate mean
    series = pd.Series(values)
    mean_value = series.mean().item()

    # Calculate overall trend
    rate_of_change = np.diff(values)
    if np.all(rate_of_change > 0):
        trend_change = "Increasing"
    elif np.all(rate_of_change < 0):
        trend_change = "Decreasing"
    else:
        trend_change = "Mixed or Flat"

    trend_info = {"Mean value": mean_value, "Trend change": trend_change}
    return trend_info


# Function to obtain data within the time series db
def obtain_trends(time_series_manager, current_ts, station_id, \
                  column_names, \
                time_intervals = ["15 min", "1 hour", "1 day", "1 week"]):
    

    # # Show last X rows for debug
    # print(time_series_manager.fetch_last_x_rows(5))

    # Add tz info to current timestamp
    current_ts = time_series_manager.convert_ms_to_timestamp(current_ts)

    # Obtain past timestamp for each time interval
    column_intervals = {x:[] for x in column_names}  # column name, list of trends
    for time_interval in time_intervals:
        # Make sure we convert to the time series db format
        past_timestamp = get_past_timestamp(time_interval, current_ts)
        past_timestamp = time_series_manager.convert_ms_to_timestamp(past_timestamp)

        values = time_series_manager.fetch_time_range(past_timestamp, \
                                                      current_ts, station_id, column_names)
        # Change to pointwise comparison
        if len(values) > 1:
            values = [values[0], values[1]] # oldest, newest


        # Iterate through each column, get the values, calculate the respective trends
        column_info = []
        for i,x in enumerate(column_names):

            # Get the values for the column
            column_values = [v[i] for v in values]
            if any([v!=v for v in column_values]):
                continue
            # print(column_values)
            trend_info = calculate_trends(column_values)
            
            trend_info['trend_type'] = "analysis for past " + time_interval

            column_intervals[x].append(trend_info)
    
    
    return column_intervals



    




if __name__ == "__main__":

    current_time = datetime.now(pytz.UTC)
    print("Current time: ", current_time)

    # Example inputs
    time_intervals = ["15 min", "1 hour", "8 hour", "1 day", "1 week"]

    for time_interval in time_intervals:
        past_timestamp = get_past_timestamp(time_interval, current_time)
        print(f"Time interval: {time_interval} - Past timestamp: {past_timestamp}")
