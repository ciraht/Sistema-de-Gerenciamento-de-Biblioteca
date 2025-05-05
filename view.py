import mimetypes
import os
import jwt
import datetime
from flask import jsonify, request, send_file, send_from_directory
import smtplib
from threading import Thread
import config
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from fpdf import FPDF
from apscheduler.schedulers.background import BackgroundScheduler
from email.message import EmailMessage
from pixqrcode import PixQrCode

senha_secreta = app.config['SECRET_KEY']


def configuracoes():
    cur = con.cursor()
    cur.execute("""
                    SELECT *
                    FROM CONFIGURACOES
                    WHERE ID_REGISTRO = (SELECT MAX(ID_REGISTRO) FROM CONFIGURACOES)
                    """)
    config_mais_recente = cur.fetchone()
    cur.close()
    return config_mais_recente


def devolucao():
    # Retorna a data de devolução do livro, adicionando o período de empréstimo à data atual.
    dias_emprestimo = configuracoes()[1]
    data_devolucao = datetime.datetime.now() + datetime.timedelta(days=dias_emprestimo)
    return data_devolucao


def agendar_tarefas():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=avisar_para_evitar_multas, trigger='cron', hour=8, minute=20)
    scheduler.add_job(func=multar_quem_precisa, trigger='cron', hour=8, minute=20)  # Essa função cria os códigos PIX
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
def buscar_livro_por_id(id, descontar_faltandos=False):
    cur = con.cursor()
    try:
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
        if descontar_faltandos:
            cur.execute("""
            SELECT COUNT(*) FROM ITENS_EMPRESTIMO IE
            WHERE IE.ID_LIVRO = ? 
            AND IE.ID_EMPRESTIMO IN (SELECT E.ID_EMPRESTIMO FROM EMPRESTIMOS E WHERE E.STATUS IN ('PENDENTE', 'ATIVO'))
            """, (id,))
            contagem_emp = cur.fetchone()
            contagem_emp = 0 if not contagem_emp else contagem_emp[0]
            cur.execute("""
            SELECT COUNT(*) FROM ITENS_RESERVA IE
            WHERE IE.ID_LIVRO = ? 
            AND IE.ID_RESERVA IN (SELECT E.ID_RESERVA FROM RESERVAS E WHERE E.STATUS IN ('EM ESPERA', 'PENDENTE'))
            """, (id,))
            contagem_res = cur.fetchone()
            contagem_res = 0 if not contagem_res else contagem_res[0]
            contagem = contagem_emp + contagem_res
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

        cur.execute("SELECT SUM(VALOR_TOTAL) FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
        valor_total = cur.fetchone()

        cur.execute("SELECT COUNT(*) FROM AVALIACOES WHERE ID_LIVRO = ?", (id,))
        qtd = cur.fetchone()

        if valor_total and valor_total[0] is not None and qtd and qtd[0] != 0:
            avaliacoes = round((valor_total[0] / qtd[0]), 2)
        else:
            avaliacoes = 0.00

        if descontar_faltandos:
            return {
                "id": livro[0],
                "titulo": livro[1],
                "autor": livro[2],
                "categoria": livro[3],
                "isbn": livro[4],
                "qtd_disponivel": livro[5] - contagem,
                "descricao": livro[6],
                "idiomas": livro[7],
                "ano_publicado": livro[8],
                "imagem": f"{livro[0]}.jpeg",
                "selectedTags": selected_tags,
                "avaliacao": avaliacoes
            }
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

    except Exception:
        print("Erro ao buscar livro por id")
        raise
    finally:
        cur.close()


def criar_notificacao(id_usuario, mensagem, titulo):
    cur = con.cursor()
    try:
        cur.execute("INSERT INTO NOTIFICACOES (ID_USUARIO, MENSAGEM, TITULO) VALUES (?, ?, ?)",
                    (id_usuario, mensagem, titulo,))  # "FALSE" é o valor padrão de LIDA
    except Exception:
        raise
    finally:
        cur.close()


def enviar_email_async(destinatario, assunto, corpo, qr_code=None):
    def enviar_email(destinatario, assunto, corpo, qr_code=None):
        msg = EmailMessage()
        msg['From'] = config.MAIL_USERNAME
        msg['To'] = destinatario
        msg['Subject'] = assunto

        # Corpo em HTML
        html = f"""<!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{assunto}</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #f2f4f8; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
            <div style="max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 10px; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1); overflow: hidden;">
                <div style="background-color: #1a73e8; color: white; padding: 24px 32px; text-align: center;">
                    <h1 style="margin: 0; font-size: 26px;">{assunto}</h1>
                </div>
                <div style="padding: 32px; color: #333;">
                    <p style="font-size: 18px; line-height: 1.6;">{corpo}</p>
                </div>
                <div style="background-color: #f1f1f1; padding: 20px; text-align: center; font-size: 12px; color: #888;">
                    © 2025 Read Raccoon. Todos os direitos reservados.<br>
                    Este é um e-mail automático, por favor, não responda.
                </div>
            </div>
        </body>
        </html>"""

        msg.set_content(corpo)
        msg.add_alternative(html, subtype='html')

        # Adicionando o qr_code como anexo, caso houver um na mensagem
        if qr_code:
            caminho_arquivo = f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{qr_code}"
            # Detecta o tipo MIME do arquivo
            tipo_mime, _ = mimetypes.guess_type(caminho_arquivo)
            tipo_principal, subtipo = tipo_mime.split('/')

            with open(caminho_arquivo, 'rb') as arquivo:  # rb = read binary
                msg.add_attachment(arquivo.read(),
                                   maintype=tipo_principal,
                                   subtype=subtipo,
                                   filename='qr_code.png')

        try:
            server = smtplib.SMTP_SSL(config.MAIL_SERVER, 465)  # Usando SMTP_SSL para criptografia imediata
            server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"Mensagem enviada com sucesso para {destinatario}")
        except Exception as e:
            print(f"Erro ao enviar e-mail: {e} \nTrazendo mensagem de erro completa")
            raise

    Thread(target=enviar_email, args=(destinatario, assunto, corpo, qr_code), daemon=True).start()


# Rota para testes de e-mail
@app.route('/email_teste', methods=['GET'])
def enviar_emails():
    cur = con.cursor()
    EMAIL_PARA_RECEBER_RESULTADOS = 'othaviohma2014@gmail.com'
    cur.execute(
        f"SELECT ID_USUARIO, NOME, EMAIL, SENHA FROM USUARIOS WHERE USUARIOS.EMAIL = '{EMAIL_PARA_RECEBER_RESULTADOS}'")
    try:
        avisar_para_evitar_multas()
        multar_quem_precisa()
        """
        usuario = cur.fetchone()
        cur.close()

        # COISAS DE QR CODE DE PIX
        nome = usuario[1]
        email = usuario[2]
        valor = 251  # Isso é R$ 2,51
        pix = PixQrCode("Teste", "tharictalon@gmail.com", "Birigui", "100")
        print(f"Esse pix é válido: {pix.is_valid()}")

        # Guardar imagem na aplicação para que o e-mail a pegue depois e use como anexo
        if not os.path.exists(f"{app.config['UPLOAD_FOLDER']}/codigos-pix"):
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "codigos-pix")
            os.makedirs(pasta_destino, exist_ok=True)

        # Verificando se já tem uma imagem para esse valor
        if not os.path.exists(f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{str(valor)}.png"):
            pix.save_qrcode(filename=f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{str(valor)}")
            print("Novo quick response code de pix criado")

        print(f"Nome: {nome}, email: {email}")
        assunto = 'Olá, ' + nome
        corpo = f'Olá {nome}, Este é um e-mail de exemplo enviado via Flask. Aqui está um QR Code para pagamento Pix nos anexos'
        enviar_email_async(email, assunto, corpo, f"{valor}.png")
        """

        return jsonify({"message": "E-mail teste enviado com sucesso!"})
    except Exception as e:
        return jsonify({"error": e})
    finally:
        cur.close()


@app.route('/configuracoes', methods=["GET"])
def trazer_configuracoes():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    data = request.get_json()
    todas = data.get('todas')  # True ou False, ou nada

    try:
        cur = con.cursor()
        if not todas:
            cur.execute("""
                    SELECT *
                    FROM CONFIGURACOES
                    WHERE ID_REGISTRO = (SELECT MAX(ID_REGISTRO) FROM CONFIGURACOES)
                    """)
            config_mais_recente = cur.fetchone()
            return jsonify({'configuracoes_mais_recentes': config_mais_recente}), 200

        cur.execute("SELECT * FROM CONFIGURACOES")
        configuracoes = cur.fetchall()
        return jsonify({'configuracoes': configuracoes}), 200
    except Exception:
        raise
    finally:
        cur.close()


@app.route('/configuracoes/criar/', methods=["POST"])
def criar_verificacoes():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    data = request.get_json()
    dias_emp = data.get('dias_validade_emprestimo')
    dias_emp_b = data.get('dias_validade_buscar')
    dias_res = data.get('dias_validade_reserva')
    dias_res_a = data.get('dias_validade_reserva_atender')

    if not all([dias_emp, dias_emp_b, dias_res, dias_res_a]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 401

    cur = con.cursor()
    cur.execute("""
                INSERT INTO CONFIGURACOES (DIAS_VALIDADE_EMPRESTIMO, 
                DIAS_VALIDADE_EMPRESTIMO_BUSCAR, 
                DIAS_VALIDADE_RESERVA_ATENDER, 
                DIAS_VALIDADE_RESERVA)
                VALUES (?, ?, ?, ?)
                """, (dias_emp, dias_emp_b, dias_res_a, dias_res))
    con.commit()
    cur.close()
    return jsonify({"message": "Novas configurações adicionadas com sucesso"}), 200


@app.route('/tem_permissao/<int:tipo>', methods=["GET"])
def verificar(tipo):
    verificacao = informar_verificacao(tipo)

    if verificacao is not None:
        return verificacao

    return jsonify({'mensagem': 'Verificação concluída com sucesso.'}), 200


@app.route('/notificacoes/ler/<int:id_notificacao>', methods=["PUT"])
def ler_notificacao(id_notificacao):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    cur = con.cursor()
    try:

        id_usuario = informar_verificacao(trazer_pl=True)['id_usuario']
        cur.execute("UPDATE NOTIFICACOES SET LIDA = TRUE WHERE ID_NOTIFICACAO = ? AND ID_USUARIO = ?",
                    (id_notificacao, id_usuario,))
        con.commit()
        return jsonify({"message": "Notificação lida com sucesso"}), 200
    except Exception:
        raise
    finally:
        cur.close()


@app.route('/notificacoes', methods=["GET"])
def trazer_notificacoes():
    verificacao = informar_verificacao(trazer_pl=True)
    if not verificacao or 'id_usuario' not in verificacao:
        return jsonify({"erro": "Usuário não autorizado"}), 401

    id_usuario = verificacao['id_usuario']

    try:
        cur = con.cursor()
        cur.execute("""
            SELECT ID_NOTIFICACAO, TITULO, MENSAGEM, LIDA, DATA_ADICIONADA
            FROM NOTIFICACOES
            WHERE ID_USUARIO = ?
        """, (id_usuario,))
        linhas = cur.fetchall()
    except Exception as e:
        return jsonify({"erro": "Erro ao buscar notificações", "detalhes": str(e)}), 500
        raise
    finally:
        cur.close()

    colunas = ['ID_NOTIFICACAO', 'TITULO', 'MENSAGEM', 'LIDA', 'DATA_ADICIONADA']
    notificacoes = [dict(zip(colunas, linha)) for linha in linhas]

    return jsonify({"notificacoes": notificacoes})


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
        corpo = """
        <p style="font-size: 18px; line-height: 1.6; color: #333;">
            Ler é uma <strong style="color: #1a73e8;">aventura</strong> e nós podemos te ajudar a embarcar nela!
        </p>

        <p style="font-size: 16px; line-height: 1.5; color: #444; margin-top: 24px;">
            Com milhares de títulos esperando por você, sua próxima jornada começa agora. <br />
            Explore novos mundos, descubra autores incríveis e transforme o hábito da leitura em parte da sua rotina.
        </p>

        <p style="font-size: 16px; line-height: 1.5; color: #444; margin-top: 24px;">
            Acesse sua biblioteca, escolha um livro e deixe a imaginação te levar.
        </p>

        <div style="text-align: center; margin-top: 32px;">
            <a href="http://localhost:5173/" target="_blank" 
               style="background-color: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                Acessar Biblioteca
            </a>
        </div>
        """

        enviar_email_async(email, assunto, corpo)

        # Criar notificação de boas-vindas
        criar_notificacao(id_usuario, "Boas-vindas ao Read Raccoon!", "Seja Bem-vindo(a)!")

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


@app.route('/livros/10dasemana', methods=["GET"])
def dez_da_semana():
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT 
                a.ID_LIVRO,
                a.TITULO,
                a.AUTOR,
                a.CATEGORIA,
                a.ISBN,
                a.QTD_DISPONIVEL,
                a.DESCRICAO,
                a.IDIOMAS,
                a.ANO_PUBLICADO
            FROM ACERVO a
            LEFT JOIN ITENS_EMPRESTIMO ie ON ie.id_livro = a.id_livro
            LEFT JOIN ITENS_RESERVA ir ON ir.ID_LIVRO = a.ID_LIVRO
            LEFT JOIN EMPRESTIMOS e ON e.ID_EMPRESTIMO = ie.ID_EMPRESTIMO
            LEFT JOIN RESERVAS r ON r.ID_RESERVA = ir.ID_RESERVA
            WHERE a.DISPONIVEL = TRUE
            AND	CAST(e.DATA_CRIACAO AS DATE) >= CURRENT_DATE - 7
              OR CAST(r.DATA_CRIACAO AS DATE) >= CURRENT_DATE - 7
            GROUP BY 
    a.ID_LIVRO,
    a.TITULO,
    a.AUTOR,
    a.CATEGORIA,
    a.ISBN,
    a.QTD_DISPONIVEL,
    a.DESCRICAO,
    a.IDIOMAS,
    a.ANO_PUBLICADO
            ORDER BY (COUNT(ie.id_livro) + COUNT(ir.id_livro)) DESC
            ROWS 1 TO 10
            """)
        livrostop = cur.fetchall()
        print(livrostop)
        if livrostop:
            livros = []
            for r in livrostop:
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

        else:
            return jsonify({"message": "Nenhum livro aqui por enquanto :("}), 200
    except Exception:
        print("Erro ao trazer os 10 da semana")
        raise
    finally:
        cur.close()


# @app.route('/livros/recomendados', methods=["GET"])
# def recomendar():
#     verificacao = informar_verificacao()
#     if verificacao:
#         return
#     cur = con.cursor()
#     try:
#         # Olhar as tags dos livros que o usuário avaliou acima de 3.5
#         cur.execute("""
#         SELECT t.ID_TAG, COUNT(*) AS total_aparicoes
#         FROM ACERVO ac
#         INNER JOIN AVALIACOES a ON a.ID_LIVRO = ac.ID_LIVRO
#         LEFT JOIN LIVRO_TAGS lt ON lt.ID_LIVRO = a.ID_LIVRO
#         LEFT JOIN TAGS t ON t.ID_TAG = lt.ID_TAG
#         WHERE a.ID_USUARIO = ?
#           AND a.VALOR_TOTAL >= 3.5
#         GROUP BY t.ID_TAG
#         ORDER BY total_aparicoes DESC
#         """)
#         tags = cur.fetchall()
#         if not tags:
#             cur.execute("")
#     except Exception:
#         print('Erro ao recomendar')
#         raise
#     finally:
#         cur.close()


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
    qtd_disponivel = int(qtd_disponivel)
    cur = con.cursor()

    # Verificando se a ISBN já está cadastrada
    cur.execute("SELECT 1 FROM acervo WHERE isbn = ?", (isbn,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "ISBN já cadastrada."}), 404

    if qtd_disponivel < 1:
        return jsonify({"message": "A quantidade disponível não pode ser menor que 1"}), 401

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
    qtd_disponivel = int(qtd_disponivel)

    print(data)
    print(tags)

    imagem = request.files.get("imagem")
    # Verificando se tem todos os dados
    if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado]):
        cur.close()
        return jsonify({"message": "Todos os campos são obrigatórios."}), 401

    if qtd_disponivel < 1:
        return jsonify({"message": "A quantidade disponível não pode ser menor que 1"}), 401

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
    cur.execute("SELECT TITULO, AUTOR FROM ACERVO WHERE ID_LIVRO = ?", (id_livro,))
    dados = cur.fetchone()
    titulo = dados[0]
    autor = dados[1]

    # Enviar um e-mail para o usuário que possuia reserva
    if id_usuario:
        for usuario in id_usuario[0]:
            cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (usuario,))
            dados = cur.fetchone()
            nome = dados[0]
            email = dados[1]

            assunto = f"{nome}, sua reserva foi cancelada"
            corpo = f"""
            <p style="font-size: 18px; line-height: 1.6; color: #333;">
                Caro leitor,
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Informamos que o livro <strong style="color: #1a73e8;">"{titulo}"</strong>, de <em>{autor}</em>, que estava reservado em seu nome, <strong>foi indisponibilizado</strong> por nossa equipe da biblioteca.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Sentimos muito pelo transtorno causado. Nossa equipe está constantemente trabalhando para melhorar a experiência dos leitores, e em breve novos exemplares estarão disponíveis.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Você pode explorar outros títulos disponíveis acessando sua conta no sistema.
            </p>

            <div style="text-align: center; margin-top: 32px;">
                <a href="http://localhost:5173/" target="_blank" 
                   style="background-color: #1a73e8; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Ver outros livros
                </a>
            </div>
            """

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
    if id_usuario:
        for usuario in id_usuario[0]:
            cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (usuario,))
            dados = cur.fetchone()
            nome = dados[0]
            email = dados[1]

            assunto = f"{nome}, seu empréstimo foi cancelado"
            corpo = f"""
            <p style="font-size: 18px; line-height: 1.6; color: #333;">
                Caro leitor,
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Informamos que o exemplar do livro <strong style="color: #1a73e8;">"{titulo}"</strong>, de <em>{autor}</em>, que se encontra atualmente emprestado em seu nome, <strong>foi marcado como indisponível</strong> por nossa equipe da biblioteca.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Solicitamos, por gentileza, que o exemplar seja <strong>devolvido o quanto antes</strong>, para que possamos regularizar a situação e garantir a disponibilidade para outros leitores.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Agradecemos sua compreensão e colaboração.
            </p>

            <div style="text-align: center; margin-top: 32px;">
                <a href="http://localhost:5173/" target="_blank"
                   style="background-color: #d93025; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
                    Devolver exemplar
                </a>
            </div>
            """
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
        SET DATA_DEVOLVIDO = CURRENT_TIMESTAMP, 
            STATUS = 'DEVOLVIDO'
        WHERE ID_EMPRESTIMO = ?
    """, (id,))

    # Descobrir os id_livro do empréstimo devolvido
    cur.execute("""
        SELECT i.id_livro
        FROM itens_emprestimo i
        WHERE i.id_emprestimo = ?
    """, (id,))
    livros = cur.fetchall()

    if livros:
        for livro in livros:
            id_livro = livro[0]

            # Verificar se há reservas pendentes para este livro
            cur.execute("""
                SELECT I.id_reserva
                FROM reservas R
                JOIN ITENS_RESERVA I ON I.ID_RESERVA = R.ID_RESERVA
                WHERE I.id_livro = ? AND R.status = 'PENDENTE'
                ORDER BY data_CRIACAO ASC
            """, (id_livro,))
            reserva_pendente = cur.fetchone()

            data_validade = devolucao()
            data_validade_format = data_validade.strftime('%Y-%m-%d %H:%M:%S')

            # Se houver, atualiza a mais antiga para "EM ESPERA"
            if reserva_pendente:
                id_reserva = reserva_pendente[0]
                cur.execute("""
                    UPDATE reservas
                    SET status = 'EM ESPERA', data_validade = ?
                    WHERE id_reserva = ?
                """, (data_validade, id_reserva))

                # Enviando e-mail e notificação para a pessoa que teve a sua reserva editada
                cur.execute("""
                    SELECT ID_USUARIO, NOME, EMAIL FROM USUARIOS 
                    WHERE ID_USUARIO IN (SELECT ID_USUARIO FROM RESERVAS WHERE ID_RESERVA = ?)
                """, (id_reserva,))
                usuario = cur.fetchone()

                cur.execute("""
                    SELECT TITULO, AUTOR FROM ACERVO a
                    INNER JOIN ITENS_RESERVA ir ON a.ID_LIVRO = ir.ID_LIVRO
                    WHERE ir.ID_LIVRO IN (SELECT ID_LIVRO FROM ITENS_RESERVA ir WHERE ir.ID_RESERVA = ?)
                    """, (id_reserva,))
                livros_reservados = cur.fetchall()

                mensagem_notificacao = 'Uma reserva sua foi alterada para "em espera", venha para a biblioteca para ser atendido.'
                criar_notificacao(usuario[0], mensagem_notificacao, "Aviso de Reserva")

                corpo = f"""
                        <p>Uma reserva sua agora está em espera!</p>
                        <p><strong>Livros que você está tentando reservar:</strong></p>
                        <ul style="padding-left: 20px; font-size: 16px;">
                        """
                for titulo, autor in livros_reservados:
                    corpo += f"<li>{titulo}, por {autor}</li>"
                corpo += "</ul><p>Agora, vá até a biblioteca para realizar o empréstimo e retirar os livros (ou cancelar).</p>"
                enviar_email_async(usuario[2], "Aviso de reserva", corpo)

    # Verificar se este empréstimo possui multas criadas pela função multar_quem_precisa e enviar e-mail para a pessoa
    cur.execute("""
        SELECT U.ID_USUARIO, U.NOME, U.EMAIL, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.DATA_ADICIONADO FROM USUARIOS U
        JOIN EMPRESTIMOS E ON E.ID_USUARIO = U.ID_USUARIO
        INNER JOIN MULTAS M ON M.ID_EMPRESTIMO =  E.ID_EMPRESTIMO
        WHERE M.PAGO = FALSE AND M.ID_EMPRESTIMO = ?
    """, (id, ))

    tangao = cur.fetchone()

    if tangao:
        data_add = tangao[5]

        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        dias_passados = (data_atual - data_add).days
        print(dias_passados)

        # Pegando valores
        cur.execute("""SELECT VALOR_BASE, VALOR_ACRESCIMO
                    FROM VALORES
                    WHERE ID_VALOR = (SELECT MAX(ID_VALOR) FROM VALORES)
                """)

        valores = cur.fetchone()
        print(f"Valores: {valores}, valor[0]: {valores[0]}, valor[1]: {valores[1]}")

        valor_base = valores[0]
        valor_ac = valores[1]

        valor = valor_base + valor_ac * dias_passados
        valor2 = valor
        valor2 = str(valor2)
        valor2.replace('.', ', ')
        print(f"Valor2: {valor2}")
        # print(f"Valor antes da formatação: {valor}")
        valor = str(valor)
        # print(f"Valor string: {valor}")
        valor = valor.replace('.', '')
        # print(f"Valor depois da formatação: {valor}")
        valor = int(valor)

        print(valor)

        nome = tangao[1]
        email = tangao[2]

        # Gerando código de pix para enviar para o e-mail de quem tem multa
        pix = PixQrCode("Read Raccoon", "tharictalon@gmail.com", "Birigui", str(valor))

        # Guardar imagem na aplicação para que o e-mail a pegue depois e use como anexo
        if not os.path.exists(f"{app.config['UPLOAD_FOLDER']}/codigos-pix"):
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "codigos-pix")
            os.makedirs(pasta_destino, exist_ok=True)

        # Verificando se já tem uma imagem para esse valor
        if not os.path.exists(f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{str(valor)}.png"):
            pix.save_qrcode(filename=f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{str(valor)}")
            # print("Novo quick response code de pix criado")

        assunto = f'Aviso de multa'
        corpo = f"""
                    Olá {nome}, você possui uma multa por não entregar um empréstimo a tempo. 
                    O valor é de R$ {valor2}.
                """
        enviar_email_async(email, assunto, corpo, f"{valor}.png")
        criar_notificacao(tangao[0], 'Você possui uma multa por entregar um empréstimo com atraso.', 'Aviso de Multa')

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


@app.route('/reserva/<int:id_reserva>/cancelar', methods=["PUT"])
def deletar_reservas(id_reserva):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

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
    cur.execute("UPDATE reservas SET STATUS = 'CANCELADA' WHERE id_reserva = ?", (id_reserva,))
    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva cancelada com sucesso."
    }), 200


@app.route('/livros/pesquisa', methods=["POST"])
def pesquisar():
    data = request.get_json()
    pesquisa = data.get("pesquisa", "").strip()
    filtros = data.get("filtros", {})

    if not pesquisa and not filtros:
        return jsonify({"message": "Nada pesquisado."}), 400

    cur = con.cursor()

    sql = """
        SELECT DISTINCT a.id_livro, a.titulo, a.autor, a.categoria, a.isbn,
                        a.qtd_disponivel, a.descricao
        FROM acervo a
        LEFT JOIN livro_tags lt ON a.id_livro = lt.id_livro
        LEFT JOIN tags t ON lt.id_tag = t.id_tag
        WHERE a.disponivel = TRUE
    """

    conditions = []
    params = []

    if pesquisa:
        conditions.append("(a.titulo CONTAINING ? OR a.autor CONTAINING ? OR a.categoria CONTAINING ?)")
        params.extend([pesquisa] * 3)

    if filtros.get("autor"):
        conditions.append("a.autor CONTAINING ?")
        params.append(filtros["autor"])

    ano = filtros.get("ano_publicacao")
    if ano and ano.isdigit() and len(ano) == 4:
        conditions.append("a.ano_publicado = ?")
        params.append(int(ano))

    isbn = filtros.get("isbn")
    if isbn:
        conditions.append("a.isbn CONTAINING ?")
        params.append(isbn)

    if filtros.get("categoria"):
        conditions.append("a.categoria CONTAINING ?")
        params.append(filtros["categoria"])

    if filtros.get("idioma"):
        conditions.append("a.idiomas CONTAINING ?")
        params.append(filtros["idioma"])

    if filtros.get("tags"):
        tag_list = filtros["tags"]
        if isinstance(tag_list, list) and tag_list:
            for tag in tag_list:
                conditions.append(
                    "EXISTS (SELECT 1 FROM livro_tags lt2 JOIN tags t2 ON lt2.id_tag = t2.id_tag WHERE lt2.id_livro = a.id_livro AND t2.nome_tag CONTAINING ?)")
                params.append(tag)
    if conditions:
        sql += " AND " + " AND ".join(conditions)

    sql += " ORDER BY a.titulo"

    cur.execute(sql, params)
    resultados = cur.fetchall()
    cur.close()

    if not resultados:
        return jsonify({"message": "Nenhum resultado encontrado."}), 404

    return jsonify({
        "message": "Pesquisa realizada com sucesso.",
        "resultados": [{
            "id": r[0],
            "titulo": r[1],
            "autor": r[2],
            "categoria": r[3],
            "isbn": r[4],
            "qtd_disponivel": r[5],
            "descricao": r[6],
            "imagem": f"{r[0]}.jpeg"
        } for r in resultados]
    }), 200


@app.route('/livros/pesquisa/<int:pagina>', methods=["POST"])
def pesquisar_livros(pagina):
    data = request.get_json()
    pesquisa = data.get("pesquisa", "").strip()
    filtros = data.get("filtros", {})

    pagina = 1 if not pagina else pagina

    if not pesquisa and not filtros:
        return jsonify({"message": "Nada pesquisado."}), 400

    cur = con.cursor()

    sql = """
        SELECT DISTINCT a.id_livro, a.titulo, a.autor, a.categoria, a.isbn,
                        a.qtd_disponivel, a.descricao
        FROM acervo a
        LEFT JOIN livro_tags lt ON a.id_livro = lt.id_livro
        LEFT JOIN tags t ON lt.id_tag = t.id_tag
        WHERE a.disponivel = TRUE
    """

    conditions = []
    params = []

    if pesquisa:
        conditions.append("(a.titulo CONTAINING ? OR a.autor CONTAINING ? OR a.categoria CONTAINING ?)")
        params.extend([pesquisa] * 3)

    if filtros.get("autor"):
        conditions.append("a.autor CONTAINING ?")
        params.append(filtros["autor"])

    ano = filtros.get("ano_publicacao")
    if ano and ano.isdigit() and len(ano) == 4:
        conditions.append("a.ano_publicado = ?")
        params.append(int(ano))

    isbn = filtros.get("isbn")
    if isbn:
        conditions.append("a.isbn CONTAINING ?")
        params.append(isbn)

    if filtros.get("categoria"):
        conditions.append("a.categoria CONTAINING ?")
        params.append(filtros["categoria"])

    if filtros.get("idioma"):
        conditions.append("a.idiomas CONTAINING ?")
        params.append(filtros["idioma"])

    if filtros.get("tags"):
        tag_list = filtros["tags"]
        if isinstance(tag_list, list) and tag_list:
            for tag in tag_list:
                conditions.append(
                    "EXISTS (SELECT 1 FROM livro_tags lt2 JOIN tags t2 ON lt2.id_tag = t2.id_tag WHERE lt2.id_livro = a.id_livro AND t2.nome_tag CONTAINING ?)")
                params.append(tag)
    if conditions:
        sql += " AND " + " AND ".join(conditions)

    inicial = pagina*10 - 9
    # print(f'ROWS {inicial} to {pagina*10}')

    sql += f" ORDER BY a.titulo ROWS {inicial} TO {pagina*10}"

    cur.execute(sql, params)
    resultados = cur.fetchall()
    cur.close()

    if not resultados:
        return jsonify({"message": "Nenhum resultado encontrado."}), 404

    return jsonify({
        "message": "Pesquisa realizada com sucesso.",
        "resultados": [{
            "id": r[0],
            "titulo": r[1],
            "autor": r[2],
            "categoria": r[3],
            "isbn": r[4],
            "qtd_disponivel": r[5],
            "descricao": r[6],
            "imagem": f"{r[0]}.jpeg"
        } for r in resultados]
    }), 200


@app.route('/livros/pesquisa/gerenciar', methods=["POST"])
def pesquisar_livros_biblio():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    data = request.get_json()
    pesquisa = data.get("pesquisa", "").strip()
    filtros = data.get("filtros", {})

    if not pesquisa and not filtros:
        return jsonify({"message": "Nada pesquisado."}), 400

    cur = con.cursor()

    sql = """
        SELECT DISTINCT a.id_livro, a.titulo, a.autor, a.categoria, a.isbn,
                        a.qtd_disponivel, a.descricao
        FROM acervo a
        LEFT JOIN livro_tags lt ON a.id_livro = lt.id_livro
        LEFT JOIN tags t ON lt.id_tag = t.id_tag
        WHERE 
    """

    conditions = []
    params = []

    if pesquisa:
        conditions.append("(a.titulo CONTAINING ? OR a.autor CONTAINING ? OR a.categoria CONTAINING ?)")
        params.extend([pesquisa] * 3)

    if filtros.get("autor"):
        conditions.append("a.autor CONTAINING ?")
        params.append(filtros["autor"])

    ano = filtros.get("ano_publicacao")
    if ano and ano.isdigit() and len(ano) == 4:
        conditions.append("a.ano_publicado = ?")
        params.append(int(ano))

    isbn = filtros.get("isbn")
    if isbn:
        conditions.append("a.isbn CONTAINING ?")
        params.append(isbn)

    if filtros.get("categoria"):
        conditions.append("a.categoria CONTAINING ?")
        params.append(filtros["categoria"])

    if filtros.get("idioma"):
        conditions.append("a.idiomas CONTAINING ?")
        params.append(filtros["idioma"])

    if filtros.get("tags"):
        tag_list = filtros["tags"]
        if isinstance(tag_list, list) and tag_list:
            for tag in tag_list:
                conditions.append(
                    "EXISTS (SELECT 1 FROM livro_tags lt2 JOIN tags t2 ON lt2.id_tag = t2.id_tag WHERE lt2.id_livro = a.id_livro AND t2.nome_tag CONTAINING ?)")
                params.append(tag)
    if conditions:
        sql += " AND ".join(conditions)

    sql += " ORDER BY a.titulo"

    cur.execute(sql, params)
    resultados = cur.fetchall()
    cur.close()

    if not resultados:
        return jsonify({"message": "Nenhum resultado encontrado."}), 404

    return jsonify({
        "message": "Pesquisa realizada com sucesso.",
        "resultados": [{
            "id": r[0],
            "titulo": r[1],
            "autor": r[2],
            "categoria": r[3],
            "isbn": r[4],
            "qtd_disponivel": r[5],
            "descricao": r[6],
            "imagem": f"{r[0]}.jpeg"
        } for r in resultados]
    }), 200


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
        cur.execute("SELECT 1 FROM AVALIACOES WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id, id_usuario,))
        if cur.fetchone():
            # print("editado")
            cur.execute("UPDATE AVALIACOES SET VALOR_TOTAL = ? WHERE ID_LIVRO = ? AND ID_USUARIO = ?",
                        (valor, id, id_usuario,))
            con.commit()
            cur.close()
            return jsonify({"message": "Avaliado com sucesso! EDITADO"}), 200
        else:
            # print("inserido")
            cur.execute("INSERT INTO AVALIACOES (VALOR_TOTAL, ID_LIVRO, ID_USUARIO) VALUES (?, ?, ?)",
                        (valor, id, id_usuario))
    except Exception as e:
        return jsonify({
            "error": f"Erro ao editar registro de avaliação: {e}\n Excluir registros de avaliacoes desse livro do banco de dados"}), 500

    return jsonify({
        "message": "Avaliado com sucesso! ADICIONADO"
    }), 200


@app.route("/livros/<int:id>", methods=["GET"])
def get_livros_id(id):
    livro = buscar_livro_por_id(id, True)
    print(livro)
    if not livro:
        return jsonify({"error": "Livro não encontrado."}), 404
    return jsonify(livro)


@app.route('/relatorio/multaspendentes', methods=['GET'])
def relatorio_multas_pendentes_json():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
                    SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver
                    FROM emprestimos e
                    JOIN usuarios u ON e.id_usuario = u.id_usuario
                    JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
                    WHERE e.status = 'ATIVO' AND e.data_devolver < CURRENT_DATE
                    AND pago = false
                    ORDER BY m.DATA_ADICIONADO
                    """)

    multas_pendentes = cur.fetchall()

    cur.close()

    # subtitulos = ["id", "titulo", "autor", "categoria", "isbn", "qtd_disponivel", "descricao", "idiomas",
    # "ano_publicado"]

    # livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

    return jsonify({
        "total": len(multas_pendentes),
        "multas_pendentes": multas_pendentes
    })


@app.route('/relatorio/multas', methods=['GET'])
def relatorio_multas_json():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
                    SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver
                    FROM emprestimos e
                    JOIN usuarios u ON e.id_usuario = u.id_usuario
                    JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
                    WHERE e.data_devolver < CURRENT_DATE
                    ORDER BY m.DATA_ADICIONADO
                    """)
    multas = cur.fetchall()

    cur.close()

    return jsonify({
        "multas": multas
    })


@app.route('/relatorio/livrosfaltando', methods=['GET'])
def relatorio_livros_faltando_json():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
            SELECT 
                a.id_livro, 
                a.titulo, 
                COUNT(ie.ID_LIVRO) AS QTD_EMPRESTADA,
                a.autor, 
                a.CATEGORIA, 
                a.ISBN, 
                a.QTD_DISPONIVEL,
                a.ANO_PUBLICADO
            FROM ACERVO a
            INNER JOIN ITENS_EMPRESTIMO ie ON a.ID_LIVRO = ie.ID_LIVRO
            INNER JOIN EMPRESTIMOS e ON ie.ID_EMPRESTIMO = e.ID_EMPRESTIMO
            WHERE e.STATUS IN ('ATIVO')
            GROUP BY 
                a.id_livro, 
                a.titulo, 
                a.autor, 
                a.CATEGORIA, 
                a.ISBN, 
                a.QTD_DISPONIVEL,  
                a.ANO_PUBLICADO
            ORDER BY a.id_livro
        """)
    livros = cur.fetchall()
    cur.close()

    subtitulos = ["id", "titulo", "qtd_emprestada", "autor", "categoria", "isbn", "qtd_total", "ano_publicado"]

    livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

    return jsonify({
        "total": len(livros_json),
        "livros": livros_json
    })


@app.route('/relatorio/livros', methods=['GET'])
def relatorio_livros_json():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
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

    subtitulos = ["id", "titulo", "autor", "categoria", "isbn", "qtd_disponivel", "descricao", "idiomas",
                  "ano_publicado"]

    livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

    return jsonify({
        "total": len(livros_json),
        "livros": livros_json
    })


@app.route('/relatorio/usuarios', methods=['GET'])
def relatorio_usuarios_json():
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

@app.route('/relatorio/gerar/livros/faltando', methods=['GET'])
def gerar_relatorio_livros_faltando():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
        SELECT 
            a.id_livro, 
            a.titulo, 
            COUNT(ie.ID_LIVRO) AS QTD_EMPRESTADA,
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.DESCRICAO, 
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        INNER JOIN ITENS_EMPRESTIMO ie ON a.ID_LIVRO = ie.ID_LIVRO
        INNER JOIN EMPRESTIMOS e ON ie.ID_EMPRESTIMO = e.ID_EMPRESTIMO
        WHERE e.STATUS in ('ATIVO')
        GROUP BY 
            a.id_livro, 
            a.titulo, 
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.DESCRICAO, 
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        ORDER BY a.id_livro
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

    subtitulos = ["ID", "Titulo", "Quantidade Emprestada", "Autor", "Categoria", "ISBN", "Quantidade Total",
                  "Descrição", "Idiomas", "Ano Publicado"]

    print(len(livros))

    for livro in livros:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(0, 5, f"{subtitulos[i]}: ")

            texto = livro[i]
            texto = str(texto)

            # Codificar em 'latin-1', ignorando caracteres que não podem ser codificados
            texto = texto.encode('latin-1', 'ignore').decode('latin-1')

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(50, 5, f"{texto}")
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

@app.route('/relatorio/gerar/livros', methods=['GET'])
def gerar_relatorio_livros():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
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
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        GROUP BY 
            a.id_livro, 
            a.titulo, 
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.DESCRICAO, 
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        ORDER BY a.id_livro
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

    subtitulos = ["ID", "Titulo", "Autor", "Categoria", "ISBN", "Quantidade Total",
                  "Descrição", "Idiomas", "Ano Publicado"]

    print(len(livros))

    for livro in livros:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(0, 5, f"{subtitulos[i]}: ")

            texto = livro[i]
            texto = str(texto)

            # Codificar em 'latin-1', ignorando caracteres que não podem ser codificados
            texto = texto.encode('latin-1', 'ignore').decode('latin-1')

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(50, 5, f"{texto}")
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

            texto = usuario[i]
            texto = str(texto)

            # Codificar em 'latin-1', ignorando caracteres que não podem ser codificados
            texto = texto.encode('latin-1', 'ignore').decode('latin-1')

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(50, 5, f"{texto}")
            pdf.ln(1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(7)

    pdf_path = "relatorio_usuarios.pdf"
    pdf.output(pdf_path)
    try:
        return send_file(pdf_path, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': f"Erro ao gerar o arquivo: {str(e)}"}), 500


@app.route('/relatorio/gerar/multas', methods=['GET'])
def gerar_relatorio_multas():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
            SELECT u.email, u.telefone, u.nome, e.data_devolver
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.status = 'ATIVO' AND e.data_devolver < CURRENT_DATE
            AND u.id_usuario IN (SELECT m.ID_USUARIO FROM MULTAS m)
            ORDER BY m.DATA_ADICIONADO DESC
        """)
    tangoes = cur.fetchall()
    cur.close()

    total_multados = len(tangoes)  # Definir o contador de livros antes do loop

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Multas", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de multas: {total_multados}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    subtitulos = ["Email", "Telefone", "Nome", "Data que Era Para Devolver"]

    for multado in tangoes:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(100, 5, f"{subtitulos[i]}: ")

            texto = multado[i]
            texto = str(texto)

            # Codificar em 'latin-1', ignorando caracteres que não podem ser codificados
            texto = texto.encode('latin-1', 'ignore').decode('latin-1')

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(100, 5, f"{texto}")
            pdf.ln(1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(7)

    pdf_path = "relatorio_multas.pdf"
    pdf.output(pdf_path)

    try:
        return send_file(pdf_path, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        print(e)
        return jsonify({'error': f"Erro ao gerar o arquivo: {str(e)}"}), 500


@app.route('/relatorio/gerar/multas/pendentes', methods=['GET'])
def gerar_relatorio_multas_pendentes():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
            SELECT u.email, u.telefone, u.nome, e.data_devolver
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.status = 'ATIVO' AND e.data_devolver < CURRENT_DATE
            AND u.id_usuario IN (SELECT m.ID_USUARIO FROM MULTAS m) and m.pago = false
            ORDER BY m.DATA_ADICIONADO DESC
        """)
    tangoes = cur.fetchall()
    cur.close()

    total_multados = len(tangoes)  # Definir o contador de livros antes do loop

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Multas Pendentes", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de multas: {total_multados}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    subtitulos = ["Email", "Telefone", "Nome", "Data que Era Para Devolver"]

    for multado in tangoes:
        for i in range(len(subtitulos)):
            pdf.set_font("Arial", 'B', 14)
            pdf.multi_cell(100, 5, f"{subtitulos[i]}: ")

            texto = multado[i]
            texto = str(texto)

            # Codificar em 'latin-1', ignorando caracteres que não podem ser codificados
            texto = texto.encode('latin-1', 'ignore').decode('latin-1')

            pdf.set_font("Arial", '', 12)
            pdf.multi_cell(100, 5, f"{texto}")
            pdf.ln(1)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(7)

    pdf_path = "relatorio_multas_pendentes.pdf"
    pdf.output(pdf_path)

    try:
        return send_file(pdf_path, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        print(e)
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
    try:

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
                SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO
                FROM MULTAS M
                WHERE M.ID_USUARIO = ? AND M.PAGO = FALSE
            """, (id_logado,))
        multas_pendentes = cur.fetchall()

        # Multas Concluidas
        cur.execute("""
                    SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO
                    FROM MULTAS M
                    WHERE M.ID_USUARIO = ? AND M.PAGO = TRUE
                """, (id_logado,))
        multas_concluidas = cur.fetchall()

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
                {"id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2], "id_emprestimo": m[3],
                 "pago": m[4]}
                for m in multas_pendentes
            ],
            "multas_concluidas": [
                {"id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2], "id_emprestimo": m[3],
                 "pago": m[4]}
                for m in multas_concluidas
            ]
        }
        return jsonify(historico)
    except Exception:
        print("Erro ao pegar histórico")
        raise
    finally:
        cur.close()


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
            (SELECT COUNT(*) FROM RESERVAS R INNER JOIN ITENS_RESERVA IR ON R.ID_RESERVA = IR.ID_RESERVA WHERE IR.ID_LIVRO = ? AND R.STATUS IN ('PENDENTE', 'EM ESPERA')) AS total_reservas,
            (SELECT COUNT(*) FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS IN ('PENDENTE', 'CONFIRMADA')) AS total_emprestimos
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
                WHERE E.STATUS IN ('ATIVO', 'PENDENTE') AND I.id_livro = ? and e.id_usuario = ?;
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
    corpo = """
        <p>
            Você fez uma <strong>reserva</strong>!, por enquanto ela está pendente, 
            quando ela for atendida nós te avisaremos para vir buscar os livros.
        </p>
        <p><strong>Livros reservados:</strong></p>
        <ul style="padding-left: 20px; font-size: 16px;">
        """
    for livro in livros_reservados:
        titulo = livro[0]
        autor = livro[1]
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += "</ul>"

    con.commit()
    cur.close()

    enviar_email_async(email, assunto, corpo)
    criar_notificacao(id_usuario,
                      """Uma reserva sua foi feita e por enquanto está marcada pendente, 
                      te avisaremos quando ela for atendida para você ir buscar na biblioteca.""", "Aviso de Reserva")

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
             WHERE IE.ID_LIVRO = ? AND E.STATUS in ('ATIVO', 'PENDENTE')) AS total_emprestimos
        FROM ACERVO 
        WHERE ID_LIVRO = ?
    """, (livro_id, livro_id))
    livro = cur.fetchone()

    # Verificar se o usuário já possui empréstimo ativo desse livro
    cur.execute("""
        SELECT 1 
        FROM EMPRESTIMOS E
        JOIN ITENS_EMPRESTIMO I ON E.ID_EMPRESTIMO = I.ID_EMPRESTIMO
        WHERE E.STATUS in ('ATIVO', 'PENDENTE') AND E.ID_USUARIO = ? AND I.ID_LIVRO = ?
    """, (payload["id_usuario"], livro_id))
    ja_tem_emprestimo = cur.fetchone() is not None

    cur.close()

    if livro and livro[0] > livro[1] and not ja_tem_emprestimo:
        return jsonify({
            "disponivel": True
        })

    return jsonify({"disponivel": False})


# Confirmar empréstimo
@app.route('/emprestar', methods=['POST'])
def confirmar_emprestimo():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id_usuario = payload["id_usuario"]
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

    # Verifica se algum livro do carrinho está reservado
    cur.execute("""
        SELECT 1 
        FROM CARRINHO_EMPRESTIMOS CE
        WHERE CE.ID_LIVRO IN (SELECT IR.ID_LIVRO FROM ITENS_RESERVA IR
            WHERE IR.ID_RESERVA IN (SELECT R.ID_RESERVA FROM RESERVAS R 
                WHERE STATUS = 'PENDENTE' OR STATUS = 'EM ESPERA')) 
    """)

    if cur.fetchone():
        cur.close()
        return jsonify({"message": "Algum dos livros no carrinho está reservado. Empréstimo bloqueado."}), 401

    # Cria o empréstimo — data_criacao já está com valor padrão no banco
    data_validade = devolucao()
    cur.execute("INSERT INTO EMPRESTIMOS (ID_USUARIO, DATA_VALIDADE) VALUES (?, ?) RETURNING ID_EMPRESTIMO",
                (id_usuario, data_validade,))
    emprestimo_id = cur.fetchone()[0]

    # Pega informações dos livros
    cur.execute("""
        SELECT TITULO, AUTOR 
        FROM ACERVO 
        WHERE ID_LIVRO IN (
            SELECT ID_LIVRO FROM CARRINHO_EMPRESTIMOS WHERE ID_USUARIO = ?
        )
    """, (id_usuario,))
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

    # Enviar o e-mail para o usuário
    cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    usuario = cur.fetchone()
    nome, email = usuario

    assunto = nome + ", sua solicitação de empréstimo foi registrada"
    corpo = f"""
        <p>Você fez uma <strong>solicitação de empréstimo</strong>!</p>
        <p><strong>Livros emprestados:</strong></p>
        <ul style="padding-left: 20px; font-size: 16px;">
        """
    for titulo, autor in livros_emprestados:
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += """</ul>
        <p>
            Por enquanto esse empréstimo está marcado como pendente, 
            vá até a biblioteca para ser atendido e retirar os livros.
        </p>"""

    enviar_email_async(email, assunto, corpo)
    cur.close()
    criar_notificacao(id_usuario,
                      """Você fez uma solicitação de empréstimo que por enquanto está pendente, 
    vá até a biblioteca para ser atendido""", "Aviso de Empréstimo")

    return jsonify({"message": "Empréstimo registrado com sucesso. Venha para a biblioteca para ser atendido."})


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
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO
            FROM MULTAS M
            WHERE M.ID_USUARIO = ? AND M.PAGO = FALSE
        """, (id,))
    multas_pendentes = cur.fetchall()

    # Multas Concluidas
    cur.execute("""
                SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO
                FROM MULTAS M
                WHERE M.ID_USUARIO = ? AND M.PAGO = TRUE
            """, (id,))
    multas_concluidas = cur.fetchall()

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
            {"id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2], "id_emprestimo": m[3],
             "pago": m[4]}
            for m in multas_pendentes
        ],
        "multas_concluidas": [
            {"id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2], "id_emprestimo": m[3],
             "pago": m[4]}
            for m in multas_concluidas
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


def avisar_para_evitar_multas():
    print('\navisar_para_evitar_multas\n')

    cur = con.cursor()

    try:

        cur.execute("""
            SELECT 
                CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) AS dias_diferenca, u.EMAIL, u.NOME, e.DATA_DEVOLVER
                FROM 
                EMPRESTIMOS e
            INNER JOIN USUARIOS u ON e.ID_USUARIO = u.ID_USUARIO
            WHERE 
                STATUS = 'ATIVO'
                AND CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) <= 4
        """)

        dias_que_faltam = cur.fetchall()

        for dias, email, nome, data_devolver in dias_que_faltam:
            data_formatada = data_devolver.strftime("%d/%m/%Y")
            corpo = f"""
            <p style="font-size: 18px; line-height: 1.6; color: #333;">
                Olá <strong>{nome}</strong>,
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Este é um lembrete de que os livros de um emprestimo seu devem ser devolvidos até <strong>{data_formatada}</strong>.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Para evitar multas por atraso, certifique-se de realizar a devolução dentro do prazo. 
                Caso os livros do empréstimo já tenham sido devolvidos, por favor, notifique um bibliotecário.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Este aviso é feito diariamente, por favor resolva isto em até <strong>{dias}</strong> dias.
            </p>

            <p style="font-size: 16px; line-height: 1.6; color: #444;">
                Agradecemos sua atenção e colaboração.
            </p>
            """
            enviar_email_async(email, "Lembrete: Devolução de Livro", corpo)
        print("Função de aviso foi executada inteira")

    except Exception:
        raise
    finally:
        cur.close()


def multar_quem_precisa():
    print('\nmultar_quem_precisa\n')

    cur = con.cursor()

    try:
        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        # Adicionando multas ao banco de dados
        cur.execute("""
                    SELECT u.id_usuario, e.id_emprestimo
                    FROM emprestimos e
                    JOIN usuarios u ON e.id_usuario = u.id_usuario
                    WHERE e.status = 'ATIVO' AND e.data_devolver < CURRENT_TIMESTAMP
                    AND u.id_usuario NOT IN (SELECT m.ID_USUARIO FROM MULTAS m WHERE m.PAGO = FALSE)
                """)

        tangoes = cur.fetchall()
        print(f'tangões: {tangoes}')

        cur.execute("""SELECT VALOR_BASE, VALOR_ACRESCIMO
            FROM VALORES
            WHERE ID_VALOR = (SELECT MAX(ID_VALOR) FROM VALORES)
        """)

        valores = cur.fetchone()
        print(f"Valores: {valores}, valor[0]: {valores[0]}, valor[1]: {valores[1]}")

        valor_base = valores[0]
        valor_ac = valores[1]

        for tangao in tangoes:
            cur.execute(
                "INSERT INTO MULTAS (ID_USUARIO, ID_EMPRESTIMO, VALOR_BASE, VALOR_ACRESCIMO) VALUES (?, ?, ?, ?)",
                (tangao[0], tangao[1], valor_base, valor_ac))

        con.commit()

    except Exception:
        raise
    finally:
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

    criar_notificacao(id_usuario, 'Uma reserva sua foi atendida.', "Aviso de Reserva")

    return jsonify({
        "message": "Reserva atendida e empréstimo registrado com sucesso.",
        "data_devolver": data_devolver
    }), 200


@app.route('/emprestimo/<int:id_emprestimo>/atender', methods=["PUT"])
def atender_emprestimo(id_emprestimo):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    cur.execute("""
        SELECT e.id_usuario, i.id_livro 
        FROM emprestimos e
        join itens_emprestimo i on e.id_emprestimo = i.id_emprestimo
        WHERE e.id_emprestimo = ? AND e.status = 'PENDENTE'
    """, (id_emprestimo,))
    dados = cur.fetchone()

    if not dados:
        cur.close()
        return jsonify({"message": "Emprestimo não encontrado ou já foi atendido/cancelado."}), 404

    id_usuario, id_livro = dados
    data_devolver = devolucao()

    cur.execute("""
        UPDATE emprestimos 
        SET status = 'ATIVO', data_devolver = ?, data_retirada = CURRENT_TIMESTAMP
        WHERE id_emprestimo = ?
    """, (data_devolver, id_emprestimo))

    con.commit()
    cur.close()

    return jsonify({
        "message": "Emprestimo atendido e registrado com sucesso.",
        "data_devolver": data_devolver
    }), 200


@app.route("/multas", methods=["GET"])
def get_all_multas():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    cur = con.cursor()

    cur.execute("""
        SELECT 
            M.ID_MULTA,
            M.ID_USUARIO,
            U.NOME,
            U.EMAIL,
            M.ID_EMPRESTIMO,
            M.VALOR_BASE,
            M.VALOR_ACRESCIMO,
            M.PAGO,
            LIST(A.TITULO, ', ') AS TITULOS
        FROM MULTAS M
        JOIN USUARIOS U ON M.ID_USUARIO = U.ID_USUARIO
        JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
        JOIN ITENS_EMPRESTIMO I ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
        JOIN ACERVO A ON A.ID_LIVRO = I.ID_LIVRO
        GROUP BY 
            M.ID_MULTA, M.ID_USUARIO, U.NOME, U.EMAIL, 
            M.ID_EMPRESTIMO, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.PAGO
    """)
    multas = cur.fetchall()

    return jsonify([
        {
            "id_multa": m[0],
            "id_usuario": m[1],
            "nome": m[2],
            "email": m[3],
            "id_emprestimo": m[4],
            "valor_base": m[5],
            "valor_acrescimo": m[6],
            "pago": bool(m[7]),
            "titulos": m[8]
        }
        for m in multas
    ])


@app.route("/usuarios/pesquisa", methods=["POST"])
def pesquisar_usuarios():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    data = request.get_json()
    pesquisa = data.get("pesquisa", "").strip()
    filtros = data.get("filtros", {})

    if not pesquisa and not filtros:
        return jsonify({"message": "Nada pesquisado."}), 400

    cur = con.cursor()

    sql = """
            SELECT DISTINCT u.id_usuario, u.nome, u.email, u.telefone, u.endereco,
                            u.tipo, u.ativo
            FROM USUARIOS u 
            WHERE 
        """

    conditions = []
    params = []

    if pesquisa:
        conditions.append("(u.nome CONTAINING ? OR u.email CONTAINING ?)")
        params.extend([pesquisa] * 3)

    if filtros.get("tipo"):
        conditions.append("(u.tipo = ?)")
        params.append(filtros["tipo"])

    if conditions:
        sql += " AND ".join(conditions)

    sql += " ORDER BY u.nome"

    cur.execute(sql, params)
    resultados = cur.fetchall()
    cur.close()

    if not resultados:
        return jsonify({"message": "Nenhum resultado encontrado."}), 404

    return jsonify({
        "message": "Pesquisa realizada com sucesso.",
        "resultados": [{
            "id": r[0],
            "nome": r[1],
            "email": r[2],
            "telefone": r[3],
            "endereco": r[4],
            "tipo": r[5],
            "ativo": r[6],
            "imagem": f"{r[0]}.jpeg"
        } for r in resultados]
    }), 200


@app.route("/usuarios/<int:id>/multas", methods=["GET"])
def get_multas_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    cur.execute("""
        SELECT 
            M.ID_MULTA,
            M.ID_USUARIO,
            U.NOME,
            U.EMAIL,
            M.ID_EMPRESTIMO,
            M.VALOR_BASE,
            M.VALOR_ACRESCIMO,
            M.PAGO,
            LIST(A.TITULO, ', ') AS TITULOS
        FROM MULTAS M
        JOIN USUARIOS U ON M.ID_USUARIO = U.ID_USUARIO
        JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
        JOIN ITENS_EMPRESTIMO I ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
        JOIN ACERVO A ON A.ID_LIVRO = I.ID_LIVRO
        WHERE M.ID_USUARIO = ?
        GROUP BY 
            M.ID_MULTA, M.ID_USUARIO, U.NOME, U.EMAIL, 
            M.ID_EMPRESTIMO, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.PAGO
    """, (id,))
    multas = cur.fetchall()

    return jsonify([
        {
            "id_multa": m[0],
            "id_usuario": m[1],
            "nome": m[2],
            "email": m[3],
            "id_emprestimo": m[4],
            "valor_base": m[5],
            "valor_acrescimo": m[6],
            "pago": bool(m[7]),
            "titulos": m[8]
        }
        for m in multas
    ])


@app.route("/usuarios/multas", methods=["GET"])
def get_multas_for_user():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    id = payload["id_usuario"]

    cur = con.cursor()

    cur.execute("""
        SELECT 
            M.ID_MULTA,
            M.ID_USUARIO,
            U.NOME,
            U.EMAIL,
            M.ID_EMPRESTIMO,
            M.VALOR_BASE,
            M.VALOR_ACRESCIMO,
            M.PAGO,
            LIST(A.TITULO, ', ') AS TITULOS
        FROM MULTAS M
        JOIN USUARIOS U ON M.ID_USUARIO = U.ID_USUARIO
        JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
        JOIN ITENS_EMPRESTIMO I ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
        JOIN ACERVO A ON A.ID_LIVRO = I.ID_LIVRO
        WHERE M.ID_USUARIO = ?
        GROUP BY 
            M.ID_MULTA, M.ID_USUARIO, U.NOME, U.EMAIL, 
            M.ID_EMPRESTIMO, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.PAGO
    """, (id,))
    multas = cur.fetchall()

    return jsonify([
        {
            "id_multa": m[0],
            "id_usuario": m[1],
            "nome": m[2],
            "email": m[3],
            "id_emprestimo": m[4],
            "valor_base": m[5],
            "valor_acrescimo": m[6],
            "pago": bool(m[7]),
            "titulos": m[8]
        }
        for m in multas
    ])


@app.route('/movimentacoes', methods=['GET'])
def get_all_movimentacoes():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Consulta de empréstimos com títulos agrupados
    cur.execute("""
        SELECT 
            E.ID_EMPRESTIMO, 
            U.NOME, 
            LIST(A.TITULO, ', ') AS TITULOS,
            E.DATA_CRIACAO, 
            E.DATA_RETIRADA, 
            E.DATA_DEVOLVER, 
            E.DATA_DEVOLVIDO, 
            E.DATA_VALIDADE,
            E.STATUS
        FROM EMPRESTIMOS E
        JOIN USUARIOS U ON E.ID_USUARIO = U.ID_USUARIO
        JOIN ITENS_EMPRESTIMO IE ON IE.ID_EMPRESTIMO = E.ID_EMPRESTIMO
        JOIN ACERVO A ON IE.ID_LIVRO = A.ID_LIVRO
        WHERE E.STATUS IN ('PENDENTE', 'ATIVO', 'CANCELADO', 'DEVOLVIDO')
        GROUP BY E.ID_EMPRESTIMO, U.NOME, E.DATA_CRIACAO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO, E.DATA_VALIDADE, E.STATUS
    """)
    emprestimos = cur.fetchall()

    # Consulta de reservas com títulos agrupados
    cur.execute("""
        SELECT 
            R.ID_RESERVA, 
            U.NOME, 
            LIST(A.TITULO, ', ') AS TITULOS,
            R.DATA_CRIACAO, 
            R.DATA_VALIDADE, 
            R.STATUS
        FROM RESERVAS R
        JOIN USUARIOS U ON R.ID_USUARIO = U.ID_USUARIO
        JOIN ITENS_RESERVA IR ON IR.ID_RESERVA = R.ID_RESERVA
        JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
        WHERE R.STATUS IN ('PENDENTE', 'EM ESPERA', 'CANCELADA', 'EXPIRADA', 'ATENDIDA')
        GROUP BY R.ID_RESERVA, U.NOME, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS
    """)
    reservas = cur.fetchall()

    movimentacoes = []

    for e in emprestimos:
        movimentacoes.append({
            'tipo': 'emprestimo',
            'id': e[0],
            'usuario': e[1],
            'titulo': e[2],
            'data_evento': e[3],  # Para ordenação
            'data_evento_str': e[3].isoformat(timespec='minutes') if e[3] else None,
            'data_criacao': e[3].isoformat(timespec='minutes') if e[3] else None,
            'data_retirada': e[4].isoformat(timespec='minutes') if e[4] else None,
            'data_devolver': e[5].isoformat(timespec='minutes') if e[5] else None,
            'data_devolvida': e[6].isoformat(timespec='minutes') if e[6] else None,
            'data_validade': e[7].isoformat(timespec='minutes') if e[7] else None,
            'status': e[8]
        })

    for r in reservas:
        movimentacoes.append({
            'tipo': 'reserva',
            'id': r[0],
            'usuario': r[1],
            'titulo': r[2],
            'data_evento': r[3],  # Para ordenação
            'data_evento_str': r[3].isoformat(timespec='minutes') if r[3] else None,
            'data_criacao': r[3].isoformat(timespec='minutes') if r[3] else None,
            'data_validade': r[4].isoformat(timespec='minutes') if r[4] else None,
            'status': r[5]
        })

    # Ordenar pela data_evento (mais recente primeiro)
    movimentacoes.sort(key=lambda x: x['data_evento'], reverse=True)

    # Remover o campo datetime bruto (não serializável)
    for m in movimentacoes:
        del m['data_evento']

    cur.close()
    return jsonify(movimentacoes)


@app.route("/movimentacoes/pesquisa", methods=["POST"])
def pesquisar_movimentacoes():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    data = request.get_json()
    pesquisa_usuario = data.get("pesquisaUsuario", "").strip()
    pesquisa_titulo = data.get("pesquisaTitulo", "").strip()
    tipo_mov = data.get("tipoMovimentacao")

    pesquisa_titulo = "" if not pesquisa_titulo else pesquisa_titulo
    pesquisa_usuario = "" if not pesquisa_usuario else pesquisa_usuario
    tipo_mov = "todos" if not tipo_mov else tipo_mov

    cur = con.cursor()

    sql_emp = """SELECT 
                E.ID_EMPRESTIMO, 
                U.NOME, 
                LIST(A.TITULO, ', ') AS TITULOS,
                E.DATA_CRIACAO, 
                E.DATA_RETIRADA, 
                E.DATA_DEVOLVER, 
                E.DATA_DEVOLVIDO, 
                E.DATA_VALIDADE,
                E.STATUS
            FROM EMPRESTIMOS E
            JOIN USUARIOS U ON E.ID_USUARIO = U.ID_USUARIO
            JOIN ITENS_EMPRESTIMO IE ON IE.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON IE.ID_LIVRO = A.ID_LIVRO
            WHERE E.STATUS IN ('PENDENTE', 'ATIVO', 'CANCELADO', 'DEVOLVIDO')
            """

    sql_res = """
            SELECT 
                R.ID_RESERVA, 
                U.NOME, 
                LIST(A.TITULO, ', ') AS TITULOS,
                R.DATA_CRIACAO, 
                R.DATA_VALIDADE, 
                R.STATUS
            FROM RESERVAS R
            JOIN USUARIOS U ON R.ID_USUARIO = U.ID_USUARIO
            JOIN ITENS_RESERVA IR ON IR.ID_RESERVA = R.ID_RESERVA
            JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
            WHERE R.STATUS IN ('PENDENTE', 'EM ESPERA', 'CANCELADA', 'EXPIRADA', 'ATENDIDA')
        """

    if tipo_mov == "devolucao":
        sql_emp += " AND E.DATA_DEVOLVIDO IS NOT NULL"

    if pesquisa_titulo:
        sql_emp += f" AND A.TITULO CONTAINING '{pesquisa_titulo}'"
        sql_res += f" AND A.TITULO CONTAINING '{pesquisa_titulo}'"

    if pesquisa_usuario:
        sql_emp += f" AND U.NOME CONTAINING '{pesquisa_usuario}'"
        sql_res += f" AND U.NOME CONTAINING '{pesquisa_usuario}'"

    sql_emp += " GROUP BY E.ID_EMPRESTIMO, U.NOME, E.DATA_CRIACAO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO, E.DATA_VALIDADE, E.STATUS"
    sql_res += " GROUP BY R.ID_RESERVA, U.NOME, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS"

    # Consulta de empréstimos com títulos agrupados
    cur.execute(sql_emp)
    emprestimos = cur.fetchall()

    # Consulta de reservas com títulos agrupados
    cur.execute(sql_res)
    reservas = cur.fetchall()

    movimentacoes = []

    if tipo_mov == "emprestimo" or tipo_mov == "todos" or tipo_mov == "devolucao":
        for e in emprestimos:
            movimentacoes.append({
                'tipo': 'emprestimo',
                'id': e[0],
                'usuario': e[1],
                'titulo': e[2],
                'data_evento': e[3],  # Para ordenação
                'data_evento_str': e[3].isoformat(timespec='minutes') if e[3] else None,
                'data_criacao': e[3].isoformat(timespec='minutes') if e[3] else None,
                'data_retirada': e[4].isoformat(timespec='minutes') if e[4] else None,
                'data_devolver': e[5].isoformat(timespec='minutes') if e[5] else None,
                'data_devolvida': e[6].isoformat(timespec='minutes') if e[6] else None,
                'data_validade': e[7].isoformat(timespec='minutes') if e[7] else None,
                'status': e[8]
            })

    if tipo_mov == "reserva" or tipo_mov == "todos":
        for r in reservas:
            movimentacoes.append({
                'tipo': 'reserva',
                'id': r[0],
                'usuario': r[1],
                'titulo': r[2],
                'data_evento': r[3],  # Para ordenação
                'data_evento_str': r[3].isoformat(timespec='minutes') if r[3] else None,
                'data_criacao': r[3].isoformat(timespec='minutes') if r[3] else None,
                'data_validade': r[4].isoformat(timespec='minutes') if r[4] else None,
                'status': r[5]
            })

    # Ordenar pela data_evento (mais recente primeiro)
    movimentacoes.sort(key=lambda x: x['data_evento'], reverse=True)

    # Remover o campo datetime bruto (não serializável)
    for m in movimentacoes:
        del m['data_evento']

    cur.close()
    return jsonify(movimentacoes)


@app.route("/valor/criar", methods=["POST"])
def criar_valor():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao
    data = request.get_json()
    valor_base = data.get('valor_base')
    valor_ac = data.get('valor_acrescimo')
    cur = con.cursor()
    cur.execute("INSERT INTO VALORES (VALOR_BASE, VALOR_ACRESCIMO) VALUES (?, ?)", (valor_base, valor_ac,))
    con.commit()
    cur.close()
    return jsonify({"message": "Novo valor criado com sucesso!"}), 200


@app.route("/valores", methods=["GET"])
def get_valores():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao
    cur = con.cursor()
    cur.execute("""
        SELECT FIRST 1
            ID_VALOR,
            DATA_ADICIONADO,
            VALOR_BASE,
            VALOR_ACRESCIMO
        FROM VALORES
        ORDER BY DATA_ADICIONADO DESC
    """)
    valores = cur.fetchone()
    cur.close()

    if not valores:
        return jsonify({"message": "Nenhum valor encontrado."}), 404

    id_valor, data_adicionado, valor_base, valor_acrescimo = valores

    return jsonify({
        "id_valor": id_valor,
        "data_adicionado": data_adicionado,
        "valor_base": float(valor_base),
        "valor_acrescimo": float(valor_acrescimo)
    }), 200


@app.route('/multa/<int:id_multa>/atender', methods=["PUT"])
def atender_multa(id_multa):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    cur.execute("""
        SELECT id_multa, pago FROM multas WHERE id_multa = ?
    """, (id_multa,))
    multa = cur.fetchone()

    if not multa:
        cur.close()
        return jsonify({"message": "Multa não encontrada."}), 404

    _, pago = multa
    if pago:
        cur.close()
        return jsonify({"message": "Multa já está paga."}), 400

    # Atualiza a multa como paga
    cur.execute("""
        UPDATE multas
        SET pago = TRUE
        WHERE id_multa = ?
    """, (id_multa,))

    con.commit()
    cur.close()

    return jsonify({"message": "Multa paga com sucesso."}), 200


@app.route("/livros/<int:id_livro>/avaliacao", methods=["GET"])
def get_avaliacao_by_user(id_livro):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]

    cur = con.cursor()
    cur.execute("""
        SELECT valor_total
        FROM avaliacoes
        WHERE id_livro = ? AND id_usuario = ?
    """, (id_livro, id_logado))

    resultado = cur.fetchone()
    cur.close()

    if not resultado:
        return jsonify({"message": "Usuário ainda não avaliou este livro."}), 404

    return jsonify({
        "valor_total": int(resultado[0])
    }), 200
