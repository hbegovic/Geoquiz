import sqlite3

conn = sqlite3.connect('geo_quiz.db')
cur = conn.cursor()

# Ispravi sve unose koji imaju Italy PNG ili pogrešan path
cur.execute("""
UPDATE questions
SET image_path = 'static/outlines/it.svg'
WHERE correct_answer = 'Italija'
""")

conn.commit()
conn.close()

print("Sve putanje za Italiju su sada ispravljene.")
