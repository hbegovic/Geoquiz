"""
Seed kategorije 'Glavni Gradovi' (category_id=3).
Za svaku zemlju iz countries.json za koju imamo glavni grad i postojeću zastavu
ubacuje jedno pitanje: slika je zastava, tačan odgovor je glavni grad,
opcije sadrže još 3 nasumična distraktora iz preostalih prijestonica.
"""
import json
import os
import random
import sqlite3

DB_PATH = 'geo_quiz.db'
COUNTRIES_JSON = 'countries.json'
FLAGS_DIR = os.path.join('static', 'flags')
CAPITALS_CATEGORY_ID = 3

# Bosanski/hrvatski nazivi prijestonica.
# Sve što nije ovdje navedeno koristi engleski iz countries.json.
CAPITAL_BS = {
    # Europa
    'AT': 'Beč', 'BE': 'Brisel', 'BA': 'Sarajevo', 'BG': 'Sofija', 'BY': 'Minsk',
    'CH': 'Bern', 'CY': 'Nikozija', 'CZ': 'Prag', 'DE': 'Berlin', 'DK': 'Kopenhagen',
    'EE': 'Talin', 'ES': 'Madrid', 'FI': 'Helsinki', 'FR': 'Pariz', 'GB': 'London',
    'GR': 'Atina', 'HR': 'Zagreb', 'HU': 'Budimpešta', 'IE': 'Dablin', 'IS': 'Rejkjavik',
    'IT': 'Rim', 'LI': 'Vaduz', 'LT': 'Vilnius', 'LU': 'Luksemburg', 'LV': 'Riga',
    'MC': 'Monako', 'MD': 'Kišinjev', 'ME': 'Podgorica', 'MK': 'Skoplje', 'MT': 'Valeta',
    'NL': 'Amsterdam', 'NO': 'Oslo', 'PL': 'Varšava', 'PT': 'Lisabon', 'RO': 'Bukurešt',
    'RS': 'Beograd', 'RU': 'Moskva', 'SE': 'Stockholm', 'SI': 'Ljubljana', 'SK': 'Bratislava',
    'SM': 'San Marino', 'TR': 'Ankara', 'UA': 'Kijev', 'VA': 'Vatikan', 'XK': 'Priština',
    'AL': 'Tirana', 'AD': 'Andora la Veja',

    # Azija
    'AE': 'Abu Dabi', 'AF': 'Kabul', 'AM': 'Jerevan', 'AZ': 'Baku', 'BD': 'Daka',
    'BH': 'Manama', 'BN': 'Bandar Seri Begavan', 'BT': 'Timphu', 'CN': 'Peking',
    'GE': 'Tbilisi', 'ID': 'Džakarta', 'IL': 'Jerusalim', 'IN': 'Nju Delhi', 'IQ': 'Bagdad',
    'IR': 'Teheran', 'JO': 'Aman', 'JP': 'Tokio', 'KG': 'Biškek', 'KH': 'Pnom Pen',
    'KP': 'Pjongjang', 'KR': 'Seul', 'KW': 'Kuvajt', 'KZ': 'Astana', 'LA': 'Vijentijan',
    'LB': 'Bejrut', 'LK': 'Kolombo', 'MM': 'Nepjido', 'MN': 'Ulan Bator', 'MV': 'Male',
    'MY': 'Kuala Lumpur', 'NP': 'Katmandu', 'OM': 'Maskat', 'PH': 'Manila', 'PK': 'Islamabad',
    'PS': 'Ramala', 'QA': 'Doha', 'SA': 'Rijad', 'SG': 'Singapur', 'SY': 'Damask',
    'TH': 'Bangkok', 'TJ': 'Dušanbe', 'TL': 'Dili', 'TM': 'Ašgabat', 'TW': 'Tajpej',
    'UZ': 'Taškent', 'VN': 'Hanoj', 'YE': 'Sana',

    # Afrika
    'AO': 'Luanda', 'BF': 'Vagadugu', 'BI': 'Gitega', 'BJ': 'Porto Novo', 'BW': 'Gaborone',
    'CD': 'Kinšasa', 'CF': 'Bangi', 'CG': 'Brazavil', 'CI': 'Jamusukro', 'CM': 'Jaunde',
    'CV': 'Praja', 'DJ': 'Džibuti', 'DZ': 'Alžir', 'EG': 'Kairo', 'ER': 'Asmara',
    'ET': 'Adis Abeba', 'GA': 'Librevil', 'GH': 'Akra', 'GM': 'Banđul', 'GN': 'Konakri',
    'GQ': 'Malabo', 'GW': 'Bisao', 'KE': 'Najrobi', 'KM': 'Moroni', 'LR': 'Monrovija',
    'LS': 'Maseru', 'LY': 'Tripoli', 'MA': 'Rabat', 'MG': 'Antananarivo', 'ML': 'Bamako',
    'MR': 'Nuakšot', 'MU': 'Port Luj', 'MW': 'Lilongve', 'MZ': 'Maputo', 'NA': 'Vindhuk',
    'NE': 'Niamej', 'NG': 'Abudža', 'RW': 'Kigali', 'SC': 'Viktorija', 'SD': 'Kartum',
    'SL': 'Friritaun', 'SN': 'Dakar', 'SO': 'Mogadiš', 'SS': 'Džuba', 'ST': 'Sao Tome',
    'SZ': 'Mbabane', 'TD': 'Ndžamena', 'TG': 'Lome', 'TN': 'Tunis', 'TZ': 'Dodoma',
    'UG': 'Kampala', 'ZA': 'Pretorija', 'ZM': 'Lusaka', 'ZW': 'Harare',

    # Amerike
    'AG': 'Sent Džons', 'AR': 'Buenos Ajres', 'BB': 'Bridžtaun', 'BO': 'Sukre',
    'BR': 'Brazilija', 'BS': 'Nasau', 'BZ': 'Belmopan', 'CA': 'Otava', 'CL': 'Santjago',
    'CO': 'Bogota', 'CR': 'San Hose', 'CU': 'Havana', 'DM': 'Roseau', 'DO': 'Santo Domingo',
    'EC': 'Kito', 'GD': 'Sent Džordžis', 'GT': 'Gvatemala', 'GY': 'Džordžtaun',
    'HN': 'Tegusigalpa', 'HT': 'Port o Prens', 'JM': 'Kingston', 'KN': 'Baster',
    'LC': 'Kastri', 'MX': 'Meksiko Siti', 'NI': 'Managva', 'PA': 'Panama', 'PE': 'Lima',
    'PY': 'Asunsion', 'SR': 'Paramaribo', 'SV': 'San Salvador', 'TT': 'Port of Spejn',
    'US': 'Vašington', 'UY': 'Montevideo', 'VC': 'Kingstaun', 'VE': 'Karakas',

    # Okeanija
    'AU': 'Kanbera', 'FJ': 'Suva', 'FM': 'Palikir', 'KI': 'Tarava', 'MH': 'Madžuro',
    'NR': 'Jaren', 'NZ': 'Velington', 'PG': 'Port Morsbi', 'PW': 'Ngerulmud',
    'SB': 'Honiara', 'TO': 'Nuku\'alofa', 'TV': 'Funafuti', 'VU': 'Port Vila',
    'WS': 'Apija',
}


def find_flag_file(cca2: str) -> str | None:
    """Vrati relativnu putanju do zastave (npr. 'flags/fr.svg') ili None."""
    code = cca2.lower()
    for ext in ('svg', 'png', 'jpg', 'jpeg', 'webp'):
        if os.path.exists(os.path.join(FLAGS_DIR, f'{code}.{ext}')):
            return f'flags/{code}.{ext}'
    return None


def main():
    with open(COUNTRIES_JSON, 'r', encoding='utf-8') as f:
        countries = json.load(f)

    # Pripremi parove (cca2, capital_label, flag_rel)
    entries = []
    for c in countries:
        cca2 = (c.get('cca2') or '').upper()
        caps = c.get('capital') or []
        if not cca2 or not caps:
            continue
        flag = find_flag_file(cca2)
        if not flag:
            continue
        capital_en = caps[0]
        capital = CAPITAL_BS.get(cca2, capital_en)
        entries.append((cca2, capital, flag))

    all_capitals = [e[1] for e in entries]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Provjera kategorije
    cur.execute('SELECT id, name FROM categories WHERE id = ?', (CAPITALS_CATEGORY_ID,))
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO categories (id, name, description, icon, color) VALUES (?, ?, ?, ?, ?)',
            (CAPITALS_CATEGORY_ID, 'Glavni Gradovi', 'Koji je glavni grad ove zemlje?', '🏛️', '#a78bfa')
        )

    # Obriši stara pitanja za ovu kategoriju (čisti seed)
    cur.execute('DELETE FROM questions WHERE category_id = ?', (CAPITALS_CATEGORY_ID,))

    inserted = 0
    for cca2, capital, flag_rel in entries:
        distractor_pool = [c for c in all_capitals if c != capital]
        random.shuffle(distractor_pool)
        distractors = distractor_pool[:3]
        options = [capital] + distractors
        random.shuffle(options)
        cur.execute(
            'INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES (?, ?, ?, ?)',
            (CAPITALS_CATEGORY_ID, flag_rel, json.dumps(options, ensure_ascii=False), capital)
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f'OK: ubaceno {inserted} pitanja u kategoriju Glavni Gradovi (id={CAPITALS_CATEGORY_ID}).')


if __name__ == '__main__':
    main()
