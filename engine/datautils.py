import urllib.request
import json, datetime, cv2
import numpy as np
from skimage import io, color, transform
from scipy.interpolate import interp2d
#from skimage.color import rgb2hsv, hsv2rgb
#from skimage.transform import rotate
from matplotlib import cm
import matplotlib.pyplot as plt

import openmeteo_requests

import requests_cache
import pandas as pd
from retry_requests import retry
import numpy as np

def fetch_meteo(args):

    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession('.cache', expire_after = -1)
    retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
    openmeteo = openmeteo_requests.Client(session = retry_session)

    # Make sure all required weather variables are listed here
    # The order of variables in hourly or daily is important to assign them correctly below
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": 52.52,
        "longitude": 13.41,
        "start_date": "2023-11-22",
        "end_date": "2024-11-22",
        "daily": ["temperature_2m_max", "temperature_2m_min", "sunshine_duration", "precipitation_sum"],
        "timezone": "Europe/Berlin"
    }
    responses = openmeteo.weather_api(url, params=params)

    # Process first location. Add a for-loop for multiple locations or weather models
    response = responses[0]
    print(f"Coordinates {response.Latitude()}°N {response.Longitude()}°E")
    print(f"Elevation {response.Elevation()} m asl")
    print(f"Timezone {response.Timezone()} {response.TimezoneAbbreviation()}")
    print(f"Timezone difference to GMT+0 {response.UtcOffsetSeconds()} s")

    # Process daily data. The order of variables needs to be the same as requested.
    daily = response.Daily()
    daily_temperature_2m_max = daily.Variables(0).ValuesAsNumpy()
    daily_temperature_2m_min = daily.Variables(1).ValuesAsNumpy()
    daily_sunshine_duration = daily.Variables(2).ValuesAsNumpy()
    daily_precipitation_sum = daily.Variables(3).ValuesAsNumpy()

    args.daily_data = {"date": pd.date_range(
        start = pd.to_datetime(daily.Time(), unit = "s", utc = True),
        end = pd.to_datetime(daily.TimeEnd(), unit = "s", utc = True),
        freq = pd.Timedelta(seconds = daily.Interval()),
        inclusive = "left"
    )}
    args.daily_data["temperature_2m_max"] = daily_temperature_2m_max
    args.daily_data["temperature_2m_min"] = daily_temperature_2m_min
    args.daily_data["sunshine_duration"] = daily_sunshine_duration
    args.daily_data["precipitation_sum"] = daily_precipitation_sum

def topolar(mat):

    angle_max = np.deg2rad(180)
    r_max = 20
    x = np.linspace(-20, 20, 800)
    y = np.linspace(20, -20, 800)
    y, x = np.ix_(y, x)
    r = np.hypot(x, y)
    a = np.arctan2(x, y)

    map_x = r / r_max * mat.shape[1]
    map_y = a / (2 * angle_max) * mat.shape[0] + mat.shape[0] * 0.5

    return cv2.remap(mat, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_CUBIC)

def render_temperature(args):

    Nt = len(args.daily_data['date'])
    tt = np.zeros((Nt,4))
    tt[:,2] = args.daily_data['temperature_2m_max']
    tt[:,3] = args.daily_data['temperature_2m_min']

    tmat = np.clip((10+tt[:,[2,3]])/40,0,1)
    tmat = np.r_['1', np.zeros((Nt,1)), tmat, np.zeros((Nt,1))].T
    ttmat = interp2d(np.r_[0:Nt], np.c_[0:4], tmat)
    ttmat = ttmat(np.r_[0:Nt], np.r_[0:1:5j,1:2:45j,2:3:5j])

    ttmat = cm.get_cmap('coolwarm')((ttmat*255).astype(np.uint8))[:,-360:]

    return ttmat

def render_precipitation(args):
    Nt = len(args.daily_data['date'])
    tt = np.zeros((Nt,4))
    tt[:,0] = args.daily_data['sunshine_duration']
    tt[:,0] = 1. - tt[:,0]/tt[:,0].max()
    tt[:,1] = args.daily_data['precipitation_sum']
    tt[:,1] /= tt[:,1].max()
    tt[:,2] = tt[:,1]
    tmat = np.sqrt(tt)
    
    ptmat = interp2d(np.r_[0:Nt], np.c_[0:4], tmat.T)
    ptmat = ptmat(np.r_[0:Nt], np.r_[0:1:8j,1:2:24j,2:3:3j])

    ptmat = cm.get_cmap('bone')((ptmat*255).astype(np.uint8))[:,-360:]

    return ptmat

def save_figure_to_png(fig, filename):
    fig.savefig(filename, format='png', dpi=300)