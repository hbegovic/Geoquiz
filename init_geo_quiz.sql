-- Kreiranje tabela
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS questions;

CREATE TABLE categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    color TEXT
);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    options TEXT NOT NULL, -- JSON format: ["A","B","C","D"]
    correct_answer TEXT NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

-- Ubacivanje kategorija
INSERT INTO categories (name, description, icon, color) VALUES
('Pogodi zastavu', 'Prepoznaj državu po njenoj zastavi', '🇺🇳', '#3498db'),
('Pogodi obris države', 'Prepoznaj državu po njenom obrisu', '🗺️', '#2ecc71');

-- Primjer pitanja za zastave
INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES
(1, '/static/flags/de.png', '["Njemačka","Belgija","Austrija","Nizozemska"]', 'Njemačka'),
(1, '/static/flags/fr.png', '["Francuska","Italija","Rumunija","Nizozemska"]', 'Francuska');

-- Primjer pitanja za obrise država
INSERT INTO questions (category_id, image_path, options, correct_answer) VALUES
(2, '/static/outlines/ba.png', '["Bosna i Hercegovina","Srbija","Hrvatska","Crna Gora"]', 'Bosna i Hercegovina'),
(2, '/static/outlines/hr.png', '["Hrvatska","Slovenija","Srbija","Mađarska"]', 'Hrvatska');
