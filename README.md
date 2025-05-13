# GPS Trip Analysis

Processes shuffled GPS data (device_id, lat, lon, ISO8601 timestamp) from a CSV file. It cleans data, sorts points, splits into trips, calculates stats, and outputs results.

## Requirements

-   Python 3.7+ (Standard library only)

## How to Run

1.  Place `your_script.py` and your input CSV (e.g., `data.csv`) in a directory.
2.  Open a terminal in that directory.
3.  Execute:
    python your_script.py data.csv
    (Replace `data.csv` with your input CSV filename).

## Outputs

Generated in the script's directory:
*   `rejects.log`: Skipped rows and reasons.
*   `trip_N.csv`: Points for each trip.
*   `trip_N.json`: Stats (distance, duration, speeds) for each trip.
*   `trips.geojson`: All trips as a colored LineString FeatureCollection.

The script aims for completion under 1 minute on a typical laptop.
