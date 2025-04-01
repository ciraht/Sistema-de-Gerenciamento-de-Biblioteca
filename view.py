import os
import jwt
import threading
import datetime
from flask import jsonify, request, send_file, send_from_directory
import config
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from fpdf import FPDF

senha_secreta = app.config['SECRET_KEY']

PERIODO_EMPRESTIMO = datetime.timedelta(weeks=2)


def devolucao():
    """Retorna a data de devolução do livro, adicionando o período de empréstimo à data atual."""
    data_devolucao = datetime.datetime.now() + PERIODO_EMPRESTIMO
    return data_devolucao.strftime("%Y-%m-%d")


# Funções relacionadas a Tokens
def generate_token(user_id):
    payload = {
        "id_usuario": user_id,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=48)
    }
    token = jwt.encode(payload, senha_secreta, algorithm='HS256')
    return token


def remover_bearer(token):
    if token.startswith("Bearer "):
        return token[len("Bearer "):]
    else:
        return token


def verificar_user(tipo, trazer_pl):
    token = request.headers.get('Authorization')
    if not token:
        return 1  # Token de autenticação necessário

    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return 2  # Token expirado
    except jwt.InvalidTokenError:
        return 3  # Token inválido

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    if tipo == 2:
        cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado,))
        biblio = cur.fetchone()
        if not biblio:
            cur.close()
            return 4  # Nível bibliotecário requerido

    elif tipo == 3:
        cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado,))
        admin = cur.fetchone()
        if not admin:
            cur.close()
            return 5  # Nível Administrador requerido

    if trazer_pl:
        cur.close()
        return payload
    cur.close()
    pass


def informar_verificacao(tipo=0, trazer_pl=False):
    verificacao = verificar_user(tipo, trazer_pl)
    if verificacao == 1:
        return jsonify({'mensagem': 'Token de autenticação necessário.'}), 401
    elif verificacao == 2:
        return jsonify({'mensagem': 'Token expirado.'}), 401
    elif verificacao == 3:
        return jsonify({'mensagem': 'Token inválido.'}), 401
    elif verificacao == 4:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido.'}), 401
    elif verificacao == 5:
        return jsonify({'mensagem': 'Nível Administrador requerido.'}), 401
    else:
        if trazer_pl:
            return verificacao
        return None


# Funções relacionadas a livros
def buscar_livro_por_id(id):
    cur = con.cursor()
    cur.execute("""
        SELECT 
            a.id_livro, 
            a.titulo, 
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.DESCRICAO, 
            a.idiomas, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        WHERE a.id_livro = ?
    """, (id,))

    livro = cur.fetchone()

    if not livro:
        return None  # Retorna None se o livro não for encontrado

    cur.execute("""
        SELECT t.id_tag, t.nome_tag
        FROM LIVRO_TAGS lt
        LEFT JOIN TAGS t ON lt.ID_TAG = t.ID_TAG
        WHERE lt.ID_LIVRO = ?
    """, (id,))

    tags = cur.fetchall()
    selected_tags = [{'id': tag[0], 'nome': tag[1]} for tag in tags]

    cur.execute("SELECT VALOR_TOTAL FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
    valor_total = cur.fetchone()
    cur.execute("SELECT QTD_AVALIACOES FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
    qtd = cur.fetchone()

    if valor_total and qtd and qtd[0] != 0:
        avaliacoes = round((valor_total[0] / qtd[0]), 2)
    else:
        avaliacoes = 0.00

    cur.close()

    return {
        "id": livro[0],
        "titulo": livro[1],
        "autor": livro[2],
        "categoria": livro[3],
        "isbn": livro[4],
        "qtd_disponivel": livro[5],
        "descricao": livro[6],
        "idiomas": livro[7],
        "ano_publicado": livro[8],
        "imagem": f"{livro[0]}.jpeg",
        "selectedTags": selected_tags,
        "avaliacao": avaliacoes
    }


# Inicializando o Flask-Mail
mail = Mail(app)


def enviar_email_async(destinatario, assunto, corpo):
    def enviar_email():
        with app.app_context():
            print(destinatario, assunto, corpo)
            msg = Message(assunto, recipients=[destinatario])
            msg.body = corpo
            # Definindo o cabeçalho Reply-To para o endereço noreply
            msg.reply_to = 'noreply@dominio.com'  # Não aceitar respostas

            try:
                mail.send(msg)
            except Exception as e:
                print(e)

    # Criando uma thread para enviar o email em segundo plano
    thread = threading.Thread(target=enviar_email)
    thread.start()


# Rota para testes
"""
@app.route('/enviar_emails', methods=['GET'])
def enviar_emails():
    cur = con.cursor()
    cur.execute("SELECT ID_USUARIO, NOME, EMAIL, SENHA FROM USUARIOS WHERE USUARIOS.EMAIL = 'dimitric2007@gmail.com'")
    usuario = cur.fetchone()

    # Enviar e-mail para todos os usuários ativos
    nome = usuario[1]
    email = usuario[2]
    print(f"Nome: {nome}, email: {email}")
    assunto = 'Olá, ' + nome
    corpo = f'Olá {nome},\n\nEste é um e-mail de exemplo enviado via Flask.'
    enviar_email_async(email, assunto, corpo)

    return jsonify({"message": "E-mails enviados com sucesso!"})
"""


@app.route('/tem_permissao/<int:tipo>', methods=["GET"])
def verificar(tipo):
    if tipo:
        verificacao = informar_verificacao(tipo)
    else:
        verificacao = informar_verificacao()

    if verificacao:
        return verificacao


@app.route('/cadastro', methods=["POST"])
def cadastrar():
    try:
        # Recebendo informações
        data = request.form

        nome = data.get('nome')
        email = data.get('email')
        telefone = data.get('telefone')
        endereco = data.get('endereco')
        senha = data.get('senha')
        confirmSenha = data.get('confirmSenha')
        tipo = int(data.get('tipo'))
        imagem = request.files.get('imagem')

        email = email.lower()

        # Verificando se tem todos os dados
        if not all([nome, email, senha, tipo, confirmSenha]):
            return jsonify({"message": "Todos os campos são obrigatórios."}), 401

        if senha != confirmSenha:
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 401

        if len(senha) < 8:
            return jsonify({"message": "Sua senha deve conter pelo menos 8 caracteres."}), 401

        tem_maiuscula = False
        tem_minuscula = False
        tem_numero = False
        tem_caract_especial = False
        caracteres_especiais = "!@#$%^&*(),-.?\":{}|<>"

        # Verifica cada caractere da senha
        for char in senha:
            if char.isupper():
                tem_maiuscula = True
            elif char.islower():
                tem_minuscula = True
            elif char.isdigit():
                tem_numero = True
            elif char in caracteres_especiais:
                tem_caract_especial = True

        # Verifica se todos os critérios foram atendidos
        if not tem_maiuscula:
            return jsonify({"message": "A senha deve conter pelo menos uma letra maiúscula."}), 401
        if not tem_minuscula:
            return jsonify({"message": "A senha deve conter pelo menos uma letra minúscula."}), 401
        if not tem_numero:
            return jsonify({"message": "A senha deve conter pelo menos um número."}), 401
        if not tem_caract_especial:
            return jsonify({"message": "A senha deve conter pelo menos um caractere especial."}), 401

        # Abrindo o Cursor
        cur = con.cursor()

        # Checando duplicações
        cur.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Email já cadastrado."}), 409

        cur.execute("SELECT 1 FROM usuarios WHERE telefone = ?", (telefone,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Telefone já cadastrado."}), 409

        senha = generate_password_hash(senha).decode('utf-8')

        # Inserindo usuário na tabela usuarios conforme seu tipo
        if tipo == 1:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 1) RETURNING ID_USUARIO",
                (nome, email, telefone, endereco, senha)
            )
            id_usuario = cur.fetchone()[0]
            con.commit()
        elif tipo == 2:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 2) RETURNING ID_USUARIO",
                (nome, email, telefone, endereco, senha)
            )
            id_usuario = cur.fetchone()[0]
            con.commit()

        elif tipo == 3:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 3) RETURNING ID_USUARIO",
                (nome, email, telefone, endereco, senha)
            )
            id_usuario = cur.fetchone()[0]
            con.commit()
        else:
            cur.close()
            return jsonify(
                {
                    "message": "Tipo de usuário inválido."
                }
            ), 401
        con.commit()

        print(id_usuario)
        cur.close()

        # Verificações de Imagem
        imagens = [
            ".jpeg",
            ".jpg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            ".heif",
            ".raw",
            ".svg",
            ".eps",
            ".pdf",
            ".ico",
            ".heic",
            ".xcf",
            ".psd"
        ]

        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
        if imagem:
            valido = False
            for ext in imagens:
                if imagem.filename.endswith(ext):
                    valido = True
            if not valido:
                return jsonify(
                    {
                        "message": "Usuário cadastrado com sucesso, mas o formato de imagem é inválido, você pode alterar editando seu perfil depois."
                    }
                ), 200
            nome_imagem = f"{id_usuario}.jpeg"
            pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
            os.makedirs(pasta_destino, exist_ok=True)
            imagem_path = os.path.join(pasta_destino, nome_imagem)
            imagem.save(imagem_path)

        return jsonify(
            {
                "message": "Usuário cadastrado com sucesso."
            }
        ), 200

    except Exception as e:
        print(e)
        return jsonify({"message": f"Erro: {str(e)}"}), 500


global_contagem_erros = {}


@app.route('/login', methods=["POST"])
def logar():
    # Recebendo informações
    data = request.get_json()
    email = data.get('email')
    senha = data.get('senha')
    email = email.lower()

    print(email, senha)

    cur = con.cursor()

    # Checando se a senha está correta
    cur.execute("SELECT senha, id_usuario FROM usuarios WHERE email = ?", (email,))
    resultado = cur.fetchone()

    if resultado:
        senha_hash = resultado[0]
        id_user = resultado[1]
        cur = con.cursor()
        ativo = cur.execute("SELECT ATIVO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user,))
        ativo = ativo.fetchone()[0]
        if not ativo:
            cur.close()
            return jsonify(
                {
                    "message": "Este usuário está inativado.",
                    "id_user": id_user
                }
            ), 401

        if check_password_hash(senha_hash, senha):

            # Pegar o tipo do usuário para levar à página certa
            tipo = cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user,))
            tipo = tipo.fetchone()[0]
            token = generate_token(id_user)
            # Excluir as tentativas que deram errado
            id_user_str = f"usuario-{id_user}"
            if id_user_str in global_contagem_erros:
                del global_contagem_erros[id_user_str]
                print("Contagem de erros deletada")
            if tipo == 2:
                cur.close()
                return jsonify(
                    {
                        "message": "Bibliotecário entrou com sucesso.",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    }
                ), 200
            elif tipo == 3:
                cur.close()
                return jsonify(
                    {
                        "message": "Administrador entrou com sucesso.",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    })
            else:
                cur.close()
                return jsonify(
                    {
                        "message": "Leitor entrou com sucesso.",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    }
                ), 200
        else:
            # Ignorar isso tudo se o usuário for administrador
            tipo = cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user,))
            tipo = tipo.fetchone()[0]
            if tipo != 3:
                print(f"Primeiro: {global_contagem_erros}")
                id_user_str = f"usuario-{id_user}"
                if id_user_str not in global_contagem_erros:
                    global_contagem_erros[id_user_str] = 1
                else:
                    global_contagem_erros[id_user_str] += 1

                    if global_contagem_erros[id_user_str] == 3:
                        cur.execute("UPDATE USUARIOS SET ATIVO = FALSE WHERE ID_USUARIO = ?", (id_user,))
                        con.commit()
                        cur.close()
                        return jsonify({"message": "Tentativas excedidas, usuário inativado."}), 401
                    elif global_contagem_erros[id_user_str] > 3:
                        global_contagem_erros[id_user_str] = 1
                        print("Contagem resetada para 1")  # Em teoria é para ser impossível a execução chegar aqui

                    print(f"Segundo: {global_contagem_erros}")

            cur.close()
            return jsonify({"message": "Credenciais inválidas."}), 401
    else:
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404


@app.route('/reativar_usuario', methods=["PUT"])
def reativar_usuario():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    data = request.get_json()
    id_usuario = data.get("id")

    cur = con.cursor()
    # Checar se existe
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404

    cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    tipo = cur.fetchone()[0]
    if tipo == 3:
        cur.close()
        return jsonify({"message": "Esse usuário não pode ser reativado."}), 401

    # Checar se já está ativo
    cur.execute("SELECT ATIVO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    if cur.fetchone()[0]:
        cur.close()
        return jsonify({"message": "Usuário já está ativo."}), 200

    cur.execute("UPDATE USUARIOS SET ATIVO = TRUE WHERE ID_USUARIO = ?", (id_usuario,))
    con.commit()
    cur.close()
    return jsonify({"message": "Usuário reativado com sucesso."})


@app.route('/inativar_usuario', methods=["PUT"])
def inativar_usuario():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    data = request.get_json()
    id_usuario = data.get("id")

    cur = con.cursor()
    # Checar se existe
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404

    cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    tipo = cur.fetchone()[0]
    if tipo == 3:
        cur.close()
        return jsonify({"message": "Esse usuário não pode ser inativado."}), 401

    # Checar se já está inativado
    cur.execute("SELECT ATIVO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    tipo = cur.fetchone()[0]
    print(tipo)
    if not tipo:
        cur.close()
        return jsonify({"message": "Usuário já está inativado."}), 200

    cur.execute("UPDATE USUARIOS SET ATIVO = FALSE WHERE ID_USUARIO = ?", (id_usuario,))
    con.commit()
    cur.close()
    return jsonify({"message": "Usuário inativado com sucesso."})


@app.route('/editar_usuario', methods=["PUT"])
def usuario_put():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    usuario_data = cur.fetchone()

    if not usuario_data:
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404

    data = request.form
    nome = data.get('nome')
    email = data.get('email')
    telefone = data.get('telefone')
    endereco = data.get('endereco')
    senha_nova = data.get('senha')
    senha_confirm = data.get('senhaConfirm')
    senha_antiga = data.get('senhaAntiga')
    imagem = request.files.get('imagem')

    if not all([nome, email, telefone, endereco]):
        cur.close()
        return jsonify({"message": "Todos os campos são obrigatórios, exceto a senha."}), 401

    if senha_nova or senha_confirm:
        if not senha_antiga:
            cur.close()
            return jsonify({"message": "Para alterar a senha, é necessário informar a senha antiga."}), 401

        if senha_nova == senha_antiga:
            cur.close()
            return jsonify({"message": "A senha nova não pode ser igual a senha atual."}), 401

        cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario,))
        senha_armazenada = cur.fetchone()[0]

        if not check_password_hash(senha_armazenada, senha_antiga):
            cur.close()
            return jsonify({"message": "Senha antiga incorreta."}), 401

        if senha_nova != senha_confirm:
            cur.close()
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 401

        if len(senha_nova) < 8 or not any(c.isupper() for c in senha_nova) or not any(
                c.islower() for c in senha_nova) or not any(c.isdigit() for c in senha_nova) or not any(
            c in "!@#$%^&*(), -.?\":{}|<>" for c in senha_nova):
            cur.close()
            return jsonify({
                "message": "A senha deve conter pelo menos 8 caracteres, uma letra maiúscula, uma letra minúscula, um número e um caractere especial."}), 401

        senha_nova = generate_password_hash(senha_nova)
        cur.execute(
            "UPDATE usuarios SET senha = ? WHERE id_usuario = ?",
            (senha_nova, id_usuario)
        )
    cur.execute("SELECT 1 FROM USUARIOS WHERE EMAIL = ? AND ID_USUARIO <> ?", (email, id_usuario))
    if cur.fetchone():
        cur.close()
        return jsonify({
            "message": "Este email pertence a outra pessoa."
        }), 401

    cur.execute("SELECT 1 FROM USUARIOS WHERE telefone = ? AND ID_USUARIO <> ?", (telefone, id_usuario))
    if cur.fetchone():
        cur.close()
        return jsonify({
            "message": "Este telefone pertence a outra pessoa."
        }), 401

    cur.execute(
        "UPDATE usuarios SET nome = ?, email = ?, telefone = ?, endereco = ? WHERE id_usuario = ?",
        (nome, email, telefone, endereco, id_usuario)
    )
    con.commit()

    if imagem:
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, f"{id_usuario}.jpeg")
        imagem.save(imagem_path)
    else:
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, f"{id_usuario}.jpeg")
        if os.path.exists(imagem_path):
            os.remove(imagem_path)

    cur.close()
    return jsonify({"message": "Usuário atualizado com sucesso."}), 200


@app.route('/deletar_usuario', methods=['DELETE'])
def deletar_usuario():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    cur = con.cursor()

    data = request.get_json()
    id_usuario = data.get("id_usuario")
    # Verificar se o usuario existe
    cur.execute("SELECT 1 FROM usuarios WHERE ID_usuario = ?", (id_usuario,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Usuário não encontrado."}), 404

    # Excluir os registros que usam o id como chave estrangeira
    cur.execute("""
    DELETE FROM ITENS_EMPRESTIMO i WHERE
     i.ID_EMPRESTIMO IN (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.ID_USUARIO = ?)
     """, (id_usuario,))
    cur.execute("DELETE FROM EMPRESTIMOS WHERE ID_USUARIO = ?", (id_usuario,))
    cur.execute("DELETE FROM RESERVAS WHERE ID_USUARIO = ?", (id_usuario,))
    cur.execute("DELETE FROM MULTAS WHERE ID_USUARIO = ?", (id_usuario,))

    # Excluir o usuario
    cur.execute("DELETE FROM usuarios WHERE ID_usuario = ?", (id_usuario,))
    con.commit()
    cur.close()

    # Excluir a imagem de usuário da aplicação caso houver
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    valido = True
    ext_real = None
    for ext in imagens:
        if os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext}"):
            valido = False
            ext_real = ext
    if not valido:
        os.remove(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext_real}")

    return jsonify({'message': "Usuário excluído com sucesso."})


# Para ADM
@app.route('/excluir_imagem_user2/<int:id_usuario>', methods=["GET"])
def excluir_imagem_adm(id_usuario):
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    valido = True
    ext_real = None
    for ext in imagens:
        if os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext}"):
            valido = False
            ext_real = ext
    if not valido:
        os.remove(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext_real}")

    return jsonify({"message": "Imagem de perfil excluída com sucesso."}, 200)


# Para usuários excluírem sua própria imagem
@app.route('/excluir_imagem_user/<int:id_usuario>', methods=["GET"])
def excluir_imagem(id_usuario):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    if payload["id_usuario"] != id_usuario:
        return jsonify({"message": "Ação não autorizada."}, 401)

    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    valido = True
    ext_real = None
    for ext in imagens:
        if os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext}"):
            valido = False
            ext_real = ext
    if not valido:
        os.remove(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext_real}")

    return jsonify({"message": "Imagem de perfil excluída com sucesso."}, 200)


@app.route('/livros', methods=["GET"])
def get_livros():
    cur = con.cursor()
    cur.execute("""
            SELECT 
                a.id_livro, 
                a.titulo, 
                a.autor, 
                a.CATEGORIA, 
                a.ISBN, 
                a.QTD_DISPONIVEL, 
                a.DESCRICAO, 
                a.idiomas, 
                a.ANO_PUBLICADO
            FROM ACERVO a
            ORDER BY a.id_livro;
        """
                )

    livros = []
    for r in cur.fetchall():
        cur.execute("""
            SELECT t.id_tag, t.nome_tag
            FROM LIVRO_TAGS lt
            LEFT JOIN TAGS t ON lt.ID_TAG = t.ID_TAG
            WHERE lt.ID_LIVRO = ?
        """, (r[0],))
        tags = cur.fetchall()

        selected_tags = [{'id': tag[0], 'nome': tag[1]} for tag in tags]

        livro = {
            'id': r[0],
            'titulo': r[1],
            'autor': r[2],
            'categoria': r[3],
            'isbn': r[4],
            'qtd_disponivel': r[5],
            'descricao': r[6],
            'idiomas': r[7],
            'ano_publicacao': r[8],
            'selectedTags': selected_tags,
            'imagem': f"{r[0]}.jpeg"
        }

        livros.append(livro)

    cur.close()
    return jsonify(livros), 200


@app.route('/adicionar_livros', methods=["POST"])
def adicionar_livros():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    data = request.form
    titulo = data.get('titulo')
    autor = data.get('autor')
    categoria = data.get('categoria')
    isbn = data.get('isbn')
    qtd_disponivel = data.get('qtd_disponivel')
    descricao = data.get('descricao')
    idiomas = data.get('idiomas')
    ano_publicado = data.get("ano_publicado")
    tags = data.get('selectedTags', []).split(",")

    imagem = request.files.get('imagem')

    print(tags)

    if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado]):
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401

    cur = con.cursor()

    # Verificando se a ISBN já está cadastrada
    cur.execute("SELECT 1 FROM acervo WHERE isbn = ?", (isbn,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "ISBN já cadastrada."}), 404

    if int(qtd_disponivel) < 1:
        cur.close()
        return jsonify({"error": "Quantidade disponível precisa ser maior que 1."}), 401
    if int(ano_publicado) > datetime.date.today().year:
        cur.close()
        return jsonify({"error": "Ano publicado deve ser condizente com a data atual."}), 401

    # Verificações de idioma
    lista_idiomas = ["Português", "Inglês", "Espanhol", "Francês"]
    if idiomas not in lista_idiomas:
        return jsonify({"error": "Este idioma não é aceito."}), 401

    # Adicionando os dados na Database
    cur.execute(
        """INSERT INTO 
        ACERVO (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado) 
        VALUES(?, ?, ?, ?, ?, ?, ?, ?) RETURNING ID_LIVRO""",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado)
    )
    livro_id = cur.fetchone()[0]
    con.commit()

    if not livro_id:
        cur.close()
        return jsonify({"error": "Erro ao recuperar ID do livro."}), 500

    # Associando tags ao livro
    for tag in tags:
        tag_id = tag
        print(f"Tag_id:{tag_id}")
        if tag_id:
            cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (livro_id, tag_id))

    # Criando um registro avaliações vazio para poder editar usando a rota de avaliar depois
    cur.execute("INSERT INTO AVALIACOES (ID_LIVRO, VALOR_TOTAL, QTD_AVALIACOES) VALUES (?, 0, 0)", (livro_id,))

    con.commit()
    cur.close()

    # Verificações de Imagem
    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]

    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    if imagem:
        valido = False
        for ext in imagens:
            if imagem.filename.endswith(ext):
                valido = True
        if not valido:
            return jsonify(
                {
                    "message": "Livro cadastrado com sucesso, mas o formato de imagem é inválido, você pode alterar editando seu perfil depois."
                }
            ), 200
        nome_imagem = f"{livro_id}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify({"message": "Livro cadastrado com sucesso.", "id_livro": livro_id}), 202


@app.route('/editar_livro/<int:id_livro>', methods=["PUT"])
def editar_livro(id_livro):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)",
                (id_logado,))
    biblio = cur.fetchone()
    if not biblio:
        return jsonify({'error': 'Nível Bibliotecário requerido.'}), 401

    data = request.form

    cur = con.cursor()
    cur.execute("SELECT titulo, autor, categoria, isbn, qtd_disponivel, descricao FROM acervo WHERE id_livro = ?",
                (id_livro,))
    acervo_data = cur.fetchone()

    if not acervo_data:
        cur.close()
        return jsonify({"message": "Livro não foi encontrado."}), 404

    titulo = data.get('titulo')
    autor = data.get('autor')
    categoria = data.get('categoria')
    isbn = data.get('isbn')
    qtd_disponivel = data.get('qtd_disponivel')
    descricao = data.get('descricao')
    tags = data.get('selectedTags', []).split(',')
    idiomas = data.get('idiomas')
    ano_publicado = data.get("ano_publicado")

    print(data)
    print(tags)

    imagem = request.files.get("imagem")
    # Verificando se tem todos os dados
    if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado]):
        cur.close()
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401

    # Verificando se os dados novos já existem na DataBase
    isbnvelho = acervo_data[3].lower()
    if isbn != isbnvelho:
        cur.execute("SELECT 1 FROM ACERVO WHERE ISBN = ? AND ID_LIVRO <> ?", (isbn, id_livro,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "ISBN já cadastrado."})
    if int(ano_publicado) > datetime.date.today().year:
        cur.close()
        return jsonify({"message": "Ano publicado deve ser condizente com a data atual."}), 401

    cur.execute(
        """UPDATE acervo SET
         titulo = ?, autor = ?, categoria = ?, isbn = ?, qtd_disponivel = ?, descricao = ?, 
         idiomas = ?, ano_publicado = ?
        WHERE
         id_livro = ?""",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado, id_livro)
    )
    con.commit()

    cur.execute("delete from livro_tags where id_livro = ? ", (id_livro,))
    insert_data = []

    # Associando tags ao livro
    for tag in tags:
        tag_id = tag
        print(f"Tag_id:{tag_id}")
        if tag_id:
            cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (id_livro, tag_id))

    con.commit()

    cur.close()

    # Verificações de Imagem
    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]

    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    if imagem:
        valido = False
        for ext in imagens:
            if imagem.filename.endswith(ext):
                valido = True
        if not valido:
            return jsonify(
                {
                    "message": "Livro editado com sucesso, mas o formato de imagem é inválido, você pode alterar editando seu perfil depois."
                }
            ), 200
        nome_imagem = f"{id_livro}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify({"message": "Livro atualizado com sucesso."}), 200


@app.route('/excluir_livro', methods=["DELETE"])
def livro_delete():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado,))
    biblio = cur.fetchone()

    if not biblio:
        cur.close()
        return jsonify({'mensagem': 'Nível Bibliotecário requerido.'}), 401

    # Obter JSON da requisição
    data = request.get_json()

    # Garantir que o ID foi enviado
    if not data or 'id_livro' not in data:
        cur.close()
        return jsonify({"error": "ID do livro não fornecido."}), 401

    id_livro = data['id_livro']

    # Verificar se o livro existe
    cur.execute("SELECT 1 FROM acervo WHERE ID_livro = ?", (id_livro,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Livro não encontrado."}), 404

    # Deleção de reservas
    cur.execute(
        'SELECT ID_USUARIO FROM RESERVAS WHERE ID_RESERVA IN (SELECT ID_RESERVA FROM ITENS_RESERVA WHERE ID_LIVRO = ?)',
        (id_livro,))
    id_usuario = cur.fetchone()
    print(f"Usuário que teve sua reserva deletada: {id_usuario}")

    cur.execute("SELECT ID_RESERVA FROM ITENS_RESERVA WHERE ID_LIVRO = ?", (id_livro,))
    reservas_deletar = cur.fetchall()
    reservas_deletar = [r[0] for r in reservas_deletar]  # Extrai apenas o valor do ID_RESERVA de cada tupla

    if reservas_deletar:
        placeholders = ', '.join('?' for _ in reservas_deletar)
        query = f"DELETE FROM RESERVAS r WHERE r.ID_RESERVA IN ({placeholders})"
        cur.execute(query, reservas_deletar)

    # Enviar um email para o usuário que possuia reserva

    # Deleção de empréstimos
    cur.execute(
        "SELECT ID_USUARIO FROM EMPRESTIMOS e WHERE e.STATUS = 'ATIVO' AND DATA_DEVOLVIDO IS NULL AND ID_EMPRESTIMO IN (SELECT ID_EMPRESTIMO FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?)",
        (id_livro,))
    id_usuario = cur.fetchone()
    print(f"Usuário que teve seu emprestimo deletado: {id_usuario}")

    cur.execute("SELECT ID_EMPRESTIMO FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?", (id_livro,))
    emprestimos_deletar = cur.fetchall()
    emprestimos_deletar = [r[0] for r in emprestimos_deletar]  # Extrai apenas o valor do ID_RESERVA de cada tupla

    if emprestimos_deletar:
        placeholders = ', '.join('?' for _ in emprestimos_deletar)
        query = f"DELETE FROM EMPRESTIMOS ie WHERE ie.ID_EMPRESTIMO IN ({placeholders})"
        cur.execute(query, emprestimos_deletar)

    # Enviar um email para o usuário que teve seu empréstimo comprometido

    # E finalmente, da lista de livros
    cur.execute("DELETE FROM acervo WHERE ID_livro = ?", (id_livro,))

    con.commit()
    cur.close()

    # Remover imagem do livro
    upload_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    for ext in imagens:
        caminho_imagem = os.path.join(upload_folder, "Livros", f"{id_livro}{ext}")
        if os.path.exists(caminho_imagem):
            os.remove(caminho_imagem)
            break

    return jsonify({'message': "Livro excluído com sucesso!"}), 200


@app.route('/devolver_emprestimo', methods=["PUT"])
def devolver_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    data = request.get_json()
    id_emprestimo = data.get("id_emprestimo")

    cur = con.cursor()

    # Verificações
    if not id_emprestimo:
        # Se não recebeu o id
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401
    else:
        # Se o ID não existe no banco de dados
        cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id_emprestimo,))
        if not cur.fetchone():
            cur.close()
            return jsonify({"message": "Id de empréstimo não encontrado."}), 404

    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ? AND DATA_DEVOLVIDO IS NOT NULL", (id_emprestimo,))
    if cur.fetchone():
        cur.close()
        return jsonify({"message": "Empréstimo já devolvido."}), 401

    # Devolver o empréstimo
    cur.execute("UPDATE EMPRESTIMOS SET DATA_DEVOLVIDO = CURRENT_DATE WHERE ID_EMPRESTIMO = ?", (id_emprestimo,))
    con.commit()
    cur.close()
    return jsonify({"message": "Devolução feita com sucesso."}), 200


@app.route('/renovar_emprestimo', methods=["PUT"])
def renovar_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    data = request.get_json()
    id_emprestimo = data.get("id_emprestimo")
    dias = data.get("dias")

    if not all([dias, id_emprestimo]):
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401

    cur = con.cursor()

    # Verificar se o id existe e se já não foi devolvido o empréstimo
    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id_emprestimo,))
    if not cur.fetchone():
        return jsonify({"message": "Id de empréstimo não existe."}), 404
    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE DATA_DEVOLVIDO IS NOT NULL AND ID_EMPRESTIMO = ?", (id_emprestimo,))
    if cur.fetchone():
        return jsonify({"message": "Este empréstimo já teve sua devolução."}), 404

    cur.execute("""UPDATE EMPRESTIMOS SET 
    DATA_DEVOLVER = DATEADD(DAY, ?, CURRENT_DATE) WHERE ID_EMPRESTIMO = ?""", (dias, id_emprestimo,))
    con.commit()
    cur.close()
    return jsonify({"message": "Empréstimo renovado com sucesso."}), 200


@app.route('/upload/usuario', methods=["POST"])
def enviar_imagem_usuario():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    imagem = request.files.get("imagem")
    id_usuario = payload["id_usuario"]
    print(id_usuario)

    # Verificações de Imagem
    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    if imagem:
        valido = False
        for ext in imagens:
            if imagem.filename.endswith(ext):
                valido = True
        if not valido:
            return jsonify(
                {
                    "message": "Formato de imagem não autorizado."
                }
            ), 401
        nome_imagem = f"{id_usuario}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify(
        {
            "message": "Imagem enviada com sucesso."
        }
    ), 200


@app.route('/upload/livro', methods=["POST"])
def enviar_imagem_livro():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado,))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    biblio = cur.fetchone()[0]
    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido.'}), 401

    imagem = request.files.get("imagem")
    data = request.form.to_dict()
    id_livro = data.get("id_livro")

    print(id_livro)

    # Verificações de Imagem
    imagens = [
        ".jpeg",
        ".jpg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".raw",
        ".svg",
        ".eps",
        ".pdf",
        ".ico",
        ".heic",
        ".xcf",
        ".psd"
    ]
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    if imagem:
        valido = False
        for ext in imagens:
            if imagem.filename.endswith(ext):
                valido = True
        if not valido:
            return jsonify(
                {
                    "message": "Formato de imagem não autorizado."
                }
            ), 401
        nome_imagem = f"{id_livro}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify(
        {
            "message": "Imagem enviada com sucesso."
        }
    ), 200


@app.route('/cancelar_reserva', methods=["PUT"])
def deletar_reservas():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    data = request.get_json()
    id_reserva = data.get("id_reserva")

    # Checando se todos os dados foram preenchidos
    if not id_reserva:
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401

    cur = con.cursor()

    # Checando se a reserva existe
    cur.execute("SELECT 1 from reservas where id_reserva = ?", (id_reserva,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "A reserva selecionada não existe."})

    # Mudar o status da Reserva
    cur.execute("UPDATE reservas SET STATUS = 'Cancelada' WHERE id_reserva = ?", (id_reserva,))
    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva cancelada com sucesso."
    }), 200


@app.route('/pesquisa', methods=["POST"])
def pesquisar():
    data = request.get_json()
    pesquisa = data.get("pesquisa")
    filtros = data.get("filtros", [])

    print(filtros)

    if not pesquisa:
        return jsonify({"message": "Nada pesquisado."})

    cur = con.cursor()

    # Pesquisando texto
    sql = """
        SELECT DISTINCT a.id_livro, a.titulo, a.autor, a.categoria, a.isbn, 
                        a.qtd_disponivel, a.descricao 
        FROM acervo a 
        LEFT JOIN LIVRO_TAGS lt ON a.ID_LIVRO = lt.ID_LIVRO
        LEFT JOIN TAGS t ON lt.ID_TAG = t.ID_TAG
        WHERE a.titulo CONTAINING ?
    """

    params = [pesquisa]

    if "autor" in filtros:
        sql += " OR a.autor CONTAINING ?"
        params.append(pesquisa)
    if "tags" in filtros:
        sql += " OR t.nome_tag CONTAINING ?"
        params.append(pesquisa)
    if "categoria" in filtros:
        sql += " OR a.categoria CONTAINING ?"
        params.append(pesquisa)
    if "isbn" in filtros:
        sql += " OR a.isbn = ?"
        params.append(pesquisa)

    sql += "\norder by a.titulo"
    cur.execute(sql, params)
    resultados = cur.fetchall()
    if not resultados:
        cur.close()
        return jsonify({"message": "Nenhum resultado encontrado."}), 404

    cur.close()
    return jsonify({
        "message": "Pesquisa realizada com sucesso.",
        "resultados": [{"id": r[0], "titulo": r[1], "autor": r[2], "categoria": r[3],
                        "isbn": r[4], "qtd_disponivel": r[5], "descricao": r[6], "imagem": f"{r[0]}.jpeg"} for r in
                       resultados]
    }), 202


@app.route('/tags', methods=["GET"])
def get_tags():
    cur = con.cursor()
    cur.execute("SELECT id_tag, nome_tag from tags")
    tags = [{'id': r[0], 'nome': r[1]} for r in cur.fetchall()]
    cur.close()
    return jsonify(tags), 200


@app.route('/tags/<int:id>', methods=["GET"])
def get_tag(id):
    cur = con.cursor()
    cur.execute("SELECT id_tag, id_livro from livro_tags where id_livro = ?", (id,))
    tags = [{'id_tag': r[0], 'id_livro': r[1]} for r in cur.fetchall()]
    cur.close()
    return jsonify(tags), 200


@app.route("/avaliarlivro/<int:id>", methods=["PUT"])
def avaliar_livro(id):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    data = request.get_json()
    valor = data.get("valor")  # De 0 a 5

    cur = con.cursor()
    cur.execute("SELECT VALOR_TOTAL FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
    valor_total = cur.fetchone()[0]
    cur.execute("SELECT QTD_AVALIACOES FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
    qtd_avaliacoes = cur.fetchone()[0]
    print(f"Valor total: {valor_total}, Avaliações: {qtd_avaliacoes}")

    cur.execute("UPDATE AVALIACOES SET VALOR_TOTAL = ?, QTD_AVALIACOES = ? WHERE ID_LIVRO = ?",
                (valor_total + valor, qtd_avaliacoes + 1, id))
    con.commit()
    cur.close()

    return jsonify({
        "message": "Avaliado com sucesso."
    })


@app.route("/livros/<int:id>", methods=["GET"])
def get_livros_id(id):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    livro = buscar_livro_por_id(id)
    if not livro:
        return jsonify({"error": "Livro não encontrado."}), 404
    return jsonify(livro)


@app.route('/relatorio/livros', methods=['GET'])
def gerar_relatorio_livros():
    cur = con.cursor()
    cur.execute("""
        SELECT 
            a.id_livro, 
            a.titulo, 
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.DESCRICAO, 
            a.idiomas, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        ORDER BY a.id_livro;
    """)
    livros = cur.fetchall()
    cur.close()

    contador_livros = len(livros)  # Definir o contador de livros antes do loop

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de livros", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de livros cadastrados: {contador_livros}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    subtitulos = ["ID", "Titulo", "Autor", "Categoria", "ISBN", "Quantidade Disponível", "Descrição", "Idiomas",
                  "Ano Publicado"]

    for livro in livros:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(0, 5, f"{subtitulos[i]}: ")

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(50, 5, f"{livro[i]}")
            pdf.ln(1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(7)

    pdf_path = "relatorio_livros.pdf"
    pdf.output(pdf_path)

    try:
        return send_file(pdf_path, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        print(e)
        return jsonify({'error': f"Erro ao gerar o arquivo: {str(e)}"}), 500


@app.route('/relatorio/usuarios', methods=['GET'])
def gerar_relatorio_usuarios():
    cur = con.cursor()
    cur.execute("""
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco
        FROM USUARIOS
        ORDER BY id_usuario;
    """)
    usuarios = cur.fetchall()
    cur.close()
    contador_usuarios = len(usuarios)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de usuários", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de usuários cadastrados: {contador_usuarios}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    subtitulos = ["ID", "Nome", "E-mail", "Telefone", "Endereço"]

    for usuario in usuarios:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(0, 5, f"{subtitulos[i]}: ")

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(50, 5, f"{usuario[i]}")
            pdf.ln(1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(7)

    pdf_path = "relatorio_usuarios.pdf"
    pdf.output(pdf_path)
    try:
        return send_file(pdf_path, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': f"Erro ao gerar o arquivo: {str(e)}"}), 500


@app.route("/user/<int:id>", methods=["GET"])
def get_user_id(id):
    print(f"Recebido ID: {id}")  # Verifique se o ID é recebido corretamente

    cur = con.cursor()
    cur.execute("""
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco, 
            senha, 
            tipo, 
            ativo
        FROM usuarios
        WHERE id_usuario = ?
    """, (id,))

    usuario = cur.fetchone()
    cur.close()

    if not usuario:  # Se o usuário não existir, retorna erro 404
        return jsonify({"error": "Usuário não encontrado."}), 404

    return jsonify({
        "id": usuario[0],
        "nome": usuario[1],
        "email": usuario[2],
        "telefone": usuario[3],
        "endereco": usuario[4],
        "senha": usuario[5],
        "tipo": usuario[6],
        "ativo": usuario[7],
        "imagem": f"{usuario[0]}.jpeg"
    })


@app.route('/usuarios', methods=["get"])
def usuarios():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    usuarios = """
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco, 
            senha, 
            tipo, 
            ativo
        FROM usuarios
        order by nome;
        """

    cur = con.cursor()
    cur.execute(usuarios)
    catchUsuarios = cur.fetchall()
    listaUsuarios = []

    for r in catchUsuarios:
        users = {
            'id_usuario': r[0],
            'nome': r[1],
            'email': r[2],
            'telefone': r[3],
            'endereco': r[4],
            'tipo': r[6],
            'ativo': r[7],
            'imagem': f"{r[0]}.jpeg"
        }

        listaUsuarios.append(users)

    cur.close()
    return jsonify(listaUsuarios), 200


@app.route('/token', methods=["POST"])
def tokenIsActive():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Token de autenticação necessário.'}), 401

    token = remover_bearer(token)

    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expirado.'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Token inválido.'}), 401

    return jsonify({'message': 'Token válido.', 'id_usuario': payload["id_usuario"]}), 200


@app.route('/uploads/<tipo>/<filename>')
def serve_file(tipo, filename):
    pasta_permitida = ["usuarios", "livros"]  # Apenas pastas permitidas
    if tipo not in pasta_permitida:
        return {"error": "Acesso negado."}, 403  # Evita acesso a outras pastas

    caminho_pasta = os.path.join(config.UPLOAD_FOLDER, tipo)
    caminho_arquivo = os.path.join(caminho_pasta, filename)

    # Verifica se o arquivo existe antes de servir
    if not os.path.isfile(caminho_arquivo):
        return {"error": "Arquivo não encontrado."}, 404

    return send_from_directory(caminho_pasta, filename)


@app.route('/trocar_tipo/<int:id>', methods=["PUT"])
def trocar_tipo(id):
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    cur = con.cursor()

    data = request.get_json()

    if data == 1:
        cur.execute("UPDATE USUARIOS SET tipo = 1 WHERE ID_USUARIO = ?", (id,))
    elif data == 2:
        cur.execute("UPDATE USUARIOS SET tipo = 2 WHERE ID_USUARIO = ?", (id,))
    elif data == 3:
        cur.execute("UPDATE USUARIOS SET tipo = 3 WHERE ID_USUARIO = ?", (id,))

    con.commit()
    cur.close()

    return jsonify({"message": "Usuário atualizado com sucesso.", "tipo": data}), 202


@app.route("/tem_permissao", methods=["get"])
def tem_permissao():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário.'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado.'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido.'}), 401

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 3 or TIPO = 2)", (id_logado,))
    bibli = cur.fetchone()
    if not bibli:
        cur.close()
        return jsonify({'mensagem': 'Nível Bibliotecário requerido.'}), 401
    cur.close()
    return jsonify({"message": "deu certo."}), 200


@app.route("/puxar_historico", methods=["GET"])
def puxar_historico():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]

    cur = con.cursor()

    # Emprestimos Ativos
    cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id_logado,))
    emprestimos_ativos = cur.fetchall()

    # Emprestimos Concluídos
    cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NOT NULL
            ORDER BY E.DATA_DEVOLVIDO DESC
        """, (id_logado,))
    emprestimos_concluidos = cur.fetchall()

    # Reservas Ativas - Obtendo os livros relacionados às reservas
    cur.execute("""
            SELECT IR.ID_LIVRO, A.TITULO, A.AUTOR, R.ID_RESERVA, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS
            FROM ITENS_RESERVA IR
            JOIN RESERVAS R ON IR.ID_RESERVA = R.ID_RESERVA
            JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
            WHERE R.ID_USUARIO = ?
            ORDER BY R.DATA_VALIDADE ASC
        """, (id_logado,))
    reservas_ativas = cur.fetchall()

    # Multas Pendentes
    cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, (M.VALOR_BASE + M.VALOR_ACRESCIMO) AS TOTAL, M.ID_EMPRESTIMO, M.PAGO
            FROM MULTAS M
            WHERE M.ID_USUARIO = ? AND M.PAGO = 0
            ORDER BY TOTAL DESC
        """, (id_logado,))
    multas_pendentes = cur.fetchall()

    historico = {
        "emprestimos_ativos": [
            {"id_livro": e[0], "titulo": e[1], "autor": e[2], "id_emprestimo": e[3], "data_retirada": e[4],
             "data_devolver": e[5]}
            for e in emprestimos_ativos
        ],
        "emprestimos_concluidos": [
            {"id_livro": e[0], "titulo": e[1], "autor": e[2], "id_emprestimo": e[3], "data_retirada": e[4],
             "data_devolver": e[5], "data_devolvido": e[6]}
            for e in emprestimos_concluidos
        ],
        "reservas_ativas": [
            {"id_livro": r[0], "titulo": r[1], "autor": r[2], "id_reserva": r[3], "data_criacao": r[4],
             "data_validade": r[5], "status": r[6]}
            for r in reservas_ativas
        ],
        "multas_pendentes": [
            {"id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2], "total": m[3], "id_emprestimo": m[4],
             "pago": m[5]}
            for m in multas_pendentes
        ]
    }

    cur.close()

    return jsonify(historico)


@app.route('/editar_usuario/<int:id_usuario>', methods=["PUT"])
def usuario_put_id(id_usuario):
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Verificar se o usuário existe
    cur.execute("SELECT * FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    usuario_data = cur.fetchone()
    if not usuario_data:
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404

    data = request.form

    nome = data.get('nome')
    email = data.get('email')
    telefone = data.get('telefone')
    endereco = data.get('endereco')
    senha_nova = data.get('senha')
    senha_confirm = data.get('senhaConfirm')
    senha_antiga = data.get('senhaAntiga')
    tipo_usuario = data.get('tipo')
    imagem = request.files.get('imagem')

    print(data)

    if not all([nome, email, telefone, endereco]):
        cur.close()
        return jsonify({"message": "Todos os campos são obrigatórios, exceto a senha."}), 401

    # Lógica para alteração de senha
    if senha_nova or senha_confirm:
        if not senha_antiga:
            cur.close()
            return jsonify({"message": "Para alterar a senha, é necessário informar a senha antiga."}), 401

        if senha_nova == senha_antiga:
            cur.close()
            return jsonify({"message": "A senha nova não pode ser igual a senha atual."})

        cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario,))
        senha_armazenada = cur.fetchone()[0]

        if not check_password_hash(senha_armazenada, senha_antiga):
            cur.close()
            return jsonify({"message": "Senha antiga incorreta."}), 401

        if senha_nova != senha_confirm:
            cur.close()
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 401

        if len(senha_nova) < 8 or not any(c.isupper() for c in senha_nova) or not any(
                c.islower() for c in senha_nova) or not any(c.isdigit() for c in senha_nova) or not any(
            c in "!@#$%^&*(), -.?\":{}|<>" for c in senha_nova):
            cur.close()
            return jsonify({
                "message": "A senha deve conter pelo menos 8 caracteres, uma letra maiúscula, uma letra minúscula, um número e um caractere especial."}), 401

        senha_nova = generate_password_hash(senha_nova)
        cur.execute(
            "UPDATE usuarios SET senha = ? WHERE id_usuario = ?",
            (senha_nova, id_usuario)
        )

    # Verificando se o email ou telefone já estão sendo usados por outro usuário
    cur.execute("SELECT 1 FROM USUARIOS WHERE EMAIL = ? AND ID_USUARIO <> ?", (email, id_usuario))
    if cur.fetchone():
        cur.close()
        return jsonify({
            "message": "Este email pertence a outra pessoa."
        }), 401

    cur.execute("SELECT 1 FROM USUARIOS WHERE telefone = ? AND ID_USUARIO <> ?", (telefone, id_usuario))
    if cur.fetchone():
        cur.close()
        return jsonify({
            "message": "Este telefone pertence a outra pessoa."
        }), 401

    # Atualizando as informações do usuário
    cur.execute(
        "UPDATE usuarios SET nome = ?, email = ?, telefone = ?, endereco = ? WHERE id_usuario = ?",
        (nome, email, telefone, endereco, id_usuario)
    )

    # Se o tipo do usuário for fornecido, atualizar o tipo
    if tipo_usuario:
        cur.execute("UPDATE USUARIOS SET tipo = ? WHERE id_usuario = ?", (tipo_usuario, id_usuario))

    # Salvar imagem se fornecida
    if imagem:
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, f"{id_usuario}.jpeg")
        imagem.save(imagem_path)
        # print("oi\n\n\n\n")

    # Commit das alterações
    con.commit()

    cur.close()
    return jsonify({"message": "Usuário atualizado com sucesso."}), 200


@app.route("/tem_permissao_adm", methods=["get"])
def tem_permissao_adm():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário.'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado.'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido.'}), 401

    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3 ", (id_logado,))
    admin = cur.fetchone()
    if not admin:
        cur.close()
        return jsonify({'mensagem': 'Nível Administrador requerido.'}), 401
    cur.close()
    return jsonify({"message": "deu certo."}), 200


# Adicionar item ao carrinho de reservas
@app.route('/carrinho_reservas', methods=['POST'])
def adicionar_carrinho_reserva():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]

    data = request.json
    id_livro = data.get("id_livro")

    cur = con.cursor()

    cur.execute("SELECT 1 from carrinho_reservas where id_livro = ? and id_usuario = ?", (id_livro, id_usuario))
    if cur.fetchone():
        return jsonify({"message": "Você não pode colocar 2 livros iguais no carrinho."}), 401

    cur.execute("INSERT INTO CARRINHO_RESERVAS (ID_USUARIO, ID_LIVRO) VALUES (?, ?)", (id_usuario, id_livro))
    con.commit()
    cur.close()
    return jsonify({"message": "Item adicionado ao carrinho de reservas."}), 201


# Remover ‘item’ do carrinho de reservas
@app.route('/carrinho_reservas/<int:item_id>', methods=['DELETE'])
def remover_carrinho_reserva(item_id):
    cur = con.cursor()
    cur.execute("DELETE FROM CARRINHO_RESERVAS WHERE ID_ITEM = ?", (item_id,))
    con.commit()
    cur.close()
    return jsonify({"message": "Item removido do carrinho de reservas."})


# Listar itens do carrinho de reservas


@app.route('/carrinho_reservas', methods=['GET'])
def listar_carrinho_reserva():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_usuario = payload["id_usuario"]

    query = """
        SELECT id_item, id_usuario, id_livro, data_adicionado 
        FROM CARRINHO_reservas
        WHERE id_usuario = ? 
        ORDER BY data_adicionado DESC;
    """

    cur = con.cursor()
    cur.execute(query, (id_usuario,))
    catchReservas = cur.fetchall()
    cur.close()

    listaReservas = []

    for e in catchReservas:
        id_livro = e[2]
        livro = buscar_livro_por_id(id_livro)  # Obtém os detalhes do livro

        reserva = {
            'id_reserva': e[0],
            'id_usuario': e[1],
            'id_livro': e[2],
            'data_adicionado': e[3],
            'imagem': f"{e[2]}.jpeg",
            'livro': livro  # Adiciona os detalhes do livro ao carrinho
        }

        listaReservas.append(reserva)

    return jsonify(listaReservas), 200


# Verificar disponibilidade para reserva
@app.route('/verificar_reserva/<int:livro_id>', methods=['GET'])
def verificar_reserva(livro_id):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    cur = con.cursor()
    cur.execute("""
        SELECT QTD_DISPONIVEL, 
            (SELECT COUNT(*) FROM RESERVAS R INNER JOIN ITENS_RESERVA IR ON R.ID_RESERVA = IR.ID_RESERVA WHERE IR.ID_LIVRO = ? AND R.STATUS IN ('Pendente', 'CONFIRMADA')) AS total_reservas,
            (SELECT COUNT(*) FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS = 'ATIVO') AS total_emprestimos
        FROM ACERVO 
        WHERE ID_LIVRO = ?
    """, (livro_id, livro_id, livro_id))
    livro = cur.fetchone()

    # Verificar se o usuário já possui alguma reserva ativa do livro
    cur.execute(
        "SELECT 1 FROM RESERVAS R INNER JOIN ITENS_RESERVA IR ON R.ID_RESERVA = IR.ID_RESERVA WHERE IR.ID_LIVRO = ? AND R.ID_USUARIO = ?"
        , (livro_id, payload["id_usuario"]))
    ja_tem = True if cur.fetchone() else False
    print(ja_tem)
    cur.close()

    if livro and (livro[2] >= livro[0] > livro[1]) and not ja_tem:
        return jsonify({"disponivel": True})
    return jsonify({"disponivel": False})


# Confirmar reserva
@app.route('/reservar', methods=['POST'])
def confirmar_reserva():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]

    cur = con.cursor()

    cur.execute("""
        SELECT ID_LIVRO FROM CARRINHO_RESERVAS WHERE ID_USUARIO = ?
    """, (id_usuario,))

    if not cur.fetchone():
        return jsonify({"message": "Não há livros no carrinho."}), 404

    cur.execute("""
        SELECT 1 
        FROM RESERVAS R
        JOIN ITENS_RESERVA I ON R.ID_RESERVA = I.ID_RESERVA
        JOIN CARRINHO_RESERVAS CR ON I.ID_LIVRO = CR.ID_LIVRO AND R.ID_USUARIO = CR.ID_USUARIO
        WHERE R.STATUS IN ('Pendente', 'Ativo') AND r.ID_USUARIO = ?;
    """, (id_usuario,))
    if cur.fetchone():
        return jsonify({"message": "Você já tem esse livro reservado."}), 401

    cur.execute("""
            SELECT 1 
            FROM EMPRESTIMOS E
            JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
            JOIN CARRINHO_RESERVAS CR ON I.ID_LIVRO = CR.ID_LIVRO AND E.ID_USUARIO = CR.ID_USUARIO
            WHERE E.STATUS IN ('ATIVO') AND E.ID_USUARIO = ?;
        """, (id_usuario,))
    if cur.fetchone():
        return jsonify({"message": "Você já tem esse livro emprestado."}), 401

    print(id_usuario)
    cur.execute("INSERT INTO RESERVAS (ID_USUARIO) VALUES (?) RETURNING ID_RESERVA;", (id_usuario,))

    reserva_id = cur.fetchone()[0]
    print(reserva_id)

    cur.execute("""
        INSERT INTO ITENS_RESERVA (ID_RESERVA, ID_LIVRO)
        SELECT ?, ID_LIVRO FROM CARRINHO_RESERVAS WHERE ID_USUARIO = ?
    """, (reserva_id, id_usuario))
    cur.execute("SELECT ID_LIVRO FROM ITENS_RESERVA IR WHERE IR.ID_RESERVA IN (SELECT ID_RESERVA FROM RESERVAS R WHERE R.ID_USUARIO = ?)", (id_usuario,))
    livros = cur.fetchall()
    print(f"Livros reservados: {livros}")

    cur.execute("DELETE FROM CARRINHO_RESERVAS WHERE ID_USUARIO = ?", (id_usuario,))

    # Pegar o nome e autor dos livros para usar no email

    # Enviar o email da reserva feita para o usuário
    cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    usuario = cur.fetchone()
    nome = usuario[0]
    email = usuario[1]
    print(f"Nome: {nome}, email: {email}")
    assunto = nome + ", Uma nota de reserva"
    corpo = f"""
    Olá {nome},\n\nSua reserva foi feita com sucesso!
    Livros reservados:
    • 
    """

    con.commit()
    cur.close()

    enviar_email_async(email, assunto, corpo)

    return jsonify({"message": "Reserva confirmada.", "id_reserva": reserva_id})


# Adicionar item ao carrinho de empréstimos
@app.route('/carrinho_emprestimos', methods=['POST'])
def adicionar_carrinho_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]
    data = request.json
    id_livro = data.get("id_livro")

    cur = con.cursor()
    cur.execute("SELECT 1 from carrinho_emprestimos where id_livro = ? and id_usuario = ?", (id_livro, id_usuario))
    if cur.fetchone():
        return jsonify({"message": "Você não pode colocar 2 livros iguais no carrinho."}), 401

    cur.execute("INSERT INTO CARRINHO_EMPRESTIMOS (ID_USUARIO, ID_LIVRO) VALUES (?, ?)", (id_usuario, id_livro))
    con.commit()
    cur.close()
    return jsonify({"message": "Item adicionado ao carrinho de empréstimos."}), 201


# Remover item do carrinho de empréstimos
@app.route('/carrinho_emprestimos/<int:item_id>', methods=['DELETE'])
def remover_carrinho_emprestimo(item_id):
    cur = con.cursor()
    cur.execute("DELETE FROM CARRINHO_EMPRESTIMOS WHERE ID_ITEM = ?", (item_id,))
    con.commit()
    cur.close()
    return jsonify({"message": "Item removido do carrinho de empréstimos."})


# Listar itens do carrinho de empréstimos
@app.route('/carrinho_emprestimos', methods=['GET'])
def listar_carrinho_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_usuario = payload["id_usuario"]

    query = """
        SELECT id_item, id_usuario, id_livro, data_adicionado 
        FROM CARRINHO_EMPRESTIMOS 
        WHERE id_usuario = ? 
        ORDER BY data_adicionado DESC;
    """

    cur = con.cursor()
    cur.execute(query, (id_usuario,))
    catchEmprestimos = cur.fetchall()
    cur.close()

    listaEmprestimos = []

    for e in catchEmprestimos:
        id_livro = e[2]
        livro = buscar_livro_por_id(id_livro)  # Obtém os detalhes do livro

        emprestimo = {
            'id_emprestimo': e[0],
            'id_usuario': e[1],
            'id_livro': e[2],
            'data_adicionado': e[3],
            'imagem': f"{e[2]}.jpeg",
            'livro': livro  # Adiciona os detalhes do livro ao carrinho
        }

        listaEmprestimos.append(emprestimo)

    return jsonify(listaEmprestimos), 200


# Verificar disponibilidade para emprestimo
@app.route('/verificar_emprestimo/<int:livro_id>', methods=['GET'])
def verificar_emprestimo(livro_id):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    cur = con.cursor()
    cur.execute("""
        SELECT QTD_DISPONIVEL, 
            (SELECT COUNT(*) FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS = 'ATIVO') AS total_emprestimos
        FROM ACERVO 
        WHERE ID_LIVRO = ?
    """, (livro_id, livro_id))
    livro = cur.fetchone()

    # Verificar se o usuário já possui algum empréstimo ativo do livro
    cur.execute("SELECT 1 FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS = 'ATIVO' AND E.ID_USUARIO = ?"
                , (livro_id, payload["id_usuario"]))
    ja_tem = True if cur.fetchone() else False

    cur.close()
    if livro and (livro[0] > livro[1]) and not ja_tem:
        return jsonify({"disponivel": True})
    return jsonify({"disponivel": False})


# Confirmar empréstimo
@app.route('/emprestar', methods=['POST'])
def confirmar_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]

    data_devolver = devolucao()

    cur = con.cursor()

    cur.execute("""
            SELECT ID_LIVRO FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?
        """, (id_usuario,))

    if not cur.fetchone():
        return jsonify({"message": "Não há livros no carrinho."}), 404

    cur.execute("""
                SELECT 1 
                FROM EMPRESTIMOS E
                JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
                JOIN CARRINHO_EMPRESTIMOS CE ON I.ID_LIVRO = CE.ID_LIVRO AND E.ID_USUARIO = CE.ID_USUARIO
                WHERE E.STATUS IN ('ATIVO') AND E.ID_USUARIO = ?;
            """, (id_usuario,))
    if cur.fetchone():
        return jsonify({"message": "Você já tem esse livro emprestado."}), 401

    cur.execute("INSERT INTO EMPRESTIMOS (ID_USUARIO, DATA_DEVOLVER) VALUES (?, ?) returning id_emprestimo",
                (id_usuario, data_devolver))
    emprestimo_id = cur.fetchone()[0]
    cur.execute("""
        INSERT INTO ITENS_EMPRESTIMO (ID_EMPRESTIMO, ID_LIVRO) 
        SELECT ?, ID_LIVRO 
        FROM CARRINHO_EMPRESTIMOS 
        WHERE ID_USUARIO = ?
    """,
                (emprestimo_id, id_usuario))
    cur.execute("DELETE FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?", (id_usuario,))
    con.commit()
    cur.close()
    return jsonify({"message": "Empréstimo confirmado.", "data_devolver": data_devolver})
