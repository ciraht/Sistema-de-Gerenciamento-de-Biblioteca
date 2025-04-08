import os
import jwt
import datetime
from flask import jsonify, request, send_file, send_from_directory
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import config
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from fpdf import FPDF
from apscheduler.schedulers.background import BackgroundScheduler
from email.message import EmailMessage

senha_secreta = app.config['SECRET_KEY']

PERIODO_EMPRESTIMO = datetime.timedelta(weeks=2)
data_validade = (datetime.datetime.now() + datetime.timedelta(days=3))


def mudardatavalidade(dataemdias):
    data_validade = (datetime.datetime.now() + datetime.timedelta(days=dataemdias))


def devolucao():
    """Retorna a data de devolução do livro, adicionando o período de empréstimo à data atual."""
    data_devolucao = datetime.datetime.now() + PERIODO_EMPRESTIMO
    return data_devolucao.strftime("%Y-%m-%d")


def agendar_tarefas():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=verificar_multas_e_enviar, trigger='cron', hour=9, minute=0)
    scheduler.start()


def generate_token(user_id):
    payload = {
        "id_usuario": user_id,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
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
        return jsonify({'mensagem': 'Token de autenticação necessário.', "verificacao": verificacao}), 401
    elif verificacao == 2:
        return jsonify({'mensagem': 'Token expirado.', "verificacao": verificacao}), 401
    elif verificacao == 3:
        return jsonify({'mensagem': 'Token inválido.', "verificacao": verificacao}), 401
    elif verificacao == 4:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido.', "verificacao": verificacao}), 401
    elif verificacao == 5:
        return jsonify({'mensagem': 'Nível Administrador requerido.', "verificacao": verificacao}), 401
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
    cur.execute("SELECT COUNT(*) FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
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


def enviar_email_async(destinatario, assunto, corpo):
    def enviar_email(destinatario, assunto, corpo):
        msg = EmailMessage()
        msg['From'] = config.MAIL_USERNAME
        msg['To'] = destinatario
        msg['Subject'] = assunto

        # Texto do e-mail
        mensagem = f"""
\n
{corpo}
\n\n
© 2025 Read Raccoon. Todos os direitos reservados.
                        """

        # Versão texto simples como fallback
        msg.set_content(corpo)
        msg.add_alternative(mensagem)

        try:
            with smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT, timeout=config.MAIL_TIMEOUT) as smtp:
                smtp.starttls()
                smtp.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
                smtp.send_message(msg)
                print("E-mail enviado com sucesso!")
        except Exception as e:
            print(f"Erro ao enviar e-mail: {e}")

    # Correção: passar argumentos corretamente para a thread
    thread = threading.Thread(target=enviar_email, args=(destinatario, assunto, corpo))
    thread.start()


"""
# Rota para testes
@app.route('/enviar_emails', methods=['GET'])
def enviar_emails():
    cur = con.cursor()
    cur.execute("SELECT ID_USUARIO, NOME, EMAIL, SENHA FROM USUARIOS WHERE USUARIOS.EMAIL = 'othaviohma2014@gmail.com'")
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
    verificacao = informar_verificacao(tipo)

    if verificacao is not None:
        return verificacao

    return jsonify({'mensagem': 'Verificação concluída com sucesso.'}), 200


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
        verificacao = informar_verificacao(3)
        if verificacao:
            tipo = int(data.get('tipo'))
        else:
            tipo = 1
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

        # Enviar e-mail de boas-vindas
        assunto = f"Boas-vindas ao Read Raccoon, {nome}!"
        corpo = f"Ler é uma aventura e nós podemos te ajudar a embarcar nela!"
        enviar_email_async(email, assunto, corpo)

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
    return jsonify({"message": "Usuário reativado com sucesso."}), 200


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
            where disponivel = true
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

@app.route('/livrosadm', methods=["GET"])
def get_livros_adm():
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
                a.ANO_PUBLICADO,
                a.disponivel
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
            'imagem': f"{r[0]}.jpeg",
            'disponivel': r[9]
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


@app.route('/alterar_disponibilidade', methods=["PUT"])
def alterar_disponibilidade_livro():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    # Obter JSON da requisição
    data = request.get_json()

    # Garantir que o ID foi enviado
    cur = con.cursor()
    if not data or 'id_livro' not in data:
        cur.close()
        return jsonify({"error": "ID do livro não fornecido."}), 401

    id_livro = data['id_livro']

    # Verificar se o livro existe
    cur.execute("SELECT 1 FROM acervo WHERE ID_livro = ?", (id_livro,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Livro não encontrado."}), 404

    cur.execute("SELECT DISPONIVEL FROM ACERVO WHERE ID_LIVRO = ?", (id_livro,))
    disponivel = cur.fetchone()
    if not disponivel:
        return jsonify({"error": "Não foi possível verificar a disponibilidade do livro."}), 401

    if not disponivel[0]:
        cur.execute("UPDATE ACERVO SET DISPONIVEL = TRUE WHERE ID_livro = ?", (id_livro,))

        con.commit()
        cur.close()
        return jsonify(
            {"message": "Livro marcado como disponível de volta"}), 200

    # Cancelamento de reservas
    cur.execute(
        'SELECT ID_USUARIO FROM RESERVAS WHERE ID_RESERVA IN (SELECT ID_RESERVA FROM ITENS_RESERVA WHERE ID_LIVRO = ?)',
        (id_livro,))
    id_usuario = cur.fetchall()
    print(f"\nUsuários que tiveram suas reservas canceladas: {id_usuario}")

    cur.execute("SELECT ID_RESERVA FROM ITENS_RESERVA WHERE ID_LIVRO = ?", (id_livro,))
    reservas_deletar = cur.fetchall()
    reservas_deletar = [r[0] for r in reservas_deletar]  # Extrai apenas o valor do ID_RESERVA de cada tupla

    if reservas_deletar:
        placeholders = ', '.join('?' for _ in reservas_deletar)
        consulta = f"UPDATE RESERVAS r SET r.STATUS = 'CANCELADA' WHERE r.ID_RESERVA IN ({placeholders})"
        cur.execute(consulta, reservas_deletar)

    # Pegar o nome e autor do livro para usar nos e-mails
    cur.execute("SELECT TITULO, AUTOR FROM ACERVO WHERE ID_LIVRO = ?", (id_livro, ))
    dados = cur.fetchone()
    titulo = dados[0]
    autor = dados[1]

    # Enviar um e-mail para o usuário que possuia reserva
    for usuario in id_usuario[0]:
        cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (usuario,))
        dados = cur.fetchone()
        nome = dados[0]
        email = dados[1]

        assunto = f"{nome}, sua reserva foi cancelada"
        corpo = (
f"""Caro leitor, \n
{titulo}, de {autor}, que fazia parte de sua reserva, foi indisponibilizado por funcionários da biblioteca, sentimos muito por essa inconveniência.""")

        enviar_email_async(email, assunto, corpo)

    # Cancelamento de empréstimos
    cur.execute(
        "SELECT ID_USUARIO FROM EMPRESTIMOS e WHERE e.STATUS = 'ATIVO' AND DATA_DEVOLVIDO IS NULL AND ID_EMPRESTIMO IN (SELECT ID_EMPRESTIMO FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?)",
        (id_livro,))
    id_usuario = cur.fetchall()
    print(f"\nUsuários que tiveram seu empréstimo cancelado: {id_usuario}")

    cur.execute("SELECT ID_EMPRESTIMO FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?", (id_livro,))
    emprestimos_deletar = cur.fetchall()
    emprestimos_deletar = [r[0] for r in emprestimos_deletar]  # Extrai apenas o valor do ID_EMPRESTIMO de cada tupla

    if emprestimos_deletar:
        placeholders = ', '.join('?' for _ in emprestimos_deletar)
        query = f"UPDATE EMPRESTIMOS E SET STATUS = 'CANCELADO' WHERE E.ID_EMPRESTIMO IN ({placeholders})"
        cur.execute(query, emprestimos_deletar)

    # Enviar um e-mail para o usuário que teve o seu empréstimo comprometido
    for usuario in id_usuario[0]:
        cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (usuario, ))
        dados = cur.fetchone()
        nome = dados[0]
        email = dados[1]

        assunto = f"{nome}, seu empréstimo foi cancelado"
        corpo = (
f"""Caro leitor, \n
{titulo}, de {autor}, que fazia parte de seu empréstimo, foi indisponibilizado por funcionários da biblioteca, pedimos que retorne o exemplar o quanto antes.""")

        enviar_email_async(email, assunto, corpo)

    # E finalmente, na lista de livros
    cur.execute("UPDATE ACERVO SET DISPONIVEL = FALSE WHERE ID_livro = ?", (id_livro,))

    con.commit()
    cur.close()

    # Remover imagem do livro
    """
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
    """

    return jsonify({'message': "Livro retirado da biblioteca com sucesso!"}), 200


@app.route('/emprestimos/<int:id>/devolver', methods=["PUT"])
def devolver_emprestimo(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Verificar se o empréstimo existe
    cur.execute("SELECT status FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        return jsonify({"message": "Id de empréstimo não encontrado."}), 404

    status_atual = row[0]
    if status_atual == "DEVOLVIDO":
        cur.close()
        return jsonify({"message": "Empréstimo já devolvido."}), 401

    # Atualizar o empréstimo como DEVOLVIDO
    cur.execute("""
        UPDATE EMPRESTIMOS 
        SET DATA_DEVOLVIDO = CURRENT_DATE, 
            STATUS = 'DEVOLVIDO'
        WHERE ID_EMPRESTIMO = ?
    """, (id,))

    # Descobrir o id_livro do empréstimo devolvido
    cur.execute("""
        SELECT i.id_livro
        FROM itens_emprestimo i
        WHERE i.id_emprestimo = ?
    """, (id,))
    livro_info = cur.fetchone()

    if livro_info:
        id_livro = livro_info[0]

        # Verificar se há reservas pendentes para este livro
        cur.execute("""
            SELECT I.id_reserva
            FROM reservas R
            JOIN ITENS_RESERVA I ON I.ID_RESERVA = R.ID_RESERVA
            WHERE id_livro = ? AND status = 'Pendente'
            ORDER BY data_CRIACAO ASC
        """, (id_livro,))
        reserva_pendente = cur.fetchone()

        # Se houver, atualiza a mais antiga para "EM ESPERA"
        if reserva_pendente:
            id_reserva = reserva_pendente[0]
            cur.execute("""
                UPDATE reservas
                SET status = 'EM ESPERA', data_validade = ?
                WHERE id_reserva = ?
            """, (data_validade, id_reserva))

    con.commit()
    cur.close()

    return jsonify({"message": "Devolução realizada com sucesso."}), 200


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
        AND a.DISPONIVEL = TRUE
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


@app.route("/avaliarlivro/<int:id>", methods=["POST"])
def avaliar_livro(id):
    try:
        verificacao = informar_verificacao()
        if verificacao:
            return verificacao
        payload = informar_verificacao(trazer_pl=True)

        data = request.get_json()
        valor = data.get("valor")
        id_usuario = payload['id_usuario']

        cur = con.cursor()
        cur.execute("SELECT 1 FROM AVALIACOES WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id, id_usuario, ))
        if cur.fetchone():
            # print("editado")
            cur.execute("UPDATE AVALIACOES SET VALOR_TOTAL = ? WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (valor, id, id_usuario, ))
            con.commit()
            cur.close()
            return jsonify({"message": "Avaliado com sucesso! EDITADO"}), 200
        else:
            # print("inserido")
            cur.execute("INSERT INTO AVALIACOES (VALOR_TOTAL, ID_LIVRO, ID_USUARIO) VALUES (?, ?, ?)", (valor, id, id_usuario))
    except Exception as e:
        return jsonify({"error": f"Erro ao editar registro de avaliação: {e}\n Excluir registros de avaliacoes desse livro do banco de dados"}), 500

    return jsonify({
        "message": "Avaliado com sucesso! ADICIONADO"
    }), 200


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
def relatorio_livros_json():
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

    subtitulos = ["id", "titulo", "autor", "categoria", "isbn", "qtd_disponivel", "descricao", "idiomas", "ano_publicado"]

    livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

    return jsonify({
        "total": len(livros_json),
        "livros": livros_json
    })


@app.route('/relatorio/usuarios', methods=['GET'])
def relatorio_usuarios_json():
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

    subtitulos = ["id", "nome", "email", "telefone", "endereco"]
    usuarios_json = [dict(zip(subtitulos, u)) for u in usuarios]

    return jsonify({
        "total": len(usuarios_json),
        "usuarios": usuarios_json
    })


@app.route('/relatorio/gerar/livros', methods=['GET'])
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


@app.route('/relatorio/gerar/usuarios', methods=['GET'])
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


@app.route("/user", methods=["GET"])
def get_self_user():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id = payload["id_usuario"]

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
    else:
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, f"{id_usuario}.jpeg")
        if os.path.exists(imagem_path):
            os.remove(imagem_path)

    # Commit das alterações
    con.commit()

    cur.close()
    return jsonify({"message": "Usuário atualizado com sucesso."}), 200


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
            (SELECT COUNT(*) FROM RESERVAS R INNER JOIN ITENS_RESERVA IR ON R.ID_RESERVA = IR.ID_RESERVA WHERE IR.ID_LIVRO = ? AND R.STATUS IN ('PENDENTE', 'CONFIRMADA')) AS total_reservas,
            (SELECT COUNT(*) FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS = 'ATIVO') AS total_emprestimos
        FROM ACERVO 
        WHERE ID_LIVRO = ?
    """, (livro_id, livro_id, livro_id))
    livro = cur.fetchone()

    # Verificar se o usuário já possui alguma reserva ativa do livro
    cur.execute("""
            SELECT 1 
            FROM RESERVAS R
            JOIN ITENS_RESERVA I ON R.ID_RESERVA = I.ID_RESERVA
            WHERE R.STATUS IN ('PENDENTE', 'EM ESPERA') AND r.ID_USUARIO = ? AND I.ID_LIVRO = ?;
        """, (payload["id_usuario"], livro_id))
    ja_tem_reserva = True if cur.fetchone() else False

    cur.execute("""
                SELECT 1
                FROM EMPRESTIMOS E
                JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
                WHERE E.STATUS IN ('ATIVO') AND I.id_livro = ? and e.id_usuario = ?;
            """, (livro_id, payload["id_usuario"]))
    ja_tem_emprestimo = True if cur.fetchone() else False

    cur.close()

    mensagem = ""

    if ja_tem_emprestimo:
        mensagem = "Você já tem esse livro emprestado"

    if ja_tem_reserva:
        mensagem = "Você já tem esse livro reservado"

    if livro and (livro[2] >= livro[0] > livro[1]) and not ja_tem_reserva and not ja_tem_emprestimo:
        return jsonify({"disponivel": True})
    return jsonify({"mensagem": mensagem, "disponivel": False})


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
        WHERE R.STATUS IN ('PENDENTE', 'ATIVO') AND r.ID_USUARIO = ?;
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

    cur.execute("INSERT INTO RESERVAS (ID_USUARIO) VALUES (?) RETURNING ID_RESERVA;", (id_usuario,))

    reserva_id = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO ITENS_RESERVA (ID_RESERVA, ID_LIVRO)
        SELECT ?, ID_LIVRO FROM CARRINHO_RESERVAS WHERE ID_USUARIO = ?
    """, (reserva_id, id_usuario))

    # Pegar o nome e autor dos livros para usar no email
    cur.execute(
        "SELECT TITULO, AUTOR FROM ACERVO WHERE ID_LIVRO IN (SELECT cr.ID_LIVRO FROM CARRINHO_RESERVAS cr)")
    livros_reservados = cur.fetchall()

    cur.execute("DELETE FROM CARRINHO_RESERVAS WHERE ID_USUARIO = ?", (id_usuario,))


    # Enviar o email da reserva feita para o usuário
    cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    usuario = cur.fetchone()

    nome = usuario[0]
    email = usuario[1]

    assunto = nome + ", uma nota de reserva"
    corpo = f"""
    Você fez uma reserva!
    Livros reservados:\n
                """
    for livro in livros_reservados:
        titulo = livro[0]
        autor = livro[1]
        corpo += (f"\n"
                  f"• {titulo}, por {autor}\n")
    print(f"Corpo formatado: {corpo}")

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
            (SELECT COUNT(*) 
             FROM EMPRESTIMOS E 
             INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO 
             WHERE IE.ID_LIVRO = ? AND E.STATUS = 'ATIVO') AS total_emprestimos
        FROM ACERVO 
        WHERE ID_LIVRO = ?
    """, (livro_id, livro_id))
    livro = cur.fetchone()

    # Verificar se o usuário já possui empréstimo ativo desse livro
    cur.execute("""
        SELECT 1 
        FROM EMPRESTIMOS E
        JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
        WHERE E.STATUS = 'ATIVO' AND E.ID_USUARIO = ? AND I.ID_LIVRO = ?
    """, (payload["id_usuario"], livro_id))
    ja_tem_emprestimo = cur.fetchone() is not None

    cur.close()

    if livro and livro[0] > livro[1] and not ja_tem_emprestimo:
        return jsonify({
            "disponivel": True
        })

    return jsonify({
        "disponivel": False
    })


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

    # Verifica se há livros no carrinho
    cur.execute("""
        SELECT ID_LIVRO FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?
    """, (id_usuario,))
    livros_carrinho = cur.fetchall()

    if not livros_carrinho:
        cur.close()
        return jsonify({"message": "Não há livros no carrinho."}), 404

    ids_livros = [livro[0] for livro in livros_carrinho]

    # Verifica se algum dos livros do carrinho já está emprestado pelo usuário
    cur.execute("""
        SELECT 1 
        FROM EMPRESTIMOS E
        JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
        WHERE E.STATUS = 'ATIVO' AND E.ID_USUARIO = ? AND I.ID_LIVRO IN (
            SELECT ID_LIVRO FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?
        )
    """, (id_usuario, id_usuario))

    if cur.fetchone():
        cur.close()
        return jsonify({"message": "Você já tem pelo menos um desses livros emprestado."}), 401

    # Verifica se algum livro do carrinho tem reserva pendente ou em espera
    cur.execute("""
        SELECT 1 
        FROM CARRINHO_EMPRESTIMOS CE
        WHERE CE.ID_LIVRO IN (SELECT IR.ID_LIVRO FROM ITENS_RESERVA IR
            WHERE IR.ID_RESERVA IN (SELECT R.ID_RESERVA FROM RESERVAS R 
                WHERE STATUS = 'PENDENTE' OR STATUS = 'EM ESPERA')) 
    """, (id_usuario,))

    if cur.fetchone():
        cur.close()
        return jsonify({"message": "Algum dos livros no carrinho está reservado. Empréstimo bloqueado."}), 401

    # Cria o empréstimo
    cur.execute("INSERT INTO EMPRESTIMOS (ID_USUARIO, DATA_DEVOLVER) VALUES (?, ?) returning id_emprestimo",
                (id_usuario, data_devolver))
    emprestimo_id = cur.fetchone()[0]

    # Enviar o e-mail com os livros emprestados
    # Pegar o nome e autor dos livros para usar no email
    cur.execute(
        "SELECT TITULO, AUTOR FROM ACERVO WHERE ID_LIVRO IN (SELECT ce.ID_LIVRO FROM CARRINHO_EMPRESTIMOS ce) ")
    livros_emprestados = cur.fetchall()

    # Adiciona os livros ao empréstimo
    cur.execute("""
        INSERT INTO ITENS_EMPRESTIMO (ID_EMPRESTIMO, ID_LIVRO) 
        SELECT ?, ID_LIVRO 
        FROM CARRINHO_EMPRESTIMOS 
        WHERE ID_USUARIO = ?
    """, (emprestimo_id, id_usuario))

    # Limpa o carrinho
    cur.execute("DELETE FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?", (id_usuario,))
    con.commit()


    # Enviar o e-mail da reserva feita para o usuário
    cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    usuario = cur.fetchone()

    nome = usuario[0]
    email = usuario[1]

    # Convertendo a string para um objeto datetime
    data_objeto = datetime.datetime.strptime(data_devolver, "%Y-%m-%d")

    # Formatando para o formato desejado "dia-mês-ano"
    data_devolver_formatada = data_objeto.strftime("%d-%m-%Y")
    assunto = nome + ", uma nota de empréstimo"
    corpo = f"""
    Você fez um empréstimo!
    Data de devolução: {data_devolver_formatada}
    Livros emprestados:\n
            """
    for livro in livros_emprestados:
        titulo = livro[0]
        autor = livro[1]
        corpo += (f"\n"
                  f"• {titulo}, por {autor}\n")
    print(f"Corpo formatado: {corpo}")

    enviar_email_async(email, assunto, corpo)
    cur.close()

    return jsonify({"message": "Empréstimo confirmado.", "data_devolver": data_devolver})


@app.route('/editar_senha', methods=["PUT"])
def editar_senha():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]
    data = request.get_json()
    nova_senha = data.get("senha")
    senha_confirm = data.get("senhaConfirm")

    if not nova_senha or not senha_confirm:
        return jsonify({"message": "Informe a nova senha e a confirmação."}), 400

    if nova_senha != senha_confirm:
        return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 400

    if len(nova_senha) < 8 or not any(c.isupper() for c in nova_senha) or not any(
            c.islower() for c in nova_senha) or not any(c.isdigit() for c in nova_senha) or not any(
        c in "!@#$%^&*(), -.?\":{}|<>" for c in nova_senha):
        return jsonify({
            "message": "A senha deve conter pelo menos 8 caracteres, uma letra maiúscula, uma letra minúscula, um número e um caractere especial."
        }), 400

    nova_senha_hash = generate_password_hash(nova_senha)
    cur = con.cursor()
    cur.execute("UPDATE usuarios SET senha = ? WHERE id_usuario = ?", (nova_senha_hash, id_usuario))
    con.commit()
    cur.close()

    return jsonify({"message": "Senha alterada com sucesso."}), 200


@app.route('/verificar_senha_antiga', methods=["POST"])
def verificar_senha_antiga():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]
    data = request.get_json()
    senha_antiga = data.get("senhaAntiga")

    if not senha_antiga:
        return jsonify({"message": "Senha antiga não informada."}), 400

    cur = con.cursor()
    cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    resultado = cur.fetchone()
    cur.close()

    if not resultado:
        return jsonify({"message": "Usuário não encontrado."}), 404

    senha_armazenada = resultado[0]

    if check_password_hash(senha_armazenada, senha_antiga):
        return jsonify({"valido": True}), 200
    else:
        return jsonify({"valido": False}), 200


@app.route("/emprestimos", methods=["GET"])
def get_all_emprestimos():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    cur.execute("""
        SELECT 
            E.ID_EMPRESTIMO, 
            E.ID_USUARIO, 
            E.DATA_RETIRADA, 
            E.DATA_DEVOLVER, 
            E.DATA_DEVOLVIDO, 
            E.STATUS, 
            A.ID_LIVRO, 
            A.TITULO
        FROM EMPRESTIMOS E
        JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
        JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
        ORDER BY E.DATA_DEVOLVER DESC
    """)

    rows = cur.fetchall()

    # Agrupar por ID_EMPRESTIMO
    emprestimos_dict = {}

    for row in rows:
        id_emprestimo = row[0]

        if id_emprestimo not in emprestimos_dict:
            emprestimos_dict[id_emprestimo] = {
                "id_emprestimo": row[0],
                "id_usuario": row[1],
                "data_retirada": str(row[2]),
                "data_devolver": str(row[3]),
                "data_devolvido": str(row[4]) if row[4] else None,
                "status": row[5],
                "livros": []
            }

        emprestimos_dict[id_emprestimo]["livros"].append({
            "id_livro": row[6],
            "titulo": row[7]
        })

    # Converter para lista
    emprestimos = list(emprestimos_dict.values())

    return jsonify(emprestimos), 200


@app.route("/puxar_historico/<int:id>", methods=["GET"])
def puxar_historico_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Emprestimos Ativos
    cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id,))
    emprestimos_ativos = cur.fetchall()

    # Emprestimos Concluídos
    cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NOT NULL
            ORDER BY E.DATA_DEVOLVIDO DESC
        """, (id,))
    emprestimos_concluidos = cur.fetchall()

    # Reservas Ativas - Obtendo os livros relacionados às reservas
    cur.execute("""
            SELECT IR.ID_LIVRO, A.TITULO, A.AUTOR, R.ID_RESERVA, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS
            FROM ITENS_RESERVA IR
            JOIN RESERVAS R ON IR.ID_RESERVA = R.ID_RESERVA
            JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
            WHERE R.ID_USUARIO = ?
            ORDER BY R.DATA_VALIDADE ASC
        """, (id,))
    reservas_ativas = cur.fetchall()

    # Multas Pendentes
    cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, (M.VALOR_BASE + M.VALOR_ACRESCIMO) AS TOTAL, M.ID_EMPRESTIMO, M.PAGO
            FROM MULTAS M
            WHERE M.ID_USUARIO = ? AND M.PAGO = 0
            ORDER BY TOTAL DESC
        """, (id,))
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


@app.route("/user/<int:id>", methods=["GET"])
def get_user_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

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


def verificar_multas_e_enviar():
    cur = con.cursor()
    hoje = datetime.datetime.now().date()
    limite = hoje + datetime.timedelta(days=4)

    cur.execute("""
        SELECT u.nome, u.email, a.titulo, e.data_devolver
        FROM emprestimos e
        JOIN usuarios u ON e.id_usuario = u.id_usuario
        JOIN itens_emprestimo i on e.id_emprestimo = i.id_emprestimo
        JOIN acervo a ON i.id_livro = a.id_livro
        WHERE e.status = 'ATIVO' AND e.data_devolver <= ?
    """, (limite,))

    emprestimos = cur.fetchall()

    for nome, email, titulo, data_devolucao in emprestimos:
        data_formatada = data_devolucao.strftime("%d/%m/%Y")
        corpo = f"""
                    Olá {nome},

                    Este é um lembrete de que o livro "{titulo}" deve ser devolvido até o dia {data_formatada}.

                    Evite multas por atraso! Caso já tenha devolvido, desconsidere este aviso.

                    Atenciosamente,
                    Sistema da Biblioteca
                    """
        enviar_email_async(email, "📚 Lembrete: Devolução de Livro", corpo)

    cur.close()


@app.route('/reserva/<int:id_reserva>/atender', methods=["PUT"])
def atender_reserva(id_reserva):
    verificacao = informar_verificacao(2)  # Apenas bibliotecários
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Verifica se a reserva existe e está em espera
    cur.execute("""
        SELECT r.id_usuario, i.id_livro 
        FROM reservas r
        join itens_reserva i on r.id_reserva = i.id_reserva
        WHERE r.id_reserva = ? AND r.status = 'EM ESPERA'
    """, (id_reserva,))
    dados = cur.fetchone()

    if not dados:
        cur.close()
        return jsonify({"message": "Reserva não encontrada ou já foi atendida/cancelada."}), 404

    id_usuario, id_livro = dados
    data_devolver = devolucao()  # Função que calcula a data de devolução, como já usada por você

    # Atualiza status da reserva para ATENDIDA
    cur.execute("""
        UPDATE reservas 
        SET status = 'ATENDIDA'
        WHERE id_reserva = ?
    """, (id_reserva,))

    # Cria novo empréstimo
    cur.execute("""
        INSERT INTO emprestimos (id_usuario, data_devolver, status) 
        VALUES (?, ?, 'ATIVO') 
        RETURNING id_emprestimo
    """, (id_usuario, data_devolver))
    id_emprestimo = cur.fetchone()[0]

    # Associa o livro ao empréstimo
    cur.execute("""
        INSERT INTO itens_emprestimo (id_emprestimo, id_livro) 
        VALUES (?, ?)
    """, (id_emprestimo, id_livro))

    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva atendida e empréstimo registrado com sucesso.",
        "data_devolver": data_devolver
    }), 200
