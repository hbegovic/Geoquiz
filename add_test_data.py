# add_test_data.py - Dodaj test kategorije u postojeću bazu

import sqlite3
import json

def add_test_data():
    conn = sqlite3.connect('geo_quiz.db')
    cursor = conn.cursor()
    
    # Obriši postojeće podatke (ako ih ima)
    cursor.execute('DELETE FROM questions')
    cursor.execute('DELETE FROM categories')
    
    print("🗑️  Obrisani stari podaci")
    
    # Dodaj kategorije
    categories = [
        (1, "Zastave Svijeta", "Pogodi kojoj zemlji pripada zastava", "🏴", "#ff6b6b"),
        (2, "Oblik Zemlje", "Prepoznaj zemlju po njezinoj konturi", "🗺️", "#4ecdc4"),
        (3, "Glavni Gradovi", "Koji je glavni grad ove zemlje?", "🏛️", "#45b7d1"),
        (4, "Kontinenti", "Geografija kontinenata i regija", "🌍", "#f9ca24"),
        (5, "Rijeke i Mora", "Vodeni putovi svijeta", "🌊", "#6c5ce7")
    ]
    
    cursor.executemany(
        'INSERT INTO categories (id, name, description, icon, color) VALUES (?, ?, ?, ?, ?)', 
        categories
    )
    
    print(f"✅ Dodano {len(categories)} kategorija")
    
    # Dodaj nekoliko test pitanja
    test_questions = [
        (1, 1, '/static/flags/croatia.png', ["Hrvatska", "Slovenija", "Srbija", "Bosna"], "Hrvatska"),
        (2, 1, '/static/flags/germany.png', ["Njemačka", "Belgija", "Nizozemska", "Austrija"], "Njemačka"),
        (3, 2, '/static/outlines/italy.png', ["Italija", "Grčka", "Španjolska", "Portugal"], "Italija"),
        (4, 3, '/static/cities/paris.jpg', ["Pariz", "London", "Berlin", "Madrid"], "Pariz"),
        (5, 4, '/static/continents/africa.png', ["Afrika", "Južna Amerika", "Australija", "Azija"], "Afrika")
    ]
    
    for q in test_questions:
        cursor.execute('''
            INSERT INTO questions (id, category_id, image_path, options, correct_answer)
            VALUES (?, ?, ?, ?, ?)
        ''', (q[0], q[1], q[2], json.dumps(q[3]), q[4]))
    
    print(f"✅ Dodano {len(test_questions)} test pitanja")
    
    # Provjeri rezultat
    cursor.execute('SELECT COUNT(*) FROM categories')
    cat_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM questions') 
    q_count = cursor.fetchone()[0]
    
    print(f"\n📊 REZULTAT:")
    print(f"   Kategorija: {cat_count}")
    print(f"   Pitanja: {q_count}")
    
    # Prikaži kategorije
    cursor.execute('SELECT id, name, description, icon FROM categories')
    cats = cursor.fetchall()
    print(f"\n📋 KATEGORIJE:")
    for cat in cats:
        print(f"   {cat[3]} {cat[1]} - {cat[2]}")
    
    conn.commit()
    conn.close()
    
    print(f"\n🎉 GOTOVO! Sada restartaj Flask server i osvježi stranicu.")

if __name__ == '__main__':
    add_test_data()