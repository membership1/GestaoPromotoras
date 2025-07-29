import os
from flask import Flask, render_template, request, redirect, session, url_for, g, send_file, flash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from io import BytesIO
from waitress import serve

# --- Configuração da Aplicação ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'um_segredo_muito_forte')
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Configuração do Banco de Dados PostgreSQL ---
# A URL de conexão será lida de uma variável de ambiente no Render
# Para teste local, você pode criar um arquivo .env ou setar a variável manualmente
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///database.db') # Usa SQLite como fallback local
engine = create_engine(DATABASE_URL)

# --- Funções de Banco de Dados (agora com SQLAlchemy) ---
def get_db():
    if 'db_conn' not in g:
        g.db_conn = engine.connect()
    return g.db_conn

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db_conn', None)
    if db is not None:
        db.close()

def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS lojas (
        id SERIAL PRIMARY KEY, razao_social TEXT NOT NULL UNIQUE,
        bandeira TEXT, cnpj TEXT UNIQUE, av_rua TEXT, cidade TEXT, uf TEXT
    );
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY, usuario TEXT NOT NULL UNIQUE,
        senha_hash TEXT NOT NULL, tipo TEXT NOT NULL, loja_id INTEGER,
        nome_completo TEXT, cpf TEXT UNIQUE, telefone TEXT UNIQUE,
        cidade TEXT, uf TEXT, ativo INTEGER DEFAULT 1,
        FOREIGN KEY (loja_id) REFERENCES lojas(id)
    );
    CREATE TABLE IF NOT EXISTS relatorios (
        id SERIAL PRIMARY KEY, usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
        "data" DATE NOT NULL, data_hora TIMESTAMP NOT NULL, venda_percebida INTEGER,
        abordagens INTEGER, brindes1 INTEGER, brindes2 INTEGER, brindes3 INTEGER,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
    );
    CREATE TABLE IF NOT EXISTS notas_fiscais (
        id SERIAL PRIMARY KEY, usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
        nota_img TEXT NOT NULL, data_hora TIMESTAMP NOT NULL,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
    );
    CREATE TABLE IF NOT EXISTS checkins (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
        tipo TEXT NOT NULL, data_hora TIMESTAMP NOT NULL,
        latitude REAL, longitude REAL, imagem_path TEXT NOT NULL,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
    );
    """
    with get_db() as conn:
        conn.execute(text(schema))
        # Verifica se o usuário master já existe
        master_user = conn.execute(text("SELECT id FROM usuarios WHERE tipo = 'master'")).fetchone()
        if not master_user:
            master_pass_hash = generate_password_hash('admin')
            conn.execute(text("""
                INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo) 
                VALUES (:user, :pass, :type, :name)
            """), {'user': 'master', 'pass': master_pass_hash, 'type': 'master', 'name': 'Admin Master'})
        conn.commit()

# --- Rotas (adaptadas para SQLAlchemy) ---
# A maioria das rotas permanece com a lógica similar, mas a execução do SQL muda.
# O código completo e adaptado está no final da resposta para facilitar.
# ...

# --- Bloco de Execução Principal (sem alterações) ---
if __name__ == '__main__':
    # init_db() # Descomente para rodar localmente e criar as tabelas no SQLite
    serve(app, host='127.0.0.1', port=5000)

