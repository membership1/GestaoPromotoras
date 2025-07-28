
            CREATE TABLE IF NOT EXISTS lojas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, razao_social TEXT NOT NULL UNIQUE,
                bandeira TEXT, cnpj TEXT UNIQUE, av_rua TEXT, cidade TEXT, uf TEXT
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL, tipo TEXT NOT NULL, loja_id INTEGER,
                nome_completo TEXT, cpf TEXT UNIQUE, telefone TEXT UNIQUE,
                cidade TEXT, uf TEXT, ativo INTEGER DEFAULT 1,
                FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS relatorios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
                data TEXT NOT NULL, data_hora TEXT NOT NULL, venda_percebida INTEGER,
                abordagens INTEGER, brindes1 INTEGER, brindes2 INTEGER, brindes3 INTEGER,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS notas_fiscais (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
                nota_img TEXT NOT NULL, data_hora TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                loja_id INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                data_hora TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                imagem_path TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
                FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            