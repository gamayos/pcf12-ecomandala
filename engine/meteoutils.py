import os, json, requests, functions_framework, openmeteo_requests
import importlib, datetime, h3, time, uuid
from flask import jsonify, make_response
from types import SimpleNamespace
import requests_cache
import pandas as pd
from retry_requests import retry
from google.cloud import storage
import numpy as np

def fetch_meteo(args):

    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession('.cache', expire_after = -1)
    retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
    openmeteo = openmeteo_requests.Client(session = retry_session)

    # Make sure all required weather variables are listed here
    # The order of variables in hourly or daily is important to assign them correctly below

    nowdate = datetime.datetime.now()
    predate = (nowdate - datetime.timedelta(days=365))

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": args.latlon[0],
        "longitude": args.latlon[1],
        "start_date": predate.strftime('%Y-%m-%d'),
        "end_date": nowdate.strftime('%Y-%m-%d'),
        "daily": ["temperature_2m_max", "temperature_2m_min", "sunshine_duration", "precipitation_sum"],
        "timezone": "Europe/Berlin"
    }
    responses = openmeteo.weather_api(url, params=params)

    # Process first location. Add a for-loop for multiple locations or weather models
    response = responses[0]
    #print(f"Coordinates {response.Latitude()}°N {response.Longitude()}°E")
    #print(f"Elevation {response.Elevation()} m asl")
    #print(f"Timezone {response.Timezone()} {response.TimezoneAbbreviation()}")
    #print(f"Timezone difference to GMT+0 {response.UtcOffsetSeconds()} s")

    # Process daily data. The order of variables needs to be the same as requested.
    daily = response.Daily()
    daily_temperature_2m_max = daily.Variables(0).ValuesAsNumpy()
    daily_temperature_2m_min = daily.Variables(1).ValuesAsNumpy()
    daily_sunshine_duration = daily.Variables(2).ValuesAsNumpy()
    daily_precipitation_sum = daily.Variables(3).ValuesAsNumpy()

    args.data.daily_data = {"date": pd.date_range(
        start=pd.to_datetime(daily.Time(), unit="s", utc=True).normalize().strftime('%Y-%m-%d'),
        end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True).normalize().strftime('%Y-%m-%d'),
        freq=pd.Timedelta(days=1),
        inclusive="left"
    )}
    args.data.daily_data['juldate'] = [d for d in args.data.daily_data['date'].to_julian_date().astype(int)]
    
    daily_sunshine_duration = pd.Series(daily_sunshine_duration).ffill().to_numpy()
    daily_precipitation_sum = pd.Series(daily_precipitation_sum).ffill().to_numpy()
    daily_temperature_2m_max = pd.Series(daily_temperature_2m_max).ffill().to_numpy()
    daily_temperature_2m_min = pd.Series(daily_temperature_2m_min).ffill().to_numpy()
    args.data.daily_data["temperatureMax"] = [round(float(t), 2) for t in daily_temperature_2m_max]
    args.data.daily_data["temperatureMin"] = [round(float(t), 2) for t in daily_temperature_2m_min]
    args.data.daily_data["sunshineDuration"] = [round(float(t/60)) for t in daily_sunshine_duration]
    args.data.daily_data["precipitationSum"] = [round(float(t), 2) for t in daily_precipitation_sum]


@functions_framework.http
def main(request):
   
    args = SimpleNamespace()
    args.request = request
    args.gcp_bucket = 'ecomandala-preprod-9f3489c8-eb24-4d88'
    #lu.init_logdb(args)

    params = request.path.split('/')
    args.pid = params[1] if params[1] else request.get_json(silent=True)['pid']
    args.h3index = args.pid.split('-')[0]

    h3id5 = h3.cell_to_parent(args.h3index, res=5)[:8]
    h3id1 = h3.cell_to_parent(args.h3index, res=1)[:5]
    gcp_path = f'{h3id1}/{h3id5}/{args.pid}'

    client = storage.Client.create_anonymous_client()
    bucket = client.bucket(args.gcp_bucket)
    blob = bucket.blob(f'{gcp_path}/{args.pid}-meteo.json')
    if blob.exists():
        url = f'https://storage.googleapis.com/{args.gcp_bucket}/{gcp_path}/{args.pid}-meteo.json'
        data = json.loads(requests.get(url).text)
    else:
        fetch_meteo(args)
        with open('/tmp/meteo.json', 'w') as f:
            json.dump(args.data.daily_data, f, default=str)
        blob.upload_from_filename('/tmp/meteo.json')
        data = args.data.daily_data

    response = make_response(jsonify(data))

    # Add CORS headers
    response.headers['Access-Control-Allow-Origin'] = '*'  # Allow all origins
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

    # Handle preflight requests
    if request.method == 'OPTIONS':
        return response, 204

    return response
