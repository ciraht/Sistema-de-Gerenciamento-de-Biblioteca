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
from random import randint
import locale

senha_secreta = app.config['SECRET_KEY']


def configuracoes():
    cur = con.cursor()
    # ID_REGISTRO, DIAS_VALIDADE_EMPRESTIMO, DIAS_VALIDADE_EMPRESTIMO_BUSCAR, CHAVE_PIX
    # RAZAO_SOCIAL, ENDERECO, TELEFONE, EMAIL, LIMITE_LIVROS_EMPRESTIMO, LIMITE_LIVROS_RESERVA, DATA_ADICIONADO
    cur.execute("""
                    SELECT *
                    FROM CONFIGURACOES
                    WHERE ID_REGISTRO = (SELECT MAX(ID_REGISTRO) FROM CONFIGURACOES)
                    """)
    config_mais_recente = cur.fetchone()
    cur.close()
    return config_mais_recente


def devolucao(data_validade=False):
    # Retorna a data de devolução do livro, adicionando o período de empréstimo à data atual.
    dias_emprestimo = configuracoes()[1]
    if data_validade:
        dias_emprestimo = configuracoes()[2]
    data_devolucao = datetime.datetime.now() + datetime.timedelta(days=dias_emprestimo)
    return data_devolucao


def calcular_paginacao(pagina):
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    return inicial, final


def agendar_tarefas():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=avisar_para_evitar_multas, trigger='cron', hour=8, minute=20)
    scheduler.add_job(func=invalidar_emp_res, trigger='cron', hour=8, minute=19)
    scheduler.start()


def avisar_para_evitar_multas():
    # print('\navisar_para_evitar_multas\n')

    cur = con.cursor()

    try:

        cur.execute("""
            SELECT 
                CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) AS dias_diferenca, u.EMAIL, u.NOME, e.DATA_DEVOLVER, u.ID_USUARIO
                FROM 
                EMPRESTIMOS e
            INNER JOIN USUARIOS u ON e.ID_USUARIO = u.ID_USUARIO
            WHERE 
                STATUS = 'ATIVO'
            AND CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) = 3
                OR CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) = 2
                OR CAST(e.DATA_DEVOLVER - CURRENT_TIMESTAMP AS INTEGER) = 1
        """)

        dias_que_faltam = cur.fetchall()

        for dias, email, nome, data_devolver, id_usuario in dias_que_faltam:
            data_formatada = data_devolver.strftime("%d/%m/%Y")
            corpo = f"""
                Olá <strong>{nome}</strong>,
            </p>

            <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                Este é um lembrete de que os livros de um emprestimo seu devem ser devolvidos até <strong>{data_formatada}</strong>.
            </p>

            <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                Para evitar multas por atraso, certifique-se de realizar a devolução dentro do prazo. 
                Caso os livros do empréstimo já tenham sido devolvidos, por favor, notifique um bibliotecário.
            </p>

            <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                Este aviso é feito diariamente, por favor resolva isto em até <strong>{dias}</strong> dias.
            </p>

            <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                Agradecemos sua atenção e colaboração.
            </p>
            """
            enviar_email_async(email, "Lembrete: Devolução de Livro", corpo)
            mensagem = f"Um empréstimo seu vence em {dias} dias, tome cuidado para evitar multas"
            criar_notificacao(id_usuario, mensagem, "Lembrete: Devolução de Livro")

    except Exception:
        raise
    finally:
        cur.close()


def invalidar_emp_res():
    cur = con.cursor()
    try:
        cur.execute("""
        SELECT u.nome, u.email, e.id_emprestimo, u.id_usuario, e.data_validade FROM USUARIOS u 
        INNER JOIN EMPRESTIMOS e ON e.ID_USUARIO = u.ID_USUARIO 
        WHERE e.DATA_RETIRADA IS NULL AND CAST(e.DATA_VALIDADE AS DATE) < CURRENT_DATE""")
        abandonadores_de_emprestimo = cur.fetchall()

        cur.execute("""
        SELECT u.nome, u.email, r.id_reserva, u.id_usuario, r.data_validade FROM USUARIOS u 
        INNER JOIN RESERVAS r ON r.ID_USUARIO = u.ID_USUARIO 
        WHERE CAST (r.DATA_VALIDADE AS DATE) < CURRENT_DATE AND r.DATA_VALIDADE IS NOT NULL""")
        abandonadores_de_reserva = cur.fetchall()

        cur.execute("""UPDATE EMPRESTIMOS e SET e.STATUS = 'CANCELADO' WHERE e.DATA_RETIRADA IS NULL
         AND CAST(e.DATA_VALIDADE AS DATE) < CURRENT_DATE""")
        cur.execute("""UPDATE RESERVAS r SET r.STATUS = 'CANCELADA' WHERE CAST (r.DATA_VALIDADE AS DATE) < CURRENT_DATE
         AND r.DATA_VALIDADE IS NOT NULL""")
        con.commit()

        for nome, email, id_emp, id_user, data_validade in abandonadores_de_emprestimo:
            cur.execute("""
            SELECT TITULO, AUTOR FROM ACERVO a 
            INNER JOIN ITENS_EMPRESTIMO ie ON ie.ID_LIVRO = a.ID_LIVRO 
            WHERE ie.ID_EMPRESTIMO = ?""", (id_emp, ))
            livros = cur.fetchall()
            data_formatada = formatar_timestamp(data_validade)

            corpo = f"""
                    Um empréstimo feito por você não foi buscado a tempo e portanto foi cancelado. 
                    </p>
                    <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                    <strong>Livros que você estava tentando pegar:</strong></p>
                    <ul style="padding-left: 20px; font-size: 16px;">
                    """
            for titulo, autor in livros:
                corpo += f"<li>{titulo}, por {autor}</li>"
            corpo += f"""
                        </ul>
                        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                        Você teve até o dia {data_formatada}.
                         </p>"""
            enviar_email_async(email, "Vencimento de Empréstimo", corpo)
            criar_notificacao(id_user, f"""Um empréstimo seu não foi buscado a tempo e portanto foi cancelado, 
            você teve até o dia {data_formatada}.
            """, "Vencimento de Empréstimo")

        for nome, email, id_res, id_user, data_validade in abandonadores_de_reserva:
            cur.execute("""
            SELECT TITULO, AUTOR FROM ACERVO a 
            INNER JOIN ITENS_EMPRESTIMO ie ON ie.ID_LIVRO = a.ID_LIVRO 
            WHERE ie.ID_EMPRESTIMO = ?""", (id_res,))
            livros = cur.fetchall()
            data_formatada = formatar_timestamp(data_validade)

            corpo = f"""
                    Uma reserva feita por você não foi buscada a tempo e portanto foi cancelada. 
                    </p>
                    <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                    <strong>Livros que você estava tentando pegar:</strong></p>
                    <ul style="padding-left: 20px; font-size: 16px;">
                    """
            for titulo, autor in livros:
                corpo += f"<li>{titulo}, por {autor}</li>"
            corpo += f"""
                        </ul>
                        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                        Você teve até o dia {data_formatada}.
                         </p>"""
            enviar_email_async(email, "Vencimento de Reserva", corpo)
            criar_notificacao(id_user, f"""Uma reserva sua não foi buscada a tempo e portanto foi cancelada, 
            você teve até o dia {data_formatada}.
            """, "Vencimento de Reserva")

    except Exception:
        print("Erro em invalidar_emp_res")
        raise
    finally:
        cur.close()


@app.route("/teste", methods=["GET"])
def testar():
    invalidar_emp_res()
    return jsonify({"message": "Testado"})


def multar_por_id_emprestimo(id_emprestimo):
    # print('\nmultar_por_id_emprestimo\n')

    cur = con.cursor()

    try:
        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        # Adicionando multas ao banco de dados
        cur.execute("""
                    SELECT u.id_usuario, e.id_emprestimo
                    FROM emprestimos e
                    INNER JOIN usuarios u ON e.id_usuario = u.id_usuario
                    WHERE e.status = 'ATIVO' AND e.data_devolver < CURRENT_TIMESTAMP
                    AND E.ID_EMPRESTIMO = ?
                    AND E.ID_EMPRESTIMO NOT IN (SELECT M.ID_EMPRESTIMO FROM MULTAS M)
                """, (id_emprestimo,))

        tangoes = cur.fetchall()

        cur.execute("""SELECT VALOR_BASE, VALOR_ACRESCIMO
            FROM VALORES
            WHERE ID_VALOR = (SELECT MAX(ID_VALOR) FROM VALORES)
        """)

        valores = cur.fetchone()

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


def agendar_expiracao_codigo(id_codigo, minutos):
    scheduler = BackgroundScheduler()
    horario_excluir = datetime.datetime.now() + datetime.timedelta(minutes=minutos)
    scheduler.add_job(func=excluir_codigo_agendado, args=(id_codigo,), trigger='date', next_run_time=horario_excluir)
    scheduler.start()


def excluir_codigo_agendado(id_codigo):
    cur = con.cursor()
    try:
        cur.execute("DELETE FROM CODIGOS_RECUPERACAO WHERE ID_CODIGO = ?", (id_codigo,))
        con.commit()
    except Exception:
        print("Erro ao excluir código de recuperação")
        raise
    finally:
        cur.close()


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
                a.ANO_PUBLICADO,
                (SELECT COUNT(*) FROM AVALIACOES av WHERE av.ID_LIVRO = a.ID_LIVRO) AS QTD_AVALIACOES
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
            # Reservas não são mais contadas aqui
            contagem = contagem_emp
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
                "avaliacao": avaliacoes,
                "qtd_avaliacoes": livro[9]
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
            "avaliacao": avaliacoes,
            "qtd_avaliacoes": livro[9]
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
                    <meta charset="UTF-8" />
                    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
                    <title>{assunto}</title>
                  </head>
                  <body style="margin: 0; padding: 0; background-color: #f2f4f8; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
                    <table align="center" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);">
                      <!-- Cabeçalho -->
                      <tr>
                        <td align="center" style="background-color: #1a73e8; padding: 30px 20px;">
                          <h1 style="color: white; margin: 0; font-size: 26px;">{assunto}</h1>
                        </td>
                      </tr>
                
                      <!-- Corpo -->
                      <tr>
                        <td style="padding: 30px; color: #333;">
                          <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                            {corpo}
                          </p>
                        </td>
                      </tr>
                
                      <!-- Rodapé -->
                      <tr>
                        <td style="background-color: #1a1a1a; color: #ccc; text-align: center; padding: 20px; font-size: 12px;">
                          © {datetime.datetime.now().year} {configuracoes()[4]} Todos os direitos reservados. 
                          Localizada em {configuracoes()[5]}.<br/>
                          Este é um e-mail automático, por favor não responda.
                        </td>
                      </tr>
                    </table>
                  </body>
                </html>
                """

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


def formatar_timestamp(timestamp, horario=None, somente_data=None):
    # Definir o locale para português (Brasil)
    locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
    timestamp = str(timestamp)

    if somente_data:
        data = datetime.datetime.strptime(timestamp, "%Y-%m-%d")
        return data.strftime("%d de %B de %Y")

    # Converter o timestamp para objeto datetime
    data = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")

    # Formatar a data para o formato por extenso

    if horario:
        return data.strftime("%d de %B de %Y %H:%M:%S")
    return data.strftime("%d de %B de %Y")


def formatar_telefone(tel):
    # (18) 12345-1234
    tel = str(tel)
    tel = ''.join(filter(str.isdigit, tel))  # Remove caracteres não numéricos
    if len(tel) == 11:
        ddd = tel[:2]
        primeira_parte = tel[2:7]
        segunda_parte = tel[7:]
        return f"({ddd}) {primeira_parte}-{segunda_parte}"
    else:
        return 0


@app.route('/configuracoes', methods=["GET"])
def trazer_configuracoes():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    todas = request.args.get('todas', default='false').lower() == 'true'

    cur = con.cursor()
    try:

        if not todas:
            cur.execute("""
                    SELECT *
                    FROM CONFIGURACOES
                    WHERE ID_REGISTRO = (SELECT MAX(ID_REGISTRO) FROM CONFIGURACOES)
                    """)
            config_mais_recente = cur.fetchone()
            if not config_mais_recente:
                return jsonify({'message': 'Nenhuma configuração encontrada'}), 404
            return jsonify({'configuracoes_mais_recentes': config_mais_recente}), 200

        cur.execute("SELECT * FROM CONFIGURACOES")
        configuracoes = cur.fetchall()
        return jsonify({'configuracoes': configuracoes}), 200
    except Exception:
        raise
    finally:
        cur.close()


@app.route('/configuracoes/criar', methods=["POST"])
def criar_verificacoes():
    verificacao = informar_verificacao(3)
    if verificacao:
        return verificacao

    data = request.get_json()
    dias_emp = data.get('dias_validade_emprestimo')
    dias_emp_b = data.get('dias_validade_buscar')
    chave_pix = data.get('chave_pix')
    raz_social = data.get('razao_social')
    endereco = data.get('endereco')
    telefone = data.get('telefone')
    email = data.get('email')
    limite_emp = data.get('limite_emprestimo')
    limite_res = data.get('limite_reserva')

    if not all([dias_emp, dias_emp_b, chave_pix, raz_social, endereco, telefone, email, limite_emp, limite_res]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    try:
        dias_emp = int(dias_emp)
        dias_emp_b = int(dias_emp_b)
        limite_emp = int(limite_emp)
        limite_res = int(limite_res)
    except (ValueError, TypeError):
        return jsonify({"message": "Os campos de dias e limites de livros devem ser numéricos."}), 400

    chave_pix = formatar_telefone(chave_pix)
    if chave_pix == 0:
        return jsonify({
            "message": 'Erro ao formatar chave pix como telefone. Siga este formato com DDD: (18) 12345-1234'}), 401

    try:
        pix = PixQrCode(raz_social, chave_pix, endereco, '100')
        teste_erro = pix.is_valid()
    except Exception as e:
        return jsonify({"message": "Os dados para Pix são inválidos."}), 401

    cur = con.cursor()
    try:

        cur.execute("""
            INSERT INTO CONFIGURACOES (
                DIAS_VALIDADE_EMPRESTIMO, 
                DIAS_VALIDADE_EMPRESTIMO_BUSCAR, 
                CHAVE_PIX, 
                RAZAO_SOCIAL,
                ENDERECO,
                TELEFONE,
                EMAIL,
                LIMITE_LIVROS_EMPRESTIMO,
                LIMITE_LIVROS_RESERVA)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (dias_emp, dias_emp_b, chave_pix, raz_social, endereco, telefone, email, limite_emp, limite_res))
    finally:
        cur.close()
        con.commit()

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
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT ID_NOTIFICACAO, TITULO, MENSAGEM, LIDA, DATA_ADICIONADA
            FROM NOTIFICACOES
            WHERE ID_USUARIO = ? AND CURRENT_DATE - CAST(DATA_ADICIONADA AS DATE) <= 20
            ORDER BY DATA_ADICIONADA ASC
        """, (id_usuario,))
        linhas = cur.fetchall()
    except Exception as e:
        return jsonify({"erro": "Erro ao buscar notificações", "detalhes": str(e)}), 500
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
                        # Em teoria é para ser impossível a execução chegar aqui

            cur.close()
            return jsonify({"message": "Credenciais inválidas."}), 401
    else:
        cur.close()
        return jsonify({"message": "Usuário não encontrado."}), 404


# 1
@app.route('/esqueci_senha', methods=["POST"])
def solicitar_recuperacao():
    data = request.get_json()
    email = data.get('email')
    email = email.lower()

    # Verificações
    cur = con.cursor()
    cur.execute("SELECT ID_USUARIO, NOME FROM USUARIOS WHERE EMAIL = ?", (email,))
    id_usuario = cur.fetchone()
    if not id_usuario:
        cur.close()
        return jsonify({"message": "Usuário não encontrado"}), 404

    # Verificar se já tem código desse usuário e excluir do banco de dados se houver
    cur.execute("SELECT 1 FROM CODIGOS_RECUPERACAO WHERE ID_USUARIO = ?", (id_usuario[0],))
    if cur.fetchone():
        cur.execute("DELETE FROM CODIGOS_RECUPERACAO WHERE ID_USUARIO = ?", (id_usuario[0],))
        con.commit()

    codigo = randint(100000, 999999)

    cur.execute("""
    INSERT INTO CODIGOS_RECUPERACAO (ID_USUARIO, CODIGO) 
    VALUES (?, ?) RETURNING ID_CODIGO
    """, (id_usuario[0], codigo,))
    id_codigo = cur.fetchone()[0]
    con.commit()
    cur.close()

    # Agendar expiração (deleção do banco de dados)
    agendar_expiracao_codigo(id_codigo, 15)

    codigo = str(codigo)
    corpo = f"""
    Olá <strong>{id_usuario[1]}</strong>,<br><br>
    Recebemos uma solicitação para redefinir sua senha. Utilize o código abaixo para concluir o processo: 
    </p>
    
    <!-- CÓDIGO -->
  <table align="center" cellpadding="0" cellspacing="6" style="margin: 20px auto;">
    <tr>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[0:1]}</td>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[1:2]}</td>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[2:3]}</td>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[3:4]}</td>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[4:5]}</td>
      <td style="background-color: #1a73e8; color: white; font-size: 20px; font-weight: bold; text-align: center; border-radius: 6px; width: 40px; height: 40px;">{codigo[5:6]}</td>
    </tr>
  </table>

  <p style="font-size: 14px; color: #555; margin-top: 30px;">
    Este código é válido por 15 minutos. Se você não solicitou a redefinição de senha, ignore este e-mail com segurança.
  </p>
    """
    enviar_email_async(email, "Recuperação de Senha", corpo)

    return jsonify({"message": "E-mail de recuperação enviado.",
                    "id_usuario": id_usuario}), 200


# 2
@app.route('/verificar_codigo', methods=['POST'])
# Verifica se o código coincide e o adiciona ao payload se sim
def verificar_recuperacao():
    data = request.get_json()
    codigo_recebido = data.get('codigo')
    id_usuario = data.get('id_usuario')

    cur = con.cursor()

    cur.execute("SELECT CODIGO FROM CODIGOS_RECUPERACAO WHERE ID_USUARIO = ?", (id_usuario,))
    codigo = cur.fetchone()
    if not codigo:
        cur.close()
        return jsonify({"message": "Código inválido."}), 401

    if codigo[0] != codigo_recebido:
        cur.close()
        return jsonify({"message": "Código inválido."}), 401

    payload = {
        "id_usuario": id_usuario,
        "codigo_recuperacao": codigo_recebido
    }
    token = jwt.encode(payload, senha_secreta, algorithm='HS256')
    return jsonify({"token": token})


# 3
@app.route('/reset_senha', methods=['PUT'])
def resetar_senha():
    data = request.get_json()
    senha_nova = data.get('senha_nova')
    senha_confirm = data.get('senha_confirm')

    cur = con.cursor()
    try:
        payload = informar_verificacao(trazer_pl=True)
        id_usuario = payload["id_usuario"]
        codigo_recebido = payload['codigo_recuperacao']

        if not all([senha_nova, senha_confirm]):
            cur.close()
            return jsonify({"message": "Todos os campos são obrigatórios."}), 401

        if senha_nova != senha_confirm:
            cur.close()
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 401

        if len(senha_nova) < 8 or not any(c.isupper() for c in senha_nova) or not any(
                c.islower() for c in senha_nova) or not any(c.isdigit() for c in senha_nova) or not any(
            c in "!@#$%^&*(), -.?\":{}|<>" for c in senha_nova):
            return jsonify({
                "message": "A senha deve conter pelo menos 8 caracteres, uma letra maiúscula, uma letra minúscula, um número e um caractere especial."}), 401

        cur.execute("SELECT CODIGO FROM CODIGOS_RECUPERACAO WHERE ID_USUARIO = ? ORDER BY ID_CODIGO DESC",
                    (id_usuario,))
        codigo = cur.fetchone()
        if not codigo:
            cur.close()
            return jsonify({"message": "Código expirado."}), 401

        if codigo[0] != codigo_recebido:
            cur.close()
            return jsonify({"message": "Código inválido."}), 401

        senha_nova = generate_password_hash(senha_nova)
        cur.execute(
            "UPDATE usuarios SET senha = ? WHERE id_usuario = ?",
            (senha_nova, id_usuario)
        )
        con.commit()

        return jsonify({"mensagem": "Senha redefinida com sucesso."}), 200

    except Exception:
        print('Erro em /reset_senha')
        raise
    finally:
        cur.close()


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
            return jsonify({"message": "A senha nova não pode ser igual à senha atual."}), 401

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
            ORDER BY a.id_livro asc;
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


@app.route('/livrosadm/<int:pagina>', methods=["GET"])
def get_livros_adm(pagina):
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
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    return jsonify(livros[inicial - 1:final]), 200


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


@app.route('/livros/novidades', methods=["GET"])
def get_livros_novos():
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
                ORDER BY a.id_livro desc
                rows 1 to 10;
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


@app.route('/livros/recomendados', methods=["GET"])
def recomendar():
    cur = con.cursor()
    try:
        # Pega as tags dos livros bem avaliados
        cur.execute("""
            SELECT t.ID_TAG
            FROM ACERVO ac
            INNER JOIN AVALIACOES a ON a.ID_LIVRO = ac.ID_LIVRO
            LEFT JOIN LIVRO_TAGS lt ON lt.ID_LIVRO = a.ID_LIVRO
            LEFT JOIN TAGS t ON t.ID_TAG = lt.ID_TAG
            WHERE (SELECT SUM(VALOR_TOTAL) FROM AVALIACOES A WHERE A.ID_LIVRO = ac.ID_LIVRO)
             / (SELECT COUNT(*) FROM AVALIACOES A WHERE A.ID_LIVRO = ac.ID_LIVRO) >= 3.5
            GROUP BY t.ID_TAG
        """)
        tags = cur.fetchall()

        # Extrai apenas os IDs das tags
        tags2 = [tag[0] for tag in tags]

        if not tags2:
            return jsonify({"tags": [], "livros": []})

        # Cria os placeholders dinamicamente
        placeholders = ', '.join(['?'] * len(tags2))
        query = f"""
            SELECT DISTINCT 
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
            JOIN LIVRO_TAGS LT ON LT.ID_LIVRO = A.ID_LIVRO 
            JOIN AVALIACOES AV ON av.ID_LIVRO = A.ID_LIVRO
            WHERE LT.ID_TAG IN ({placeholders})
              AND a.disponivel = true
            ORDER BY (SELECT SUM(VALOR_TOTAL) FROM AVALIACOES AV WHERE AV.ID_LIVRO = a.ID_LIVRO)
             / (SELECT COUNT(*) FROM AVALIACOES AV WHERE AV.ID_LIVRO = a.ID_LIVRO) DESC
        """

        cur.execute(query, tags2)
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

        return jsonify({"livros": livros, "tags": tags2})

    except Exception as e:
        print('Erro ao recomendar')
        raise
    finally:
        cur.close()


@app.route('/livros/porqueleu', methods=["GET"])
def recomendar_com_base_em():
    verificacao = informar_verificacao()
    if verificacao:
        return jsonify({"visivel": False})

    id_usuario = informar_verificacao(trazer_pl=True)["id_usuario"]

    cur = con.cursor()

    # Selecionar os livros que o usuário leu e escolher o mais recente
    cur.execute("""
        SELECT DISTINCT A.ID_LIVRO, A.TITULO FROM ACERVO A
        INNER JOIN ITENS_EMPRESTIMO IE ON IE.ID_LIVRO = A.ID_LIVRO
        WHERE IE.ID_EMPRESTIMO IN (SELECT E.ID_EMPRESTIMO FROM EMPRESTIMOS E WHERE E.ID_USUARIO = ?) 
            AND A.DISPONIVEL = TRUE
        ORDER BY IE.ID_ITEM DESC
        ROWS 1
        """, (id_usuario,))
    livro_analisado = cur.fetchone()
    if not livro_analisado:
        cur.close()
        return jsonify({"visivel": False})

    # Trazer livros que tenham as mesmas tags que o livro escolhido
    cur.execute("""
                SELECT DISTINCT 
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
                JOIN LIVRO_TAGS LT ON LT.ID_LIVRO = A.ID_LIVRO 
                    WHERE LT.ID_TAG IN (SELECT ID_TAG FROM LIVRO_TAGS WHERE ID_LIVRO = ?) 
                    AND a.disponivel = true AND A.ID_LIVRO <> ?
                
                ORDER BY a.id_livro asc;
            """, (livro_analisado[0], livro_analisado[0],))

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
    return jsonify({"livroAnalisado": livro_analisado, "livros": livros, "visivel": True}), 200


@app.route('/livros/minhalista', methods=["GET"])
def trazer_minha_lista():
    verificacao = informar_verificacao()
    if verificacao:
        return jsonify({"visivel": False})

    id_usuario = informar_verificacao(trazer_pl=True)["id_usuario"]

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
        INNER JOIN LISTAGEM L ON L.ID_LIVRO = a.ID_LIVRO
        WHERE a.disponivel = true 
        AND L.ID_USUARIO = ?
        ORDER BY a.id_livro ASC;
            """
                , (id_usuario,))

    livros_listados = cur.fetchall()

    livros = []
    for r in livros_listados:
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


@app.route('/livros/minhalista/adicionar/<int:id_livro>', methods=["POST"])
def adicionar_na_minha_lista(id_livro):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    id_usuario = informar_verificacao(trazer_pl=True)["id_usuario"]

    cur = con.cursor()
    try:

        # Verificações
        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ?", (id_livro,))
        if not cur.fetchone():
            return jsonify({"message": "ID de livro não encontrado."}), 404

        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ? AND DISPONIVEL = FALSE", (id_livro,))
        if cur.fetchone():
            return jsonify({"message": "Este livro não está disponível."}), 401

        cur.execute("""
            SELECT 1 FROM ACERVO A 
            INNER JOIN LISTAGEM L ON L.ID_LIVRO = A.ID_LIVRO 
            WHERE A.ID_LIVRO = ? AND L.ID_USUARIO = ?
            """, (id_livro, id_usuario))
        if cur.fetchone():
            return jsonify({"message": "Este livro já está em sua lista."}), 401

        # Adicionando na tabela de listagem
        cur.execute("INSERT INTO LISTAGEM (ID_USUARIO, ID_LIVRO) VALUES(?, ?)", (id_usuario, id_livro,))
        con.commit()
        return jsonify({"message": "Livro adicionado em Minha Lista."}), 200
    except Exception:
        print("Erro ao adicionar livro em minhalista")
        raise
    finally:
        cur.close()


@app.route("/livros/minhalista/excluir/<int:id_livro>", methods=["DELETE"])
def excluir_da_minha_lista(id_livro):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    id_usuario = informar_verificacao(trazer_pl=True)["id_usuario"]

    cur = con.cursor()
    try:

        # Verificações
        cur.execute("SELECT 1 FROM LISTAGEM WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id_livro, id_usuario,))
        if not cur.fetchone():
            cur.close()
            return jsonify({"message": "ID de livro não encontrado."}), 404

        # Excluindo da tabela de listagem
        cur.execute("DELETE FROM LISTAGEM WHERE ID_USUARIO = ? AND ID_LIVRO = ?", (id_usuario, id_livro,))
        con.commit()
        return jsonify({"message": "Livro excluído de Minha Lista."}), 200
    except Exception:
        print("Erro ao excluir livro de minha lista")
        raise
    finally:
        cur.close()


@app.route("/livros/minhalista/<int:id_livro>/verificar", methods=["GET"])
def lista_by_id(id_livro):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    id_usuario = informar_verificacao(trazer_pl=True)["id_usuario"]

    cur = con.cursor()
    try:

        # Verificações
        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ?", (id_livro,))
        if not cur.fetchone():
            cur.close()
            return jsonify({"message": "ID de livro não encontrado."}), 404

        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ? AND DISPONIVEL = FALSE", (id_livro,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Este livro não está disponível."}), 401

        cur.execute("""
            SELECT 1 FROM ACERVO A 
            FULL JOIN LISTAGEM L ON L.ID_LIVRO = A.ID_LIVRO 
            WHERE A.ID_LIVRO = ? AND L.ID_USUARIO = ?
            """, (id_livro, id_usuario))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Este livro já está em sua lista.", "inList": True}), 200
        else:
            cur.close()
            return jsonify({"message": "Este livro não está em sua lista.", "inList": False}), 200
    except Exception:
        print("Erro ao verificar livro")
        raise
    finally:
        cur.close()


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
        cur.close()
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

            assunto = f"{nome}, Sua Reserva Foi Cancelada"
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

            assunto = f"{nome}, Seu Empréstimo Foi Cancelado"
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
            mensagem = f"O livro {titulo}, de {autor} foi marcado como indisponível, o devolva o quanto antes."
            criar_notificacao(usuario, mensagem, "Aviso de Cancelamento de Empréstimo")

    cur.execute("SELECT ID_USUARIO FROM LISTAGEM WHERE ID_LIVRO = ?", (id_livro,))
    listadores_livro = cur.fetchall()
    for listador in listadores_livro:
        # Excluindo da tabela de listagem
        id_usuario = listador[0]
        cur.execute("DELETE FROM LISTAGEM WHERE ID_USUARIO = ? AND ID_LIVRO = ?", (id_usuario, id_livro,))

        mensagem = f"O livro {titulo}, de {autor} agora está indisponível e portanto foi retirado de sua lista."
        titulo = "Aviso sobre Seus Livros Listados"
        criar_notificacao(id_usuario, mensagem, titulo)

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

    multar_por_id_emprestimo(id)

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

            data_validade = devolucao(data_validade=True)
            data_validade_format = formatar_timestamp(data_validade)

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

                mensagem_notificacao = f"""Uma reserva sua foi alterada para "em espera",
                 venha para a biblioteca até {data_validade_format} para ser atendido."""
                criar_notificacao(usuario[0], mensagem_notificacao, "Aviso de Reserva")

                corpo = f"""
                        Uma reserva sua agora está em espera! 
                        Compareça à biblioteca em até 
                        <strong>{configuracoes()[2]} dias ({data_validade_format})</strong></p>
                        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;"><strong>Livros que você está tentando reservar:</strong></p>
                        <ul style="padding-left: 20px; font-size: 16px;">
                        """
                for titulo, autor in livros_reservados:
                    corpo += f"<li>{titulo}, por {autor}</li>"
                corpo += f"""
                </ul>
                <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">Agora,
                 vá até a biblioteca para realizar o empréstimo e retirar os livros (ou cancelar),
                  a biblioteca está em <strong>{configuracoes()[5]}</strong>.</p>"""
                enviar_email_async(usuario[2], "Aviso de reserva", corpo)

    # Verificar se este empréstimo possui multas criadas pela função multar_quem_precisa e enviar e-mail para a pessoa
    cur.execute("""
        SELECT U.ID_USUARIO, U.NOME, U.EMAIL, M.VALOR_BASE, M.VALOR_ACRESCIMO, E.DATA_DEVOLVER, M.ID_MULTA FROM USUARIOS U
        JOIN EMPRESTIMOS E ON E.ID_USUARIO = U.ID_USUARIO
        INNER JOIN MULTAS M ON M.ID_EMPRESTIMO =  E.ID_EMPRESTIMO
        WHERE M.PAGO = FALSE AND M.ID_EMPRESTIMO = ?
    """, (id,))

    tangao = cur.fetchone()

    if tangao:
        data_add = tangao[5]
        data_add = data_add.date()

        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        dias_passados = (data_atual - data_add).days

        # Pegando valores
        cur.execute("""SELECT VALOR_BASE, VALOR_ACRESCIMO
                    FROM MULTAS
                    WHERE ID_MULTA = ?
                """, (tangao[6],))

        valores = cur.fetchone()

        valor_base = valores[0]
        valor_ac = valores[1]

        valor = valor_base + valor_ac * dias_passados
        valor2 = valor
        valor2 = str(valor2)
        valor2.replace('.', ', ')
        # print(f"Valor antes da formatação: {valor}")
        valor = str(valor)
        # print(f"Valor string: {valor}")
        valor = valor.replace('.', '')
        # print(f"Valor depois da formatação: {valor}")
        valor = int(valor)

        nome = tangao[1]
        email = tangao[2]

        chave_pix = configuracoes()[3]
        chave_pix = formatar_telefone(chave_pix)
        if chave_pix == 0:
            cur.close()
            return jsonify(
                {"message": "Erro ao recuperar chave PIX, edite ela nas configurações para um telefone válido."})

        # Gerando código de pix para enviar para o e-mail de quem tem multa
        pix = PixQrCode("Read Raccoon", chave_pix, "Birigui", str(valor))

        # Guardar imagem na aplicação para que o e-mail a pegue depois e use como anexo
        if not os.path.exists(f"{app.config['UPLOAD_FOLDER']}/codigos-pix"):
            if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
            pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "codigos-pix")
            os.makedirs(pasta_destino, exist_ok=True)
        pix.save_qrcode(filename=f"{app.config['UPLOAD_FOLDER']}/codigos-pix/{str(valor)}")
        # print("Novo quick response code de pix criado")

        assunto = f'Aviso de Multa'
        corpo = f"""
                    Olá {nome}, você possui uma multa por não entregar um empréstimo a tempo. 
                    O valor é de R$ {valor2}.
                """
        enviar_email_async(email, assunto, corpo, f"{valor}.png")
        criar_notificacao(tangao[0], 'Você possui uma multa por entregar um empréstimo com atraso.', 'Aviso de Multa')

    con.commit()
    cur.close()

    return jsonify({"message": "Devolução realizada com sucesso."}), 200


# @app.route('/renovar_emprestimo', methods=["PUT"])
# def renovar_emprestimo():
#     verificacao = informar_verificacao()
#     if verificacao:
#         return verificacao
#     data = request.get_json()
#     id_emprestimo = data.get("id_emprestimo")
#     dias = data.get("dias")
#
#     if not all([dias, id_emprestimo]):
#         return jsonify({"message": "Todos os campos são obrigatórios."}), 401
#
#     cur = con.cursor()
#
#     # Verificar se o id existe e se já não foi devolvido o empréstimo
#     cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id_emprestimo,))
#     if not cur.fetchone():
#         return jsonify({"message": "Id de empréstimo não existe."}), 404
#     cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE DATA_DEVOLVIDO IS NOT NULL AND ID_EMPRESTIMO = ?", (id_emprestimo,))
#     if cur.fetchone():
#         return jsonify({"message": "Este empréstimo já teve sua devolução."}), 404
#
#     cur.execute("""UPDATE EMPRESTIMOS SET
#     DATA_DEVOLVER = DATEADD(DAY, ?, CURRENT_DATE) WHERE ID_EMPRESTIMO = ?""", (dias, id_emprestimo,))
#     con.commit()
#     cur.close()
#     return jsonify({"message": "Empréstimo renovado com sucesso."}), 200


@app.route('/upload/usuario', methods=["POST"])
def enviar_imagem_usuario():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao
    payload = informar_verificacao(trazer_pl=True)

    imagem = request.files.get("imagem")
    id_usuario = payload["id_usuario"]

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

    return jsonify(
        {
            "message": "Imagem editada com sucesso."
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

    cur.execute("SELECT ID_USUARIO, NOME, EMAIL FROM USUARIOS u "
                "WHERE u.ID_USUARIO IN (SELECT r.ID_USUARIO FROM RESERVAS r WHERE r.ID_RESERVA = ?)", (id_reserva, ))

    usuario = cur.fetchone()
    cur.execute("""
                SELECT TITULO, AUTOR FROM ACERVO a 
                INNER JOIN ITENS_RESERVA ie ON ie.ID_LIVRO = a.ID_LIVRO 
                WHERE ie.ID_RESERVA = ?""", (id_reserva,))
    livros = cur.fetchall()

    corpo = f"""
            Uma reserva sua foi cancelada por um bibliotecário. 
            </p>
            <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
            <strong>Livros que você estava tentando pegar:</strong></p>
            <ul style="padding-left: 20px; font-size: 16px;">
            """
    for titulo, autor in livros:
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += f"""
            </ul>"""
    enviar_email_async(usuario[2], "Cancelamento de Reserva", corpo)
    criar_notificacao(usuario[0], f"""Uma reserva sua foi cancelada por um bibliotecário.
    """, "Cancelamento de Reserva")

    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva cancelada com sucesso."
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

    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    sql += f" ORDER BY a.titulo ROWS {inicial} TO {final}"

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


@app.route('/livros/pesquisa/gerenciar/<int:pagina>', methods=["POST"])
def pesquisar_livros_biblio(pagina):
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

    sql += " ORDER BY a.titulo "

    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    sql += f'ROWS {inicial} to {final}'

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

        # Ver se o livro existe
        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ?", (id,))
        if not cur.fetchone():
            return jsonify({"message": "Tentativa de avaliar livro inexistente."}), 404

        # Ver se o usuário já leu o livro e só então liberar caso tenha
        sql = """
            SELECT 1 FROM EMPRESTIMOS 
            WHERE ID_EMPRESTIMO IN (SELECT ID_EMPRESTIMO FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?) 
            AND ID_USUARIO = ?"""

        cur.execute(sql, (id, id_usuario,))

        if not cur.fetchone():
            return jsonify({"message": "Você precisa ler o livro antes de o avaliar."}), 401

        cur.execute("SELECT 1 FROM AVALIACOES WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id, id_usuario,))
        if cur.fetchone():
            # print("editado")
            cur.execute("UPDATE AVALIACOES SET VALOR_TOTAL = ? WHERE ID_LIVRO = ? AND ID_USUARIO = ?",
                        (valor, id, id_usuario,))
            con.commit()
            cur.close()
            return jsonify({"message": "Livro avaliado."}), 200
        else:
            # print("inserido")
            cur.execute("INSERT INTO AVALIACOES (VALOR_TOTAL, ID_LIVRO, ID_USUARIO) VALUES (?, ?, ?)",
                        (valor, id, id_usuario))
    except Exception as e:
        print(e)
        return jsonify({
            "error": f"Erro ao editar registro de avaliação: {e}"}), 500

    return jsonify({
        "message": "Livro avaliado."
    }), 200


@app.route("/avaliarlivro/<int:id>", methods=["DELETE"])
def delete_avaliacao_livro(id):
    try:
        verificacao = informar_verificacao()
        if verificacao:
            return verificacao
        payload = informar_verificacao(trazer_pl=True)

        id_usuario = payload['id_usuario']
        cur = con.cursor()

        # Verifica se o livro existe
        cur.execute("SELECT 1 FROM ACERVO WHERE ID_LIVRO = ?", (id,))
        if not cur.fetchone():
            return jsonify({"message": "Tentativa de deletar avaliação de livro inexistente."}), 404

        # Verifica se a avaliação existe
        cur.execute("SELECT 1 FROM AVALIACOES WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id, id_usuario,))
        if not cur.fetchone():
            return jsonify({"message": "Avaliação não encontrada para exclusão."}), 404

        # Deleta a avaliação
        cur.execute("DELETE FROM AVALIACOES WHERE ID_LIVRO = ? AND ID_USUARIO = ?", (id, id_usuario,))
        con.commit()
        cur.close()

        return jsonify({"message": "Avaliação deletada com sucesso."}), 200

    except Exception as e:
        print(e)
        return jsonify({"error": f"Erro ao deletar avaliação: {e}"}), 500


@app.route("/livros/<int:id>", methods=["GET"])
def get_livros_id(id):
    livro = buscar_livro_por_id(id, True)
    if not livro:
        return jsonify({"error": "Livro não encontrado."}), 404
    return jsonify(livro)


@app.route('/relatorio/multaspendentes/<int:pagina>', methods=['GET'])
def relatorio_multas_pendentes_json(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    sql = """
            SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.data_devolver < CURRENT_DATE
            AND pago = false
            ORDER BY m.DATA_ADICIONADO
            """

    sql += f' ROWS {inicial} to {final}'

    cur = con.cursor()
    cur.execute(sql)

    multas_pendentes = cur.fetchall()

    # subtitulos = ["id", "titulo", "autor", "categoria", "isbn", "qtd_disponivel", "descricao", "idiomas",
    # "ano_publicado"]

    # livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

    cur.execute("""
            SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.data_devolver < CURRENT_DATE
            AND pago = false
            ORDER BY m.DATA_ADICIONADO
            """)
    multas = cur.fetchall()
    cur.close()

    return jsonify({
        "total": len(multas),
        "multas_pendentes": multas_pendentes
    })


@app.route('/relatorio/multas/<int:pagina>', methods=['GET'])
def relatorio_multas_json(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')
    sql = """
            SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver, m.pago
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.data_devolver < CURRENT_DATE
            ORDER BY m.DATA_ADICIONADO
            """
    sql += f' ROWS {inicial} TO {final}'
    cur.execute(sql)
    multas = cur.fetchall()

    cur.execute("""
            SELECT u.email, u.telefone, u.nome, e.id_emprestimo, e.data_devolver, m.pago
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.data_devolver < CURRENT_DATE
            ORDER BY m.DATA_ADICIONADO
            """)
    multas2 = cur.fetchall()

    return jsonify({
        "total": len(multas2),
        "multas": multas
    })


@app.route('/relatorio/livros/faltando/<int:pagina>', methods=['GET'])
def relatorio_livros_faltando_json(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')
    sql = """
    SELECT 
        a.id_livro, 
        a.titulo, 
        (SELECT COUNT(*) FROM ITENS_EMPRESTIMO IE
            WHERE IE.ID_LIVRO = a.ID_LIVRO
            AND IE.ID_EMPRESTIMO IN
            (SELECT E.ID_EMPRESTIMO FROM EMPRESTIMOS E WHERE E.STATUS IN ('PENDENTE', 'ATIVO'))) AS QTD_EMPRESTADA,
        a.qtd_disponivel,
        a.autor,
        a.categoria, 
        a.isbn,
        a.ano_publicado,
        LIST(u.nome) AS usuarios,
        LIST(u.ID_USUARIO) AS id_usuarios
    FROM acervo a
    INNER JOIN itens_emprestimo ie ON a.id_livro = ie.id_livro
    INNER JOIN emprestimos e ON ie.id_emprestimo = e.id_emprestimo
    INNER JOIN usuarios u ON u.id_usuario = e.id_usuario
    WHERE ie.ID_EMPRESTIMO IN (SELECT E.ID_EMPRESTIMO FROM EMPRESTIMOS E WHERE E.STATUS IN ('PENDENTE', 'ATIVO'))
    GROUP BY 
        a.id_livro, 
        a.titulo, 
        a.autor, 
        a.categoria, 
        a.isbn, 
        a.qtd_disponivel,  
        a.ano_publicado
    ORDER BY a.id_livro

        """
    sql += f' ROWS {inicial} to {final}'
    cur.execute(sql)
    livros = cur.fetchall()

    subtitulos = ["id", "titulo", "qtd_emprestada", "qtd_total", "autor", "categoria", "isbn", "ano_publicado",
                  "usuarios", "id_usuarios"]

    livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

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

    return jsonify({
        "total": len(livros),
        "livros": livros_json
    })


@app.route('/relatorio/livros/<int:pagina>', methods=['GET'])
def relatorio_livros_json(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')
    sql = """
        SELECT 
            a.id_livro, 
            a.titulo,
            (SELECT COUNT(*) FROM ITENS_EMPRESTIMO IE
            WHERE IE.ID_LIVRO = a.ID_LIVRO
            AND IE.ID_EMPRESTIMO IN
            (SELECT E.ID_EMPRESTIMO FROM EMPRESTIMOS E WHERE E.STATUS IN ('PENDENTE', 'ATIVO'))) AS QTD_EMPRESTADA,
            a.QTD_DISPONIVEL, 
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.DESCRICAO, 
            a.idiomas, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        ORDER BY a.id_livro DESC
    """
    sql += f' ROWS {inicial} to {final}'
    cur.execute(sql)
    livros = cur.fetchall()

    subtitulos = ["id", "titulo", "qtd_emprestada", "qtd_total", "autor", "categoria", "isbn", "descricao", "idiomas",
                  "ano_publicado"]

    livros_json = [dict(zip(subtitulos, livro)) for livro in livros]

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
        ORDER BY a.id_livro
    """)
    livros = cur.fetchall()
    cur.close()

    return jsonify({
        "total": len(livros),
        "livros": livros_json
    })


@app.route('/relatorio/usuarios/<int:pagina>', methods=['GET'])
def relatorio_usuarios_json(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')
    sql = """
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco
        FROM USUARIOS
        ORDER BY id_usuario
    """
    sql += f' ROWS {inicial} to {final}'
    cur.execute(sql)
    usuarios = cur.fetchall()

    subtitulos = ["id", "nome", "email", "telefone", "endereco"]
    usuarios_json = [dict(zip(subtitulos, u)) for u in usuarios]

    cur.execute("""
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco
        FROM USUARIOS
        ORDER BY id_usuario
    """)
    usuarios = cur.fetchall()
    cur.close()

    return jsonify({
        "total": len(usuarios),
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
        dados.titulo,
        dados.qtd_emprestada,
        dados.qtd_disponivel,
        dados.autor,
        dados.categoria,
        dados.isbn,
        dados.idiomas,
        dados.ano_publicado,
        LIST(dados.usuario_info) AS usuarios
    FROM (
        SELECT  
            a.id_livro,
            a.titulo, 
            a.qtd_disponivel,
            a.autor, 
            a.categoria, 
            a.isbn, 
            a.IDIOMAS,
            a.ano_publicado,
            u.nome || ' (' || u.email || ', ' || u.telefone || ') (Retirado em: ' || e.DATA_RETIRADA || ', Devolver em: ' || e.DATA_DEVOLVER || ')' AS usuario_info,
            (SELECT COUNT(*) FROM ITENS_EMPRESTIMO IE2
                WHERE IE2.ID_LIVRO = a.ID_LIVRO
                AND IE2.ID_EMPRESTIMO IN
                (SELECT E2.ID_EMPRESTIMO FROM EMPRESTIMOS E2 WHERE E2.STATUS IN ('PENDENTE', 'ATIVO'))
            ) AS qtd_emprestada
        FROM acervo a
        INNER JOIN itens_emprestimo ie ON a.id_livro = ie.id_livro
        INNER JOIN emprestimos e ON ie.id_emprestimo = e.id_emprestimo
        INNER JOIN usuarios u ON u.id_usuario = e.id_usuario
        WHERE e.status = 'ATIVO'
    ) dados
    GROUP BY  
        dados.id_livro,
        dados.titulo,
        dados.qtd_emprestada,
        dados.qtd_disponivel,
        dados.autor,
        dados.categoria, 
        dados.isbn, 
        dados.idiomas,
        dados.ano_publicado
    ORDER BY dados.qtd_emprestada DESC;

    """)
    livros = cur.fetchall()
    cur.close()

    data = [
        ("Livro", "QTD Emprestada", "QTD Total",
         "Autor", "Categoria", "ISBN",
         "Idiomas", "Publicação", "Portadores")
    ]
    mm_pdf = 190.0015555555555
    multiplicador = 0.11111111111111111111111111111111
    larguras = [mm_pdf * (multiplicador - 0.02), mm_pdf * multiplicador, mm_pdf * (multiplicador - 0.04),
                mm_pdf * multiplicador, mm_pdf * multiplicador, mm_pdf * (multiplicador - 0.02),
                mm_pdf * (multiplicador - 0.02), mm_pdf * multiplicador, mm_pdf * (multiplicador + 0.1), ]
    for livro in livros:
        data.append(livro)
    contador_livros = len(livros)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Livros Emprestados", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de livros emprestados: {contador_livros}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha

    pdf.set_font("Arial", size=10)

    line_height = pdf.font_size * 2
    # col_width = pdf.epw / 10

    lh_list = []  # list with proper line_height for each row
    use_default_height = 0  # flag

    # create lh_list of line_heights which size is equal to num rows of data
    for row in data:
        for dado in row:
            dado = str(dado)
            word_list = dado.split()
            number_of_words = len(word_list)  # how many words
            if number_of_words > 2:
                use_default_height = 1
                new_line_height = pdf.font_size * (number_of_words / 1.15)  # new height change according to data
        if not use_default_height:
            lh_list.append(line_height)
        else:
            lh_list.append(new_line_height)
        use_default_height = 0

    # create your fpdf table ..passing also max_line_height!
    for j, row in enumerate(data):
        i = 0
        for dado in row:
            dado = str(dado)
            dado = dado.encode('latin-1', 'ignore').decode('latin-1')
            line_height = lh_list[j]  # choose right height for current row
            pdf.multi_cell(larguras[i], line_height, dado, border=1, align='C', ln=3,
                           max_line_height=pdf.font_size)
            i += 1
        pdf.ln(line_height)

    pdf_path = "relatorio_livros_faltando.pdf"
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
            a.titulo,
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL,
            (SELECT COUNT(*) FROM ITENS_EMPRESTIMO IE2
            WHERE IE2.ID_LIVRO = a.ID_LIVRO
            AND IE2.ID_EMPRESTIMO IN
            (SELECT E2.ID_EMPRESTIMO FROM EMPRESTIMOS E2 WHERE E2.STATUS IN ('PENDENTE', 'ATIVO'))
            ) AS qtd_emprestada,
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        FROM ACERVO a
        WHERE a.DISPONIVEL = TRUE
        GROUP BY
            a.id_livro,
            a.titulo,
            a.autor, 
            a.CATEGORIA, 
            a.ISBN, 
            a.QTD_DISPONIVEL, 
            a.IDIOMAS, 
            a.ANO_PUBLICADO
        ORDER BY a.id_livro
    """)
    livros = cur.fetchall()
    cur.close()

    data = [
        ("Livro", "Autor", "Categoria",
         "ISBN", "QTD Total", "QTD Emprestada",
         "Idiomas", "Publicação")
    ]
    mm_pdf = 190.0015555555555
    multiplicador = 0.125
    larguras = [mm_pdf * (multiplicador - 0.03), mm_pdf * multiplicador, mm_pdf * multiplicador,
                mm_pdf * (multiplicador + 0.05), mm_pdf * multiplicador, mm_pdf * multiplicador,
                mm_pdf * multiplicador, mm_pdf * (multiplicador - 0.02)]
    for livro in livros:
        data.append(livro)
    contador_livros = len(livros)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Livros", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de livros: {contador_livros}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha

    pdf.set_font("Arial", size=10)

    line_height = pdf.font_size * 2
    # col_width = pdf.epw / 10

    lh_list = []  # list with proper line_height for each row
    use_default_height = 0  # flag

    # create lh_list of line_heights which size is equal to num rows of data
    for row in data:
        for dado in row:
            dado = str(dado)
            word_list = dado.split()
            number_of_words = len(word_list)  # how many words
            if number_of_words > 2:  # names and cities formed by 2 words like Los Angeles are ok)
                use_default_height = 1
                new_line_height = pdf.font_size * (number_of_words / 1)  # new height change according to data
        if not use_default_height:
            lh_list.append(line_height)
        else:
            lh_list.append(new_line_height)
        use_default_height = 0

    # create your fpdf table ..passing also max_line_height!
    for j, row in enumerate(data):
        i = 0
        for dado in row:
            dado = str(dado)
            dado = dado.encode('latin-1', 'ignore').decode('latin-1')
            line_height = lh_list[j]  # choose right height for current row
            pdf.multi_cell(larguras[i], line_height, dado, border=1, align='C', ln=3,
                           max_line_height=pdf.font_size)
            i += 1
        pdf.ln(line_height)

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
        ORDER BY id_usuario DESC;
    """)
    usuarios = cur.fetchall()
    cur.close()
    contador_usuarios = len(usuarios)

    data = [
        ("id_usuario", "Nome", "Email",
         "Telefone", "Endereço")
    ]
    mm_pdf = 190.0015555555555
    multiplicador = 0.25
    larguras = [mm_pdf * multiplicador, mm_pdf * multiplicador,
                mm_pdf * multiplicador, mm_pdf * multiplicador, 0]
    for livro in usuarios:
        data.append(livro)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Usuários", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de Usuários Cadastrados: {contador_usuarios}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha

    pdf.set_font("Arial", size=10)

    line_height = pdf.font_size * 1.75
    # col_width = pdf.epw / 10

    lh_list = []  # list with proper line_height for each row
    use_default_height = 0  # flag

    # create lh_list of line_heights which size is equal to num rows of data
    for row in data:
        for dado in row:
            dado = str(dado)
            word_list = dado.split()
            number_of_words = len(word_list)  # how many words
            if number_of_words > 2:  # names and cities formed by 2 words like Los Angeles are ok)
                use_default_height = 1
                new_line_height = pdf.font_size * (number_of_words / 1)  # new height change according to data
        if not use_default_height:
            lh_list.append(line_height)
        else:
            lh_list.append(new_line_height)
        use_default_height = 0

    # create your fpdf table ..passing also max_line_height!
    for j, row in enumerate(data):
        i = 0
        for dado in row:
            # print(f"Dado: {dado}, i: {i}")
            if i == 0:
                i += 1
                continue
            dado = str(dado)
            dado = dado.encode('latin-1', 'ignore').decode('latin-1')
            line_height = lh_list[j]  # choose right height for current row
            pdf.multi_cell(larguras[i], line_height, dado, border=1, align='C', ln=3,
                           max_line_height=pdf.font_size)
            i += 1
        pdf.ln(line_height)

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
            SELECT u.nome, u.email, u.telefone, u.endereco, e.data_devolver, m.pago
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE u.id_usuario IN (SELECT m.ID_USUARIO FROM MULTAS m)
            ORDER BY m.DATA_ADICIONADO DESC
        """)
    tangoes = cur.fetchall()
    cur.close()

    data = [
        ("Nome", "Email", "Telefone",
         "Endereço", "Data de Devolver", "Paga")
    ]
    mm_pdf = 190.0015555555555
    multiplicador = 0.16666666666666666666666666666667
    larguras = [mm_pdf * multiplicador, mm_pdf * multiplicador, mm_pdf * multiplicador,
                mm_pdf * multiplicador, mm_pdf * multiplicador, mm_pdf * multiplicador]
    contador_usuarios = len(tangoes)
    for livro in tangoes:
        data.append(livro)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Multas", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de Multas: {contador_usuarios}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha

    pdf.set_font("Arial", size=10)

    line_height = pdf.font_size * 1.75
    # col_width = pdf.epw / 10

    lh_list = []  # list with proper line_height for each row
    use_default_height = 0  # flag

    # create lh_list of line_heights which size is equal to num rows of data
    for row in data:
        for dado in row:
            dado = str(dado)
            word_list = dado.split()
            number_of_words = len(word_list)  # how many words
            if number_of_words > 2:  # names and cities formed by 2 words like Los Angeles are ok)
                use_default_height = 1
                new_line_height = pdf.font_size * (number_of_words / 1)  # new height change according to data
        if not use_default_height:
            lh_list.append(line_height)
        else:
            lh_list.append(new_line_height)
        use_default_height = 0

    # create your fpdf table ..passing also max_line_height!
    for j, row in enumerate(data):
        i = 0
        for dado in row:
            # print(f"Dado: {dado}, i: {i}")
            dado2 = dado
            if dado == True:
                dado2 = "Sim"
            elif dado == False:
                dado2 = "Não"
            dado2 = str(dado2)
            dado2 = dado2.encode('latin-1', 'ignore').decode('latin-1')
            line_height = lh_list[j]  # choose right height for current row
            pdf.multi_cell(larguras[i], line_height, dado2, border=1, align='C', ln=3,
                           max_line_height=pdf.font_size)
            i += 1
        pdf.ln(line_height)

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
            SELECT u.nome, u.email, u.telefone, u.endereco, e.data_devolver, m.pago
            FROM emprestimos e
            JOIN usuarios u ON e.id_usuario = u.id_usuario
            JOIN MULTAS m ON e.id_emprestimo = m.id_emprestimo
            WHERE e.data_devolver < CURRENT_DATE
            AND u.id_usuario IN (SELECT m.ID_USUARIO FROM MULTAS m) and m.pago = false
            ORDER BY m.DATA_ADICIONADO DESC
        """)
    tangoes = cur.fetchall()
    cur.close()

    data = [
        ("Nome", "Email", "Telefone",
         "Endereço", "Data de Devolver", "Paga")
    ]
    mm_pdf = 190.0015555555555
    multiplicador = 0.16666666666666666666666666666667
    larguras = [mm_pdf * multiplicador, mm_pdf * multiplicador, mm_pdf * multiplicador,
                mm_pdf * multiplicador, mm_pdf * multiplicador, mm_pdf * multiplicador]
    contador_usuarios = len(tangoes)
    for livro in tangoes:
        data.append(livro)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de Multas Pendentes", ln=True, align='C')
    pdf.set_font("Arial", style='B', size=13)
    pdf.cell(200, 10, f"Total de Multas Pendentes: {contador_usuarios}", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha

    pdf.set_font("Arial", size=10)

    line_height = pdf.font_size * 1.75
    # col_width = pdf.epw / 10

    lh_list = []  # list with proper line_height for each row
    use_default_height = 0  # flag

    # create lh_list of line_heights which size is equal to num rows of data
    for row in data:
        for dado in row:
            dado = str(dado)
            word_list = dado.split()
            number_of_words = len(word_list)  # how many words
            if number_of_words > 2:  # names and cities formed by 2 words like Los Angeles are ok)
                use_default_height = 1
                new_line_height = pdf.font_size * (number_of_words / 1)  # new height change according to data
        if not use_default_height:
            lh_list.append(line_height)
        else:
            lh_list.append(new_line_height)
        use_default_height = 0

    # create your fpdf table ..passing also max_line_height!
    for j, row in enumerate(data):
        i = 0
        for dado in row:
            # print(f"Dado: {dado}, i: {i}")
            dado2 = dado
            if dado == True:
                dado2 = "Sim"
            elif dado == False:
                dado2 = "Não"
            dado2 = str(dado2)
            dado2 = dado2.encode('latin-1', 'ignore').decode('latin-1')
            line_height = lh_list[j]  # choose right height for current row
            pdf.multi_cell(larguras[i], line_height, dado2, border=1, align='C', ln=3,
                           max_line_height=pdf.font_size)
            i += 1
        pdf.ln(line_height)

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


@app.route('/usuarios/<int:pagina>', methods=["get"])
def usuarios(pagina):
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
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    return jsonify(listaUsuarios[inicial - 1:final])


@app.route('/uploads/<tipo>/<filename>')
def serve_file(tipo, filename):
    pasta_permitida = ["usuarios", "livros", "banners"]  # Apenas pastas permitidas
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


@app.route("/historico/emprestimos_pendentes/<int:pagina>", methods=["GET"])
def historico_emprestimos_pendentes(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_VALIDADE
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL AND E.STATUS = 'PENDENTE'
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id_logado,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_emprestimo": d[3], "data_validade": d[4]
        } for d in dados])
    finally:
        cur.close()


@app.route("/historico/emprestimos_ativos/<int:pagina>", methods=["GET"])
def historico_emprestimos_ativos(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL AND E.STATUS = 'ATIVO'
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id_logado,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_emprestimo": d[3], "data_retirada": d[4], "data_devolver": d[5]
        } for d in dados])
    finally:
        cur.close()


@app.route("/historico/emprestimos_concluidos/<int:pagina>", methods=["GET"])
def historico_emprestimos_concluidos(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NOT NULL
            ORDER BY E.DATA_DEVOLVIDO DESC
        """, (id_logado,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_emprestimo": d[3], "data_retirada": d[4],
            "data_devolver": d[5], "data_devolvido": d[6]
        } for d in dados])
    finally:
        cur.close()


@app.route("/historico/reservas_ativas/<int:pagina>", methods=["GET"])
def historico_reservas_ativas(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("""
            SELECT IR.ID_LIVRO, A.TITULO, A.AUTOR, R.ID_RESERVA, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS
            FROM ITENS_RESERVA IR
            JOIN RESERVAS R ON IR.ID_RESERVA = R.ID_RESERVA
            JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
            WHERE R.ID_USUARIO = ?
            ORDER BY R.DATA_VALIDADE ASC
        """, (id_logado,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_reserva": d[3], "data_criacao": d[4],
            "data_validade": d[5], "status": d[6]
        } for d in dados])
    finally:
        cur.close()


@app.route("/historico/multas_pendentes/<int:pagina>", methods=["GET"])
def historico_multas_pendentes(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO, M.VALOR_BASE, CAST(E.DATA_DEVOLVER AS DATE)
            FROM MULTAS M
            JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
            WHERE M.ID_USUARIO = ? AND M.PAGO = FALSE
        """, (id_logado,))
        dados = cur.fetchall()
        multas = [d + (((data_atual - d[6]).days * d[2]) + d[1],) for d in dados]
        i, f = calcular_paginacao(pagina)
        multas = multas[i - 1:f]
        return jsonify([{
            "id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2],
            "id_emprestimo": m[3], "pago": m[4], "total": m[7]
        } for m in multas])
    finally:
        cur.close()


@app.route("/historico/multas_concluidas/<int:pagina>", methods=["GET"])
def historico_multas_concluidas(pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    payload = informar_verificacao(trazer_pl=True)
    id_logado = payload["id_usuario"]
    cur = con.cursor()

    try:
        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO, M.VALOR_BASE, CAST(E.DATA_DEVOLVIDO AS DATE)
            FROM MULTAS M
            JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
            WHERE M.ID_USUARIO = ? AND M.PAGO = TRUE
        """, (id_logado,))
        dados = cur.fetchall()
        multas = [d + (((data_atual - d[6]).days * d[2]) + d[1],) for d in dados]
        i, f = calcular_paginacao(pagina)
        multas = multas[i - 1:f]
        return jsonify([{
            "id_multa": m[0], "valor_base": m[1], "valor_acrescimo": m[2],
            "id_emprestimo": m[3], "pago": m[4], "total": m[7]
        } for m in multas])
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
    limite_res = configuracoes()[9]

    cur = con.cursor()

    # Livros em carrinho:
    cur.execute("SELECT COUNT(*) FROM CARRINHO_RESERVAS cr WHERE cr.ID_USUARIO = ?", (id_usuario,))
    qtd_carrinho = cur.fetchone()[0]

    # Livros em empréstimos ativos:
    cur.execute("""
            SELECT COUNT(*) FROM ITENS_RESERVA ir WHERE ir.ID_RESERVA IN
    (SELECT r.ID_RESERVA FROM RESERVAS r WHERE r.ID_USUARIO = ? AND r.STATUS = 'PENDENTE' )
        """, (id_usuario,))
    qtd_reservada_por_usuario = cur.fetchone()[0]

    qtd_reservada = qtd_carrinho + qtd_reservada_por_usuario
    if qtd_reservada >= limite_res:
        cur.close()
        return jsonify({"message": f"Seu limite de livros em reservas ({limite_res}) foi alcançado."}), 401

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
            (SELECT COUNT(*) FROM EMPRESTIMOS E INNER JOIN ITENS_EMPRESTIMO IE ON E.ID_EMPRESTIMO = IE.ID_EMPRESTIMO WHERE IE.ID_LIVRO = ? AND E.STATUS IN ('PENDENTE', 'ATIVO')) AS total_emprestimos
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

    mensagem = "Esse livro não está disponível no momento"

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

    assunto = nome + ", Uma Nota de Reserva"
    corpo = f"""
            Você fez uma <strong>reserva</strong>!, por enquanto ela está pendente, 
            quando os livros dela estiverem disponíveis 
            nós te avisaremos para vir buscar. Local: <strong>{configuracoes()[5]}</strong>.
        </p>
        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;"><strong>Livros reservados:</strong></p>
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
                      te avisaremos quando os livros estiverem prontos para você ir buscar na biblioteca.
                      """, "Aviso de Reserva")

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

    limite_emp = configuracoes()[8]

    cur = con.cursor()

    # Livros em carrinho:
    cur.execute("SELECT COUNT(*) FROM CARRINHO_EMPRESTIMOS ce WHERE ce.ID_USUARIO = ?", (id_usuario,))
    qtd_carrinho = cur.fetchone()[0]

    # Livros em empréstimos ativos:
    cur.execute("""
        SELECT COUNT(*) FROM ITENS_EMPRESTIMO ie WHERE ie.ID_EMPRESTIMO IN
    (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.ID_USUARIO = ? AND e.STATUS IN ('ATIVO', 'PENDENTE') )
    """, (id_usuario,))
    qtd_emprestada_por_usuario = cur.fetchone()[0]

    qtd_pega = qtd_carrinho + qtd_emprestada_por_usuario
    if qtd_pega >= limite_emp:
        cur.close()
        return jsonify({"message": f"Seu limite de livros em empréstimo ({limite_emp}) foi alcançado."}), 401

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

    limite_emp = configuracoes()[8]
    # Livros em carrinho:
    cur.execute("SELECT COUNT(*) FROM CARRINHO_EMPRESTIMOS ce WHERE ce.ID_USUARIO = ?", (id_usuario,))
    qtd_carrinho = cur.fetchone()[0]

    # Livros em empréstimos ativos:
    cur.execute("""
            SELECT COUNT(*) FROM ITENS_EMPRESTIMO ie WHERE ie.ID_EMPRESTIMO IN
        (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.ID_USUARIO = ? AND e.STATUS IN ('ATIVO', 'PENDENTE') )
        """, (id_usuario,))
    qtd_emprestada_por_usuario = cur.fetchone()[0]

    qtd_pega = qtd_carrinho + qtd_emprestada_por_usuario
    if qtd_pega > limite_emp:
        cur.close()
        return jsonify({"message":
                            f"""Seu limite de livros em empréstimo ({limite_emp}) foi alcançado. Você possui {qtd_carrinho} no carrinho e 
        {qtd_emprestada_por_usuario} em empréstimos ativos ou pendentes.
"""}), 401

    # Cria o empréstimo — data_criacao já está com valor padrão no banco
    data_validade = devolucao(data_validade=True)
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

    assunto = nome + ", Sua Solicitação de Empréstimo Foi Registrada"
    corpo = f"""
        Você fez uma <strong>solicitação de empréstimo</strong>!</p>
        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;"><strong>Livros emprestados:</strong></p>
        <ul style="padding-left: 20px; font-size: 16px;">
        """
    for titulo, autor in livros_emprestados:
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += f"""</ul>
        <p style="font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
            Por enquanto esse empréstimo está marcado como pendente, 
            vá até a biblioteca para ser atendido e retirar os livros, em <strong>{configuracoes()[5]}</strong>.
        </p>"""

    enviar_email_async(email, assunto, corpo)
    cur.close()
    criar_notificacao(id_usuario,
                      """Você fez uma solicitação de empréstimo que por enquanto está pendente, 
    vá até a biblioteca para ser atendido.""", "Aviso de Empréstimo")

    return jsonify({"message": "Empréstimo registrado com sucesso. Venha para a biblioteca para ser atendido."}), 200


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

@app.route("/historico/<int:id_usuario>/emprestimos_ativos/<int:pagina>", methods=["GET"])
def historico_emprestimos_ativos_por_usuario(id_usuario, pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL
            AND STATUS = 'ATIVO'
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id_usuario,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_emprestimo": d[3], "data_retirada": d[4], "data_devolver": d[5]
        } for d in dados])
    finally:
        cur.close()


@app.route("/historico/<int:id_usuario>/emprestimos_pendentes/<int:pagina>", methods=["GET"])
def historico_emprestimos_pendentes_por_id(id_usuario, pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()

    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_VALIDADE
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NULL AND E.STATUS = 'PENDENTE'
            ORDER BY E.DATA_DEVOLVER ASC
        """, (id_usuario,))

        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]

        return jsonify([{
            "id_livro": d[0],
            "titulo": d[1],
            "autor": d[2],
            "id_emprestimo": d[3],
            "data_validade": d[4]
        } for d in dados])
    finally:
        cur.close()

@app.route("/historico/<int:id_usuario>/emprestimos_concluidos/<int:pagina>", methods=["GET"])
def historico_emprestimos_concluidos_por_usuario(id_usuario, pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    try:
        cur.execute("""
            SELECT I.ID_LIVRO, A.TITULO, A.AUTOR, E.ID_EMPRESTIMO, E.DATA_RETIRADA, E.DATA_DEVOLVER, E.DATA_DEVOLVIDO
            FROM ITENS_EMPRESTIMO I
            JOIN EMPRESTIMOS E ON I.ID_EMPRESTIMO = E.ID_EMPRESTIMO
            JOIN ACERVO A ON I.ID_LIVRO = A.ID_LIVRO
            WHERE E.ID_USUARIO = ? AND E.DATA_DEVOLVIDO IS NOT NULL
            ORDER BY E.DATA_DEVOLVIDO DESC
        """, (id_usuario,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_emprestimo": d[3], "data_retirada": d[4],
            "data_devolver": d[5], "data_devolvido": d[6]
        } for d in dados])
    finally:
        cur.close()

@app.route("/historico/<int:id_usuario>/reservas_ativas/<int:pagina>", methods=["GET"])
def historico_reservas_ativas_por_usuario(id_usuario, pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    try:
        cur.execute("""
            SELECT IR.ID_LIVRO, A.TITULO, A.AUTOR, R.ID_RESERVA, R.DATA_CRIACAO, R.DATA_VALIDADE, R.STATUS
            FROM ITENS_RESERVA IR
            JOIN RESERVAS R ON IR.ID_RESERVA = R.ID_RESERVA
            JOIN ACERVO A ON IR.ID_LIVRO = A.ID_LIVRO
            WHERE R.ID_USUARIO = ?
            ORDER BY R.DATA_VALIDADE ASC
        """, (id_usuario,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_livro": d[0], "titulo": d[1], "autor": d[2],
            "id_reserva": d[3], "data_criacao": d[4],
            "data_validade": d[5], "status": d[6]
        } for d in dados])
    finally:
        cur.close()

@app.route("/historico/<int:id_usuario>/multas_pendentes/<int:pagina>", methods=["GET"])
def historico_multas_pendentes_por_usuario(id_usuario, pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    try:
        cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO
            FROM MULTAS M
            WHERE M.ID_USUARIO = ? AND M.PAGO = FALSE
        """, (id_usuario,))
        dados = cur.fetchall()
        i, f = calcular_paginacao(pagina)
        dados = dados[i - 1:f]
        return jsonify([{
            "id_multa": d[0], "valor_base": d[1], "valor_acrescimo": d[2],
            "id_emprestimo": d[3], "pago": d[4]
        } for d in dados])
    finally:
        cur.close()

@app.route("/historico/<int:id_usuario>/multas_concluidas/<int:pagina>", methods=["GET"])
def historico_multas_concluidas_por_id(id_usuario, pagina):
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    cur = con.cursor()

    try:
        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        cur.execute("""
            SELECT M.ID_MULTA, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.ID_EMPRESTIMO, M.PAGO, M.VALOR_BASE, CAST(E.DATA_DEVOLVIDO AS DATE)
            FROM MULTAS M
            JOIN EMPRESTIMOS E ON E.ID_EMPRESTIMO = M.ID_EMPRESTIMO
            WHERE M.ID_USUARIO = ? AND M.PAGO = TRUE
        """, (id_usuario,))

        dados = cur.fetchall()
        multas = [d + (((data_atual - d[6]).days * d[2]) + d[1],) for d in dados]
        i, f = calcular_paginacao(pagina)
        multas = multas[i - 1:f]

        return jsonify([{
            "id_multa": m[0],
            "valor_base": m[1],
            "valor_acrescimo": m[2],
            "id_emprestimo": m[3],
            "pago": m[4],
            "total": m[7]
        } for m in multas])
    finally:
        cur.close()



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


@app.route('/reserva/<int:id_reserva>/atender', methods=["PUT"])
def atender_reserva(id_reserva):
    verificacao = informar_verificacao(2)  # Apenas bibliotecários
    if verificacao:
        return verificacao

    cur = con.cursor()

    # Verifica se a reserva existe e está em espera
    cur.execute("""
        SELECT r.id_usuario 
        FROM reservas r
        WHERE r.id_reserva = ? AND r.status = 'EM ESPERA'
    """, (id_reserva,))
    dados = cur.fetchone()

    if not dados:
        cur.close()
        return jsonify({"message": "Reserva não encontrada ou já foi atendida/cancelada."}), 404

    id_usuario = dados[0]
    cur.execute("SELECT ID_LIVRO FROM ITENS_RESERVA WHERE ID_RESERVA = ?", (id_reserva, ))
    livros = cur.fetchall()
    data_devolver = devolucao()

    # Atualiza status da reserva para ATENDIDA
    cur.execute("""
        UPDATE reservas 
        SET status = 'ATENDIDA'
        WHERE id_reserva = ?
    """, (id_reserva,))

    # Cria novo empréstimo
    cur.execute("""
        INSERT INTO emprestimos (id_usuario, data_devolver, status, DATA_RETIRADA) 
        VALUES (?, ?, 'ATIVO', CURRENT_TIMESTAMP)
        RETURNING id_emprestimo
    """, (id_usuario, data_devolver))
    id_emprestimo = cur.fetchone()[0]

    # Associa os livros ao empréstimo
    for livro in livros:
        cur.execute("""
            INSERT INTO itens_emprestimo (id_emprestimo, id_livro) 
            VALUES (?, ?)
        """, (id_emprestimo, livro[0], ))

    cur.execute("SELECT NOME, EMAIL FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario,))
    nome, email = cur.fetchone()

    con.commit()

    cur.execute("""
    SELECT A.TITULO, A.AUTOR FROM ITENS_EMPRESTIMO IE 
    INNER JOIN ACERVO A ON A.ID_LIVRO = IE.ID_LIVRO 
    WHERE ID_RESERVA = ?
    """, (id_reserva,))

    livros_emprestados = cur.fetchall()

    cur.close()

    data_devolver = formatar_timestamp(str(data_devolver))
    corpo = f"""
        Olá {nome}, Você possui um empréstimo ativo feito a partir do atentimento de uma reserva. <br>
        Devolva até {data_devolver} para evitar multas. <br>
        Livros Emprestados: <br>
        <ul style="padding-left: 20px; font-size: 16px;">
        """

    for titulo, autor in livros_emprestados:
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += "</ul>"

    titulo = "Nota de Empréstimo por Atendimento de Reserva"

    enviar_email_async(email, titulo, corpo)
    criar_notificacao(id_usuario, f'Uma reserva sua foi atendida e agora é um empréstimo, devolva até {data_devolver}.',
                      titulo)

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
        SELECT e.id_usuario 
        FROM emprestimos e
        WHERE e.id_emprestimo = ? AND e.status = 'PENDENTE'
    """, (id_emprestimo,))
    dados = cur.fetchone()

    if not dados:
        cur.close()
        return jsonify({"message": "Emprestimo não encontrado ou já foi atendido/cancelado."}), 404

    id_usuario = dados[0]
    data_devolver = devolucao()

    cur.execute("""
    SELECT U.EMAIL, U.NOME FROM EMPRESTIMOS E 
    JOIN USUARIOS U ON U.ID_USUARIO = E.ID_USUARIO 
    WHERE E.ID_EMPRESTIMO = ?
    """, (id_emprestimo,))

    dados2 = cur.fetchone()
    email, nome = dados2

    cur.execute("""
        UPDATE emprestimos 
        SET status = 'ATIVO', data_devolver = ?, data_retirada = CURRENT_TIMESTAMP
        WHERE id_emprestimo = ?
    """, (data_devolver, id_emprestimo,))

    con.commit()

    # Enviar um e-mail de empréstimo confirmado e ativo

    cur.execute("""
    SELECT A.TITULO, A.AUTOR FROM ACERVO A 
    WHERE A.ID_LIVRO IN (SELECT IE.ID_LIVRO FROM ITENS_EMPRESTIMO IE 
        WHERE IE.ID_EMPRESTIMO = ?)
    """, (id_emprestimo,))
    livros_emprestados = cur.fetchall()

    data_devolver = formatar_timestamp(str(data_devolver))
    corpo = f"""
    Olá {nome}, você fez um empréstimo que agora está marcado como "ATIVO". <br>
    Devolva até: {data_devolver} para evitar multas. <br>
    Livros emprestados: <br>
    <ul style="padding-left: 20px; font-size: 16px;">
    """

    for titulo, autor in livros_emprestados:
        corpo += f"<li>{titulo}, por {autor}</li>"
    corpo += "</ul>"

    titulo = "Nota de Empréstimo"

    enviar_email_async(email, titulo, corpo)
    criar_notificacao(id_usuario, f"Um empréstimo seu foi atendido, devolva até {data_devolver}", titulo)

    cur.close()
    return jsonify({
        "message": "Emprestimo atendido e registrado com sucesso.",
        "data_devolver": data_devolver
    }), 200


@app.route("/multas/<int:pagina>", methods=["GET"])
def get_all_multas(pagina):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao
    cur = con.cursor()
    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')
    sql = """
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
    """
    sql += f' ROWS {inicial} to {final}'
    cur.execute(sql)
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


@app.route("/usuarios/pesquisa/<int:pagina>", methods=["POST"])
def pesquisar_usuarios(pagina):
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

    sql += " ORDER BY u.nome "

    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    sql += f'ROWS {inicial} to {final}'

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


@app.route('/movimentacoes/<int:pagina>', methods=['GET'])
def get_all_movimentacoes(pagina):
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

    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    return jsonify(movimentacoes[inicial - 1:final])


@app.route("/movimentacoes/pesquisa/<int:pagina>", methods=["POST"])
def pesquisar_movimentacoes(pagina):
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

    inicial = pagina * 10 - 9 if pagina == 1 else pagina * 8 - 7
    final = pagina * 8
    # print(f'ROWS {inicial} to {final}')

    return jsonify(movimentacoes[inicial - 1:final])


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

    cur.execute("""
            SELECT U.NOME, U.EMAIL, M.VALOR_BASE, M.VALOR_ACRESCIMO, M.DATA_ADICIONADO, U.ID_USUARIO FROM USUARIOS U
            JOIN EMPRESTIMOS E ON E.ID_USUARIO = U.ID_USUARIO
            INNER JOIN MULTAS M ON M.ID_EMPRESTIMO =  E.ID_EMPRESTIMO
            WHERE M.ID_MULTA = ?
        """, (id_multa,))

    tangao = cur.fetchone()

    if tangao:
        data_add = tangao[4]

        nome = tangao[0]
        email = tangao[1]
        valor_base = tangao[2]
        valor_ac = tangao[3]

        cur.execute("SELECT CURRENT_DATE FROM RDB$DATABASE")
        data_atual = cur.fetchone()[0]

        dias_passados = (data_atual - data_add).days

        # Pegando valores
        cur.execute("""SELECT VALOR_BASE, VALOR_ACRESCIMO
                        FROM MULTAS
                        WHERE ID_MULTA = ?
                    """, (id_multa,))

        valores = cur.fetchone()

        valor = valor_base + valor_ac * dias_passados
        valor2 = valor
        valor2 = str(valor2)
        valor2.replace('.', ', ')
        # print(f"Valor antes da formatação: {valor}")
        valor = str(valor)
        # print(f"Valor string: {valor}")
        valor = valor.replace('.', '')
        # print(f"Valor depois da formatação: {valor}")
        valor = int(valor)
    else:
        con.commit()
        cur.close()
        return jsonify(
            {"message": "Multa paga. Erro ao consultar informações de usuário para envio de informações"}), 500

    assunto = f"""
        Olá {nome}, uma multa sua foi marcada como paga, você pagou R$ {valor2}.
    """
    titulo = "Nota de Pagamento"
    enviar_email_async(email, assunto, titulo)
    criar_notificacao(tangao[5], assunto, titulo)

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


@app.route("/banners", methods=["POST"])
def create_banner():
    verificacao = informar_verificacao()
    if verificacao:
        return verificacao

    data = request.form
    banner = request.files.get('banner')
    startDate = data.get("startdate")
    finishDate = data.get("finishdate")
    title = data.get("title")

    if finishDate != "":

        if startDate > finishDate:
            return jsonify({"message": "A data de inicio deve ser menor ou igual à data de término do banner"}), 400

        data_atual = datetime.datetime.now().strftime("%Y-%m-%d")

        cur = con.cursor()

        cur.execute("INSERT INTO BANNERS(TITULO, DATAINICIO, DATAFIM) VALUES(?,?,?) returning id_banner",
                    (title, startDate, finishDate))
    else:
        cur = con.cursor()

        cur.execute("INSERT INTO BANNERS(TITULO, DATAINICIO) VALUES(?,?) returning id_banner",
                    (title, startDate))

    id_banner = cur.fetchone()
    id_banner = id_banner[0]

    # Verificações de Imagem
    banners = [
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
    if banner:
        valido = False
        for ext in banners:
            if banner.filename.endswith(ext):
                valido = True
        if not valido:
            return jsonify(
                {
                    "message": "Tipo de arquivo do banner não corresponde com o esperado."
                }
            ), 400
        nome_banner = f"{id_banner}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "banners")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_banner)
        banner.save(imagem_path)

    con.commit()

    return jsonify({
        "message": "Banner criado com sucesso",
    }), 200


@app.route("/banners/users", methods=["GET"])
def get_banners_in_use():
    cur = con.cursor()
    cur.execute(
        "SELECT ID_BANNER, TITULO, DATAINICIO, DATAFIM FROM BANNERS WHERE DATAINICIO <= CURRENT_DATE AND DATAFIM >= "
        "CURRENT_DATE OR DATAFIM IS NULL")
    response = cur.fetchall()

    banners = []
    for r in response:
        imagePath = f"{r[0]}.jpeg"
        banner = {
            "title": r[1],
            "startDate": r[2],
            "finishDate": r[3],
            "imagePath": imagePath
        }
        banners.append(banner)

    return jsonify({"banners": banners}), 200


@app.route("/banners/biblios", methods=["GET"])
def get_banners_all():
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    cur.execute(
        "SELECT ID_BANNER, TITULO, DATAINICIO, DATAFIM FROM BANNERS")
    response = cur.fetchall()

    banners = []
    for r in response:
        finish_date = r[3]
        start_date = r[2]
        imagePath = f"{r[0]}.jpeg"
        finish_date = r[3] + datetime.timedelta(days=1) if r[3] else "—"
        start_date = r[2] + datetime.timedelta(days=1) if r[2] else "—"
        banner = {
            "id_banner": r[0],
            "title": r[1],
            "startDate": start_date,
            "finishDate": finish_date,
            "imagePath": imagePath
        }
        banners.append(banner)

    return jsonify({"banners": banners}), 200


@app.route("/banners/<int:id>/biblios", methods=["PUT"])
def put_banners_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    data = request.form
    startDate = data.get("startdate")
    finishDate = data.get("finishdate")
    title = data.get("title")
    banner = request.files.get("banner")

    if finishDate != "":

        if startDate > finishDate:
            return jsonify({"message": "A data de inicio deve ser menor ou igual à data de término do banner"}), 400

        data_atual = datetime.date.today().strftime("%y-%m-%d")

        cur = con.cursor()
        cur.execute("UPDATE BANNERS SET TITULO = ?, DATAINICIO = ?, DATAFIM = ? WHERE ID_BANNER = ?",
                    (title, startDate, finishDate, id))
    else:
        cur = con.cursor()
        cur.execute("UPDATE BANNERS SET TITULO = ?, DATAINICIO = ?, DATAFIM = NULL WHERE ID_BANNER = ?",
                    (title, startDate, id))

    if banner:
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "banners")
        os.makedirs(pasta_destino, exist_ok=True)
        banner_path = os.path.join(pasta_destino, f"{id}.jpeg")
        banner.save(banner_path)

    con.commit()

    return jsonify({"message": "Banner editado com sucesso"}), 200


@app.route("/banners/<int:id>/biblios", methods=["GET"])
def get_banners_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    cur.execute("SELECT ID_BANNER, TITULO, DATAINICIO, DATAFIM FROM BANNERS WHERE ID_BANNER = ?", (id,))
    response = cur.fetchone()
    imagePath = f"{response[0]}.jpeg"

    banner = {
        "title": response[1],
        "startDate": response[2],
        "finishDate": response[3],
        "imagePath": imagePath
    }

    return jsonify({"banner": banner}), 200


@app.route("/banners/<int:id>/biblios", methods=["DELETE"])
def delete_banner_by_id(id):
    verificacao = informar_verificacao(2)
    if verificacao:
        return verificacao

    cur = con.cursor()
    cur.execute("DELETE FROM BANNERS WHERE ID_BANNER = ?", (id,))

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
        if os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\banners\{str(id) + ext}"):
            valido = False
            ext_real = ext
    if not valido:
        os.remove(rf"{app.config['UPLOAD_FOLDER']}\banners\{str(id) + ext_real}")

    con.commit()

    return jsonify({"message": "Banner removido com sucesso"}), 200
