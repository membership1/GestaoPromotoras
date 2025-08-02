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
import psycopg2
from psycopg2.extras import DictCursor


# --- Configuração da Aplicação ---
app = Flask(__name__)
# As chaves agora vêm de variáveis de ambiente para maior segurança
app.secret_key = os.environ.get('SECRET_KEY', 'fallback_secret_key_for_local_dev')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL') # URL do PostgreSQL do Render
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'xls'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# --- Funções de Banco de Dados (PostgreSQL) ---
def get_db():
    if 'db' not in g:
        # Conecta-se ao PostgreSQL usando a URL da variável de ambiente
        g.db = psycopg2.connect(app.config['DATABASE_URL'])
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    # O schema SQL agora está dentro do código para evitar FileNotFoundError no deploy.
    SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS grupos (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS lojas (
            id SERIAL PRIMARY KEY, 
            razao_social TEXT NOT NULL UNIQUE,
            bandeira TEXT, 
            cnpj TEXT UNIQUE, 
            av_rua TEXT, 
            cidade TEXT, 
            uf TEXT,
            grupo_id INTEGER,
            FOREIGN KEY (grupo_id) REFERENCES grupos(id)
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY, 
            usuario TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL, 
            tipo TEXT NOT NULL,
            nome_completo TEXT, 
            cpf TEXT UNIQUE, 
            telefone TEXT UNIQUE,
            cidade TEXT, 
            uf TEXT, 
            ativo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS promotora_lojas (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL,
            loja_id INTEGER NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
            FOREIGN KEY (loja_id) REFERENCES lojas (id) ON DELETE CASCADE,
            UNIQUE (usuario_id, loja_id)
        );
        CREATE TABLE IF NOT EXISTS campos_relatorio (
            id SERIAL PRIMARY KEY,
            grupo_id INTEGER NOT NULL,
            nome_campo TEXT NOT NULL,
            label_campo TEXT NOT NULL,
            FOREIGN KEY (grupo_id) REFERENCES grupos(id)
        );
        CREATE TABLE IF NOT EXISTS relatorios (
            id SERIAL PRIMARY KEY, 
            usuario_id INTEGER NOT NULL, 
            loja_id INTEGER NOT NULL,
            data DATE NOT NULL, 
            data_hora TIMESTAMP NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id), 
            FOREIGN KEY (loja_id) REFERENCES lojas(id)
        );
        CREATE TABLE IF NOT EXISTS dados_relatorio (
            id SERIAL PRIMARY KEY,
            relatorio_id INTEGER NOT NULL,
            campo_id INTEGER NOT NULL,
            valor TEXT,
            FOREIGN KEY (relatorio_id) REFERENCES relatorios(id),
            FOREIGN KEY (campo_id) REFERENCES campos_relatorio(id)
        );
        CREATE TABLE IF NOT EXISTS notas_fiscais (
            id SERIAL PRIMARY KEY, 
            usuario_id INTEGER NOT NULL, 
            loja_id INTEGER NOT NULL,
            nota_img TEXT NOT NULL, 
            data_hora TIMESTAMP NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id), 
            FOREIGN KEY (loja_id) REFERENCES lojas(id)
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL, 
            loja_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            data_hora TIMESTAMP NOT NULL,
            latitude REAL, 
            longitude REAL, 
            imagem_path TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id), 
            FOREIGN KEY (loja_id) REFERENCES lojas(id)
        );
    """
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # Verifica se a tabela 'usuarios' existe no PostgreSQL
        cursor.execute("SELECT to_regclass('public.usuarios');")
        if cursor.fetchone()[0] is None:
            # Executa o schema diretamente da string, eliminando a dependência do ficheiro .sql
            cursor.execute(SCHEMA_SQL)
            master_pass_hash = generate_password_hash('admin')
            cursor.execute("INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo) VALUES (%s, %s, %s, %s)",
                           ('master', master_pass_hash, 'master', 'Administrador Master'))
            db.commit()
        cursor.close()

# --- ROTAS ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['login_field']
        senha_input = request.form.get('senha', '')
        db = get_db()
        cursor = db.cursor(cursor_factory=DictCursor)

        # Tenta fazer login como promotora primeiro
        cursor.execute("SELECT * FROM usuarios WHERE telefone = %s AND tipo = 'promotora'", (login_input,))
        user_db = cursor.fetchone()
        
        if user_db and check_password_hash(user_db['senha_hash'], senha_input):
            if not user_db['ativo']:
                flash('Este usuário está inativo.', 'warning')
                return redirect(url_for('login'))
            
            session.clear()
            session['user_id'] = user_db['id']
            session['user_name'] = user_db['nome_completo']
            session['user_type'] = user_db['tipo']
            cursor.close()
            return redirect(url_for('formulario'))

        # Se o login da promotora falhar, tenta como master
        cursor.execute("SELECT * FROM usuarios WHERE usuario = %s AND tipo = 'master'", (login_input,))
        user_db = cursor.fetchone()
        if user_db and check_password_hash(user_db['senha_hash'], senha_input):
            session.clear()
            session['user_id'] = user_db['id']
            session['user_name'] = user_db['nome_completo']
            session['user_type'] = user_db['tipo']
            cursor.close()
            return redirect(url_for('admin_redirect'))

        cursor.close()
        flash('Login ou senha inválidos.', 'danger')
        return redirect(url_for('login'))
        
    return render_template('login.html', title="Login")

# --- ÁREA DA PROMOTORA ---

def get_promotora_lojas(usuario_id):
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    query = """
        SELECT l.id, l.razao_social, l.cnpj, l.grupo_id
        FROM lojas l JOIN promotora_lojas pl ON l.id = pl.loja_id
        WHERE pl.usuario_id = %s ORDER BY l.razao_social
    """
    cursor.execute(query, (usuario_id,))
    lojas = cursor.fetchall()
    cursor.close()
    return lojas

@app.route('/formulario', methods=['GET', 'POST'])
def formulario():
    if 'user_type' not in session or session['user_type'] != 'promotora': 
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    usuario_id = session['user_id']
    
    cursor.execute("SELECT * FROM usuarios WHERE id = %s", (usuario_id,))
    user = cursor.fetchone()
    lojas_associadas = get_promotora_lojas(usuario_id)

    if not lojas_associadas:
        flash("Você não está associada a nenhuma loja. Contacte o administrador.", "warning")
        return render_template('formulario.html', user=user, lojas=[], campos=[], historico_relatorios=[])

    if request.method == 'POST':
        loja_id_selecionada = request.form.get('loja_id')
        if not loja_id_selecionada:
            flash("É necessário selecionar uma loja para enviar o relatório.", "danger")
            return redirect(url_for('formulario'))

        cursor.execute("SELECT grupo_id FROM lojas WHERE id = %s", (loja_id_selecionada,))
        loja_selecionada = cursor.fetchone()
        if not loja_selecionada or not loja_selecionada['grupo_id']:
            flash("A loja selecionada não pertence a um grupo com relatório configurado.", "warning")
            return redirect(url_for('formulario'))

        cursor.execute("SELECT * FROM campos_relatorio WHERE grupo_id = %s", (loja_selecionada['grupo_id'],))
        campos = cursor.fetchall()
        
        cursor.execute(
            "INSERT INTO relatorios (usuario_id, loja_id, data, data_hora) VALUES (%s, %s, %s, %s) RETURNING id",
            (usuario_id, loja_id_selecionada, str(datetime.today().date()), datetime.now())
        )
        relatorio_id = cursor.fetchone()['id']

        for campo in campos:
            valor_enviado = request.form.get(f"campo_{campo['id']}")
            if valor_enviado:
                cursor.execute(
                    "INSERT INTO dados_relatorio (relatorio_id, campo_id, valor) VALUES (%s, %s, %s)",
                    (relatorio_id, campo['id'], valor_enviado)
                )
        
        db.commit()
        cursor.close()
        flash("Relatório enviado com sucesso!", "success")
        return redirect(url_for('formulario'))

    # Lógica para GET
    loja_id_para_campos = request.args.get('loja_id')
    if not loja_id_para_campos and lojas_associadas:
        loja_id_para_campos = lojas_associadas[0]['id']

    campos = []
    if loja_id_para_campos:
        cursor.execute("SELECT grupo_id FROM lojas WHERE id = %s", (loja_id_para_campos,))
        loja_atual = cursor.fetchone()
        if loja_atual and loja_atual['grupo_id']:
            cursor.execute("SELECT * FROM campos_relatorio WHERE grupo_id = %s ORDER BY id", (loja_atual['grupo_id'],))
            campos = cursor.fetchall()
    
    historico_query = """
        SELECT r.id, r.data_hora, l.razao_social FROM relatorios r JOIN lojas l ON r.loja_id = l.id
        WHERE r.usuario_id = %s ORDER BY r.data_hora DESC LIMIT 10
    """
    cursor.execute(historico_query, (usuario_id,))
    reports = cursor.fetchall()
    historico_relatorios = []
    for report in reports:
        cursor.execute("SELECT cr.label_campo, dr.valor FROM dados_relatorio dr JOIN campos_relatorio cr ON dr.campo_id = cr.id WHERE dr.relatorio_id = %s", (report['id'],))
        dados = cursor.fetchall()
        historico_relatorios.append({'info': report, 'dados': dados})
    
    cursor.close()
    return render_template('formulario.html', user=user, lojas=lojas_associadas, campos=campos, loja_selecionada_id=int(loja_id_para_campos) if loja_id_para_campos else None, historico_relatorios=historico_relatorios, title="Relatório Diário")


@app.route('/enviar-nota', methods=['GET', 'POST'])
def enviar_nota():
    if 'user_type' not in session or session['user_type'] != 'promotora': return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    usuario_id = session['user_id']
    lojas_associadas = get_promotora_lojas(usuario_id)

    if not lojas_associadas:
        flash("Você não está associada a nenhuma loja para enviar notas.", "warning")
        return render_template('enviar_nota.html', lojas=[], notas_enviadas=[])

    if request.method == 'POST':
        loja_id_selecionada = request.form.get('loja_id')
        nota_file = request.files.get('nota')

        if not loja_id_selecionada or not nota_file:
            flash("É necessário selecionar uma loja e um arquivo.", "danger")
            return redirect(url_for('enviar_nota'))

        if '.' in nota_file.filename and nota_file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
            cursor.execute("SELECT cnpj FROM lojas WHERE id = %s", (loja_id_selecionada,))
            loja_selecionada = cursor.fetchone()
            
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')[:-3]
            cnpj = loja_selecionada['cnpj'] if loja_selecionada and loja_selecionada['cnpj'] else 'sem_cnpj'
            extensao = nota_file.filename.rsplit('.', 1)[1].lower()
            novo_nome = f"{cnpj}_{timestamp}.{extensao}"
            fn_n = secure_filename(novo_nome)
            
            nota_file.save(os.path.join(app.config['UPLOAD_FOLDER'], fn_n))
            cursor.execute(
                "INSERT INTO notas_fiscais (usuario_id, loja_id, nota_img, data_hora) VALUES (%s, %s, %s, %s)",
                (usuario_id, loja_id_selecionada, fn_n, datetime.now())
            )
            db.commit()
            flash('Nota fiscal enviada com sucesso!', 'success')
            return redirect(url_for('enviar_nota'))

    cursor.execute("SELECT nf.*, l.razao_social FROM notas_fiscais nf JOIN lojas l ON nf.loja_id = l.id WHERE nf.usuario_id = %s ORDER BY nf.data_hora DESC", (usuario_id,))
    notas_enviadas = cursor.fetchall()
    cursor.close()
    return render_template('enviar_nota.html', lojas=lojas_associadas, notas_enviadas=notas_enviadas, title="Enviar Nota Fiscal")


@app.route('/checkin', methods=['GET', 'POST'])
def checkin():
    if 'user_type' not in session or session['user_type'] != 'promotora':
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    usuario_id = session['user_id']
    lojas_associadas = get_promotora_lojas(usuario_id)

    if not lojas_associadas:
        flash("Você não está associada a nenhuma loja para fazer check-in.", "warning")
        return render_template('checkin.html', lojas=[], registros=[])

    if request.method == 'POST':
        loja_id_selecionada = request.form.get('loja_id')
        tipo = request.form.get('tipo')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        imagem_file = request.files.get('imagem')

        if not all([loja_id_selecionada, tipo, imagem_file]):
            flash('Todos os campos são obrigatórios.', 'warning')
            return redirect(url_for('checkin'))
            
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        extensao = imagem_file.filename.rsplit('.', 1)[1].lower()
        nome_arquivo = secure_filename(f"{tipo}_{usuario_id}_{timestamp}.{extensao}")
        imagem_file.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo))
        
        cursor.execute("""
            INSERT INTO checkins (usuario_id, loja_id, tipo, data_hora, latitude, longitude, imagem_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (usuario_id, loja_id_selecionada, tipo, datetime.now(), latitude, longitude, nome_arquivo))
        db.commit()
        flash(f'{tipo.capitalize()} registado com sucesso!', 'success')
        return redirect(url_for('checkin'))

    cursor.execute("SELECT c.*, l.razao_social FROM checkins c JOIN lojas l ON c.loja_id = l.id WHERE c.usuario_id = %s ORDER BY c.data_hora DESC", (usuario_id,))
    registros = cursor.fetchall()
    cursor.close()
    return render_template('checkin.html', lojas=lojas_associadas, registros=registros, title="Check-in / Checkout")


@app.route('/obrigado')
def obrigado():
    return '<p style="font-family: sans-serif; text-align: center; margin-top: 50px; font-size: 1.2em;">Operação realizada com sucesso!</p>'

# --- ÁREA DO ADMINISTRADOR ---

@app.route('/admin')
def admin_redirect():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/admin/dashboard')
def dashboard():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    today = datetime.now().strftime('%Y-%m-%d')
    
    cursor.execute("SELECT COUNT(id) as total FROM usuarios WHERE tipo = 'promotora' AND ativo = 1")
    total_promotoras = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(id) as total FROM lojas")
    total_lojas = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(id) as total FROM relatorios WHERE data = %s", (today,))
    relatorios_hoje = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(id) as total FROM checkins WHERE data_hora::date = %s", (today,))
    checkins_hoje = cursor.fetchone()['total']
    
    cursor.execute("SELECT data_hora::date as dia, COUNT(id) as total FROM relatorios WHERE data_hora::date >= NOW() - INTERVAL '6 days' GROUP BY dia ORDER BY dia ASC")
    reports_by_day = cursor.fetchall()
    cursor.execute("SELECT tipo, COUNT(id) as total FROM checkins WHERE data_hora::date = %s GROUP BY tipo", (today,))
    checkins_by_type = cursor.fetchall()
    
    report_labels = [r['dia'].strftime('%d/%m') for r in reports_by_day]
    report_data = [r['total'] for r in reports_by_day]
    checkin_labels = [r['tipo'].capitalize() for r in checkins_by_type]
    checkin_data = [r['total'] for r in checkins_by_type]
    cursor.close()
    return render_template('dashboard.html', title="Dashboard", total_promotoras=total_promotoras, total_lojas=total_lojas, relatorios_hoje=relatorios_hoje, checkins_hoje=checkins_hoje, report_labels=report_labels, report_data=report_data, checkin_labels=checkin_labels, checkin_data=checkin_data)

@app.route('/admin/gerenciamento')
def gerenciamento():
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM grupos ORDER BY nome")
    grupos = cursor.fetchall()
    cursor.execute("SELECT l.*, g.nome as grupo_nome FROM lojas l LEFT JOIN grupos g ON l.grupo_id = g.id ORDER BY l.razao_social")
    lojas_all = cursor.fetchall()
    cursor.execute("""
        SELECT u.*, COUNT(pl.loja_id) as total_lojas
        FROM usuarios u LEFT JOIN promotora_lojas pl ON u.id = pl.usuario_id
        WHERE u.tipo = 'promotora' GROUP BY u.id ORDER BY u.nome_completo
    """)
    promotoras = cursor.fetchall()
    cursor.close()
    return render_template('gerenciamento.html', title="Gerenciamento", lojas=lojas_all, promotoras=promotoras, grupos=grupos, lojas_all=lojas_all)

# --- ROTAS DE GRUPOS E LOJAS ---
@app.route('/admin/grupos')
def gerenciar_grupos():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM grupos ORDER BY nome")
    grupos = cursor.fetchall()
    cursor.close()
    return render_template('grupos.html', title="Gerir Grupos", grupos=grupos)

@app.route('/admin/grupo/add', methods=['POST'])
def add_grupo():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    nome_grupo = request.form.get('nome_grupo')
    if nome_grupo:
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute("INSERT INTO grupos (nome) VALUES (%s)", (nome_grupo,))
            db.commit()
            flash(f"Grupo '{nome_grupo}' criado com sucesso.", "success")
        except psycopg2.IntegrityError:
            db.rollback()
            flash(f"O grupo '{nome_grupo}' já existe.", "warning")
        finally:
            cursor.close()
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/grupo/delete/<int:id>', methods=['POST'])
def delete_grupo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE lojas SET grupo_id = NULL WHERE grupo_id = %s", (id,))
    cursor.execute("DELETE FROM campos_relatorio WHERE grupo_id = %s", (id,))
    cursor.execute("DELETE FROM grupos WHERE id = %s", (id,))
    db.commit()
    cursor.close()
    flash("Grupo removido com sucesso.", "success")
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/grupo/<int:id>')
def detalhe_grupo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM grupos WHERE id = %s", (id,))
    grupo = cursor.fetchone()
    if not grupo:
        return redirect(url_for('gerenciar_grupos'))
    cursor.execute("SELECT * FROM campos_relatorio WHERE grupo_id = %s ORDER BY label_campo", (id,))
    campos = cursor.fetchall()
    cursor.close()
    return render_template('grupo_detalhe.html', title=f"Grupo {grupo['nome']}", grupo=grupo, campos=campos)

@app.route('/admin/grupo/<int:id>/campo/add', methods=['POST'])
def add_campo(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    label_campo = request.form.get('label_campo')
    if label_campo:
        nome_campo = label_campo.lower().replace(" ", "_")
        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO campos_relatorio (grupo_id, nome_campo, label_campo) VALUES (%s, %s, %s)",
                   (id, nome_campo, label_campo))
        db.commit()
        cursor.close()
        flash(f"Campo '{label_campo}' adicionado.", "success")
    return redirect(url_for('detalhe_grupo', id=id))

@app.route('/admin/grupo/campo/delete/<int:campo_id>', methods=['POST'])
def delete_campo(campo_id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT grupo_id FROM campos_relatorio WHERE id = %s", (campo_id,))
    campo = cursor.fetchone()
    if campo:
        cursor_dml = db.cursor()
        cursor_dml.execute("DELETE FROM dados_relatorio WHERE campo_id = %s", (campo_id,))
        cursor_dml.execute("DELETE FROM campos_relatorio WHERE id = %s", (campo_id,))
        db.commit()
        cursor_dml.close()
        flash("Campo removido.", "success")
        return redirect(url_for('detalhe_grupo', id=campo['grupo_id']))
    cursor.close()
    return redirect(url_for('gerenciar_grupos'))

@app.route('/admin/loja/add', methods=['POST'])
def add_loja():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO lojas (razao_social, bandeira, cnpj, av_rua, cidade, uf, grupo_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], 
                    request.form['av_rua'], request.form['cidade'], request.form['uf'], request.form['grupo_id']))
        db.commit()
        flash("Loja adicionada com sucesso!", "success")
    except psycopg2.IntegrityError:
        db.rollback()
        flash("Uma loja com este nome ou CNPJ já existe.", "warning")
    finally:
        cursor.close()
    return redirect(url_for('gerenciamento'))

@app.route('/admin/loja/edit/<int:id>', methods=['GET', 'POST'])
def edit_loja(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    if request.method == 'POST':
        cursor_dml = db.cursor()
        cursor_dml.execute("UPDATE lojas SET razao_social = %s, bandeira = %s, cnpj = %s, av_rua = %s, cidade = %s, uf = %s, grupo_id = %s WHERE id = %s",
                   (request.form['razao_social'], request.form['bandeira'], request.form['cnpj'], 
                    request.form['av_rua'], request.form['cidade'], request.form['uf'], request.form['grupo_id'], id))
        db.commit()
        cursor_dml.close()
        flash("Loja atualizada com sucesso!", "success")
        return redirect(url_for('gerenciamento'))
    
    cursor.execute("SELECT * FROM lojas WHERE id = %s", (id,))
    loja = cursor.fetchone()
    cursor.execute("SELECT * FROM grupos ORDER BY nome")
    grupos = cursor.fetchall()
    cursor.close()
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
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(cnpj) DO UPDATE SET
                    razao_social=excluded.razao_social, bandeira=excluded.bandeira,
                    av_rua=excluded.av_rua, cidade=excluded.cidade, uf=excluded.uf,
                    grupo_id=excluded.grupo_id;
            """
            cursor.execute(sql, (row.get('RAZAO_SOCIAL'), str(row.get('CNPJ')), row.get('BANDEIRA'), row.get('ENDERECO'), row.get('CIDADE'), row.get('UF'), grupo_id))
        db.commit()
        cursor.close()
        flash(f"Lojas importadas com sucesso para o grupo selecionado!", 'success')
    except Exception as e:
        db.rollback()
        flash(f'Erro ao processar a planilha: {e}', 'danger')
    return redirect(url_for('gerenciamento'))

# --- ROTA DE RELATÓRIOS ATUALIZADA ---
@app.route('/admin/relatorios', methods=['GET', 'POST'])
def relatorios():
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    
    cursor.execute("SELECT * FROM grupos ORDER BY nome")
    grupos = cursor.fetchall()
    cursor.execute("SELECT id, nome_completo FROM usuarios WHERE tipo = 'promotora' ORDER BY nome_completo")
    promotoras = cursor.fetchall()
    cursor.execute("SELECT id, razao_social FROM lojas ORDER BY razao_social")
    lojas = cursor.fetchall()

    if request.method == 'POST':
        active_tab = 'avancado'
    else:
        active_tab = request.args.get('tab', 'diario')

    # Filtros Relatórios Diários
    filtros_diarios = {'grupo_id': request.args.get('filtro_grupo_id', ''), 'data': request.args.get('filtro_data', datetime.now().strftime('%Y-%m-%d'))}
    relatorios_diarios = []
    if filtros_diarios['grupo_id'] and filtros_diarios['data']:
        query_diario = "SELECT r.id, r.data_hora, u.nome_completo, l.razao_social FROM relatorios r JOIN usuarios u ON r.usuario_id = u.id JOIN lojas l ON r.loja_id = l.id WHERE l.grupo_id = %s AND r.data = %s ORDER BY r.data_hora DESC"
        cursor.execute(query_diario, (filtros_diarios['grupo_id'], filtros_diarios['data']))
        reports = cursor.fetchall()
        for report in reports:
            cursor.execute("SELECT cr.label_campo, dr.valor FROM dados_relatorio dr JOIN campos_relatorio cr ON dr.campo_id = cr.id WHERE dr.relatorio_id = %s", (report['id'],))
            dados = cursor.fetchall()
            relatorios_diarios.append({'info': report, 'dados': dados})

    # Filtros Relatórios Avançados
    filtros_avancados = MultiDict(request.form) if request.method == 'POST' else MultiDict(request.args)
    campos_disponiveis = []
    grupo_id_avancado = filtros_avancados.get('grupo_id')
    if grupo_id_avancado:
        cursor.execute("SELECT id, label_campo FROM campos_relatorio WHERE grupo_id = %s ORDER BY label_campo", (grupo_id_avancado,))
        campos_disponiveis = cursor.fetchall()

    resultados_avancados = []
    headers = []
    if request.method == 'POST' and filtros_avancados.getlist('campos'):
        campos_selecionados = filtros_avancados.getlist('campos')
        data_inicio = filtros_avancados.get('data_inicio')
        data_fim = filtros_avancados.get('data_fim')
        promotora_id_avancado = filtros_avancados.get('promotora_id')
        loja_id_avancado = filtros_avancados.get('loja_id')
        campos_info = {str(c['id']): c['label_campo'] for c in campos_disponiveis}
        colunas_select = []
        headers = ['Promotora', 'Loja']
        for campo in campos_selecionados:
            campo_id, tipo_agregacao = campo.split('_')
            nome_coluna = campos_info.get(campo_id, f'Campo {campo_id}')
            if tipo_agregacao == 'total':
                colunas_select.append(f"SUM(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) ELSE 0 END) AS \"{nome_coluna}_total\"")
                headers.append(f"{nome_coluna} (Total)")
            elif tipo_agregacao == 'media':
                colunas_select.append(f"AVG(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) END) AS \"{nome_coluna}_media\"")
                headers.append(f"{nome_coluna} (Média)")
        
        if colunas_select:
            query_base = f"SELECT u.nome_completo, l.razao_social, {', '.join(colunas_select)} FROM relatorios r JOIN usuarios u ON r.usuario_id = u.id JOIN lojas l ON r.loja_id = l.id JOIN dados_relatorio dr ON r.id = dr.relatorio_id"
            where_clauses = ["l.grupo_id = %s", "r.data BETWEEN %s AND %s"]
            params = [grupo_id_avancado, data_inicio, data_fim]
            if promotora_id_avancado:
                where_clauses.append("u.id = %s")
                params.append(promotora_id_avancado)
            if loja_id_avancado:
                where_clauses.append("l.id = %s")
                params.append(loja_id_avancado)
            query_dinamica = query_base + " WHERE " + " AND ".join(where_clauses) + " GROUP BY u.id, l.id ORDER BY u.nome_completo"
            cursor.execute(query_dinamica, tuple(params))
            resultados_avancados = cursor.fetchall()

    # LÓGICA PARA HISTÓRICO DE CHECK-INS
    filtros_checkins = {'promotora_id': request.args.get('filtro_checkin_promotora_id', ''), 'loja_id': request.args.get('filtro_checkin_loja_id', ''), 'data_inicio': request.args.get('filtro_checkin_data_inicio', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')), 'data_fim': request.args.get('filtro_checkin_data_fim', datetime.now().strftime('%Y-%m-%d'))}
    query_checkins_base = "SELECT c.data_hora, c.tipo, c.latitude, c.longitude, c.imagem_path, u.nome_completo, l.razao_social FROM checkins c JOIN usuarios u ON c.usuario_id = u.id JOIN lojas l ON c.loja_id = l.id WHERE c.data_hora::date BETWEEN %s AND %s"
    params_checkins = [filtros_checkins['data_inicio'], filtros_checkins['data_fim']]
    if filtros_checkins['promotora_id']:
        query_checkins_base += " AND u.id = %s"
        params_checkins.append(filtros_checkins['promotora_id'])
    if filtros_checkins['loja_id']:
        query_checkins_base += " AND l.id = %s"
        params_checkins.append(filtros_checkins['loja_id'])
    query_checkins_base += " ORDER BY c.data_hora DESC"
    cursor.execute(query_checkins_base, tuple(params_checkins))
    historico_checkins = cursor.fetchall()
    
    cursor.close()
    return render_template('relatorios.html', title="Relatórios", grupos=grupos, promotoras=promotoras, lojas=lojas, relatorios_diarios=relatorios_diarios, resultados_avancados=resultados_avancados, headers=headers, filtros_diarios=filtros_diarios, filtros_avancados=filtros_avancados, campos_disponiveis=campos_disponiveis, historico_checkins=historico_checkins, filtros_checkins=filtros_checkins, active_tab=active_tab)

# --- NOVAS ROTAS DE EXPORTAÇÃO ---
@app.route('/admin/relatorios/exportar/diario')
def exportar_relatorio_diario():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    grupo_id = request.args.get('filtro_grupo_id')
    data = request.args.get('filtro_data')
    if not all([grupo_id, data]):
        flash("Filtros de grupo e data são necessários para exportar.", "warning")
        return redirect(url_for('relatorios'))
    query = """
        SELECT r.data_hora, u.nome_completo as "Promotora", l.razao_social as "Loja", cr.label_campo, dr.valor
        FROM relatorios r JOIN usuarios u ON r.usuario_id = u.id JOIN lojas l ON r.loja_id = l.id
        JOIN dados_relatorio dr ON r.id = dr.relatorio_id JOIN campos_relatorio cr ON dr.campo_id = cr.id
        WHERE l.grupo_id = %s AND r.data = %s ORDER BY r.data_hora, u.nome_completo
    """
    df_long = pd.read_sql_query(query, db, params=(grupo_id, data))
    if df_long.empty:
        flash("Nenhum dado encontrado para exportar com os filtros selecionados.", "info")
        return redirect(url_for('relatorios', tab='diario', filtro_grupo_id=grupo_id, filtro_data=data))
    df_wide = df_long.pivot_table(index=['data_hora', 'Promotora', 'Loja'], columns='label_campo', values='valor', aggfunc='first').reset_index()
    output = BytesIO()
    df_wide.to_excel(output, index=False, sheet_name='Relatorio_Diario')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'relatorio_diario_{data}.xlsx')

@app.route('/admin/relatorios/exportar/avancado')
def exportar_relatorio_avancado():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    filtros = MultiDict(request.args)
    campos_selecionados = filtros.getlist('campos')
    if not campos_selecionados:
        flash("Nenhum campo selecionado para exportar.", "warning")
        return redirect(url_for('relatorios', **request.args))
    grupo_id_avancado = filtros.get('grupo_id')
    data_inicio = filtros.get('data_inicio')
    data_fim = filtros.get('data_fim')
    promotora_id_avancado = filtros.get('promotora_id')
    loja_id_avancado = filtros.get('loja_id')
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT id, label_campo FROM campos_relatorio WHERE grupo_id = %s ORDER BY label_campo", (grupo_id_avancado,))
    campos_disponiveis = cursor.fetchall()
    campos_info = {str(c['id']): c['label_campo'] for c in campos_disponiveis}
    colunas_select = []
    for campo in campos_selecionados:
        campo_id, tipo_agregacao = campo.split('_')
        nome_coluna = campos_info.get(campo_id, f'Campo {campo_id}')
        if tipo_agregacao == 'total':
            colunas_select.append(f"SUM(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) ELSE 0 END) AS \"{nome_coluna} (Total)\"")
        elif tipo_agregacao == 'media':
            colunas_select.append(f"AVG(CASE WHEN dr.campo_id = {campo_id} THEN CAST(dr.valor AS REAL) END) AS \"{nome_coluna} (Média)\"")
    if not colunas_select:
        flash("Erro ao processar campos para exportação.", "danger")
        return redirect(url_for('relatorios', **request.args))
    query_base = f'SELECT u.nome_completo as "Promotora", l.razao_social as "Loja", {", ".join(colunas_select)} FROM relatorios r JOIN usuarios u ON r.usuario_id = u.id JOIN lojas l ON r.loja_id = l.id JOIN dados_relatorio dr ON r.id = dr.relatorio_id'
    where_clauses = ["l.grupo_id = %s", "r.data BETWEEN %s AND %s"]
    params = [grupo_id_avancado, data_inicio, data_fim]
    if promotora_id_avancado:
        where_clauses.append("u.id = %s")
        params.append(promotora_id_avancado)
    if loja_id_avancado:
        where_clauses.append("l.id = %s")
        params.append(loja_id_avancado)
    query_dinamica = query_base + " WHERE " + " AND ".join(where_clauses) + " GROUP BY u.id, l.id ORDER BY u.nome_completo"
    df = pd.read_sql_query(query_dinamica, db, params=tuple(params))
    if df.empty:
        flash("Nenhum dado encontrado para exportar com os filtros selecionados.", "info")
        return redirect(url_for('relatorios', **request.args))
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Relatorio_Avancado')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'relatorio_avancado_{data_inicio}_a_{data_fim}.xlsx')

@app.route('/admin/relatorios/exportar/checkin')
def exportar_historico_checkin():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    filtros = {'promotora_id': request.args.get('filtro_checkin_promotora_id', ''), 'loja_id': request.args.get('filtro_checkin_loja_id', ''), 'data_inicio': request.args.get('filtro_checkin_data_inicio'), 'data_fim': request.args.get('filtro_checkin_data_fim')}
    query_base = "SELECT c.data_hora, u.nome_completo as \"Promotora\", l.razao_social as \"Loja\", c.tipo, c.latitude, c.longitude FROM checkins c JOIN usuarios u ON c.usuario_id = u.id JOIN lojas l ON c.loja_id = l.id WHERE c.data_hora::date BETWEEN %s AND %s"
    params = [filtros['data_inicio'], filtros['data_fim']]
    if filtros['promotora_id']:
        query_base += " AND u.id = %s"
        params.append(filtros['promotora_id'])
    if filtros['loja_id']:
        query_base += " AND l.id = %s"
        params.append(filtros['loja_id'])
    query_base += " ORDER BY c.data_hora DESC"
    df = pd.read_sql_query(query_base, db, params=tuple(params))
    if df.empty:
        flash("Nenhum dado encontrado para exportar com os filtros selecionados.", "info")
        return redirect(url_for('relatorios', **request.args))
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Historico_Checkins')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'historico_checkins_{filtros["data_inicio"]}_a_{filtros["data_fim"]}.xlsx')

@app.route('/api/grupo/<int:grupo_id>/campos')
def api_get_campos_grupo(grupo_id):
    if 'user_type' not in session or session['user_type'] != 'master':
        return jsonify({'error': 'Não autorizado'}), 403
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT id, label_campo FROM campos_relatorio WHERE grupo_id = %s", (grupo_id,))
    campos = cursor.fetchall()
    cursor.close()
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
    query = 'SELECT l.razao_social, l.cnpj, l.bandeira, l.av_rua, l.cidade, l.uf, g.nome as grupo FROM lojas l LEFT JOIN grupos g ON l.grupo_id = g.id'
    df = pd.read_sql_query(query, db)
    df.rename(columns={'razao_social': 'RAZAO_SOCIAL','cnpj': 'CNPJ','bandeira': 'BANDEIRA','av_rua': 'ENDERECO','cidade': 'CIDADE','uf': 'UF', 'grupo': 'GRUPO'}, inplace=True)
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Lojas')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='lojas_export.xlsx')

@app.route('/admin/promotoras/exportar')
def exportar_promotoras():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    query = "SELECT u.nome_completo, u.cpf, u.telefone, u.cidade, u.uf, l.cnpj as cnpj_loja, g.nome as grupo FROM usuarios u JOIN promotora_lojas pl ON u.id = pl.usuario_id JOIN lojas l ON pl.loja_id = l.id LEFT JOIN grupos g ON l.grupo_id = g.id WHERE u.tipo = 'promotora'"
    df = pd.read_sql_query(query, db)
    df.rename(columns={'nome_completo': 'NOME', 'cpf': 'CPF', 'telefone': 'TELEFONE','cidade': 'CIDADE', 'uf': 'UF', 'cnpj_loja': 'CNPJ_LOJA', 'grupo': 'GRUPO'}, inplace=True)
    output = BytesIO()
    df.to_excel(output, index=False, sheet_name='Promotoras')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='promotoras_export.xlsx')

@app.route('/admin/promotoras/importar', methods=['POST'])
def importar_promotoras():
    if 'user_type' not in session or session['user_type'] != 'master':
        return redirect(url_for('login'))
    file = request.files.get('planilha_promotoras')
    if not file or file.filename == '':
        flash('Nenhum ficheiro selecionado', 'danger')
        return redirect(url_for('gerenciamento'))
    try:
        df = None
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file, dtype={'TELEFONE': str, 'CPF': str, 'CNPJ_LOJA': str, 'GRUPO': str})
        elif file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file, dtype={'TELEFONE': str, 'CPF': str, 'CNPJ_LOJA': str, 'GRUPO': str})
        else:
            flash('Formato de ficheiro inválido. Use .xlsx, .xls ou .csv', 'danger')
            return redirect(url_for('gerenciamento'))
        db = get_db()
        cursor = db.cursor()
        df.columns = [str(col).strip().upper() for col in df.columns]
        for telefone, group in df.groupby('TELEFONE'):
            if pd.isna(telefone): continue
            row = group.iloc[0]
            nome_completo = row.get('NOME', '')
            cpf = str(row.get('CPF', ''))
            cidade = row.get('CIDADE', '')
            uf = row.get('UF', '')
            senha_gerada = f"hub@{telefone}"
            senha_hash = generate_password_hash(senha_gerada)
            sql_upsert_user = "INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo, cpf, telefone, cidade, uf) VALUES (%s, %s, 'promotora', %s, %s, %s, %s, %s) ON CONFLICT(telefone) DO UPDATE SET nome_completo=excluded.nome_completo, cpf=excluded.cpf, cidade=excluded.cidade, uf=excluded.uf RETURNING id;"
            cursor.execute(sql_upsert_user, (telefone, senha_hash, nome_completo, cpf, telefone, cidade, uf))
            promotora_id = cursor.fetchone()[0]
            cursor.execute("DELETE FROM promotora_lojas WHERE usuario_id = %s", (promotora_id,))
            for _, sub_row in group.iterrows():
                grupo_nome = sub_row.get('GRUPO')
                cnpj_loja = sub_row.get('CNPJ_LOJA')
                if pd.notna(grupo_nome) and str(grupo_nome).strip() != '':
                    cursor.execute("SELECT id FROM lojas WHERE grupo_id = (SELECT id FROM grupos WHERE nome = %s)", (str(grupo_nome).strip(),))
                    lojas_do_grupo = cursor.fetchall()
                    for loja in lojas_do_grupo:
                        cursor.execute("INSERT INTO promotora_lojas (usuario_id, loja_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (promotora_id, loja[0]))
                elif pd.notna(cnpj_loja):
                    cursor.execute("SELECT id FROM lojas WHERE cnpj = %s", (str(cnpj_loja),))
                    loja = cursor.fetchone()
                    if loja:
                        cursor.execute("INSERT INTO promotora_lojas (usuario_id, loja_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (promotora_id, loja[0]))
        db.commit()
        cursor.close()
        flash('Planilha de promotoras importada com sucesso!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Erro ao processar a planilha: {e}', 'danger')
    return redirect(url_for('gerenciamento'))

@app.route('/admin/promotora/add', methods=['POST'])
def add_promotora():
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    nome_completo = request.form.get('nome_completo')
    cpf = request.form.get('cpf')
    telefone = request.form.get('telefone')
    cidade = request.form.get('cidade')
    uf = request.form.get('uf')
    loja_ids = request.form.getlist('loja_ids')
    if not telefone or not nome_completo:
        flash("Nome e telefone são obrigatórios.", "warning")
        return redirect(url_for('gerenciamento'))
    if not loja_ids:
        flash("Selecione pelo menos uma loja para associar.", "warning")
        return redirect(url_for('gerenciamento'))
    senha_hash = generate_password_hash(f"hub@{telefone}")
    try:
        cursor.execute("INSERT INTO usuarios (usuario, senha_hash, tipo, nome_completo, cpf, telefone, cidade, uf) VALUES (%s, %s, 'promotora', %s, %s, %s, %s, %s) RETURNING id", (telefone, senha_hash, nome_completo, cpf, telefone, cidade, uf))
        promotora_id = cursor.fetchone()[0]
        for loja_id in loja_ids:
            cursor.execute("INSERT INTO promotora_lojas (usuario_id, loja_id) VALUES (%s, %s)", (promotora_id, loja_id))
        db.commit()
        flash("Promotora cadastrada com sucesso!", "success")
    except psycopg2.IntegrityError:
        db.rollback()
        flash("Já existe uma promotora com esse telefone ou CPF.", "danger")
    finally:
        cursor.close()
    return redirect(url_for('gerenciamento'))

@app.route('/admin/promotora/edit/<int:id>', methods=['GET', 'POST'])
def edit_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    if request.method == 'POST':
        cursor_dml = db.cursor()
        nome_completo = request.form.get('nome_completo')
        cpf = request.form.get('cpf')
        telefone = request.form.get('telefone')
        cidade = request.form.get('cidade')
        uf = request.form.get('uf')
        loja_ids_selecionadas = request.form.getlist('loja_ids')
        cursor_dml.execute("UPDATE usuarios SET nome_completo=%s, cpf=%s, telefone=%s, cidade=%s, uf=%s WHERE id=%s", (nome_completo, cpf, telefone, cidade, uf, id))
        cursor_dml.execute("DELETE FROM promotora_lojas WHERE usuario_id = %s", (id,))
        for loja_id in loja_ids_selecionadas:
            cursor_dml.execute("INSERT INTO promotora_lojas (usuario_id, loja_id) VALUES (%s, %s)", (id, loja_id))
        db.commit()
        cursor_dml.close()
        flash("Promotora atualizada com sucesso!", "success")
        return redirect(url_for('gerenciamento'))
    cursor.execute("SELECT * FROM usuarios WHERE id = %s", (id,))
    promotora = cursor.fetchone()
    cursor.execute("SELECT * FROM lojas ORDER BY razao_social")
    lojas = cursor.fetchall()
    cursor.execute("SELECT loja_id FROM promotora_lojas WHERE usuario_id = %s", (id,))
    lojas_associadas_raw = cursor.fetchall()
    lojas_associadas_ids = [item['loja_id'] for item in lojas_associadas_raw]
    cursor.close()
    return render_template('edit_promotora.html', promotora=promotora, lojas=lojas, lojas_associadas_ids=lojas_associadas_ids)

@app.route('/admin/promotora/toggle/<int:id>', methods=['POST'])
def toggle_active_promotora(id):
    if 'user_type' not in session or session['user_type'] != 'master': return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT ativo FROM usuarios WHERE id = %s", (id,))
    promotora = cursor.fetchone()
    if promotora:
        novo_status = 0 if promotora['ativo'] else 1
        cursor_dml = db.cursor()
        cursor_dml.execute("UPDATE usuarios SET ativo = %s WHERE id = %s", (novo_status, id))
        db.commit()
        cursor_dml.close()
        flash("Status da promotora atualizado.", "success")
    cursor.close()
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
