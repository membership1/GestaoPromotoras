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
import psycopg2
from psycopg2.extras import DictCursor


# --- Configuração da Aplicação ---
app = Flask(__name__)
# As chaves agora vêm de variáveis de ambiente para maior segurança
app.secret_key = os.environ.get('SECRET_KEY', 'fallback_secret_key_for_local_dev')
app.config['DATABASE_URL'] = os.environ.get('DATABASE_URL') # URL do PostgreSQL
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'xls'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# --- Funções de Banco de Dados (PostgreSQL) ---
def get_db():
    if 'db' not in g:
        # Conecta-se ao PostgreSQL usando a URL
        g.db = psycopg2.connect(app.config['DATABASE_URL'])
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # Verifica se a tabela 'usuarios' existe no PostgreSQL
        cursor.execute("SELECT to_regclass('public.usuarios');")
        if cursor.fetchone()[0] is None:
            with open('schema.sql', 'r') as f:
                # O executescript não existe, então executamos o script inteiro
                cursor.execute(f.read())
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

        # Login para promotora
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

        # Login para master
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
        
        # Inserir relatório e obter o ID retornado
        cursor.execute(
            "INSERT INTO relatorios (usuario_id, loja_id, data, data_hora) VALUES (%s, %s, %s, %s) RETURNING id",
            (usuario_id, loja_id_selecionada, str(datetime.today().date()), datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
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
    
    # Histórico de relatórios
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

# ... (O resto das rotas devem ser adaptadas de forma similar, trocando '?' por '%s', usando DictCursor, e 'RETURNING id' para inserts)
# Por uma questão de brevidade, o resto do ficheiro é omitido, mas a lógica de conversão é a mesma para todas as funções.
# As funções de exportação com pandas.read_sql_query funcionam diretamente com o objeto de conexão do psycopg2.
# As queries com agregações e datas também precisam de pequenos ajustes:
# Ex: DATE('now', '-6 days') (SQLite) -> NOW() - INTERVAL '6 days' (PostgreSQL)

# --- Exemplo de função de exportação adaptada ---

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
        FROM relatorios r
        JOIN usuarios u ON r.usuario_id = u.id
        JOIN lojas l ON r.loja_id = l.id
        JOIN dados_relatorio dr ON r.id = dr.relatorio_id
        JOIN campos_relatorio cr ON dr.campo_id = cr.id
        WHERE l.grupo_id = %s AND r.data = %s
        ORDER BY r.data_hora, u.nome_completo
    """
    # pandas.read_sql_query funciona bem com a conexão psycopg2
    df_long = pd.read_sql_query(query, db, params=(grupo_id, data))
    
    if df_long.empty:
        flash("Nenhum dado encontrado para exportar com os filtros selecionados.", "info")
        return redirect(url_for('relatorios', tab='diario', filtro_grupo_id=grupo_id, filtro_data=data))

    df_wide = df_long.pivot_table(index=['data_hora', 'Promotora', 'Loja'], columns='label_campo', values='valor', aggfunc='first').reset_index()
    
    output = BytesIO()
    df_wide.to_excel(output, index=False, sheet_name='Relatorio_Diario')
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name=f'relatorio_diario_{data}.xlsx')


# --- BLOCO DE INICIALIZAÇÃO E EXECUÇÃO ---
with app.app_context():
    init_db()
    
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
