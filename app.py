import os
import sqlite3
import json
import random
import re
import time
import string
import uuid
from datetime import datetime
from flask import Flask, jsonify, render_template, request, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from werkzeug.utils import secure_filename
from functools import wraps
from flask import redirect
from flask import render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "promijeni_me")



# --------- KONFIG ---------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tajna_za_kviz'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --------- MULTIPLAYER ---------
ROOMS = {}  # {room_id: {id, name, mode, category_id, max_players, time_per_q, total_questions,
            #            status, host_sid, players: {}, questions: [], current_q_idx, q_start_time}}

def generate_room_id():
    """Generiši 6-karakterni room kod."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

POPULATION_CATEGORY_ID = 7

def generate_questions_for_room(category_id, n=20):
    """Generiši N pitanja za multiplayer sobu."""
    conn = get_db()
    questions = []

    # Posebna logika za Broj Stanovnika
    if category_id == POPULATION_CATEGORY_ID:
        all_codes = list(COUNTRY_POPULATION.keys())
        random.shuffle(all_codes)
        for code in all_codes[:n]:
            pop = COUNTRY_POPULATION[code]
            name = ALIASES.get(code, code.upper())
            questions.append({
                'id': code,
                'type': 'population',
                'image_path': f'flags/{code}.svg',
                'country': name,
                'population': pop,
                'options': [],
                'correct': str(pop),
            })
        return questions

    asked_ids = set()

    # Za flags/outlines: učitaj sve nazive za distraktore
    if category_id in (CATEGORY_FLAGS_ID, CATEGORY_OUTLINES_ID):
        all_names_rows = conn.execute(
            'SELECT DISTINCT correct_answer FROM questions WHERE category_id=?',
            (category_id,)
        ).fetchall()
        all_names = [(r['correct_answer'] or '').strip() for r in all_names_rows if r['correct_answer']]

    for _ in range(n * 3):
        if len(questions) >= n:
            break
        try:
            q = conn.execute(
                f'SELECT id, image_path, options, correct_answer FROM questions '
                f'WHERE category_id=? AND id NOT IN ({",".join("?" * len(asked_ids) if asked_ids else "0")})'
                f'ORDER BY RANDOM() LIMIT 1',
                (category_id, *list(asked_ids)) if asked_ids else (category_id,)
            ).fetchone()
            if not q:
                break
            asked_ids.add(q['id'])

            correct = (q['correct_answer'] or '').strip()
            if not correct:
                continue

            raw_path = (q['image_path'] or '').strip().lstrip('/')

            if category_id in (CATEGORY_FLAGS_ID, CATEGORY_OUTLINES_ID):
                wrongs = [x for x in all_names if x != correct]
                random.shuffle(wrongs)
                options = [correct] + wrongs[:3]
                random.shuffle(options)
            else:
                try:
                    options = json.loads(q['options']) if isinstance(q['options'], str) else (q['options'] or [])
                    if not isinstance(options, list):
                        options = [correct]
                except:
                    options = [correct]

            questions.append({
                'id': q['id'],
                'type': 'multiple_choice',
                'image_path': raw_path,
                'options': options,
                'correct': correct,
            })
        except Exception as e:
            app.logger.error(f"Error generating question: {e}")
            continue

    return questions

def calculate_score(time_used, time_limit, is_correct):
    """Izračunaj bodove: 100 za točan + do 50 speed bonusa."""
    if not is_correct:
        return 0
    base = 100
    if time_used <= 1:
        return base + 50
    if time_used >= time_limit:
        return base
    # Interpolate: time=1 → 50, time=time_limit → 0
    speed_bonus = max(0, int(50 * (1 - (time_used - 1) / (time_limit - 1))))
    return base + speed_bonus

DB_PATH = 'geo_quiz.db'
UPLOAD_FOLDER = 'static'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'svg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Kategorije
CATEGORY_FLAGS_ID = 1
CATEGORY_OUTLINES_ID = 2
FLAGS_DIR = os.path.join(app.config['UPLOAD_FOLDER'], 'flags')      # static/flags
OUTLINES_DIR = os.path.join(app.config['UPLOAD_FOLDER'], 'outlines') # static/outlines


# --------- POMOĆNE ---------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --------- Tabele ---------
def ensure_users_table():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)

def ensure_leaderboard_table():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            user_id INTEGER,
            category_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            score INTEGER NOT NULL,
            correct_answers INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            accuracy INTEGER NOT NULL,
            time_spent INTEGER DEFAULT 0,
            avatar TEXT DEFAULT '🎯',
            completed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)

# pozovi jednom pri startu
ensure_users_table()
ensure_leaderboard_table()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user'):
            flash('Prijavi se da bi nastavio.', 'warning')
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = session.get('user')
        if not user:
            return redirect(url_for('login', next=request.path))
        if not user.get('is_admin'):
            flash('Nemate admin privilegije.', 'danger')
            return redirect(url_for('home'))
        return view(*args, **kwargs)
    return wrapped

def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token") or request.args.get("token")
        if token != ADMIN_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ISO/alias -> bosanski naziv (najčešći)
ALIASES = {
    "ad": "Andora",
    "ae": "Ujedinjeni Arapski Emirati",
    "af": "Avganistan",
    "ag": "Antigva i Barbuda",
    "ai": "Angvila",
    "al": "Albanija",
    "am": "Armenija",
    "ao": "Angola",
    "aq": "Antarktik",
    "ar": "Argentina",
    "as": "Američka Samoa",
    "at": "Austrija",
    "au": "Australija",
    "aw": "Aruba",
    "ax": "Alandska ostrva",
    "az": "Azerbejdžan",
    "ba": "Bosna i Hercegovina",
    "bih": "Bosna i Hercegovina",
    "bb": "Barbados",
    "bd": "Bangladeš",
    "be": "Belgija",
    "bf": "Burkina Faso",
    "bg": "Bugarska",
    "bh": "Bahrein",
    "bi": "Burundi",
    "bj": "Benin",
    "bl": "Sveti Bartolomej",
    "bm": "Bermudi",
    "bn": "Brunej",
    "bo": "Bolivija",
    "bq": "Karipska Holandija",
    "br": "Brazil",
    "bs": "Bahami",
    "bt": "Butan",
    "bv": "Buve Ostrvo",
    "bw": "Bocvana",
    "by": "Bjelorusija",
    "bz": "Beliz",
    "ca": "Kanada",
    "cc": "Kokosova ostrva",
    "cd": "Demokratska Republika Kongo",
    "cf": "Centralnoafrička Republika",
    "cg": "Kongo",
    "ch": "Švicarska",
    "ci": "Obala Slonovače",
    "ck": "Kukova ostrva",
    "cl": "Čile",
    "cm": "Kamerun",
    "cn": "Kina",
    "co": "Kolumbija",
    "cr": "Kostarika",
    "cu": "Kuba",
    "cv": "Zelenortska Ostrva",
    "cw": "Kurasao",
    "cx": "Božićno ostrvo",
    "cy": "Kipar",
    "cz": "Češka",
    "de": "Njemačka",
    "dj": "Džibuti",
    "dk": "Danska",
    "dm": "Dominika",
    "do": "Dominikanska Republika",
    "dz": "Alžir",
    "ec": "Ekvador",
    "ee": "Estonija",
    "eg": "Egipat",
    "eh": "Zapadna Sahara",
    "er": "Eritreja",
    "es": "Španija",
    "et": "Etiopija",
    "fi": "Finska",
    "fj": "Fidži",
    "fk": "Foklandska ostrva",
    "fm": "Mikronezija",
    "fo": "Farska ostrva",
    "fr": "Francuska",
    "ga": "Gabon",
    "gb": "Ujedinjeno Kraljevstvo",
    "uk": "Ujedinjeno Kraljevstvo",
    "gd": "Grenada",
    "ge": "Gruzija",
    "gf": "Francuska Gvajana",
    "gg": "Gernzi",
    "gh": "Gana",
    "gi": "Gibraltar",
    "gl": "Grenland",
    "gm": "Gambija",
    "gn": "Gvineja",
    "gp": "Gvadelupe",
    "gq": "Ekvatorska Gvineja",
    "gr": "Grčka",
    "gs": "Južna Džordžija i Južna Sendvička Ostrva",
    "gt": "Gvatemala",
    "gu": "Guam",
    "gw": "Gvineja-Bisao",
    "gy": "Gvajana",
    "hk": "Hong Kong",
    "hm": "Herd i Mekdonald Ostrva",
    "hn": "Honduras",
    "hr": "Hrvatska",
    "ht": "Haiti",
    "hu": "Mađarska",
    "id": "Indonezija",
    "ie": "Irska",
    "il": "Izrael",
    "im": "Ostrvo Man",
    "in": "Indija",
    "io": "Britanska teritorija Indijskog okeana",
    "iq": "Irak",
    "ir": "Iran",
    "is": "Island",
    "it": "Italija",
    "je": "Džersi",
    "jm": "Jamajka",
    "jo": "Jordan",
    "jp": "Japan",
    "ke": "Kenija",
    "kg": "Kirgistan",
    "kh": "Kambodža",
    "ki": "Kiribati",
    "km": "Komori",
    "kn": "Sveti Kits i Nevis",
    "kp": "Sjeverna Koreja",
    "kr": "Južna Koreja",
    "kw": "Kuvajt",
    "ky": "Kajmanska ostrva",
    "kz": "Kazahstan",
    "la": "Laos",
    "lb": "Liban",
    "lc": "Sveta Lucija",
    "li": "Lihtenštajn",
    "lk": "Šri Lanka",
    "lr": "Liberija",
    "ls": "Lesoto",
    "lt": "Litvanija",
    "lu": "Luksemburg",
    "lv": "Latvija",
    "ly": "Libija",
    "ma": "Maroko",
    "mc": "Monako",
    "md": "Moldavija",
    "me": "Crna Gora",
    "mf": "Sveti Martin (francuski dio)",
    "mg": "Madagaskar",
    "mh": "Maršalska ostrva",
    "mk": "Sjeverna Makedonija",
    "ml": "Mali",
    "mm": "Mjanmar",
    "mn": "Mongolija",
    "mo": "Makao",
    "mp": "Sjeverna Marijanska ostrva",
    "mq": "Martinik",
    "mr": "Mauritanija",
    "ms": "Monserat",
    "mt": "Malta",
    "mu": "Mauricijus",
    "mv": "Maldivi",
    "mw": "Malavi",
    "mx": "Meksiko",
    "my": "Malezija",
    "mz": "Mozambik",
    "na": "Namibija",
    "nc": "Nova Kaledonija",
    "ne": "Niger",
    "nf": "Norfolk Ostrvo",
    "ng": "Nigerija",
    "ni": "Nikaragva",
    "nl": "Nizozemska",
    "no": "Norveška",
    "np": "Nepal",
    "nr": "Nauru",
    "nu": "Niue",
    "nz": "Novi Zeland",
    "om": "Oman",
    "pa": "Panama",
    "pe": "Peru",
    "pf": "Francuska Polinezija",
    "pg": "Papua Nova Gvineja",
    "ph": "Filipini",
    "pk": "Pakistan",
    "pl": "Poljska",
    "pm": "Sveti Petar i Mikelon",
    "pn": "Pitkern",
    "pr": "Portoriko",
    "ps": "Palestina",
    "pt": "Portugal",
    "pw": "Palau",
    "py": "Paragvaj",
    "qa": "Katar",
    "re": "Reunion",
    "ro": "Rumunija",
    "rs": "Srbija",
    "ru": "Rusija",
    "rw": "Ruanda",
    "sa": "Saudijska Arabija",
    "sb": "Solomonska ostrva",
    "sc": "Sejšeli",
    "sd": "Sudan",
    "se": "Švedska",
    "sg": "Singapur",
    "sh": "Sveta Helena",
    "si": "Slovenija",
    "sj": "Svalbard i Jan Majen",
    "sk": "Slovačka",
    "sl": "Sijera Leone",
    "sm": "San Marino",
    "sn": "Senegal",
    "so": "Somalija",
    "sr": "Surinam",
    "ss": "Južni Sudan",
    "st": "Sao Tome i Principe",
    "sv": "Salvador",
    "sx": "Sveti Martin (nizozemski dio)",
    "sy": "Sirija",
    "sz": "Esvatini",
    "tc": "Ostrva Turks i Kaikos",
    "td": "Čad",
    "tf": "Francuske južne teritorije",
    "tg": "Togo",
    "th": "Tajland",
    "tj": "Tadžikistan",
    "tk": "Tokelau",
    "tl": "Istočni Timor",
    "tm": "Turkmenistan",
    "tn": "Tunis",
    "to": "Tonga",
    "tr": "Turska",
    "tt": "Trinidad i Tobago",
    "tv": "Tuvalu",
    "tw": "Tajvan",
    "tz": "Tanzanija",
    "ua": "Ukrajina",
    "ug": "Uganda",
    "um": "Udaljena ostrva SAD",
    "us": "Sjedinjene Američke Države",
    "usa": "Sjedinjene Američke Države",
    "uy": "Urugvaj",
    "uz": "Uzbekistan",
    "va": "Vatikan",
    "vc": "Sveti Vincent i Grenadini",
    "ve": "Venecuela",
    "vg": "Britanska Djevičanska Ostrva",
    "vi": "Američka Djevičanska Ostrva",
    "vn": "Vijetnam",
    "vu": "Vanuatu",
    "wf": "Valis i Futuna",
    "ws": "Samoa",
    "ye": "Jemen",
    "yt": "Majot",
    "za": "Južnoafrička Republika",
    "zm": "Zambija",
    "zw": "Zimbabve",
    "xk": "Kosovo",
    "ic": "Kanarska ostrva"
}

# Fajlovi koji nisu suverene države — preskoči pri bulk importu
SKIP_TOKENS = {
    'xx',           # placeholder / nepoznato
    'eu',           # Evropska unija
    'un',           # Ujedinjene nacije
    'cp',           # Kliperton (nenaseljen)
    'dg',           # Diego Garcia (vojna baza)
    'pc',           # Pacifička zajednica
    'ac',           # Ostrvo Uzašašća
    'ta',           # Tristan da Cunha
    'ea',           # Ceuta i Melila
    'gbeng', 'gbnir', 'gbsct', 'gbwls',   # UK regije
    'esct', 'esga', 'espv',               # Španske regije
    'arab', 'asean', 'cefta', 'eac',      # Organizacije
}

# (opcionalno) Ako želiš automatska eng imena za ISO2
try:
    import pycountry  # pip install pycountry (nije obavezno)
except Exception:
    pycountry = None

def guess_country_name(filename_no_ext: str) -> str:
    """
    Pogodi naziv države iz naziva fajla (ba.svg, bih.png, bosnia_and_herzegovina.svg, ...).
    Vraća bosanski naziv kad postoji u ALIASES; inače pycountry eng; inače title-case heuristika.
    """
    s = filename_no_ext.strip().lower()
    s_clean = re.sub(r'[^a-z0-9]+', ' ', s)  # zamijeni separatore razmacima
    token = s_clean.replace(' ', '')

    if token in ALIASES:
        return ALIASES[token]

    if pycountry and len(token) == 2:
        try:
            c = pycountry.countries.get(alpha_2=token.upper())
            if c and c.name:
                return c.name  # eng naziv ako nema lokalizacije u ALIASES
        except Exception:
            pass

    # npr. "bosnia and herzegovina" -> "Bosnia And Herzegovina"
    return ' '.join(w.capitalize() for w in s_clean.split())

def load_existing_image_paths(conn, category_id):
    rows = conn.execute(
        "SELECT image_path FROM questions WHERE category_id = ?",
        (category_id,)
    ).fetchall()
    return {r['image_path'] for r in rows}

def build_distractors(all_names, correct_name, k=3):
    pool = [n for n in all_names if n.lower() != correct_name.lower()]
    random.shuffle(pool)
    while len(pool) < k:
        pool.append(random.choice(all_names))
    return pool[:k]

def pick_distractors(all_names, correct, k=3):
    pool = [n for n in all_names if n and n.strip().lower() != (correct or '').strip().lower()]
    random.shuffle(pool)
    if len(pool) < k:
        more = [v for v in set(ALIASES.values())
                if v.strip().lower() != (correct or '').strip().lower() and v not in pool]
        random.shuffle(more)
        pool += more
    while len(pool) < k:
        pool.append("N/A")
    return pool[:k]

# --------- GRUPE VIZUALNO SLIČNIH ZASTAVA / GEOGRAFSKI BLISKIH OBRISA ---------
FLAG_GROUPS = {
    # Polumjesec (i zvijezda)
    'crescent':      ['tr','pk','az','uz','tm','dz','ly','tn','my','mv','mr','km','eh'],
    # Nordijski križ
    'nordic_cross':  ['dk','no','se','fi','is','fo'],
    # Crvena-bijela-plava horizontalna trikolor (Francuska, Rusija, Holandija...)
    'rwb_tricolor':  ['fr','nl','lu','ru','rs','hr','sk','si','cz','py'],
    # Zelena-bijela-crvena/narančasta vertikalna (Italija, Meksiko, Irska...)
    'gwr_vertical':  ['it','mx','ie','ci','ng'],
    # Panarapske boje (crna-bijela-crvena-zelena)
    'pan_arab':      ['jo','ps','ae','iq','sy','ye','sd','kw','eg','bh','qa','sa'],
    # Panafričke boje (crvena-žuta-zelena)
    'pan_africa':    ['et','gh','gn','gw','ml','sn','cm','tg','bf','bj','cg','bi'],
    # Union Jack u uglu (Australija, NZ + britanske teritorije)
    'union_jack':    ['au','nz','fj','tv','ck','ai','bm','vg','ky','fk','gi','ms','sh','tc','io'],
    # Plavo-bijele (dominantno)
    'blue_white':    ['gr','il','hn','sv','ni','ar','uy','so','sm'],
    # Crvena-bijela horizontalna (gotovo identične: Poljska, Indonezija, Monako)
    'red_white':     ['pl','id','mc'],
    # Srednja Azija (post-sovjetske, ornamenti i sunce)
    'central_asia':  ['kz','kg','tj','uz','tm'],
    # Andske boje (žuta-plava-crvena: Kolumbija, Venecuela, Ekvador)
    'andean':        ['co','ve','ec','pe','bo'],
    # Karibi (šarene, zvijezde, sunce)
    'caribbean':     ['bb','lc','vc','ag','dm','gd','kn','tt','jm','bs'],
    # Skoro identične (Čad i Rumunija — plava-žuta-crvena vertikalna)
    'chad_romania':  ['td','ro'],
    # Balkanske (crvena-plava-bijela s grbom)
    'balkan':        ['ba','rs','hr','me','mk','al','bg'],
    # Baltičke horizontalne trikolor
    'baltic':        ['ee','lv','lt'],
    # Žuto-plave (Ukraina, Kazahstan, Palau, Bosna...)
    'yellow_blue':   ['ua','se','ba','pw','sv'],
    # Zastave s orlom
    'eagle':         ['al','me','mx','eg','pl','de'],
}

OUTLINE_GROUPS = {
    'ex_yugoslavia':   ['ba','rs','hr','si','me','mk'],
    'central_europe':  ['at','ch','de','cz','sk','hu','pl'],
    'benelux':         ['be','nl','lu'],
    'scandinavia':     ['no','se','fi','dk','is'],
    'baltic_states':   ['ee','lv','lt'],
    'caucasus':        ['ge','am','az'],
    'central_asia':    ['kz','kg','tj','uz','tm'],
    'gulf':            ['sa','ae','kw','bh','qa','om','ye'],
    'north_africa':    ['ma','dz','tn','ly','eg','sd'],
    'west_africa':     ['sn','gm','gw','gn','sl','lr','ci','gh','tg','bj','ng','bf','ml','ne','mr'],
    'east_africa':     ['et','er','dj','so','ke','ug','rw','bi','tz'],
    'southern_africa': ['za','ls','sz','mz','zw','bw','na','zm'],
    'southeast_asia':  ['th','la','kh','vn','mm','my','ph','id'],
    'south_asia':      ['in','pk','bd','lk','np','bt'],
    'central_america': ['mx','gt','bz','hn','sv','ni','cr','pa'],
    'caribbean':       ['cu','jm','ht','do','tt','bb','lc','vc','gd'],
    'south_america':   ['co','ve','gy','sr','br','ec','pe','bo','py','ar','cl','uy'],
    'iberian':         ['es','pt'],
    'italy_balkans':   ['it','al','gr','ba','hr','me','mk'],
}

# Reverse mapping: ISO kod -> lista grupa
_FLAG_CODE_TO_GROUPS = {}
for _grp, _codes in FLAG_GROUPS.items():
    for _c in _codes:
        _FLAG_CODE_TO_GROUPS.setdefault(_c, []).append(_grp)

_OUTLINE_CODE_TO_GROUPS = {}
for _grp, _codes in OUTLINE_GROUPS.items():
    for _c in _codes:
        _OUTLINE_CODE_TO_GROUPS.setdefault(_c, []).append(_grp)

# --------- REGIJE ZA FILTER ---------
COUNTRY_REGIONS = {
    'europa': [
        'ad','al','at','ba','be','bg','by','ch','cy','cz','de','dk',
        'ee','es','fi','fo','fr','gb','ge','gr','hr','hu','ie','is',
        'it','li','lt','lu','lv','mc','md','me','mk','mt','nl','no',
        'pl','pt','ro','rs','ru','se','si','sk','sm','tr','ua','va','xk',
    ],
    'azija': [
        'ae','af','am','az','bh','bn','bt','cn','id','il','in','iq',
        'ir','jo','jp','kg','kh','kp','kr','kw','kz','la','lb','lk',
        'mm','mn','mo','mv','my','np','om','ph','pk','ps','qa','sa',
        'sg','sy','tj','tl','tm','tw','uz','vn','ye',
    ],
    'afrika': [
        'ao','bf','bi','bj','bw','cd','cf','cg','ci','cm','cv','dj',
        'dz','eg','er','et','ga','gh','gm','gn','gq','gw','ke','km',
        'lr','ls','ly','ma','mg','ml','mr','mu','mw','mz','na','ne',
        'ng','rw','sc','sd','sl','sn','so','ss','st','sz','td','tg',
        'tn','tz','ug','za','zm','zw',
    ],
    'amerike': [
        'ag','ar','aw','bb','bo','br','bs','bz','ca','cl','co','cr',
        'cu','dm','do','ec','gd','gt','gy','hn','ht','jm','kn','lc',
        'mx','ni','pa','pe','pr','py','sr','sv','tt','us','uy','vc','ve',
    ],
    'okeanija': [
        'au','ck','fj','fm','ki','mh','nr','nu','nz','pg','pw','sb',
        'to','tv','vu','ws',
    ],
}


def pick_similar_distractors(all_names, correct, country_code, category_id, k=3, allowed_codes=None):
    """
    Bira distraktore vizualno slične ispravnom odgovoru.
    allowed_codes: ako je zadan, svi distraktori moraju biti iz tog skupa ISO kodova.
    Fallback na all_names (koji je već filtriran po regionu od strane pozivača).
    """
    code = (country_code or '').lower().strip()

    if category_id == CATEGORY_FLAGS_ID:
        reverse_map = _FLAG_CODE_TO_GROUPS
        groups_dict = FLAG_GROUPS
    else:
        reverse_map = _OUTLINE_CODE_TO_GROUPS
        groups_dict = OUTLINE_GROUPS

    groups = reverse_map.get(code, [])

    similar_codes = set()
    for grp in groups:
        similar_codes.update(groups_dict[grp])
    similar_codes.discard(code)

    # Ako je aktivan region filter, zadrži samo kodove iz tog regiona
    if allowed_codes:
        similar_codes = similar_codes & allowed_codes

    similar_names = [
        ALIASES[c] for c in similar_codes
        if c in ALIASES and ALIASES[c].strip().lower() != (correct or '').strip().lower()
    ]
    random.shuffle(similar_names)
    distractors = similar_names[:k]

    # Fallback na all_names (već filtriran po regionu)
    if len(distractors) < k:
        pool = [
            n for n in all_names
            if n and n.strip().lower() != (correct or '').strip().lower()
            and n not in distractors
        ]
        random.shuffle(pool)
        distractors += pool[:k - len(distractors)]

    while len(distractors) < k:
        distractors.append("N/A")

    return distractors[:k]

# --------- COUNTRIES.JSON — GRANICE ---------
_BORDERS_COUNTRIES  = []
_CCA3_TO_CCA2       = {}
_CCA3_TO_NAME       = {}
_CCA2_TO_EMOJI      = {}
_CCA2_TO_BORDERS    = {}   # 'ba' -> ['HRV','SRB',...]
_CCA2_TO_CAPITAL    = {}   # 'ba' -> 'Sarajevo'
_CCA2_TO_CURRENCY   = {}   # 'ba' -> 'Konvertibilna marka (KM)'
_CCA2_TO_PHONE      = {}   # 'ba' -> '+387'
_CCA2_TO_AREA       = {}   # 'ba' -> 51209.0  (km²)

_REGION_ENG = {
    'europa':  'Europe',
    'azija':   'Asia',
    'afrika':  'Africa',
    'amerike': 'Americas',
    'okeanija':'Oceania',
}

try:
    _cjson_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'countries.json')
    with open(_cjson_path, 'r', encoding='utf-8') as _fh:
        _raw = json.load(_fh)

    for _c in _raw:
        _cca2  = (_c.get('cca2') or '').lower()
        _cca3  = (_c.get('cca3') or '').upper()
        _emoji = _c.get('flag', '')
        _name  = ALIASES.get(_cca2) or (_c.get('name') or {}).get('common', '')

        if _cca2 and _cca3:
            _CCA3_TO_CCA2[_cca3] = _cca2
        if _cca3 and _name:
            _CCA3_TO_NAME[_cca3] = _name
        if _cca2 and _emoji:
            _CCA2_TO_EMOJI[_cca2] = _emoji

        _borders = _c.get('borders') or []
        if _cca2 and _borders:
            _CCA2_TO_BORDERS[_cca2] = _borders
            _BORDERS_COUNTRIES.append({
                'cca2':    _cca2,
                'cca3':    _cca3,
                'name':    _name,
                'emoji':   _emoji,
                'borders': _borders,
                'region':  _c.get('region', ''),
            })

        # Geo Mix podaci
        _caps = _c.get('capital') or []
        if _caps and _cca2:
            _CCA2_TO_CAPITAL[_cca2] = _caps[0]

        _curs = _c.get('currencies') or {}
        if _curs and _cca2:
            _cc, _cd = next(iter(_curs.items()))
            _cn = (_cd or {}).get('name', _cc)
            _cs = (_cd or {}).get('symbol', '')
            _CCA2_TO_CURRENCY[_cca2] = f"{_cn} ({_cs})" if _cs else _cn

        _idd  = _c.get('idd') or {}
        _root = _idd.get('root', '')
        _suf  = (_idd.get('suffixes') or [''])[0]
        if _root and _cca2:
            _CCA2_TO_PHONE[_cca2] = _root + _suf

        _area = _c.get('area')
        if _area and _area > 0 and _cca2:
            _CCA2_TO_AREA[_cca2] = _area

    app.logger.info("countries.json: %d zemalja s granicama ucitano.", len(_BORDERS_COUNTRIES))
except Exception as _e:
    app.logger.warning("countries.json nije pronadjen ili je nevazeci: %s", _e)

# Obrnuta mapa: capital → country code (za regionalne distraktore)
_CAPITAL_TO_CCA2 = {}
for _cca2, _capital in _CCA2_TO_CAPITAL.items():
    _CAPITAL_TO_CCA2[_capital.lower()] = _cca2

# --------- STRANICE ---------
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/quiz/<int:category_id>')
@login_required
def quiz_page(category_id):
    return render_template('quiz.html', category_id=category_id)

@app.route('/leaderboard')
def leaderboard_page():
    return render_template('leaderboard.html')

@app.route('/api/leaderboard')
def api_leaderboard():
    category = request.args.get('category', 'all').lower()
    limit = int(request.args.get('limit', 100))

    db = get_db()
    params = []
    sql = """
        SELECT 
            player_name,
            category_name,
            score,
            correct_answers,
            total_questions,
            CAST(ROUND(100.0 * correct_answers / NULLIF(total_questions,0)) AS INT) AS accuracy,
            COALESCE(avatar, '🎯') AS avatar,
            completed_at
        FROM leaderboard
    """
    if category != 'all':
        sql += " WHERE LOWER(category_name) = ?"
        params.append(category)

    sql += " ORDER BY score DESC, completed_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()

    result = []
    for r in rows:
        result.append({
            "name": r["player_name"],
            "category": (r["category_name"] or "").lower(),
            "score": r["score"],
            "games": 1,  # ako nemaš kolonu 'games' — računaj da je svaki zapis jedna igra
            "accuracy": r["accuracy"] or 0,
            "avatar": r["avatar"]
        })
    return jsonify(result)

@app.route('/api/submit_score', methods=['POST'])
def api_submit_score():
    data = request.get_json(silent=True) or {}

    # pull & validate
    try:
        category_id     = int(data.get('category_id'))
        score           = int(data.get('score', 0))
        correct_answers = int(data.get('correct_answers', 0))
        total_questions = int(data.get('total_questions', 0))
        time_spent      = int(data.get('time_spent', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid payload'}), 400

    if total_questions <= 0:
        return jsonify({'ok': False, 'error': 'total_questions required'}), 400

    accuracy = data.get('accuracy')
    if accuracy is None:
        accuracy = int(round(100.0 * correct_answers / total_questions))

    # ko je igrač
    player_name = (session.get('user') or {}).get('username', 'Gost')
    user_id     = (session.get('user') or {}).get('id')

    # Mapiraj category_id na filter nazive za leaderboard
    _cat_map = {
        CATEGORY_FLAGS_ID: 'flags',         # 1
        CATEGORY_OUTLINES_ID: 'shapes',    # 2
        3: 'cities',                       # Glavni Gradovi
        6: 'borders',                      # Granice Država
        7: 'population',                   # Broj Stanovnika
        8: 'geomix',                       # Geo Ekspert
        9: 'comparison',                   # Geo Duel
    }
    category_name = _cat_map.get(category_id, f'category_{category_id}')

    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO leaderboard
                (player_name, user_id, category_id, category_name, score,
                 correct_answers, total_questions, accuracy, time_spent, avatar, completed_at)
                VALUES (?,?,?,?,?,?,?,?,?, '🎯', datetime('now'))
            """, (player_name, user_id, category_id, category_name, score,
                  correct_answers, total_questions, accuracy, time_spent))
    except Exception:
        app.logger.exception("Leaderboard insert failed")
        return jsonify({'ok': False, 'error': 'db_error'}), 500

    return jsonify({'ok': True})

@app.route('/multiplayer')
def multiplayer_page():
    return render_template('multiplayer.html')

# --------- ADMIN (ručni unos) ---------
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    conn = get_db()
    categories = conn.execute('SELECT * FROM categories').fetchall()

    if request.method == 'POST':
        # Sigurno parsiranje kategorije
        try:
            category_id = int(request.form.get('category_id') or 0)
        except ValueError:
            return "❌ Nevažeća kategorija.", 400

        file = request.files.get('image')

        # --- Grana: Zastave svijeta ---
        if category_id == CATEGORY_FLAGS_ID:
            # 1) Validacija fajla
            if not (file and allowed_file(file.filename)):
                return "❌ Za zastave moraš dodati sliku (SVG/PNG/JPG).", 400

            filename = secure_filename(file.filename)
            name_no_ext, _ = os.path.splitext(filename)

            # preskoči specijalne/ne-ISO ako su definisani
            skip_tokens = globals().get('SKIP_TOKENS', set())
            token = re.sub(r'[^a-z0-9]+', '', name_no_ext.lower())
            if token in skip_tokens:
                return "❌ Ovaj fajl nije država (preskočeno).", 400

            # 2) Snimi sliku u static/flags
            save_dir = FLAGS_DIR
            os.makedirs(save_dir, exist_ok=True)
            file.save(os.path.join(save_dir, filename))
            image_path = f'flags/{filename}'  # RELATIVNO (bez /static/)

            # Duplikat?
            exists = conn.execute(
                'SELECT 1 FROM questions WHERE category_id=? AND image_path=?',
                (CATEGORY_FLAGS_ID, image_path)
            ).fetchone()
            if exists:
                return "ℹ️ Pitanje za ovu zastavu već postoji.", 200

            # 3) Izračunaj tačan odgovor iz imena fajla
            correct = guess_country_name(name_no_ext)

            # 4) Skupi bazen naziva za distraktore (baza + ALIASES)
            rows = conn.execute(
                'SELECT DISTINCT correct_answer FROM questions WHERE category_id=?',
                (CATEGORY_FLAGS_ID,)
            ).fetchall()
            all_names = [ (r['correct_answer'] or '').strip() for r in rows if r['correct_answer'] ]
            all_names = sorted(set(all_names + list(ALIASES.values())))

            wrongs = build_distractors(all_names, correct, k=3)
            options = [correct] + wrongs
            random.shuffle(options)

            # 5) Upis u bazu
            conn.execute('''
                INSERT INTO questions (category_id, image_path, options, correct_answer)
                VALUES (?, ?, ?, ?)
            ''', (CATEGORY_FLAGS_ID, image_path, json.dumps(options), correct))
            conn.commit()

            return f"✅ Dodano: {correct}"

        # --- Grana: Obrisi država ---
        elif category_id == CATEGORY_OUTLINES_ID:
            # 1) Validacija fajla
            if not (file and allowed_file(file.filename)):
                return "❌ Za obrise moraš dodati sliku (SVG/PNG/JPG).", 400

            filename = secure_filename(file.filename)
            name_no_ext, _ = os.path.splitext(filename)

            # preskoči specijalne/ne-ISO ako su definisani
            skip_tokens = globals().get('SKIP_TOKENS', set())
            token = re.sub(r'[^a-z0-9]+', '', name_no_ext.lower())
            if token in skip_tokens:
                return "❌ Ovaj fajl nije država (preskočeno).", 400

            # 2) Snimi sliku u static/outlines
            save_dir = OUTLINES_DIR
            os.makedirs(save_dir, exist_ok=True)
            file.save(os.path.join(save_dir, filename))
            image_path = f'outlines/{filename}'  # RELATIVNO (bez /static/)

            # Duplikat?
            exists = conn.execute(
                'SELECT 1 FROM questions WHERE category_id=? AND image_path=?',
                (CATEGORY_OUTLINES_ID, image_path)
            ).fetchone()
            if exists:
                return "ℹ️ Pitanje za ovaj obris već postoji.", 200

            # 3) Izračunaj tačan odgovor iz imena fajla
            correct = guess_country_name(name_no_ext)

            # 4) Skupi bazen naziva za distraktore (baza + ALIASES)
            rows = conn.execute(
                'SELECT DISTINCT correct_answer FROM questions WHERE category_id=?',
                (CATEGORY_OUTLINES_ID,)
            ).fetchall()
            all_names = [ (r['correct_answer'] or '').strip() for r in rows if r['correct_answer'] ]
            all_names = sorted(set(all_names + list(ALIASES.values())))

            wrongs = build_distractors(all_names, correct, k=3)
            options = [correct] + wrongs
            random.shuffle(options)

            # 5) Upis u bazu
            conn.execute('''
                INSERT INTO questions (category_id, image_path, options, correct_answer)
                VALUES (?, ?, ?, ?)
            ''', (CATEGORY_OUTLINES_ID, image_path, json.dumps(options), correct))
            conn.commit()

            return f"✅ Dodano: {correct}"

        # --- Grana: OSTALE kategorije (ručni unos) ---
        else:
            correct = (request.form.get('correct_answer') or '').strip()
            wrong1  = (request.form.get('wrong1') or '').strip()
            wrong2  = (request.form.get('wrong2') or '').strip()
            wrong3  = (request.form.get('wrong3') or '').strip()

            if not all([correct,
                         wrong1, wrong2, wrong3]):
                return "❌ Unesi sva 4 odgovora.", 400

            image_path = ""
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                folder_name = 'other'  # ili drugi folder po tvom izboru
                save_dir = os.path.join(app.config['UPLOAD_FOLDER'], folder_name)
                os.makedirs(save_dir, exist_ok=True)
                file.save(os.path.join(save_dir, filename))
                image_path = f'{folder_name}/{filename}'

            options = [correct, wrong1, wrong2, wrong3]
            conn.execute('''
                INSERT INTO questions (category_id, image_path, options, correct_answer)
                VALUES (?, ?, ?, ?)
            ''', (category_id, image_path, json.dumps(options), correct))
            conn.commit()

            return "✅ Pitanje dodano!"

    return render_template('admin.html', categories=categories)

# --------- API ---------
@app.route('/api/categories')
def api_categories():
    conn = get_db()
    cats = conn.execute('SELECT * FROM categories').fetchall()
    return jsonify([dict(c) for c in cats])

@app.route('/api/random_question/<int:category_id>')
def random_question(category_id):
    """
    Vrati nasumično pitanje s GARANCIJOM da je tačan odgovor u opcijama
    i (za kat. 1 i 2) da postoji slika.
    """
    conn = get_db()

    where = 'WHERE category_id=?'
    params = [category_id]

    if category_id in (CATEGORY_FLAGS_ID, CATEGORY_OUTLINES_ID):
        where += ' AND image_path IS NOT NULL AND TRIM(image_path) <> ""'

    region = request.args.get('region', '').lower().strip()
    if region and region in COUNTRY_REGIONS:
        codes = COUNTRY_REGIONS[region]
        like_clauses = ' OR '.join(f"image_path LIKE '%/{c}.%'" for c in codes)
        where += f' AND ({like_clauses})'

    # Izuzmi već viđena pitanja
    exclude_raw = request.args.get('exclude', '').strip()
    exclude_ids = [int(x) for x in exclude_raw.split(',') if x.strip().isdigit()]
    where_excl = where
    params_excl = list(params)
    if exclude_ids:
        placeholders = ','.join('?' * len(exclude_ids))
        where_excl += f' AND id NOT IN ({placeholders})'
        params_excl += exclude_ids

    q = conn.execute(
        f'''SELECT id, category_id, image_path, options, correct_answer
            FROM questions
            {where_excl}
            ORDER BY RANDOM() LIMIT 1''',
        params_excl
    ).fetchone()

    # Fallback ako su sva pitanja iscrpljena
    if not q and exclude_ids:
        q = conn.execute(
            f'''SELECT id, category_id, image_path, options, correct_answer
                FROM questions
                {where}
                ORDER BY RANDOM() LIMIT 1''',
            params
        ).fetchone()

    if not q:
        return jsonify({"error": "Nema pitanja za ovu kategoriju"}), 404

    # ispravan naziv
    correct = (q['correct_answer'] or '').strip()
    if not correct:
        raw = (q['image_path'] or '').split('/')[-1]
        base = os.path.splitext(raw)[0]
        correct = guess_country_name(base)

    # Distraktori — pool ograničen na isti region ako je aktivan filter
    rows = conn.execute(
        'SELECT DISTINCT correct_answer FROM questions WHERE category_id=?',
        (category_id,)
    ).fetchall()
    all_names = [(r['correct_answer'] or '').strip() for r in rows if r['correct_answer']]

    region_codes = set(COUNTRY_REGIONS[region]) if region and region in COUNTRY_REGIONS else None

    if region_codes:
        region_name_set = {ALIASES[c] for c in region_codes if c in ALIASES}
        filtered = [n for n in all_names if n in region_name_set]
        if len(filtered) >= 4:  # dovoljno za 3 distraktora + 1 tačan
            all_names = filtered

    fname = (q['image_path'] or '').split('/')[-1]
    country_code = os.path.splitext(fname)[0].lower()

    wrongs = pick_similar_distractors(all_names, correct, country_code, category_id, k=3,
                                      allowed_codes=region_codes)
    options = [correct] + wrongs
    random.shuffle(options)

    # normalizuj URL slike
    raw_path = (q['image_path'] or '').lstrip('/')
    raw_path = raw_path.replace('\\', '/')

    if raw_path:
        image_url = url_for('static', filename=raw_path)
    else:
        image_url = ""

    return jsonify({
        "id": q['id'],
        "category_id": q['category_id'],
        "image_path": image_url,
        "options": options
    })



@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm  = request.form.get('confirm') or ''

        # validacije
        if len(username) < 3:
            flash('Korisničko ime mora imati najmanje 3 znaka.', 'danger')
            return render_template('register.html')
        if password != confirm or len(password) < 4:
            flash('Lozinke se ne poklapaju ili su prekratke (min 4).', 'danger')
            return render_template('register.html')

        # unos u bazu
        with get_db() as conn:
            exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
            if exists:
                flash('Korisničko ime je zauzeto.', 'danger')
                return render_template('register.html')
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )
        flash('Registracija uspješna! Prijavi se.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        with get_db() as conn:
            u = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not u or not check_password_hash(u['password_hash'], password):
            flash('Pogrešno korisničko ime ili lozinka.', 'danger')
            return render_template('login.html')

        session['user'] = {'id': u['id'], 'username': u['username'], 'is_admin': bool(u['is_admin'])}
        next_url = request.args.get('next') or url_for('home')  # promijeni 'home' u naziv tvoje / rute ako je drugačije
        return redirect(next_url)
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('Odjavljeni ste.', 'info')
    return redirect(url_for('home'))  # promijeni ako ti je naziv rute drugačiji

@app.post('/api/check')
def api_check():
    data = request.get_json(force=True)
    qid = data.get('question_id')
    answer = (data.get('answer') or '').strip()
    time_used = float(data.get('time_used') or 0.0)

    conn = get_db()
    row = conn.execute('SELECT correct_answer FROM questions WHERE id=?', (qid,)).fetchone()
    if not row:
        return jsonify({"error": "Pitanje ne postoji"}), 400

    correct_answer = row['correct_answer'].strip()
    is_correct = correct_answer.lower() == answer.lower()

    base_points = 100 if is_correct else 0
    bonus = max(0, 50 - int(time_used * 5)) if is_correct else 0
    points = base_points + bonus

    return jsonify({
        "correct": is_correct,
        "correct_answer": correct_answer,
        "points": points
    })

@app.post('/admin/cleanup_outlines_no_image')
def cleanup_outlines_no_image():
    conn = get_db()
    cur = conn.execute(
        'DELETE FROM questions WHERE category_id=? AND (image_path IS NULL OR TRIM(image_path)="")',
        (CATEGORY_OUTLINES_ID,)
    )
    conn.commit()
    return jsonify({"status": "ok", "deleted": cur.rowcount})

# --------- ADMIN: privremena DELETE ruta ---------
@app.route('/admin/delete_flags', methods=['POST'])
def delete_flags():
    """Obriši SVA pitanja iz kategorije Zastave svijeta (category_id = 1)."""
    conn = get_db()
    cur = conn.execute("DELETE FROM questions WHERE category_id = ?", (CATEGORY_FLAGS_ID,))
    conn.commit()
    return jsonify({"status": "ok", "deleted": cur.rowcount})

@app.route('/admin/delete_outlines', methods=['POST'])
def delete_outlines():
    """Obriši SVA pitanja iz kategorije Obrisi država (category_id = 2)."""
    conn = get_db()
    cur = conn.execute("DELETE FROM questions WHERE category_id = ?", (CATEGORY_OUTLINES_ID,))
    conn.commit()
    return jsonify({"status": "ok", "deleted": cur.rowcount})

# --------- BULK IMPORT ZASTAVA ---------
@app.route('/admin/bulk_import_flags', methods=['POST'])
def bulk_import_flags():
    """
    Pročita sve fajlove iz static/flags i za svaki doda pitanje u kategoriju 1 (Zastave).
    """
    if not os.path.isdir(FLAGS_DIR):
        return jsonify({"error": f"Nije pronađen folder {FLAGS_DIR}"}), 400

    conn = get_db()
    existing = load_existing_image_paths(conn, CATEGORY_FLAGS_ID)

    # 1) skupi (image_rel, country_name) – ali preskoči ne-ISO/regionalne
    entries = []
    scanned = 0
    skipped_non_iso = 0

    for fname in os.listdir(FLAGS_DIR):
        if not allowed_file(fname):
            continue

        scanned += 1
        name_no_ext = os.path.splitext(fname)[0]
        token = re.sub(r'[^a-z0-9]+', '', name_no_ext.lower())  # "gb-eng" -> "gbeng"

        # dozvoli dvoslovne ISO (fr, ba, de, ...) ILI one koji su eksplicitno u ALIASES (bih, usa, uk, ...)
        if not (len(token) == 2 or token in ALIASES):
            skipped_non_iso += 1
            continue

        country_name = guess_country_name(name_no_ext)
        image_rel = f"flags/{fname}"  # relativno (bez /static/)
        entries.append((image_rel, country_name))

    if not entries:
        return jsonify({"error": "Nije pronađen nijedan validan fajl u static/flags"}), 400

    # 2) pripremi skup svih naziva za distraktore
    all_country_names = sorted(set(n for _, n in entries))

    # 3) insert u bazu
    inserted = 0
    skipped_existing = 0

    for image_rel, country_name in entries:
        if image_rel in existing:
            skipped_existing += 1
            continue

        wrongs = build_distractors(all_country_names, country_name, k=3)
        options = [country_name] + wrongs
        random.shuffle(options)

        conn.execute("""
            INSERT INTO questions (category_id, image_path, options, correct_answer)
            VALUES (?, ?, ?, ?)
        """, (CATEGORY_FLAGS_ID, image_rel, json.dumps(options), country_name))
        inserted += 1

    conn.commit()

    return jsonify({
        "status": "ok",
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_non_iso": skipped_non_iso,
        "total_files_scanned": scanned
    })

# --------- BULK IMPORT OBRISA ---------
@app.route('/admin/bulk_import_outlines', methods=['POST'])
def bulk_import_outlines():
    """
    Pročita sve fajlove iz static/outlines i za svaki doda pitanje u kategoriju 2 (Obrisi država).
    """
    if not os.path.isdir(OUTLINES_DIR):
        return jsonify({"error": f"Nije pronađen folder {OUTLINES_DIR}"}), 400

    conn = get_db()
    existing = load_existing_image_paths(conn, CATEGORY_OUTLINES_ID)

    # 1) skupi (image_rel, country_name) – ali preskoči ne-ISO/regionalne
    entries = []
    scanned = 0
    skipped_non_iso = 0

    for fname in os.listdir(OUTLINES_DIR):
        if not allowed_file(fname):
            continue

        scanned += 1
        name_no_ext = os.path.splitext(fname)[0]
        token = re.sub(r'[^a-z0-9]+', '', name_no_ext.lower())  # "gb-eng" -> "gbeng"

        # dozvoli dvoslovne ISO (fr, ba, de, ...) ILI one koji su eksplicitno u ALIASES (bih, usa, uk, ...)
        if not (len(token) == 2 or token in ALIASES):
            skipped_non_iso += 1
            continue

        country_name = guess_country_name(name_no_ext)
        image_rel = f"outlines/{fname}"  # relativno (bez /static/)
        entries.append((image_rel, country_name))

    if not entries:
        return jsonify({"error": "Nije pronađen nijedan validan fajl u static/outlines"}), 400

    # 2) pripremi skup svih naziva za distraktore
    all_country_names = sorted(set(n for _, n in entries))

    # 3) insert u bazu
    inserted = 0
    skipped_existing = 0

    for image_rel, country_name in entries:
        if image_rel in existing:
            skipped_existing += 1
            continue

        wrongs = build_distractors(all_country_names, country_name, k=3)
        options = [country_name] + wrongs
        random.shuffle(options)

        conn.execute("""
            INSERT INTO questions (category_id, image_path, options, correct_answer)
            VALUES (?, ?, ?, ?)
        """, (CATEGORY_OUTLINES_ID, image_rel, json.dumps(options), country_name))
        inserted += 1

    conn.commit()

    return jsonify({
        "status": "ok",
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_non_iso": skipped_non_iso,
        "total_files_scanned": scanned
    })

@app.route('/admin/flags')
def admin_flags():
    """Pregled i brisanje pitanja za kategoriju Zastave (ID=1)."""
    page = max(1, int(request.args.get('page', 1)))
    per_page = 24
    offset = (page - 1) * per_page
    q = (request.args.get('q') or '').strip().lower()

    conn = get_db()

    # WHERE uslov (po potrebi pretrage)
    base_params = [CATEGORY_FLAGS_ID]
    where = 'WHERE category_id = ?'
    if q:
        where += ' AND (LOWER(correct_answer) LIKE ? OR LOWER(image_path) LIKE ?)'
        like = f'%{q}%'
        base_params += [like, like]

    total = conn.execute(f'SELECT COUNT(*) AS c FROM questions {where}', base_params).fetchone()['c']
    rows = conn.execute(
        f'''SELECT id, image_path, correct_answer
            FROM questions {where}
            ORDER BY correct_answer
            LIMIT ? OFFSET ?''',
        base_params + [per_page, offset]
    ).fetchall()

    # pripremi podatke + url slike
    items = []
    for r in rows:
        raw = (r['image_path'] or '').lstrip('/')
        rel = raw[len('static/'):] if raw.startswith('static/') else raw  # "flags/de.svg"
        image_url = url_for('static', filename=rel) if rel else ""
        items.append({
            "id": r['id'],
            "correct": r['correct_answer'],
            "image_url": image_url,
            "filename": os.path.basename(rel or "")
        })

    pages = max(1, (total + per_page - 1) // per_page)
    return render_template('admin_flags.html',
                           items=items, page=page, pages=pages,
                           total=total, q=q)

@app.route('/admin/outlines')
def admin_outlines():
    """Pregled i brisanje pitanja za kategoriju Obrisi država (ID=2)."""
    page = max(1, int(request.args.get('page', 1)))
    per_page = 24
    offset = (page - 1) * per_page
    q = (request.args.get('q') or '').strip().lower()

    conn = get_db()

    # WHERE uslov (po potrebi pretrage)
    base_params = [CATEGORY_OUTLINES_ID]
    where = 'WHERE category_id = ?'
    if q:
        where += ' AND (LOWER(correct_answer) LIKE ? OR LOWER(image_path) LIKE ?)'
        like = f'%{q}%'
        base_params += [like, like]

    total = conn.execute(f'SELECT COUNT(*) AS c FROM questions {where}', base_params).fetchone()['c']
    rows = conn.execute(
        f'''SELECT id, image_path, correct_answer
            FROM questions {where}
            ORDER BY correct_answer
            LIMIT ? OFFSET ?''',
        base_params + [per_page, offset]
    ).fetchall()

    # pripremi podatke + url slike
    items = []
    for r in rows:
        raw = (r['image_path'] or '').lstrip('/')
        rel = raw[len('static/'):] if raw.startswith('static/') else raw  # "outlines/ba.svg"
        image_url = url_for('static', filename=rel) if rel else ""
        items.append({
            "id": r['id'],
            "correct": r['correct_answer'],
            "image_url": image_url,
            "filename": os.path.basename(rel or "")
        })

    pages = max(1, (total + per_page - 1) // per_page)
    return render_template('admin_outlines.html',
                           items=items, page=page, pages=pages,
                           total=total, q=q)

@app.post('/admin/flags/delete/<int:qid>')
def admin_flags_delete(qid):
    """Obriši pitanje (i opcionalno fajl sa diska)."""
    conn = get_db()
    row = conn.execute(
        'SELECT image_path FROM questions WHERE id=? AND category_id=?',
        (qid, CATEGORY_FLAGS_ID)
    ).fetchone()
    if not row:
        return redirect(url_for('admin_flags'))

    # obriši iz baze
    conn.execute('DELETE FROM questions WHERE id=?', (qid,))
    conn.commit()

    # po želji izbriši i fajl sa diska (ako je čekirano)
    if (request.form.get('delete_file') or '') == '1':
        raw = (row['image_path'] or '').lstrip('/')
        rel = raw[len('static/'):] if raw.startswith('static/') else raw
        if rel:
            abs_path = os.path.join(app.static_folder, os.path.normpath(rel))
            try:
                os.remove(abs_path)
            except FileNotFoundError:
                pass

    return redirect(url_for('admin_flags'))

@app.post('/admin/outlines/delete/<int:qid>')
def admin_outlines_delete(qid):
    """Obriši pitanje (i opcionalno fajl sa diska)."""
    conn = get_db()
    row = conn.execute(
        'SELECT image_path FROM questions WHERE id=? AND category_id=?',
        (qid, CATEGORY_OUTLINES_ID)
    ).fetchone()
    if not row:
        return redirect(url_for('admin_outlines'))

    # obriši iz baze
    conn.execute('DELETE FROM questions WHERE id=?', (qid,))
    conn.commit()

    # po želji izbriši i fajl sa diska (ako je čekirano)
    if (request.form.get('delete_file') or '') == '1':
        raw = (row['image_path'] or '').lstrip('/')
        rel = raw[len('static/'):] if raw.startswith('static/') else raw
        if rel:
            abs_path = os.path.join(app.static_folder, os.path.normpath(rel))
            try:
                os.remove(abs_path)
            except FileNotFoundError:
                pass

    return redirect(url_for('admin_outlines'))


# --------- POPULACIJA (u milionima, procjena 2023) ---------
COUNTRY_POPULATION = {
    # Europa
    'ru':146,'de':83,'fr':67,'gb':67,'it':60,'es':47,'pl':38,'ua':44,
    'ro':19,'nl':17,'be':11,'cz':11,'gr':11,'pt':10,'se':10,'hu':10,
    'at':9,'by':9,'ch':8.6,'bg':7,'rs':7,'dk':5.8,'fi':5.5,'sk':5.5,
    'no':5.4,'ie':5,'hr':4,'ba':3.5,'md':2.6,'al':2.8,'lt':2.8,
    'mk':2.1,'si':2.1,'lv':1.9,'xk':1.8,'ee':1.3,'cy':1.2,
    'me':0.6,'lu':0.65,'mt':0.5,'is':0.37,
    # Azija
    'cn':1412,'in':1393,'id':273,'pk':225,'bd':167,'jp':125,'ph':111,
    'vn':98,'ir':85,'tr':85,'th':70,'mm':55,'kr':52,'af':40,'iq':41,
    'sa':35,'uz':35,'my':33,'ye':30,'kp':26,'np':30,'lk':22,'tw':23,
    'kz':19,'sy':17,'kh':16,'ae':10,'jo':10,'az':10,'tj':10,'il':9,
    'la':7,'kg':7,'sg':6,'lb':5,'om':5,'am':3,'qa':3,'ge':4,
    'mn':3,'bh':2,'tl':1.3,'bt':0.8,'mv':0.5,'bn':0.45,
    # Afrika
    'ng':213,'et':117,'eg':103,'cd':92,'tz':62,'za':60,'ke':54,
    'ug':46,'dz':44,'sd':44,'ma':37,'ao':33,'mz':32,'gh':32,
    'mg':28,'cm':27,'ci':27,'ne':25,'ml':22,'bf':22,'mw':19,
    'zm':18,'so':16,'sn':17,'td':16,'gn':13,'zw':15,'rw':13,
    'bi':12,'bj':12,'ss':11,'tn':12,'sl':8,'tg':8,'ly':7,'lr':5,
    'cf':5,'er':4,'mr':4,'na':2.6,'bw':2.6,'ls':2.1,'gm':2.4,
    'gw':2,'mu':1.3,'dj':1,'sz':1.1,'km':0.87,'cv':0.56,
    # Amerike
    'us':332,'br':215,'mx':130,'co':51,'ar':45,'pe':33,'ve':28,
    'cl':19,'ec':18,'gt':17,'cu':11,'ht':11,'do':11,'bo':12,
    'hn':10,'py':7.4,'sv':6.5,'ni':6.6,'cr':5.2,'pa':4.4,
    'uy':3.5,'jm':3,'ca':38,'tt':1.4,'gy':0.8,'sr':0.6,'bz':0.4,
    # Okeanija
    'au':26,'pg':9,'nz':5,'fj':0.9,'sb':0.7,'vu':0.33,'ws':0.22,
}

# --------- GRANICE KVIZ ---------
@app.route('/quiz/borders')
@login_required
def quiz_borders():
    return render_template('quiz_borders.html')


@app.route('/api/borders_question')
def api_borders_question():
    region    = request.args.get('region', '').lower().strip()
    region_eng = _REGION_ENG.get(region, '')

    pool_all = [c for c in _BORDERS_COUNTRIES
                if not region_eng or c['region'] == region_eng]

    if not pool_all:
        return jsonify({'error': 'Nema zemalja s granicama za ovaj region'}), 404

    exclude = {c.strip().lower() for c in request.args.get('exclude', '').split(',') if c.strip()}
    pool_filtered = [c for c in pool_all if c['cca2'].lower() not in exclude]
    pool = pool_filtered or pool_all  # ako su sve viđene, dozvoli ponavljanje

    for _ in range(15):
        country = random.choice(pool)

        # Razriješi granice u nazive
        real_borders = []
        for b3 in country['borders']:
            b2   = _CCA3_TO_CCA2.get(b3, '')
            name = ALIASES.get(b2) or _CCA3_TO_NAME.get(b3, '')
            if name:
                real_borders.append(name)

        if not real_borders:
            continue

        correct = random.choice(real_borders)
        border_set = {n.lower() for n in real_borders} | {country['name'].lower()}

        def _names_not_in(src):
            return [c['name'] for c in src if c['name'].lower() not in border_set]

        # Korak 1: distraktori iz istog regiona (pool je već filtriran)
        distractors = _names_not_in(pool)

        # Korak 2: dodaj ostale države istog regiona iz COUNTRY_REGIONS
        #          (uključuje i otočne/nemrežne, koje nisu u _BORDERS_COUNTRIES)
        if len(distractors) < 3 and region and region in COUNTRY_REGIONS:
            region_extras = [
                ALIASES[c] for c in COUNTRY_REGIONS[region]
                if c in ALIASES
                and ALIASES[c].lower() not in border_set
                and ALIASES[c] not in distractors
            ]
            random.shuffle(region_extras)
            distractors += region_extras[:3 - len(distractors)]

        # Korak 3: krajnji fallback — bilo koja zemlja
        #          (samo za ekstremno male regione poput Okeanije)
        if len(distractors) < 3:
            fallback = _names_not_in(_BORDERS_COUNTRIES)
            random.shuffle(fallback)
            distractors += fallback[:3 - len(distractors)]

        if len(distractors) < 3:
            continue

        random.shuffle(distractors)
        options = [correct] + distractors[:3]
        random.shuffle(options)

        return jsonify({
            'country':      country['name'],
            'country_code': country['cca2'],
            'emoji':        country.get('emoji', '🌍'),
            'flag_url':     url_for('static', filename=f'flags/{country["cca2"]}.svg'),
            'options':      options,
            'correct':      correct,
            'border_count': len(real_borders),
        })

    return jsonify({'error': 'Nije moguće generisati pitanje za ovaj region'}), 500


# --------- POPULACIJA KVIZ ---------
@app.route('/quiz/population')
@login_required
def quiz_population():
    return render_template('quiz_population.html')


@app.route('/api/population_question')
def api_population_question():
    region = request.args.get('region', '').lower().strip()
    region_codes = set(COUNTRY_REGIONS.get(region, [])) if region and region in COUNTRY_REGIONS else None

    pool_all = [
        (code, pop) for code, pop in COUNTRY_POPULATION.items()
        if not region_codes or code in region_codes
    ]
    if not pool_all:
        return jsonify({'error': 'Nema zemalja za ovaj region'}), 404

    exclude = {c.strip().lower() for c in request.args.get('exclude', '').split(',') if c.strip()}
    pool = [p for p in pool_all if p[0] not in exclude] or pool_all

    code, population = random.choice(pool)
    name  = ALIASES.get(code, code.upper())
    emoji = _CCA2_TO_EMOJI.get(code, '🌍')

    return jsonify({
        'country':      name,
        'country_code': code,
        'emoji':        emoji,
        'flag_url':     url_for('static', filename=f'flags/{code}.svg'),
        'population':   population,
    })


# --------- GEO MIX + DUEL ---------

def _fmt_area(km2):
    if km2 >= 1_000_000:
        return f"{km2/1_000_000:.2f} mil km²"
    return f"{int(km2):,} km²".replace(',', '.')

def _area_tier(km2):
    for t, lim in enumerate([500, 5_000, 50_000, 200_000, 600_000, 2_000_000, 8_000_000]):
        if km2 < lim: return t
    return 7

def _q_capital(code, pool):
    cap = _CCA2_TO_CAPITAL.get(code)
    if not cap: return None
    dist = [_CCA2_TO_CAPITAL[c] for c in pool if c in _CCA2_TO_CAPITAL and _CCA2_TO_CAPITAL[c] != cap]
    if len(dist) < 3:
        dist += [v for v in _CCA2_TO_CAPITAL.values() if v != cap and v not in dist]
    random.shuffle(dist)
    opts = [cap] + dist[:3]; random.shuffle(opts)
    return {'type':'capital','type_icon':'🏙️','type_label':'Glavni grad',
            'question':'Koji je glavni grad ove države?','options':opts,'correct':cap}

def _q_currency(code, pool):
    cur = _CCA2_TO_CURRENCY.get(code)
    if not cur: return None
    dist = list({_CCA2_TO_CURRENCY[c] for c in pool if c in _CCA2_TO_CURRENCY and _CCA2_TO_CURRENCY[c] != cur})
    if len(dist) < 3:
        dist += [v for v in set(_CCA2_TO_CURRENCY.values()) if v != cur and v not in dist]
    random.shuffle(dist)
    opts = [cur] + dist[:3]; random.shuffle(opts)
    return {'type':'currency','type_icon':'💰','type_label':'Valuta',
            'question':'Koja valuta se koristi u ovoj državi?','options':opts,'correct':cur}

def _q_phone(code, pool):
    ph = _CCA2_TO_PHONE.get(code)
    if not ph: return None
    dist = [_CCA2_TO_PHONE[c] for c in pool if c in _CCA2_TO_PHONE and _CCA2_TO_PHONE[c] != ph]
    if len(dist) < 3:
        dist += [v for v in set(_CCA2_TO_PHONE.values()) if v != ph and v not in dist]
    random.shuffle(dist)
    opts = [ph] + dist[:3]; random.shuffle(opts)
    return {'type':'phone','type_icon':'📞','type_label':'Pozivni broj',
            'question':'Koji je telefonski pozivni broj ove države?','options':opts,'correct':ph}

def _q_area(code, pool):
    area = _CCA2_TO_AREA.get(code)
    if not area: return None
    tier = _area_tier(area)
    correct_str = _fmt_area(area)
    tier_pool = [c for c in pool if c in _CCA2_TO_AREA and c != code
                 and abs(_area_tier(_CCA2_TO_AREA[c]) - tier) <= 1]
    if len(tier_pool) < 3:
        tier_pool = [c for c in _CCA2_TO_AREA if c != code
                     and abs(_area_tier(_CCA2_TO_AREA[c]) - tier) <= 1]
    random.shuffle(tier_pool)
    dist = [_fmt_area(_CCA2_TO_AREA[c]) for c in tier_pool if _fmt_area(_CCA2_TO_AREA[c]) != correct_str]
    if len(dist) < 3:
        dist += [_fmt_area(_CCA2_TO_AREA[c]) for c in _CCA2_TO_AREA
                 if c != code and _fmt_area(_CCA2_TO_AREA[c]) not in dist]
    random.shuffle(dist)
    opts = [correct_str] + dist[:3]; random.shuffle(opts)
    return {'type':'area','type_icon':'📐','type_label':'Površina',
            'question':'Kolika je površina ove države?','options':opts,'correct':correct_str}

_GEO_MIX_GENERATORS = [_q_capital, _q_currency, _q_phone, _q_area]


@app.route('/quiz/geomix')
@login_required
def quiz_geomix():
    return render_template('quiz_geomix.html')

@app.route('/quiz/comparison')
@login_required
def quiz_comparison():
    return render_template('quiz_comparison.html')


@app.route('/api/geomix_question')
def api_geomix_question():
    region = request.args.get('region', '').lower().strip()
    region_codes = set(COUNTRY_REGIONS.get(region, [])) if region and region in COUNTRY_REGIONS else None
    pool_all = [c for c in ALIASES if not region_codes or c in region_codes]

    exclude = {c.strip().lower() for c in request.args.get('exclude', '').split(',') if c.strip()}
    pool_filtered = [c for c in pool_all if c.lower() not in exclude]
    pick_pool = pool_filtered or pool_all  # fallback ako su sve iscrpljene

    for _ in range(25):
        code = random.choice(pick_pool)
        gen  = random.choice(_GEO_MIX_GENERATORS)
        q    = gen(code, pool_all)
        if not q:
            continue
        return jsonify({
            'country':      ALIASES.get(code, code.upper()),
            'country_code': code,
            'flag_url':     url_for('static', filename=f'flags/{code}.svg'),
            'emoji':        _CCA2_TO_EMOJI.get(code, '🌍'),
            **q
        })
    return jsonify({'error': 'Nije moguće generisati pitanje'}), 500


@app.route('/api/comparison_question')
def api_comparison_question():
    region = request.args.get('region', '').lower().strip()
    region_codes = set(COUNTRY_REGIONS.get(region, [])) if region and region in COUNTRY_REGIONS else None
    pool = [c for c in ALIASES if not region_codes or c in region_codes]

    # exclude: sortirani parovi "a:b" — već viđene kombinacije
    exclude_pairs = {
        tuple(sorted(p.strip().lower().split(':')))
        for p in request.args.get('exclude', '').split(',')
        if ':' in p
    }

    METRICS = [
        ('population', '👥', 'Koja država ima VIŠE stanovnika?',
         lambda c: COUNTRY_POPULATION.get(c)),
        ('area',       '📐', 'Koja država je VEĆA površinom?',
         lambda c: _CCA2_TO_AREA.get(c)),
        ('borders',    '🤝', 'Koja država graniči s VIŠE zemalja?',
         lambda c: len(_CCA2_TO_BORDERS.get(c, []))),
    ]

    def cdata(c):
        return {'code': c, 'name': ALIASES.get(c, c.upper()),
                'flag_url': url_for('static', filename=f'flags/{c}.svg'),
                'emoji': _CCA2_TO_EMOJI.get(c, '🌍')}

    # 1. faza: poštuj exclude; 2. faza (fallback): dozvoli ponavljanje
    for phase_excludes in (exclude_pairs, set()):
        for _ in range(60):
            mk, mi, mq, getter = random.choice(METRICS)
            eligible = [c for c in pool if getter(c)]
            if len(eligible) < 2:
                continue
            a, b = random.sample(eligible, 2)
            va, vb = getter(a), getter(b)
            if va == vb:
                continue
            pair_key = tuple(sorted([a.lower(), b.lower()]))
            if pair_key in phase_excludes:
                continue
            return jsonify({
                'country_a':    cdata(a),
                'country_b':    cdata(b),
                'metric':       mk,
                'metric_icon':  mi,
                'question':     mq,
                'correct_code': a if va > vb else b,
            })
    return jsonify({'error': 'Nije moguće generisati pitanje'}), 500


# --------- ADMIN USERS ---------
@app.route('/admin/users')
@admin_required
def admin_users():
    conn = get_db()
    users = conn.execute(
        'SELECT id, username, email, is_admin, created_at FROM users ORDER BY created_at DESC'
    ).fetchall()
    stats = {
        'total':  len(users),
        'admins': sum(1 for u in users if u['is_admin']),
    }
    return render_template('admin_users.html', users=users, stats=stats)

@app.post('/admin/users/<int:uid>/toggle_admin')
@admin_required
def admin_toggle_admin(uid):
    me = session['user']['id']
    if uid == me:
        flash('Ne možeš mijenjati vlastite privilegije.', 'warning')
        return redirect(url_for('admin_users'))
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('Korisnik ne postoji.', 'danger')
        return redirect(url_for('admin_users'))
    new_val = 0 if user['is_admin'] else 1
    conn.execute('UPDATE users SET is_admin=? WHERE id=?', (new_val, uid))
    conn.commit()
    action = 'dodijeljen' if new_val else 'uklonjen'
    flash(f'Admin status {action} korisniku {user["username"]}.', 'success')
    return redirect(url_for('admin_users'))

@app.post('/admin/users/<int:uid>/delete')
@admin_required
def admin_delete_user(uid):
    me = session['user']['id']
    if uid == me:
        flash('Ne možeš obrisati vlastiti nalog.', 'warning')
        return redirect(url_for('admin_users'))
    conn = get_db()
    user = conn.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('Korisnik ne postoji.', 'danger')
        return redirect(url_for('admin_users'))
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    conn.commit()
    flash(f'Korisnik {user["username"]} obrisan.', 'success')
    return redirect(url_for('admin_users'))


# --------- MULTIPLAYER SOCKET EVENTS (OPCIONALNO) ---------
@app.route('/api/rooms')
def api_rooms():
    """Lista aktivnih čekajućih soba."""
    result = []
    for room_id, room in ROOMS.items():
        if room['status'] == 'waiting':
            result.append({
                'id': room_id,
                'name': room['name'],
                'mode': room['mode'],
                'category_id': room['category_id'],
                'players': room['players'],
                'max_players': room['max_players'],
                'time_per_q': room['time_per_q'],
                'status': room['status'],
            })
    return jsonify(result)

# --------- MULTIPLAYER SOCKET EVENTS ---------

@socketio.on('create_room')
def on_create_room(data):
    """Stvori novu sobu."""
    room_id = generate_room_id()
    player_name = data.get('player_name', 'Gost')
    user = session.get('user')
    user_id = (user or {}).get('id')

    ROOMS[room_id] = {
        'id': room_id,
        'name': data.get('name', 'Soba'),
        'mode': data.get('mode', 'quick'),
        'category_id': data.get('category_id'),
        'max_players': data.get('max_players', 2),
        'time_per_q': data.get('time_per_q', 60),
        'total_questions': 20,
        'status': 'waiting',
        'host_sid': request.sid,
        'players': {
            request.sid: {
                'sid': request.sid,
                'name': player_name,
                'user_id': user_id,
                'score': 0,
                'answered': False,
                'answer_time': 0,
            }
        },
        'questions': [],
        'current_q_idx': -1,
        'q_start_time': None,
        'current_answers': {},
    }

    join_room(room_id)
    emit('room_state', ROOMS[room_id])
    emit('player_list', ROOMS[room_id]['players'], to=room_id)

@socketio.on('join_room')
def on_join_room(data):
    """Priključi se postojećoj sobi."""
    room_id = data.get('room_id')
    if room_id not in ROOMS:
        emit('error', 'Soba ne postoji')
        return

    room = ROOMS[room_id]
    if room['status'] != 'waiting':
        emit('error', 'Soba je već počela')
        return

    if len(room['players']) >= room['max_players']:
        emit('error', 'Soba je puna')
        return

    player_name = data.get('player_name', 'Gost')
    user = session.get('user')
    user_id = (user or {}).get('id')

    room['players'][request.sid] = {
        'sid': request.sid,
        'name': player_name,
        'user_id': user_id,
        'score': 0,
        'answered': False,
        'answer_time': 0,
    }

    join_room(room_id)
    emit('room_state', room)
    emit('player_list', room['players'], to=room_id)

@socketio.on('start_game')
def on_start_game(data):
    """Host startuje igru."""
    room_id = data.get('room_id')
    if room_id not in ROOMS:
        emit('error', 'Soba ne postoji')
        return

    room = ROOMS[room_id]
    if request.sid != room['host_sid']:
        emit('error', 'Samo host može startati igru')
        return

    if len(room['players']) < 2:
        emit('error', 'Trebaju najmanje 2 igrača')
        return

    room['questions'] = generate_questions_for_room(room['category_id'], room['total_questions'])
    if len(room['questions']) < room['total_questions']:
        emit('error', 'Nema dovoljno pitanja u kategoriji', to=room_id)
        return

    room['status'] = 'playing'
    room['current_q_idx'] = 0
    room['q_start_time'] = time.time()

    emit('game_started', room, to=room_id)
    socketio.start_background_task(question_timer_task, room_id, 0)
    q0 = room['questions'][0]
    emit('new_question', {
        'image_path': q0['image_path'],
        'options': q0.get('options', []),
        'type': q0.get('type', 'multiple_choice'),
        'country': q0.get('country', ''),
    }, to=room_id)

@socketio.on('submit_answer')
def on_submit_answer(data):
    """Igrač podnese odgovor."""
    room_id = data.get('room_id')
    answer = data.get('answer')

    if room_id not in ROOMS:
        emit('error', 'Soba ne postoji')
        return

    room = ROOMS[room_id]
    if room['status'] != 'playing':
        return

    if request.sid not in room['players']:
        return

    if request.sid in room['current_answers']:
        return

    time_used = time.time() - room['q_start_time']
    room['current_answers'][request.sid] = {
        'answer': answer,
        'time': time_used
    }
    room['players'][request.sid]['answered'] = True
    room['players'][request.sid]['answer_time'] = time_used

    emit('answer_ack', to=request.sid)

    if len(room['current_answers']) >= len(room['players']):
        end_question(room_id)

def question_timer_task(room_id, q_idx):
    """Background task: timer za pitanje."""
    if room_id not in ROOMS:
        return
    room = ROOMS[room_id]

    time.sleep(room['time_per_q'])

    # Provjeri da li je pitanje i dalje aktivno
    if room_id in ROOMS and ROOMS[room_id]['current_q_idx'] == q_idx:
        end_question(room_id)

def calculate_population_score(guess_str, real_pop, time_used, time_limit):
    """Score za population pitanje: baziran na preciznosti + time bonus."""
    try:
        guess = float(guess_str)
    except:
        return 0
    if guess <= 0 or real_pop <= 0:
        return 0
    ratio = max(guess / real_pop, real_pop / guess)
    accuracy_score = max(0, round(100 * (1 - (ratio - 1))))
    time_bonus = round(20 * max(0, 1 - time_used / time_limit)) if ratio <= 1.5 else 0
    return accuracy_score + time_bonus

def end_question(room_id):
    """Završi trenutno pitanje i pošalji rezultate."""
    if room_id not in ROOMS:
        return

    room = ROOMS[room_id]
    q_idx = room['current_q_idx']

    if q_idx >= len(room['questions']):
        return

    q = room['questions'][q_idx]
    correct = q['correct']
    is_population = q.get('type') == 'population'

    for sid, answer_data in room['current_answers'].items():
        answer = answer_data['answer'] if isinstance(answer_data, dict) else answer_data
        time_used = room['players'][sid]['answer_time']
        if is_population:
            points = calculate_population_score(answer, float(correct), time_used, room['time_per_q'])
        else:
            is_correct = (answer or '').lower() == correct.lower()
            points = calculate_score(time_used, room['time_per_q'], is_correct)
        room['players'][sid]['score'] += points

    for sid in room['players']:
        answer = None
        points_earned = 0
        if sid in room['current_answers']:
            answer_data = room['current_answers'][sid]
            answer = answer_data['answer'] if isinstance(answer_data, dict) else answer_data
            time_used = room['players'][sid]['answer_time']
            if is_population:
                points_earned = calculate_population_score(answer, float(correct), time_used, room['time_per_q'])
            else:
                is_correct = (answer or '').lower() == correct.lower()
                points_earned = calculate_score(time_used, room['time_per_q'], is_correct)

        socketio.emit('question_result', {
            'correct_answer': correct,
            'my_answer': answer,
            'points_earned': points_earned,
            'room_state': room,
        }, to=sid)

    room['current_q_idx'] += 1
    room['current_answers'] = {}
    for sid in room['players']:
        room['players'][sid]['answered'] = False

    if room['current_q_idx'] >= room['total_questions']:
        room['status'] = 'finished'
        rankings = sorted(
            [{'name': room['players'][sid]['name'], 'score': room['players'][sid]['score']} for sid in room['players']],
            key=lambda x: x['score'],
            reverse=True
        )
        socketio.emit('game_ended', {
            'rankings': rankings,
            'room_state': room,
        }, to=room_id)
    else:
        room['q_start_time'] = time.time()
        q = room['questions'][room['current_q_idx']]
        socketio.emit('new_question', {
            'image_path': q['image_path'],
            'options': q.get('options', []),
            'type': q.get('type', 'multiple_choice'),
            'country': q.get('country', ''),
        }, to=room_id)
        socketio.start_background_task(question_timer_task, room_id, room['current_q_idx'])

@socketio.on('leave_room')
def on_leave_room(data):
    """Igrač napušta sobu."""
    room_id = data.get('room_id')
    if room_id not in ROOMS:
        return

    room = ROOMS[room_id]
    if request.sid not in room['players']:
        return

    del room['players'][request.sid]
    leave_room(room_id)

    if len(room['players']) == 0:
        # Obriši praznu sobu
        del ROOMS[room_id]
    else:
        # Ako je host napustio, pretvori prvog igrača u hosta
        if request.sid == room['host_sid']:
            room['host_sid'] = next(iter(room['players'].keys()))

        # Ako je igra u toku i nema dovoljno igrača, završi je
        if room['status'] == 'playing' and len(room['players']) < 2:
            room['status'] = 'finished'
            socketio.emit('game_ended', {
                'results': [{'name': room['players'][sid]['name'], 'score': room['players'][sid]['score']}
                           for sid in room['players']]
            }, to=room_id)

# --------- RUN ---------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8000, debug=False, allow_unsafe_werkzeug=True)