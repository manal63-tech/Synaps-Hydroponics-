import sqlite3
import csv

conn = sqlite3.connect('hydromon.db')
c = conn.cursor()

# Get column names
c.execute("PRAGMA table_info(readings)")
columns = [col[1] for col in c.fetchall()]
print("Columns:", columns)

# Count rows
c.execute("SELECT COUNT(*) FROM readings")
count = c.fetchone()[0]
print(f"Total rows: {count}")

# Export to CSV
c.execute("SELECT * FROM readings ORDER BY created_at DESC")
rows = c.fetchall()

with open('my_sensor_data.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(columns)
    writer.writerows(rows)

print("✅ Saved to my_sensor_data.csv")
conn.close()