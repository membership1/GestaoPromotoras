# Usar uma imagem base oficial do Python
FROM python:3.11-slim

# Instalar ferramentas de compilação do sistema para pacotes complexos
# Isso previne erros caso alguma dependência precise compilar algo
RUN apt-get update && apt-get install -y build-essential gcc

# Definir o diretório de trabalho dentro do contêiner
WORKDIR /app

# Copiar seu arquivo de requisitos e instalar os pacotes Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código da sua aplicação para o contêiner
COPY . .

# O comando para executar sua aplicação usando Waitress (o Render define a $PORT)
CMD waitress-serve --host=0.0.0.0 --port=${PORT} app:app