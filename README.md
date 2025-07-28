# Sistema de Relatório de Promotoras v2

Esta é uma versão aprimorada do protótipo inicial, com persistência de dados, segurança de senhas e funcionalidades adicionais.

## Como Executar

1.  **Crie um ambiente virtual** (recomendado):
    ```bash
    python -m venv venv
    ```
    Ative o ambiente:
    * No Windows: `venv\Scripts\activate`
    * No macOS/Linux: `source venv/bin/activate`

2.  **Instale as dependências** a partir do arquivo `requirements.txt`:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Execute a aplicação:**
    ```bash
    python app.py
    ```
    Na primeira vez que executar, o arquivo `database.db` será criado automaticamente.

4.  Abra seu navegador e acesse **http://127.0.0.1:5000**.

## Credenciais Padrão

-   **Master:**
    -   **Usuário:** `master`
    -   **Senha:** `admin`
-   **Promotora (Exemplo):**
    -   **Usuário:** `ana`
    -   **Senha:** `1234`