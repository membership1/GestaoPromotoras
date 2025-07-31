import sqlite3
import os
import math
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, session, url_for, g, send_file, flash
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve

# --- Configuração da Aplicação ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'um_segredo_muito_forte')
app.config['DATABASE'] = 'database.db'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'xls'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# --- Funções de Banco de Dados (SQLite) ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios';")
        if not cursor.fetchone():
            with open('schema.sql', 'r') as f:
                db.executescript(f.read())
            # Dados Iniciais
            master_pass_hash = generate_password_hash('admin')
            cursor.execute("INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo) VALUES (?, ?, ?, ?)",
                           ('master', master_pass_hash, 'master', 'Administrador Master'))
            cursor.execute("INSERT INTO lojas (razao_social, bandeira, cnpj, av_rua, cidade, uf) VALUES (?, ?, ?, ?, ?, ?)",
                           ('Loja A', 'Bandeira A', '11111111000111', 'Rua Exemplo, 123', 'São Paulo', 'SP'))
            
            telefone_ana = '11987654321'
            senha_ana_gerada = f"hub@{telefone_ana}"
            ana_pass_hash = generate_password_hash(senha_ana_gerada)
            cursor.execute("""
                INSERT INTO usuarios (usuario, senha_hash, tipo, loja_id, nome_completo, cpf, telefone, cidade, uf) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (telefone_ana, ana_pass_hash, 'promotora', 1, 'Ana Silva', '12345678900', telefone_ana, 'São Paulo', 'SP'))
            db.commit()

def create_schema_file():
    if not os.path.exists('schema.sql'):
        with open('schema.sql', 'w') as f:
            f.write('''
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
            ''')

# --- ROTAS ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['login_field']
        senha_input = request.form.get('senha', '')
        db = get_db()
        user_db = db.execute("SELECT * FROM usuarios WHERE telefone = ? AND tipo = 'promotora'", (login_input,)).fetchone()
        if user_db:
            senha_a_checar = f"hub@{user_db['telefone']}"
            if check_password_hash(user_db['senha_hash'], senha_a_checar):
                if not user_db['ativo']:
                    flash('Este usuário está inativo.', 'warning')
                    return redirect(url_for('login'))
                session.clear()
                session['user_id'] = user_db['id']
                session['user_name'] = user_db['nome_completo']
                session['user_type'] = user_db['tipo']
                return redirect(url_for('formulario'))
        
        user_db = db.execute("SELECT * FROM usuarios WHERE usuario = ? AND tipo = 'master'", (login_input,)).fetchone()
        if user_db and check_password_hash(user_db['senha_hash'], senha_input):
            session.clear()
            session['user_id'] = user_db['id']
            session['user_name'] = user_db['nome_completo']
            session['user_type'] = user_db['tipo']
            return redirect(url_for('admin_redirect'))

        flash('Login ou senha inválidos.', 'danger')
        return redirect(url_for('login'))
        
    return render_template('login.html', title="Login")

@app.route('/formulario', methods=['GET', 'POST'])
def formulario():
    if 'user_type' not in session or session['user_type'] != 'promotora': return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT u.*, l.razao_social, l.bandeira, l.cnpj, l.av_rua, l.cidade, l.uf FROM usuarios u JOIN lojas l ON u.loja_id = l.id WHERE u.id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        db.execute(
            "INSERT INTO relatorios (usuario_id, loja_id, data, data_hora, venda_percebida, abordagens, brindes1, brindes2, brindes3) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session['user_id'], user['loja_id'], str(datetime.today().date()), datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             request.form['venda_percebida'], request.form['abordagens'], request.form['brindes1'], request.form['brindes2'], request.form['brindes3'])
        )
        db.commit()
        return redirect(url_for('obrigado'))
    return render_template('formulario.html', user=user, title="Relatório Diário")

@app.route('/enviar-nota', methods=['GET', 'POST'])
def enviar_nota():
    if 'user_type' not in session or session['user_type'] != 'promotora': return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT u.*, l.cnpj FROM usuarios u JOIN lojas l ON u.loja_id = l.id WHERE u.id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        nota_file = request.files.get('nota')
        if nota_file and '.' in nota_file.filename and nota_file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]
            cnpj = user['cnpj']
            extensao = nota_file.filename.rsplit('.', 1)[1].lower()
            novo_nome = f"{cnpj}_{timestamp}.{extensao}"
            fn_n = secure_filename(novo_nome)
            nota_file.save(os.path.join(app.config['UPLOAD_FOLDER'], fn_n))
            db.execute(
                "INSERT INTO notas_fiscais (usuario_id, loja_id, nota_img, data_hora) VALUES (?, ?, ?, ?)",
                (session['user_id'], user['loja_id'], fn_n, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
            db.commit()
            flash('Nota fiscal enviada com sucesso!', 'success')
            return redirect(url_for('enviar_nota'))
    notas_enviadas = db.execute("SELECT * FROM notas_fiscais WHERE usuario_id = ? ORDER BY data_hora DESC", (session['user_id'],)).fetchall()
    return render_template('enviar_nota.html', notas_enviadas=notas_enviadas, title="Enviar Nota Fiscal")

@app.route('/checkin', methods=['GET', 'POST'])
def checkin():
    if 'user_type' not in session or session['user_type'] != 'promotora':
        return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        tipo = request.form.get('tipo')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        imagem_file = request.files.get('imagem')
        if not all([tipo, imagem_file]):
            flash('Todos os campos são obrigatórios.', 'warning')
            return redirect(url_for('checkin'))
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        extensao = imagem_file.filename.rsplit('.', 1)[1].lower()
        nome_arquivo = secure_filename(f"{tipo}_{user['id']}_{timestamp}.{extensao}")
        imagem_file.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo))
        db.execute("""
            INSERT INTO checkins (usuario_id, loja_id, tipo, data_hora, latitude, longitude, imagem_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session['user_id'], user['loja_id'], tipo, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
              latitude, longitude, nome_arquivo))
        db.commit()
        flash(f'{tipo.capitalize()} registrado com sucesso!', 'success')
        return redirect(url_for('checkin'))
    registros = db.execute("SELECT * FROM checkins WHERE usuario_id = ? ORDER BY data_hora DESC", (session['user_id'],)).fetchall()
    return render_template('checkin.html', registros=registros, title="Check-in / Checkout")

@app.route('/obrigado')
def obrigado():
    return '<p style="font-family: sans-serif; text-align: center; margin-top: 50px; font-size: 1.2em;">Operação realizada com sucesso!</p>'

@app.route('/admin')
def admin_redirect():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/admin/dashboard')
def dashboard():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    total_promotoras = db.execute("SELECT COUNT(id) FROM usuarios WHERE tipo = 'promotora' AND ativo = 1").fetchone()[0]
    total_lojas = db.execute("SELECT COUNT(id) FROM lojas").fetchone()[0]
    relatorios_hoje = db.execute("SELECT COUNT(id) FROM relatorios WHERE data = ?", (today,)).fetchone()[0]
    checkins_hoje = db.execute("SELECT COUNT(id) FROM checkins WHERE DATE(data_hora) = ?", (today,)).fetchone()[0]
    reports_by_day = db.execute("SELECT DATE(data_hora) as dia, COUNT(id) as total FROM relatorios WHERE DATE(data_hora) >= DATE('now', '-6 days') GROUP BY dia ORDER BY dia ASC").fetchall()
    checkins_by_type = db.execute("SELECT tipo, COUNT(id) as total FROM checkins WHERE DATE(data_hora) = ? GROUP BY tipo", (today,)).fetchall()
    report_labels = [datetime.strptime(r['dia'], '%Y-%m-%d').strftime('%d/%m') for r in reports_by_day]
    report_data = [r['total'] for r in reports_by_day]
    checkin_labels = [r['tipo'].capitalize() for r in checkins_by_type]
    checkin_data = [r['total'] for r in checkins_by_type]
    return render_template('dashboard.html', title="Dashboard", total_promotoras=total_promotoras, total_lojas=total_lojas, relatorios_hoje=relatorios_hoje, checkins_hoje=checkins_hoje, report_labels=report_labels, report_data=report_data, checkin_labels=checkin_labels, checkin_data=checkin_data)

@app.route('/admin/gerenciamento')
def gerenciamento():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    PER_PAGE = 5
    page_lojas = request.args.get('page_lojas', 1, type=int)
    search_lojas = request.args.get('search_lojas', '', type=str)
    query_lojas = "SELECT * FROM lojas WHERE razao_social LIKE ? ORDER BY razao_social LIMIT ? OFFSET ?"
    count_query_lojas = "SELECT COUNT(id) FROM lojas WHERE razao_social LIKE ?"
    offset_lojas = (page_lojas - 1) * PER_PAGE
    params_lojas_search = (f'%{search_lojas}%',)
    total_lojas = db.execute(count_query_lojas, params_lojas_search).fetchone()[0]
    lojas = db.execute(query_lojas, params_lojas_search + (PER_PAGE, offset_lojas)).fetchall()
    total_pages_lojas = math.ceil(total_lojas / PER_PAGE)
    page_promotoras = request.args.get('page_promotoras', 1, type=int)
    search_promotoras = request.args.get('search_promotoras', '', type=str)
    query_promotoras = "SELECT u.id, u.nome_completo, u.telefone, u.ativo, l.razao_social FROM usuarios u LEFT JOIN lojas l ON u.loja_id = l.id WHERE u.tipo = 'promotora' AND u.nome_completo LIKE ? ORDER BY u.nome_completo LIMIT ? OFFSET ?"
    count_query_promotoras = "SELECT COUNT(u.id) FROM usuarios u WHERE u.tipo = 'promotora' AND u.nome_completo LIKE ?"
    offset_promotoras = (page_promotoras - 1) * PER_PAGE
    params_promotoras_search = (f'%{search_promotoras}%',)
    total_promotoras = db.execute(count_query_promotoras, params_promotoras_search).fetchone()[0]
    promotoras = db.execute(query_promotoras, params_promotoras_search + (PER_PAGE, offset_promotoras)).fetchall()
    total_pages_promotoras = math.ceil(total_promotoras / PER_PAGE)
    filtro_promotora_id = request.args.get('filtro_promotora_id', '')
    filtro_loja_id = request.args.get('filtro_loja_id', '')
    filtro_uf = request.args.get('filtro_uf', '')
    filtro_data_inicio = request.args.get('filtro_data_inicio', '')
    filtro_data_fim = request.args.get('filtro_data_fim', '')
    query_notas = "SELECT nf.*, u.nome_completo as usuario_nome, l.razao_social, l.uf FROM notas_fiscais nf JOIN usuarios u ON nf.usuario_id = u.id JOIN lojas l ON nf.loja_id = l.id WHERE 1=1"
    params_notas = []
    if filtro_promotora_id: query_notas += " AND nf.usuario_id = ?"; params_notas.append(filtro_promotora_id)
    if filtro_loja_id: query_notas += " AND nf.loja_id = ?"; params_notas.append(filtro_loja_id)
    if filtro_uf: query_notas += " AND l.uf LIKE ?"; params_notas.append(f'%{filtro_uf}%')
    if filtro_data_inicio: query_notas += " AND DATE(nf.data_hora) >= ?"; params_notas.append(filtro_data_inicio)
    if filtro_data_fim: query_notas += " AND DATE(nf.data_hora) <= ?"; params_notas.append(filtro_data_fim)
    query_notas += " ORDER BY nf.data_hora DESC"
    notas_fiscais = db.execute(query_notas, tuple(params_notas)).fetchall()
    query_relatorios = "SELECT r.*, u.nome_completo as usuario_nome, l.razao_social, (SELECT COUNT(nf.id) FROM notas_fiscais nf WHERE nf.usuario_id = r.usuario_id AND DATE(nf.data_hora) = r.data) as notas_no_dia FROM relatorios r JOIN usuarios u ON r.usuario_id = u.id JOIN lojas l ON r.loja_id = l.id ORDER BY r.data_hora DESC"
    relatorios = db.execute(query_relatorios).fetchall()
    query_checkins = "SELECT c.*, u.nome_completo, l.razao_social FROM checkins c JOIN usuarios u ON c.usuario_id = u.id JOIN lojas l ON c.loja_id = l.id ORDER BY c.data_hora DESC"
    checkins = db.execute(query_checkins).fetchall()
    promotoras_all = db.execute("SELECT id, nome_completo FROM usuarios WHERE tipo = 'promotora' AND ativo = 1 ORDER BY nome_completo").fetchall()
    lojas_all = db.execute("SELECT * FROM lojas ORDER BY razao_social").fetchall()
    return render_template('gerenciamento.html', title="Gerenciamento", lojas=lojas, total_pages_lojas=total_pages_lojas, current_page_lojas=page_lojas, search_lojas=search_lojas, promotoras=promotoras, total_pages_promotoras=total_pages_promotoras, current_page_promotoras=page_promotoras, search_promotoras=search_promotoras, relatorios=relatorios, notas_fiscais=notas_fiscais, lojas_all=lojas_all, promotoras_all=promotoras_all, checkins=checkins, filtros={'promotora_id': filtro_promotora_id, 'loja_id': filtro_loja_id, 'uf': filtro_uf, 'data_inicio': filtro_data_inicio, 'data_fim': filtro_data_fim})

@app.route('/admin/performance', methods=['GET', 'POST'])
def performance():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    data_fim = request.form.get('data_fim', datetime.now().strftime('%Y-%m-%d'))
    data_inicio = request.form.get('data_inicio', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    query = "SELECT l.razao_social, COUNT(r.id) as total_relatorios, SUM(r.venda_percebida) as total_vendas, AVG(r.venda_percebida) as media_vendas, SUM(r.abordagens) as total_abordagens FROM relatorios r JOIN lojas l ON r.loja_id = l.id WHERE r.data BETWEEN ? AND ? GROUP BY l.id, l.razao_social ORDER BY total_vendas DESC"
    ranking_lojas = db.execute(query, (data_inicio, data_fim)).fetchall()
    return render_template('performance.html', title="Relatório de Performance", ranking_lojas=ranking_lojas, data_inicio=data_inicio, data_fim=data_fim)

@app.route('/admin/nota/excluir/<int:id>', methods=['POST'])
def excluir_nota(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    nota = db.execute("SELECT nota_img FROM notas_fiscais WHERE id = ?", (id,)).fetchone()
    if nota and nota['nota_img']:
        db.execute("DELETE FROM notas_fiscais WHERE id = ?", (id,))
        db.commit()
        try:
            caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], nota['nota_img'])
            if os.path.exists(caminho_arquivo): os.remove(caminho_arquivo)
            flash('Nota fiscal excluída com sucesso.', 'success')
        except Exception as e: flash(f'Erro ao excluir o arquivo físico: {e}', 'danger')
    else: flash('Nota fiscal não encontrada.', 'warning')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/promotora/add', methods=['POST'])
def add_promotora():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    telefone = request.form['telefone']
    nome_completo = request.form['nome_completo']
    senha_gerada = f"hub@{telefone}"
    senha_hash = generate_password_hash(senha_gerada)
    usuario_login = telefone 
    db = get_db()
    try:
        db.execute("INSERT INTO usuarios (usuario, senha_hash, tipo, loja_id, nome_completo, cpf, telefone, cidade, uf) VALUES (?, ?, 'promotora', ?, ?, ?, ?, ?, ?)", (usuario_login, senha_hash, request.form['loja_id'], nome_completo, request.form['cpf'], telefone, request.form['cidade'], request.form['uf']))
        db.commit()
        flash(f"Promotora '{nome_completo}' cadastrada! Login: {telefone} | Senha: {senha_gerada}", 'success')
    except sqlite3.IntegrityError as e: flash(f"Erro: Não foi possível cadastrar. Verifique se o CPF ou Telefone já existem.", 'danger')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/promotora/edit/<int:id>', methods=['GET', 'POST'])
def edit_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    if request.method == 'POST':
        db.execute("UPDATE usuarios SET nome_completo = ?, cpf = ?, telefone = ?, cidade = ?, uf = ?, loja_id = ? WHERE id = ?", (request.form['nome_completo'], request.form['cpf'], request.form['telefone'], request.form['cidade'], request.form['uf'], request.form['loja_id'], id))
        db.commit()
        flash("Dados da promotora atualizados com sucesso.", 'success')
        return redirect(url_for('gerenciamento'))
    promotora = db.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    if promotora is None:
        flash(f"Promotora com ID {id} não foi encontrada.", "danger")
        return redirect(url_for('gerenciamento'))
    lojas = db.execute("SELECT * FROM lojas ORDER BY razao_social").fetchall()
    return render_template('edit_promotora.html', promotora=promotora, lojas=lojas, title="Editar Promotora")

@app.route('/admin/promotora/toggle-active/<int:id>', methods=['POST'])
def toggle_active_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT ativo FROM usuarios WHERE id = ?", (id,)).fetchone()
    if user:
        novo_status = 0 if user['ativo'] else 1
        db.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (novo_status, id))
        db.commit()
        flash(f"Status da promotora alterado.", 'info')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/loja/add', methods=['POST'])
def add_loja():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    try:
        db.execute("INSERT INTO lojas (razao_social, bandeira, cnpj, av_rua, cidade, uf) VALUES (?, ?, ?, ?, ?, ?)",(request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], request.form['av_rua'], request.form['cidade'], request.form['uf']))
        db.commit()
    except sqlite3.IntegrityError: pass
    return redirect(url_for('gerenciamento'))

@app.route('/admin/loja/edit/<int:id>', methods=['GET', 'POST'])
def edit_loja(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    if request.method == 'POST':
        db.execute("UPDATE lojas SET razao_social = ?, bandeira = ?, cnpj = ?, av_rua = ?, cidade = ?, uf = ? WHERE id = ?", (request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], request.form['av_rua'], request.form['cidade'], request.form['uf'], id))
        db.commit()
        return redirect(url_for('gerenciamento'))
    loja = db.execute("SELECT * FROM lojas WHERE id = ?", (id,)).fetchone()
    return render_template('edit_loja.html', loja=loja, title="Editar Loja")

@app.route('/admin/lojas/exportar')
def exportar_lojas():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    query = "SELECT razao_social, cnpj, bandeira, av_rua, cidade, uf FROM lojas"
    df = pd.read_sql_query(query, db)
    df.rename(columns={'razao_social': 'RAZAO_SOCIAL','cnpj': 'CNPJ','bandeira': 'BANDEIRA','av_rua': 'ENDERECO','cidade': 'CIDADE','uf': 'UF'}, inplace=True)
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Lojas')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='lojas_export.xlsx')

@app.route('/admin/lojas/importar', methods=['POST'])
def importar_lojas():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    file = request.files.get('planilha_lojas')
    if not file or file.filename == '':
        flash('Nenhum arquivo selecionado', 'danger'); return redirect(url_for('gerenciamento'))
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(file)
            db = get_db()
            cursor = db.cursor()
            df.columns = [col.strip().upper() for col in df.columns]
            for index, row in df.iterrows():
                if pd.isna(row['CNPJ']): continue
                sql = "INSERT INTO lojas (razao_social, cnpj, bandeira, av_rua, cidade, uf) VALUES (:RAZAO_SOCIAL, :CNPJ, :BANDEIRA, :ENDERECO, :CIDADE, :UF) ON CONFLICT(cnpj) DO UPDATE SET razao_social=excluded.razao_social, bandeira=excluded.bandeira, av_rua=excluded.av_rua, cidade=excluded.cidade, uf=excluded.uf;"
                row_dict = row.fillna('').to_dict()
                cursor.execute(sql, row_dict)
            db.commit()
            flash('Planilha de lojas importada com sucesso!', 'success')
        except Exception as e: flash(f'Erro ao processar a planilha: {e}', 'danger')
        return redirect(url_for('gerenciamento'))
    flash('Formato de arquivo inválido. Use .xlsx ou .xls', 'danger')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/promotoras/exportar')
def exportar_promotoras():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    query = "SELECT u.nome_completo, u.cpf, u.telefone, u.cidade, u.uf, l.cnpj FROM usuarios u LEFT JOIN lojas l ON u.loja_id = l.id WHERE u.tipo = 'promotora'"
    df = pd.read_sql_query(query, db)
    df.rename(columns={'nome_completo': 'NOME', 'cpf': 'CPF', 'telefone': 'TELEFONE','cidade': 'CIDADE', 'uf': 'UF', 'cnpj': 'CNPJ_LOJA'}, inplace=True)
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Promotoras')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='promotoras_export.xlsx')

@app.route('/admin/promotoras/importar', methods=['POST'])
def importar_promotoras():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    file = request.files.get('planilha_promotoras')
    if not file or file.filename == '':
        flash('Nenhum arquivo selecionado', 'danger'); return redirect(url_for('gerenciamento'))
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(file, dtype={'TELEFONE': str, 'CPF': str, 'CNPJ_LOJA': str})
            db = get_db()
            cursor = db.cursor()
            df.columns = [col.strip().upper() for col in df.columns]
            linhas_ignoradas = 0
            for index, row in df.iterrows():
                if pd.isna(row['TELEFONE']): continue
                loja = db.execute("SELECT id FROM lojas WHERE cnpj = ?", (str(row['CNPJ_LOJA']),)).fetchone()
                if not loja:
                    linhas_ignoradas += 1; continue
                loja_id = loja['id']
                telefone = str(row['TELEFONE'])
                senha_gerada = f"hub@{telefone}"
                senha_hash = generate_password_hash(senha_gerada)
                sql = "INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo, cpf, telefone, cidade, uf, loja_id) VALUES (:TELEFONE, :senha_hash, 'promotora', :NOME, :CPF, :TELEFONE, :CIDADE, :UF, :loja_id) ON CONFLICT(telefone) DO UPDATE SET nome_completo=excluded.nome_completo, cpf=excluded.cpf, cidade=excluded.cidade, uf=excluded.uf, loja_id=excluded.loja_id;"
                row_dict = row.fillna('').to_dict()
                row_dict.update({'senha_hash': senha_hash, 'loja_id': loja_id})
                cursor.execute(sql, row_dict)
            db.commit()
            flash(f'Planilha de promotoras importada! {linhas_ignoradas} linhas foram ignoradas por CNPJ inválido.', 'success' if linhas_ignoradas == 0 else 'warning')
        except Exception as e: flash(f'Erro ao processar a planilha: {e}', 'danger')
        return redirect(url_for('gerenciamento'))
    flash('Formato de arquivo inválido. Use .xlsx ou .xls', 'danger')
    return redirect(url_for('gerenciamento'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado com sucesso.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    create_schema_file()
    init_db()
    # Para teste local fácil com debug e reload automático
    #app.run(host='127.0.0.1', port=5000, debug=True)
    
    # Para teste local com o servidor de produção (sem debug ou reload)
    # serve(app, host='127.0.0.1', port=5000)
    
    # Usando Waitress para servir a aplicação de forma similar à produção
    serve(app, host='0.0.0.0', port=5000)
