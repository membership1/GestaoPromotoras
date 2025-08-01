import sqlite3
import os
import math
import pandas as pd
from io import BytesIO
from flask import Flask, render_template, request, redirect, session, url_for, g, send_file, flash, jsonify
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve
from werkzeug.datastructures import MultiDict

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

def create_schema_file():
    if not os.path.exists('schema.sql'):
        with open('schema.sql', 'w') as f:
            f.write('''
            CREATE TABLE IF NOT EXISTS grupos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS lojas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                razao_social TEXT NOT NULL UNIQUE,
                bandeira TEXT, cnpj TEXT UNIQUE, av_rua TEXT, cidade TEXT, uf TEXT,
                grupo_id INTEGER,
                FOREIGN KEY (grupo_id) REFERENCES grupos(id)
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL, tipo TEXT NOT NULL, loja_id INTEGER,
                nome_completo TEXT, cpf TEXT UNIQUE, telefone TEXT UNIQUE,
                cidade TEXT, uf TEXT, ativo INTEGER DEFAULT 1,
                FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS campos_relatorio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grupo_id INTEGER NOT NULL,
                nome_campo TEXT NOT NULL,
                label_campo TEXT NOT NULL,
                FOREIGN KEY (grupo_id) REFERENCES grupos(id)
            );
            CREATE TABLE IF NOT EXISTS relatorios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
                data TEXT NOT NULL, data_hora TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS dados_relatorio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relatorio_id INTEGER NOT NULL,
                campo_id INTEGER NOT NULL,
                valor TEXT,
                FOREIGN KEY (relatorio_id) REFERENCES relatorios(id),
                FOREIGN KEY (campo_id) REFERENCES campos_relatorio(id)
            );
            CREATE TABLE IF NOT EXISTS notas_fiscais (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
                nota_img TEXT NOT NULL, data_hora TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL, loja_id INTEGER NOT NULL,
                tipo TEXT NOT NULL, data_hora TEXT NOT NULL,
                latitude REAL, longitude REAL, imagem_path TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id), FOREIGN KEY (loja_id) REFERENCES lojas(id)
            );
            ''')

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios';")
        if not cursor.fetchone():
            create_schema_file()
            with open('schema.sql', 'r') as f:
                db.executescript(f.read())
            master_pass_hash = generate_password_hash('admin')
            cursor.execute("INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo) VALUES (?, ?, ?, ?)",
                           ('master', master_pass_hash, 'master', 'Administrador Master'))
            db.commit()

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
    if 'user_type' not in session or session['user_type'] != 'promotora': 
        return redirect(url_for('login'))
    
    db = get_db()
    
    user_query = "SELECT u.*, l.razao_social, l.grupo_id FROM usuarios u JOIN lojas l ON u.loja_id = l.id WHERE u.id = ?"
    user = db.execute(user_query, (session['user_id'],)).fetchone()

    if not user or not user['grupo_id']:
        flash("A sua loja não está associada a nenhum grupo de relatório. Contacte o administrador.", "warning")
        return render_template('formulario.html', user=user, campos=[])

    campos = db.execute("SELECT * FROM campos_relatorio WHERE grupo_id = ? ORDER BY id", (user['grupo_id'],)).fetchall()

    if request.method == 'POST':
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO relatorios (usuario_id, loja_id, data, data_hora) VALUES (?, ?, ?, ?)",
            (session['user_id'], user['loja_id'], str(datetime.today().date()), datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        relatorio_id = cursor.lastrowid

        for campo in campos:
            valor_enviado = request.form.get(f"campo_{campo['id']}")
            if valor_enviado:
                db.execute(
                    "INSERT INTO dados_relatorio (relatorio_id, campo_id, valor) VALUES (?, ?, ?)",
                    (relatorio_id, campo['id'], valor_enviado)
                )
        
        db.commit()
        flash("Relatório enviado com sucesso!", "success")
        return redirect(url_for('obrigado'))

    return render_template('formulario.html', user=user, campos=campos, title="Relatório Diário")

@app.route('/enviar-nota', methods=['GET', 'POST'])
def enviar_nota():
    if 'user_type' not in session or session['user_type'] != 'promotora': return redirect(url_for('login'))
    db = get_db()
    user = db.execute("SELECT u.*, l.cnpj FROM usuarios u JOIN lojas l ON u.loja_id = l.id WHERE u.id = ?", (session['user_id'],)).fetchone()
    if request.method == 'POST':
        nota_file = request.files.get('nota')
        if nota_file and '.' in nota_file.filename and nota_file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]
            cnpj = user['cnpj'] if user and user['cnpj'] else 'sem_cnpj'
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
        flash(f'{tipo.capitalize()} registado com sucesso!', 'success')
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
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    grupos = db.execute("SELECT * FROM grupos ORDER BY nome").fetchall()
    lojas_all = db.execute("SELECT l.*, g.nome as grupo_nome FROM lojas l LEFT JOIN grupos g ON l.grupo_id = g.id ORDER BY l.razao_social").fetchall()
    promotoras = db.execute("SELECT u.*, l.razao_social FROM usuarios u LEFT JOIN lojas l ON u.loja_id = l.id WHERE u.tipo = 'promotora' ORDER BY u.nome_completo").fetchall()

    filtros = {
        'promotora_id': request.args.get('filtro_promotora_id', ''),
        'loja_id': request.args.get('filtro_loja_id', ''),
        'uf': request.args.get('filtro_uf', ''),
        'data_inicio': request.args.get('filtro_data_inicio', ''),
        'data_fim': request.args.get('filtro_data_fim', '')
    }

    return render_template(
        'gerenciamento.html',
        title="Gerenciamento",
        lojas=lojas_all,
        promotoras=promotoras,
        grupos=grupos,
        lojas_all=lojas_all,
        filtros=filtros
    )


@app.route('/admin/grupos')
def gerenciar_grupos():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    grupos = db.execute("SELECT * FROM grupos ORDER BY nome").fetchall()
    return render_template('grupos.html', title="Gerir Grupos", grupos=grupos)

@app.route('/admin/grupo/add', methods=['POST'])
def add_grupo():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    nome_grupo = request.form.get('nome_grupo')
    if nome_grupo:
        db = get_db()
        try:
            db.execute("INSERT INTO grupos (nome) VALUES (?)", (nome_grupo,))
            db.commit()
            flash(f"Grupo '{nome_grupo}' criado com sucesso.", "success")
        except sqlite3.IntegrityError:
            flash(f"O grupo '{nome_grupo}' já existe.", "warning")
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/grupo/delete/<int:id>', methods=['POST'])
def delete_grupo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    db.execute("UPDATE lojas SET grupo_id = NULL WHERE grupo_id = ?", (id,))
    db.execute("DELETE FROM campos_relatorio WHERE grupo_id = ?", (id,))
    db.execute("DELETE FROM grupos WHERE id = ?", (id,))
    db.commit()
    flash("Grupo removido com sucesso.", "success")
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/grupo/<int:id>')
def detalhe_grupo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    grupo = db.execute("SELECT * FROM grupos WHERE id = ?", (id,)).fetchone()
    if not grupo:
        return redirect(url_for('gerenciar_grupos'))
    campos = db.execute("SELECT * FROM campos_relatorio WHERE grupo_id = ? ORDER BY label_campo", (id,)).fetchall()
    return render_template('grupo_detalhe.html', title=f"Grupo {grupo['nome']}", grupo=grupo, campos=campos)

@app.route('/admin/grupo/<int:id>/campo/add', methods=['POST'])
def add_campo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    label_campo = request.form.get('label_campo')
    if label_campo:
        nome_campo = label_campo.lower().replace(" ", "_")
        db = get_db()
        db.execute("INSERT INTO campos_relatorio (grupo_id, nome_campo, label_campo) VALUES (?, ?, ?)",
                   (id, nome_campo, label_campo))
        db.commit()
        flash(f"Campo '{label_campo}' adicionado.", "success")
    return redirect(url_for('detalhe_grupo', id=id))

@app.route('/admin/grupo/campo/delete/<int:campo_id>', methods=['POST'])
def delete_campo(campo_id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    campo = db.execute("SELECT grupo_id FROM campos_relatorio WHERE id = ?", (campo_id,)).fetchone()
    if campo:
        db.execute("DELETE FROM dados_relatorio WHERE campo_id = ?", (campo_id,))
        db.execute("DELETE FROM campos_relatorio WHERE id = ?", (campo_id,))
        db.commit()
        flash("Campo removido.", "success")
        return redirect(url_for('detalhe_grupo', id=campo['grupo_id']))
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/loja/add', methods=['POST'])
def add_loja():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    try:
        db.execute("INSERT INTO lojas (razao_social, bandeira, cnpj, av_rua, cidade, uf, grupo_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], 
                    request.form['av_rua'], request.form['cidade'], request.form['uf'], request.form['grupo_id']))
        db.commit()
    except sqlite3.IntegrityError:
        flash("Uma loja com este nome ou CNPJ já existe.", "warning")
    return redirect(url_for('gerenciamento'))

@app.route('/admin/loja/edit/<int:id>', methods=['GET', 'POST'])
def edit_loja(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    if request.method == 'POST':
        db.execute("UPDATE lojas SET razao_social = ?, bandeira = ?, cnpj = ?, av_rua = ?, cidade = ?, uf = ?, grupo_id = ? WHERE id = ?",
                   (request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], 
                    request.form['av_rua'], request.form['cidade'], request.form['uf'], request.form['grupo_id'], id))
        db.commit()
        return redirect(url_for('gerenciamento'))
    loja = db.execute("SELECT * FROM lojas WHERE id = ?", (id,)).fetchone()
    grupos = db.execute("SELECT * FROM grupos ORDER BY nome").fetchall()
    return render_template('edit_loja.html', loja=loja, grupos=grupos, title="Editar Loja")

@app.route('/admin/lojas/importar', methods=['POST'])
def importar_lojas():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    grupo_id = request.form.get('grupo_id_import')
    file = request.files.get('planilha_lojas')
    if not all([grupo_id, file]):
        flash("É necessário selecionar um grupo e um ficheiro para importar.", "warning")
        return redirect(url_for('gerenciamento'))
    try:
        df = pd.read_excel(file)
        db = get_db()
        cursor = db.cursor()
        df.columns = [col.strip().upper() for col in df.columns]
        for index, row in df.iterrows():
            if pd.isna(row['CNPJ']): continue
            sql = """
                INSERT INTO lojas (razao_social, cnpj, bandeira, av_rua, cidade, uf, grupo_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cnpj) DO UPDATE SET
                    razao_social=excluded.razao_social, bandeira=excluded.bandeira,
                    av_rua=excluded.av_rua, cidade=excluded.cidade, uf=excluded.uf,
                    grupo_id=excluded.grupo_id;
            """
            cursor.execute(sql, (row.get('RAZAO_SOCIAL'), row.get('CNPJ'), row.get('BANDEIRA'), row.get('ENDERECO'), row.get('CIDADE'), row.get('UF'), grupo_id))
        db.commit()
        flash(f"Lojas importadas com sucesso para o grupo selecionado!", 'success')
    except Exception as e:
        flash(f'Erro ao processar a planilha: {e}', 'danger')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/relatorios', methods=['GET', 'POST'])
def relatorios():
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    grupos = db.execute("SELECT * FROM grupos ORDER BY nome").fetchall()

    # -------------------------
    # Filtros Relatórios Diários
    # -------------------------
    filtros_diarios = {
        'grupo_id': request.args.get('filtro_grupo_id', ''),
        'data': request.args.get('filtro_data', datetime.now().strftime('%Y-%m-%d'))
    }

    relatorios_diarios = []
    if filtros_diarios['grupo_id'] and filtros_diarios['data']:
        query_diario = """
            SELECT r.id, r.data_hora, u.nome_completo, l.razao_social
            FROM relatorios r
            JOIN usuarios u ON r.usuario_id = u.id
            JOIN lojas l ON r.loja_id = l.id
            WHERE l.grupo_id = ? AND r.data = ?
            ORDER BY r.data_hora DESC
        """
        reports = db.execute(query_diario, (filtros_diarios['grupo_id'], filtros_diarios['data'])).fetchall()

        for report in reports:
            dados = db.execute("""
                SELECT cr.label_campo, dr.valor
                FROM dados_relatorio dr
                JOIN campos_relatorio cr ON dr.campo_id = cr.id
                WHERE dr.relatorio_id = ?
            """, (report['id'],)).fetchall()
            relatorios_diarios.append({'info': report, 'dados': dados})

    # -------------------------
    # Filtros Relatórios Avançados
    # -------------------------
    if request.method == 'POST':
        filtros_avancados = request.form
    else:
        filtros_avancados = request.args

    # Garantir que seja um MultiDict para .getlist()
    if not hasattr(filtros_avancados, "getlist"):
        filtros_avancados = MultiDict(filtros_avancados)

    # Carregar campos disponíveis
    campos_disponiveis = []
    grupo_id_avancado = filtros_avancados.get('grupo_id')
    if grupo_id_avancado:
        campos_disponiveis = db.execute("""
            SELECT id, label_campo
            FROM campos_relatorio
            WHERE grupo_id = ?
            ORDER BY label_campo
        """, (grupo_id_avancado,)).fetchall()

    # -------------------------
    # Execução Relatório Avançado
    # -------------------------
    resultados_avancados = []
    headers = []
    if request.method == 'POST' and filtros_avancados.getlist('campos'):
        campos_selecionados = filtros_avancados.getlist('campos')
        data_inicio = filtros_avancados.get('data_inicio')
        data_fim = filtros_avancados.get('data_fim')

        campos_info = {str(c['id']): c['label_campo'] for c in campos_disponiveis}

        colunas_select = []
        headers = ['Promotora', 'Loja']
        for campo in campos_selecionados:
            campo_id, tipo_agregacao = campo.split('_')
            nome_coluna = campos_info.get(campo_id, f'Campo {campo_id}')

            if tipo_agregacao == 'total':
                colunas_select.append(
                    f"SUM(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) ELSE 0 END) AS '{nome_coluna}_total'"
                )
                headers.append(f"{nome_coluna} (Total)")
            elif tipo_agregacao == 'media':
                colunas_select.append(
                    f"AVG(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) END) AS '{nome_coluna}_media'"
                )
                headers.append(f"{nome_coluna} (Média)")

        if colunas_select:
            query_dinamica = f"""
                SELECT u.nome_completo, l.razao_social, {', '.join(colunas_select)}
                FROM relatorios r
                JOIN usuarios u ON r.usuario_id = u.id
                JOIN lojas l ON r.loja_id = l.id
                JOIN dados_relatorio dr ON r.id = dr.relatorio_id
                WHERE l.grupo_id = ? AND r.data BETWEEN ? AND ?
                GROUP BY u.id, u.nome_completo, l.id, l.razao_social
                ORDER BY u.nome_completo
            """
            resultados_avancados = db.execute(
                query_dinamica, (grupo_id_avancado, data_inicio, data_fim)
            ).fetchall()

    return render_template(
        'relatorios.html',
        title="Relatórios",
        grupos=grupos,
        relatorios_diarios=relatorios_diarios,
        resultados_avancados=resultados_avancados,
        headers=headers,
        filtros_diarios=filtros_diarios,
        filtros_avancados=filtros_avancados,
        campos_disponiveis=campos_disponiveis
    )


@app.route('/api/grupo/<int:grupo_id>/campos')
def api_get_campos_grupo(grupo_id):
    if 'user_type' not in session or session['user_type'] != 'master':
        return jsonify({'error': 'Não autorizado'}), 403
    db = get_db()
    campos = db.execute("SELECT id, label_campo FROM campos_relatorio WHERE grupo_id = ?", (grupo_id,)).fetchall()
    return jsonify([dict(c) for c in campos])

@app.route('/admin/performance', methods=['GET', 'POST'])
def performance():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    data_fim = request.form.get('data_fim', datetime.now().strftime('%Y-%m-%d'))
    data_inicio = request.form.get('data_inicio', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    
    ranking_lojas = [] 
    
    return render_template('performance.html', title="Relatório de Performance", ranking_lojas=ranking_lojas, data_inicio=data_inicio, data_fim=data_fim)

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
        flash('Nenhum ficheiro selecionado', 'danger'); return redirect(url_for('gerenciamento'))
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
                sql = "INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo, cpf, telefone, cidade, uf, loja_id) VALUES (?, ?, 'promotora', ?, ?, ?, ?, ?, ?) ON CONFLICT(telefone) DO UPDATE SET nome_completo=excluded.nome_completo, cpf=excluded.cpf, cidade=excluded.cidade, uf=excluded.uf, loja_id=excluded.loja_id;"
                row_dict = row.fillna('').to_dict()
                row_dict.update({'senha_hash': senha_hash, 'loja_id': loja_id, 'usuario': telefone})
                cursor.execute(sql, row_dict)
            db.commit()
            flash(f'Planilha de promotoras importada! {linhas_ignoradas} linhas foram ignoradas por CNPJ inválido.', 'success' if linhas_ignoradas == 0 else 'warning')
        except Exception as e: flash(f'Erro ao processar a planilha: {e}', 'danger')
        return redirect(url_for('gerenciamento'))
    flash('Formato de ficheiro inválido. Use .xlsx ou .xls', 'danger')
    return redirect(url_for('gerenciamento'))
    
# --- Cadastro manual de promotora ---
@app.route('/admin/promotora/add', methods=['POST'])
def add_promotora():
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    nome_completo = request.form.get('nome_completo')
    cpf = request.form.get('cpf')
    telefone = request.form.get('telefone')
    cidade = request.form.get('cidade')
    uf = request.form.get('uf')
    loja_id = request.form.get('loja_id')

    if not telefone or not nome_completo:
        flash("Nome e telefone são obrigatórios.", "warning")
        return redirect(url_for('gerenciamento'))

    senha_hash = generate_password_hash(f"hub@{telefone}")

    db = get_db()
    try:
        db.execute("""
            INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo, cpf, telefone, cidade, uf, loja_id)
            VALUES (?, ?, 'promotora', ?, ?, ?, ?, ?, ?)
        """, (telefone, senha_hash, nome_completo, cpf, telefone, cidade, uf, loja_id))
        db.commit()
        flash("Promotora cadastrada com sucesso!", "success")
    except sqlite3.IntegrityError:
        flash("Já existe uma promotora com esse telefone ou CPF.", "danger")

    return redirect(url_for('gerenciamento'))

# --- Edição de promotora ---
@app.route('/admin/promotora/edit/<int:id>', methods=['GET', 'POST'])
def edit_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    if request.method == 'POST':
        nome_completo = request.form.get('nome_completo')
        cpf = request.form.get('cpf')
        telefone = request.form.get('telefone')
        cidade = request.form.get('cidade')
        uf = request.form.get('uf')
        loja_id = request.form.get('loja_id')

        db.execute("""
            UPDATE usuarios
            SET nome_completo=?, cpf=?, telefone=?, cidade=?, uf=?, loja_id=?
            WHERE id=?
        """, (nome_completo, cpf, telefone, cidade, uf, loja_id, id))
        db.commit()

        flash("Promotora atualizada com sucesso!", "success")
        return redirect(url_for('gerenciamento'))

    promotora = db.execute("SELECT * FROM usuarios WHERE id = ?", (id,)).fetchone()
    lojas = db.execute("SELECT * FROM lojas ORDER BY razao_social").fetchall()
    return render_template('edit_promotora.html', promotora=promotora, lojas=lojas)

# --- Ativar/Inativar promotora ---
@app.route('/admin/promotora/toggle/<int:id>', methods=['POST'])
def toggle_active_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    promotora = db.execute("SELECT ativo FROM usuarios WHERE id = ?", (id,)).fetchone()
    if promotora:
        novo_status = 0 if promotora['ativo'] else 1
        db.execute("UPDATE usuarios SET ativo = ? WHERE id = ?", (novo_status, id))
        db.commit()
        flash("Status da promotora atualizado.", "success")

    return redirect(url_for('gerenciamento'))


@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado com sucesso.', 'info')
    return redirect(url_for('login'))

# --- BLOCO DE INICIALIZAÇÃO E EXECUÇÃO ---
with app.app_context():
    init_db()
    
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
