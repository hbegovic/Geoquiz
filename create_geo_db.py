import sqlite3
import json
import os

DB_PATH = 'geo_quiz.db'

# Ako već postoji, obriši da krećemo od nule
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Kreiranje tabela
cur.execute('''
CREATE TABLE categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    color TEXT
)
''')

cur.execute('''
CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    options TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id)
)
''')

# Ubacivanje kategorija
cur.execute("INSERT INTO categories (name, description, icon, color) VALUES (?, ?, ?, ?)",
            ('Pogodi zastavu', 'Prepoznaj državu po njenoj zastavi', '🇺🇳', '#3498db'))
cur.execute("INSERT INTO categories (name, description, icon, color) VALUES (?, ?, ?, ?)",
            ('Pogodi obris države', 'Prepoznaj državu po njenom obrisu', '🗺️', '#2ecc71'))

# Primjer pitanja za zastave
cur.execute("INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES (?, ?, ?, ?)",
            (1, '/static/flags/de.png', json.dumps(["Njemačka","Belgija","Austrija","Nizozemska"]), 'Njemačka'))
cur.execute("INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES (?, ?, ?, ?)",
            (1, '/static/flags/fr.png', json.dumps(["Francuska","Italija","Rumunija","Nizozemska"]), 'Francuska'))

# Primjer pitanja za obrise država
cur.execute("INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES (?, ?, ?, ?)",
            (2, '/static/outlines/ba.png', json.dumps(["Bosna i Hercegovina","Srbija","Hrvatska","Crna Gora"]), 'Bosna i Hercegovina'))
cur.execute("INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES (?, ?, ?, ?)",
            (2, '/static/outlines/hr.png', json.dumps(["Hrvatska","Slovenija","Srbija","Mađarska"]), 'Hrvatska'))

conn.commit()
conn.close()

print("✅ Baza geo_quiz.db je kreirana i popunjena!")
