import csv
import json
from datetime import datetime, timezone, timedelta
import math
import sys
import os

# --- Constants ---
R_EARTH_KM = 6371.0  # Radius of Earth in kilometers
MAX_TIME_GAP_MINUTES = 25.0
MAX_DISTANCE_JUMP_KM = 2.0
DUPLICATE_POINT_FOR_LINESTRING = True # For GeoJSON LineString for single-point trips

GEOJSON_COLORS = [
    "#E6194B", "#3CB44B", "#FFE119", "#4363D8", "#F58231", "#911EB4",
    "#46F0F0", "#F032E6", "#BCF60C", "#FABEBE", "#008080", "#E6BEFF",
    "#9A6324", "#FFFAC8", "#800000", "#AAFFC3", "#808000", "#FFD8B1",
    "#000075", "#808080"  # 20 distinct colors
]

# --- Helper Functions ---

def parse_timestamp(ts_str, row_details_for_log, rejects_file_handle):
    """
    Parses an ISO 8601 timestamp string.
    Handles 'Z' for UTC, timezone offsets, and space separators.
    Normalizes to UTC. Returns None on failure.
    """
    if not ts_str:
        rejects_file_handle.write(f"{row_details_for_log}: Timestamp is empty.\n")
        return None

    # Replace space with 'T' if present (common in some ISO 8601 variants like in the sample image)
    ts_str_iso = ts_str.replace(' ', 'T')

    try:
        # datetime.fromisoformat handles 'Z' (by parsing as +00:00 if ends with Z)
        # and offsets like +HH:MM or -HH:MM.
        if ts_str_iso.endswith('Z'):
            # Ensure 'Z' is correctly interpreted as UTC for fromisoformat
            dt = datetime.fromisoformat(ts_str_iso[:-1] + '+00:00')
        else:
            dt = datetime.fromisoformat(ts_str_iso)
        
        # If datetime object is naive (no tzinfo), assume it's UTC as per common practice.
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Normalize to UTC if it had an offset (e.g. +02:00)
        return dt.astimezone(timezone.utc)
    except ValueError:
        rejects_file_handle.write(f"{row_details_for_log}: Invalid ISO 8601 timestamp format '{ts_str}'.\n")
        return None

def validate_coordinates(lat_str, lon_str, row_details_for_log, rejects_file_handle):
    """
    Validates latitude and longitude strings.
    Returns (float_lat, float_lon) or None on failure.
    """
    if not lat_str or not lon_str:
        rejects_file_handle.write(f"{row_details_for_log}: Latitude or longitude is empty.\n")
        return None
    try:
        lat = float(lat_str)
        lon = float(lon_str)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            rejects_file_handle.write(f"{row_details_for_log}: Invalid coordinates lat={lat_str}, lon={lon_str}. Out of range.\n")
            return None
        return lat, lon
    except ValueError:
        rejects_file_handle.write(f"{row_details_for_log}: Non-numeric latitude or longitude (lat='{lat_str}', lon='{lon_str}').\n")
        return None

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance in kilometers between two points 
    on the earth (specified in decimal degrees).
    """
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad

    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R_EARTH_KM * c

# --- Main Processing Logic ---
def process_gps_data(input_filepath, rejects_log_path="rejects.log"):
    valid_points = []
    
    try:
        # Ensure output directories/permissions are fine by opening rejects.log early
        with open(rejects_log_path, 'w') as r_file:
            try:
                with open(input_filepath, 'r', newline='', encoding='utf-8') as csvfile:
                    reader = csv.reader(csvfile)
                    # The problem does not specify if there's a header.
                    # If there is one, it will likely be rejected by validation and logged.
                    # Example: next(reader, None) # Skip header row if known
                    
                    for i, row in enumerate(reader):
                        row_num_for_log = i + 1 # 1-based for logging
                        # For logging, join row back to a string, limit length if row is huge
                        row_content_str = ','.join(row)
                        if len(row_content_str) > 100: row_content_str = row_content_str[:97] + "..."
                        row_details = f"Row {row_num_for_log} ('{row_content_str}')"

                        if len(row) != 4: # device_id, lat, lon, timestamp
                            r_file.write(f"{row_details}: Expected 4 columns, got {len(row)}.\n")
                            continue
                        
                        device_id, lat_str, lon_str, ts_str = row[0], row[1], row[2], row[3]

                        coords = validate_coordinates(lat_str, lon_str, row_details, r_file)
                        timestamp = parse_timestamp(ts_str, row_details, r_file)

                        if coords and timestamp:
                            valid_points.append({
                                'device_id': device_id,
                                'lat': coords[0],
                                'lon': coords[1],
                                'timestamp': timestamp, # datetime object (UTC)
                                'original_timestamp_str': ts_str # Keep if needed for exact CSV output format
                            })
            except FileNotFoundError:
                error_msg = f"CRITICAL: Input file '{input_filepath}' not found."
                print(error_msg)
                r_file.write(error_msg + "\n")
                return
            except Exception as e:
                error_msg = f"CRITICAL: Error reading CSV file '{input_filepath}': {e}"
                print(error_msg)
                r_file.write(error_msg + "\n")
                return

            # Order points by timestamp
            valid_points.sort(key=lambda p: p['timestamp'])

            if not valid_points:
                info_msg = "INFO: No valid GPS points found after cleaning and sorting."
                print(info_msg)
                r_file.write(info_msg + "\n")
                # Create empty GeoJSON if no trips
                with open("trips.geojson", 'w') as gjson_file:
                    json.dump({"type": "FeatureCollection", "features": []}, gjson_file)
                return

            # Split into trips
            all_trips_points_list = []
            if valid_points: # Ensure there's at least one point to start a trip
                current_trip_points = [valid_points[0]]
                for i in range(1, len(valid_points)):
                    prev_p = current_trip_points[-1]
                    curr_p = valid_points[i]

                    time_gap_seconds = (curr_p['timestamp'] - prev_p['timestamp']).total_seconds()
                    time_gap_minutes = time_gap_seconds / 60.0
                    
                    distance_jump_km = 0.0
                    # Only calculate haversine if points are different to avoid math errors with identical points
                    if prev_p['lat'] != curr_p['lat'] or prev_p['lon'] != curr_p['lon']:
                        distance_jump_km = haversine(prev_p['lat'], prev_p['lon'], curr_p['lat'], curr_p['lon'])

                    # Split criteria
                    if time_gap_minutes > MAX_TIME_GAP_MINUTES or distance_jump_km > MAX_DISTANCE_JUMP_KM:
                        all_trips_points_list.append(list(current_trip_points)) # Store a copy
                        current_trip_points = [curr_p] # Start new trip
                    else:
                        current_trip_points.append(curr_p)
                
                # Add the last ongoing trip
                if current_trip_points:
                    all_trips_points_list.append(list(current_trip_points))

            geojson_features = []
            for i, trip_points in enumerate(all_trips_points_list):
                trip_num = i + 1

                # Output trip_<n>.csv
                trip_csv_filename = f"trip_{trip_num}.csv"
                with open(trip_csv_filename, 'w', newline='', encoding='utf-8') as tcf:
                    writer = csv.writer(tcf)
                    # writer.writerow(["device_id", "lat", "lon", "timestamp"]) # Optional header for trip CSVs
                    for p in trip_points:
                        # Format timestamp to ISO 8601 with 'Z' for UTC
                        ts_iso_z = p['timestamp'].isoformat()
                        if ts_iso_z.endswith('+00:00'):
                           ts_iso_z = ts_iso_z[:-6] + 'Z'
                        writer.writerow([p['device_id'], p['lat'], p['lon'], ts_iso_z])

                # Compute trip statistics
                total_distance_km = 0.0
                max_segment_speed_kmh = 0.0
                
                if len(trip_points) >= 2:
                    for j in range(len(trip_points) - 1):
                        p1, p2 = trip_points[j], trip_points[j+1]
                        segment_dist_km = 0.0
                        if p1['lat'] != p2['lat'] or p1['lon'] != p2['lon']:
                             segment_dist_km = haversine(p1['lat'], p1['lon'], p2['lat'], p2['lon'])
                        total_distance_km += segment_dist_km
                        
                        segment_time_seconds = (p2['timestamp'] - p1['timestamp']).total_seconds()
                        # Avoid division by zero or near-zero for instantaneous speed
                        if segment_time_seconds > 1e-6: 
                            segment_speed_kmh = (segment_dist_km / (segment_time_seconds / 3600.0))
                            if segment_speed_kmh > max_segment_speed_kmh:
                                max_segment_speed_kmh = segment_speed_kmh
                        elif segment_dist_km > 0: # Distance moved in (near) zero time
                             # This implies very high speed; how to represent?
                             # For now, if duration is tiny, max_speed will reflect the last valid calc or stay 0
                             pass

                # Duration from first to last point of the trip
                duration_seconds = (trip_points[-1]['timestamp'] - trip_points[0]['timestamp']).total_seconds()
                duration_min = duration_seconds / 60.0

                avg_speed_kmh = 0.0
                if duration_seconds > 1e-6: # Avoid division by zero for average speed
                    duration_hours = duration_seconds / 3600.0
                    avg_speed_kmh = total_distance_km / duration_hours
                elif total_distance_km > 0 : # Travelled distance in (near) zero time
                    avg_speed_kmh = 0 # Policy: If duration is effectively zero, avg_speed is 0 to avoid 'inf'
                                      # Could also be a very large number if JSON spec allowed Infinity or specific string.

                trip_stats = {
                    "distance_km": round(total_distance_km, 3),
                    "duration_min": round(duration_min, 3),
                    "avg_speed_kmh": round(avg_speed_kmh, 3),
                    "max_speed_kmh": round(max_segment_speed_kmh, 3)
                }
                with open(f"trip_{trip_num}.json", 'w', encoding='utf-8') as tjf:
                    json.dump(trip_stats, tjf, indent=2)

                # Prepare GeoJSON LineString
                coordinates = [[p['lon'], p['lat']] for p in trip_points]
                if not coordinates: continue # Should not happen if trip_points is not empty
                
                # A GeoJSON LineString requires at least two positions.
                # If a trip consists of a single point, duplicate it to form a valid LineString.
                if len(coordinates) == 1 and DUPLICATE_POINT_FOR_LINESTRING:
                    coordinates.append(list(coordinates[0])) # Duplicate the point

                if len(coordinates) >= 2: # Only add if it can form a LineString
                    feature_properties = {
                        "trip_id": f"trip_{trip_num}", 
                        "color": GEOJSON_COLORS[(trip_num - 1) % len(GEOJSON_COLORS)], 
                        **trip_stats # Unpack the computed stats into properties
                    }
                    feature = {
                        "type": "Feature", 
                        "geometry": {"type": "LineString", "coordinates": coordinates}, 
                        "properties": feature_properties
                    }
                    geojson_features.append(feature)
                else:
                    # This case occurs if a trip has 0 or 1 point AND DUPLICATE_POINT_FOR_LINESTRING is False
                    r_file.write(f"INFO: Trip {trip_num} has < 2 coordinates for LineString, not included in GeoJSON.\n")

            # Output GeoJSON FeatureCollection
            geojson_output = {"type": "FeatureCollection", "features": geojson_features}
            with open("trips.geojson", 'w', encoding='utf-8') as gjson_file:
                json.dump(geojson_output, gjson_file, indent=2)
            
            print(f"Processing complete. {len(all_trips_points_list)} trips identified.")
            print(f"Output files generated in the current directory.")
            print(f"Rejects (if any) logged to: {rejects_log_path}")

    except IOError as e: # Catch issues like inability to write output files
        fatal_error_msg = f"FATAL IO Error (e.g., cannot write to '{rejects_log_path}' or other output files): {e}"
        print(fatal_error_msg)
        # Try to append to rejects_log if it was opened, otherwise this error won't be logged there.
        try:
            with open(rejects_log_path, 'a') as r_file_append: # append mode
                r_file_append.write(fatal_error_msg + "\n")
        except:
            pass # If rejects_log itself is the problem, can't do much here.
    except Exception as e: # Catch any other unexpected errors during main processing
        unexpected_error_msg = f"CRITICAL: An unexpected error occurred during processing: {e}"
        print(unexpected_error_msg)
        # Attempt to log unexpected error to rejects_log if possible
        try:
            with open(rejects_log_path, 'a') as r_file_append:
                r_file_append.write(unexpected_error_msg + "\n")
        except:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python your_script.py <input_csv_filepath>")
        sys.exit(1)
    
    input_file_arg = sys.argv[1]
    
    if not os.path.isfile(input_file_arg): # Check if it's a file and exists
        print(f"Error: Input file '{input_file_arg}' not found or is not a file.")
        # Log to rejects.log if possible, though rejects.log might not be created yet or path is unknown
        try:
            with open("rejects.log", 'w') as r_file: # Create/overwrite rejects.log
                 r_file.write(f"CRITICAL: Input file '{input_file_arg}' not found or is not a file.\n")
        except IOError:
            print("Additionally, could not write to rejects.log in the current directory.")
        sys.exit(1)
        
    process_gps_data(input_file_arg, "rejects.log")