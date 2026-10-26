import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np

# Load data
df = pd.read_csv('C:/Users/ELBADR-INFO/Documents/finale one plsss/hydromon/my_sensor_data.csv')
df['created_at'] = pd.to_datetime(df['created_at'])
df = df.sort_values('created_at')

# Clean impossible sensor values (mark as NaN)
df.loc[(df['ph'] < 0) | (df['ph'] > 14), 'ph'] = np.nan
df.loc[df['tds'] < 0, 'tds'] = np.nan
df.loc[df['water_temperature'] < -50, 'water_temperature'] = np.nan
df.loc[(df['temperature'] > 80) | (df['temperature'] < -10), 'temperature'] = np.nan

print("=" * 60)
print("RAPPORT D'ANALYSE DES DONNÉES - SYSTÈME HYDROMON") 
print("=" * 60)
print(f"Période: {df['created_at'].min()} à {df['created_at'].max()}")