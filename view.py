import os
import jwt
import datetime
from flask import Flask, jsonify, request, send_file, send_from_directory, json
import config
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from fpdf import FPDF

senha_secreta = app.config['SECRET_KEY']


def generate_token(user_id):
    payload = {
        "id_usuario": user_id,
        'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=45)
    }
    token = jwt.encode(payload, senha_secreta, algorithm='HS256')
    return token


def remover_bearer(token):
    if token.startswith("Bearer "):
        return token[len("Bearer "):]
    else:
        return token


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
            return jsonify({"message": "Todos os campos são obrigatórios"}), 400

        if senha != confirmSenha:
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 400

        if len(senha) < 8:
            return jsonify({"message": "Sua senha deve conter pelo menos 8 caracteres"}),401

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
            return jsonify({"message": "A senha deve conter pelo menos uma letra maiúscula."})
        if not tem_minuscula:
            return jsonify({"message": "A senha deve conter pelo menos uma letra minúscula."})
        if not tem_numero:
            return jsonify({"message": "A senha deve conter pelo menos um número."})
        if not tem_caract_especial:
            return jsonify({"message": "A senha deve conter pelo menos um caractere especial."})

        # Abrindo o Cursor
        cur = con.cursor()

        # Checando duplicações
        cur.execute("SELECT 1 FROM usuarios WHERE email = ?", (email, ))
        if cur.fetchone():
            return jsonify({"message": "Email já cadastrado"}), 409

        cur.execute("SELECT 1 FROM usuarios WHERE telefone = ?", (telefone, ))
        if cur.fetchone():
            return jsonify({"message": "Telefone já cadastrado"}), 409

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
            return jsonify(
                {
                    "message": "Tipo de usuário inválido"
                }
            ), 400
        con.commit()

        print(id_usuario)
        cur.close()

        # Verificações de Imagem
        imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
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
                        "message": "Usuário cadastrado com sucesso, mas o formato de imagem é inválido, você pode alterar editando seu perfil depois"
                    }
                ), 200
            nome_imagem = f"{id_usuario}.jpeg"
            pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
            os.makedirs(pasta_destino, exist_ok=True)
            imagem_path = os.path.join(pasta_destino, nome_imagem)
            imagem.save(imagem_path)

        return jsonify(
            {
                "message": "Usuário cadastrado com sucesso"
            }
        ), 200

    except Exception as e:
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
    cur.execute("SELECT senha, id_usuario FROM usuarios WHERE email = ?", (email, ))
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
                    "message": "Este usuário está inativado",
                    "id_user": id_user
                }
            ), 400

        if check_password_hash(senha_hash, senha):

            # Pegar o tipo do usuário para levar à página certa
            tipo = cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user, ))
            tipo = tipo.fetchone()[0]
            token = generate_token(id_user)
            if tipo == 2:
                return jsonify(
                    {
                        "message": "Bibliotecário entrou com sucesso",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    }
                ), 200
            elif tipo == 3:
                return jsonify(
                    {
                        "message": "Administrador entrou com sucesso",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    })
            else:
                return jsonify(
                    {
                        "message": "Leitor entrou com sucesso",
                        "token": token,
                        "id_user": id_user,
                        "tipo": tipo
                    }
                ), 200

        else:
            # Ignorar isso tudo se o usuário for administrador
            tipo = cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user, ))
            tipo = tipo.fetchone()[0]
            if tipo != 3:
                print(f"Primeiro: {global_contagem_erros}")
                id_user_str = f"usuario-{id_user}"
                if id_user_str not in global_contagem_erros:
                    global_contagem_erros[id_user_str] = 1
                else:
                    if global_contagem_erros[id_user_str] > 3:
                        return jsonify({"message": "Tentativas excedidas, usuário já está inativado"}), 401
                    global_contagem_erros[id_user_str] += 1

                    print(f"Segundo: {global_contagem_erros}")

                    if global_contagem_erros[id_user_str] >= 3:
                        cur.execute("UPDATE USUARIOS SET ATIVO = FALSE WHERE ID_USUARIO = ?", (id_user, ))
                        con.commit()
                        cur.close()
                        return jsonify({"message": "Tentativas excedidas, usuário inativado"}), 401

            return jsonify({"message": "Credenciais inválidas"}), 401
    else:
        return jsonify({"message": "Usuário não encontrado"}), 404


@app.route('/reativar_usuario', methods=["PUT"])
def reativar_usuario():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    admin = cur.fetchone()[0]
    if not admin:
        return jsonify({'mensagem': 'Nível Administrador requerido'}), 401

    data = request.get_json()
    id_usuario = data.get("id")

    cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario, ))
    tipo = cur.fetchone()[0]
    if tipo == 3:
        return jsonify({"message": "Esse usuário não pode ser reativado"})

    cur.execute("UPDATE USUARIOS SET ATIVO = TRUE WHERE ID_USUARIO = ?", (id_usuario, ))
    con.commit()
    cur.close()
    return jsonify({"message": "Usuário reativado com sucesso"})


@app.route('/inativar_usuario', methods=["PUT"])
def inativar_usuario():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    admin = cur.fetchone()[0]
    if not admin:
        return jsonify({'mensagem': 'Nível Administrador requerido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    admin = cur.fetchone()[0]
    if not admin:
        return jsonify({'mensagem': 'Nível Administrador requerido'}), 401

    data = request.get_json()
    id_usuario = data.get("id")
    cur = con.cursor()

    cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_usuario, ))
    tipo = cur.fetchone()[0]
    if tipo == 3:
        return jsonify({"message": "Esse usuário não pode ser inativado"})

    cur.execute("UPDATE USUARIOS SET ATIVO = FALSE WHERE ID_USUARIO = ?", (id_usuario, ))
    con.commit()
    cur.close()
    return jsonify({"message": "Usuário inativado com sucesso"})


@app.route('/editar_usuario', methods=["PUT"])
def usuario_put():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401

    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_usuario = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario, ))
    usuario_data = cur.fetchone()

    if not usuario_data:
        cur.close()
        return jsonify({"message": "Usuário não encontrado"}), 404

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
        return jsonify({"message": "Todos os campos são obrigatórios, exceto a senha"}), 400

    if senha_nova or senha_confirm:
        if not senha_antiga:
            return jsonify({"message": "Para alterar a senha, é necessário informar a senha antiga."}), 400

        cur.execute("SELECT senha FROM usuarios WHERE id_usuario = ?", (id_usuario, ))
        senha_armazenada = cur.fetchone()[0]

        if not check_password_hash(senha_armazenada, senha_antiga):
            return jsonify({"message": "Senha antiga incorreta."}), 400

        if senha_nova != senha_confirm:
            return jsonify({"message": "A nova senha e a confirmação devem ser iguais."}), 400

        if len(senha_nova) < 8 or not any(c.isupper() for c in senha_nova) or not any(
                c.islower() for c in senha_nova) or not any(c.isdigit() for c in senha_nova) or not any(
                c in "!@#$%^&*(), -.?\":{}|<>" for c in senha_nova):
            return jsonify({
                               "message": "A senha deve conter pelo menos 8 caracteres, uma letra maiúscula, uma letra minúscula, um número e um caractere especial."}), 400

        senha_nova = generate_password_hash(senha_nova)
        cur.execute(
            "UPDATE usuarios SET senha = ? WHERE id_usuario = ?",
            (senha_nova, id_usuario)
        )
    cur.execute("SELECT 1 FROM USUARIOS WHERE EMAIL = ? AND ID_USUARIO <> ?", (email, id_usuario))
    if cur.fetchone():
        return jsonify({
            "message": "Este email pertence a outra pessoa"
        }), 400

    cur.execute("SELECT 1 FROM USUARIOS WHERE telefone = ? AND ID_USUARIO <> ?", (telefone, id_usuario))
    if cur.fetchone():
        return jsonify({
            "message": "Este telefone pertence a outra pessoa"
        }), 400

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

    cur.close()
    return jsonify({"message": "Usuário atualizado com sucesso"}), 200


@app.route('/deletar_usuario', methods=['DELETE'])
def deletar_usuario():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    admin = cur.fetchone()[0]
    if not admin:
        return jsonify({'mensagem': 'Nível Administrador requerido'}), 401

    cur = con.cursor()

    data = request.get_json()
    id_usuario = data.get("id_usuario")
    # Verificar se o usuario existe
    cur.execute("SELECT 1 FROM usuarios WHERE ID_usuario = ?", (id_usuario, ))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Usuário não encontrado"}), 404

    # Excluir os registros que usam o id como chave estrangeira
    cur.execute("""
    DELETE FROM ITENS_EMPRESTIMO i WHERE
     i.ID_EMPRESTIMO IN (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.ID_USUARIO = ?)
     """, (id_usuario, ))
    cur.execute("DELETE FROM EMPRESTIMOS WHERE ID_USUARIO = ?", (id_usuario, ))
    cur.execute("DELETE FROM RESERVAS WHERE ID_USUARIO = ?", (id_usuario, ))
    cur.execute("DELETE FROM MULTAS WHERE ID_USUARIO = ?", (id_usuario, ))

    # Excluir o usuario
    cur.execute("DELETE FROM usuarios WHERE ID_usuario = ?", (id_usuario, ))
    con.commit()
    cur.close()

    # Excluir a imagem de usuário da aplicação caso houver
    print(
        f"Existe o folder: {os.path.exists(app.config['UPLOAD_FOLDER'])}, Existe o arquivo: {os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{id_usuario}")}")
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
    valido = True
    ext_real = None
    for ext in imagens:
        if os.path.exists(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext}"):
            valido = False
            ext_real = ext
    if not valido:
        os.remove(rf"{app.config['UPLOAD_FOLDER']}\Usuarios\{str(id_usuario) + ext_real}")

    return jsonify({'message': "Usuário excluído com sucesso"})


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
        """, (r[0], ))
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
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado, ))
    biblio = cur.fetchone()
    if not biblio:
        return jsonify({'error': 'Nível Bibliotecário requerido'}), 401

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
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Verificando se a ISBN já está cadastrada
    cur.execute("SELECT 1 FROM acervo WHERE isbn = ?", (isbn, ))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "ISBN já cadastrada"}), 404

    if int(qtd_disponivel) < 1:
        cur.close()
        return jsonify({"error": "Quantidade disponível precisa ser maior que 1"}), 401
    if int(ano_publicado) > datetime.date.today().year:
        return jsonify({"error": "Ano publicado deve ser condizente com a data atual"}), 401

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
        return jsonify({"error": "Erro ao recuperar ID do livro"}), 500

    # Associando tags ao livro
    for tag in tags:
        tag_id = tag
        print(f"Tag_id:{tag_id}")
        if tag_id:
            cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (livro_id, tag_id))

    # Criando um registro avaliações vazio para poder editar usando a rota de avaliar depois
    cur.execute("INSERT INTO AVALIACOES (ID_LIVRO, VALOR_TOTAL, QTD_AVALIACOES) VALUES (?, 0, 0)", (livro_id, ))

    con.commit()
    cur.close()

    # Verificações de Imagem
    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
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
                    "message": "Livro cadastrado com sucesso, mas o formato de imagem é inválido, edite o livro criado"
                }
            ), 200
        nome_imagem = f"{livro_id}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify({"message": "Livro cadastrado com sucesso", "id_livro": livro_id}), 202


@app.route('/editar_livro/<int:id_livro>', methods=["PUT"])
def editar_livro(id_livro):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)",
                (id_logado, ))
    biblio = cur.fetchone()
    if not biblio:
        return jsonify({'error': 'Nível Bibliotecário requerido'}), 401

    data = request.form

    cur = con.cursor()
    cur.execute("SELECT titulo, autor, categoria, isbn, qtd_disponivel, descricao FROM acervo WHERE id_livro = ?",
                (id_livro, ))
    acervo_data = cur.fetchone()

    if not acervo_data:
        cur.close()
        return jsonify({"message": "Livro não foi encontrado"}), 404

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
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    # Verificando se os dados novos já existem na DataBase
    isbnvelho = acervo_data[3].lower()
    if isbn != isbnvelho:
        cur.execute("SELECT 1 FROM ACERVO WHERE ISBN = ? AND ID_LIVRO <> ?", (isbn, id_livro, ))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "ISBN já cadastrado"})
    if int(ano_publicado) > datetime.date.today().year:
        return jsonify({"message": "Ano publicado deve ser condizente com a data atual"}), 401

    cur.execute(
        """UPDATE acervo SET
         titulo = ?, autor = ?, categoria = ?, isbn = ?, qtd_disponivel = ?, descricao = ?, 
         idiomas = ?, ano_publicado = ?
        WHERE
         id_livro = ?""",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado, id_livro)
    )
    con.commit()

    cur.execute("delete from livro_tags where id_livro = ? ", (id_livro, ))
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
    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
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
                    "message": "Livro editado com sucesso, mas o formato de imagem é inválido."
                }
            ), 200
        nome_imagem = f"{id_livro}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify({"message": "Livro atualizado com sucesso"}), 200


@app.route('/excluir_livro', methods=["DELETE"])
def livro_delete():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401

    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado, ))
    biblio = cur.fetchone()

    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    # Obter JSON da requisição
    data = request.get_json()

    # Garantir que o ID foi enviado
    if not data or 'id_livro' not in data:
        return jsonify({"error": "ID do livro não fornecido"}), 400

    id_livro = data['id_livro']

    # Verificar se o livro existe
    cur.execute("SELECT 1 FROM acervo WHERE ID_livro = ?", (id_livro, ))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Livro não encontrado"}), 404

    # Excluir registros relacionados ao livro
    cur.execute("DELETE FROM LIVRO_TAGS WHERE ID_LIVRO = ?", (id_livro, ))
    cur.execute("DELETE FROM RESERVAS WHERE ID_LIVRO = ?", (id_livro, ))
    cur.execute("DELETE FROM AVALIACOES WHERE ID_LIVRO = ?", (id_livro, ))
    cur.execute("DELETE FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?", (id_livro, ))
    cur.execute("DELETE FROM acervo WHERE ID_livro = ?", (id_livro, ))

    con.commit()
    cur.close()

    # Remover imagem do livro
    upload_folder = app.config['UPLOAD_FOLDER']
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
    for ext in imagens:
        caminho_imagem = os.path.join(upload_folder, "Livros", f"{id_livro}{ext}")
        if os.path.exists(caminho_imagem):
            os.remove(caminho_imagem)
            break

    return jsonify({'message': "Livro excluído com sucesso!"})


@app.route('/emprestimo_livros', methods=["POST"])
def emprestar_livros():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    data = request.get_json()
    conjunto_livros = data.get('livros', [])
    id_leitor = data.get('id_usuario')

    # Checando se todos os dados foram preenchidos
    if not all([id_leitor, conjunto_livros]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se todos os livros existem
    for livro in conjunto_livros:
        id_livro = livro[0]
        cur.execute("SELECT 1 from acervo where id_livro = ?", (id_livro, ))
        if not cur.fetchone():
            cur.close()
            return jsonify({"message": "Um dos livros selecionados não existe"})

    # Checando se todos os lívros estão disponíveis
    for livro in conjunto_livros:
        cur.execute(
            "SELECT QTD_DISPONIVEL FROM ACERVO WHERE ID_LIVRO = ?",
            (livro[0], )
        )
        qtd_maxima = cur.fetchone()[0]
        # livros de Itens_Emprestimo que possuem o id do livro e pertencem a um emprestimo sem devolução
        cur.execute(
            """SELECT count(ID_ITEM) FROM ITENS_EMPRESTIMO i WHERE
             i.id_livro = ? AND i.ID_EMPRESTIMO IN 
             (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.DATA_DEVOLVIDO IS NULL)""",
            (livro[0], )
        )
        qtd_emprestada = cur.fetchone()[0]
        livros_nao_emprestados = qtd_maxima - qtd_emprestada
        if livros_nao_emprestados <= 0:
            cur.close()
            return jsonify({
                "message": "Todos os exemplares disponíveis desse livro já estão emprestados"
            })

        # Contar as reservas do livro (que não são de quem está a receber o empréstimo)
        # e comparar com a quantidade não emprestada
        cur.execute(
            """SELECT count(id_reserva) FROM RESERVAS r WHERE
             r.id_livro = ? AND r.DATA_VALIDADE >= CURRENT_DATE AND r.ID_USUARIO <> ?""",
            (livro[0], id_leitor)
        )
        livros_reservados = cur.fetchone()[0]
        print(
            f"\nlivros não emprestados: {livros_nao_emprestados}, livros reservados: {livros_reservados}, {livros_reservados >= livros_nao_emprestados}")
        if livros_reservados >= livros_nao_emprestados:
            cur.close()
            return jsonify({
                "message": "Os exemplares restantes de um dos livros já estão reservados"
            })

    cur.execute(
        """INSERT INTO EMPRESTIMOS (ID_USUARIO, DATA_RETIRADA, DATA_DEVOLVER)
          VALUES (?, CURRENT_DATE, DATEADD(DAY, 7, CURRENT_DATE)) RETURNING ID_EMPRESTIMO""",
        (id_leitor, )
    )
    id_emprestimo = cur.fetchone()[0]
    con.commit()

    # Inserindo os registros individuais de cada livro e os ligando ao empréstimo
    for livro in conjunto_livros:
        cur.execute("INSERT INTO ITENS_EMPRESTIMO (ID_LIVRO, ID_EMPRESTIMO) VALUES (?, ?)", (livro[0], id_emprestimo, ))
    con.commit()
    cur.close()
    return jsonify("Empréstimo feito com sucesso")


@app.route('/devolver_emprestimo', methods=["PUT"])
def devolver_emprestimo():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    data = request.get_json()
    id_emprestimo = data.get("id_emprestimo")
    cur = con.cursor()

    # Verificações
    if not id_emprestimo:
        # Se não recebeu o id
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400
    else:
        # Se o ID não existe no banco de dados
        cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id_emprestimo, ))
        if not cur.fetchone():
            return jsonify({"message": "Id de empréstimo não encontrado"}), 404

    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ? AND DATA_DEVOLVIDO IS NOT NULL", (id_emprestimo, ))
    if cur.fetchone():
        return jsonify({"message": "Empréstimo já devolvido"}), 400

    # Devolver o empréstimo
    cur.execute("UPDATE EMPRESTIMOS SET DATA_DEVOLVIDO = CURRENT_DATE WHERE ID_EMPRESTIMO = ?", (id_emprestimo, ))
    con.commit()
    cur.close()
    return jsonify({"message": "Devolução feita com sucesso"}), 200


@app.route('/renovar_emprestimo', methods=["PUT"])
def renovar_emprestimo():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401
    data = request.get_json()
    id_emprestimo = data.get("id_emprestimo")
    dias = data.get("dias")
    cur = con.cursor()

    if not all([dias, id_emprestimo]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    # Verificar se o id existe e se já não foi devolvido o empréstimo
    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE ID_EMPRESTIMO = ?", (id_emprestimo, ))
    if not cur.fetchone():
        return jsonify({"message": "Id de empréstimo não existe"}), 404
    cur.execute("SELECT 1 FROM EMPRESTIMOS WHERE DATA_DEVOLVIDO IS NOT NULL AND ID_EMPRESTIMO = ?", (id_emprestimo, ))
    if cur.fetchone():
        return jsonify({"message": "Este empréstimo já teve sua devolução"}), 404

    cur.execute("""UPDATE EMPRESTIMOS SET 
    DATA_DEVOLVER = DATEADD(DAY, ?, CURRENT_DATE) WHERE ID_EMPRESTIMO = ?""", (dias, id_emprestimo, ))
    con.commit()
    cur.close()
    return jsonify({"message": "Empréstimo renovado com sucesso"}), 200


@app.route('/reserva_livro', methods=["POST"])
def reservar_livros():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401
    data = request.get_json()
    id_leitor = data.get('id_leitor')
    id_livro = data.get('id_livro')

    # Checando se todos os dados foram preenchidos
    if not all([id_leitor, id_livro]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se o livro existe
    cur.execute("SELECT 1 from acervo where id_livro = ?", (id_livro, ))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "O livro selecionado não existe"})

    # Adicionando a reserva no banco de dados
    cur.execute(
        "INSERT INTO reservas (id_leitor, id_livro, DATA_VALIDADE, DATA_RESERVADO) "
        "VALUES (?, ?, DATEADD(DAY, 7, CURRENT_DATE), CURRENT_DATE)",
        (id_leitor, id_livro)
    )

    con.commit()
    cur.close()

    return jsonify({
        "message": "Livro reservado com sucesso"
    })


@app.route('/upload/usuario', methods=["POST"])
def enviar_imagem_usuario():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    imagem = request.files.get("imagem")
    id_usuario = payload["id_usuario"]
    print(id_usuario)

    # Verificações de Imagem
    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
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
                    "message": "Formato de imagem não autorizado"
                }
            ), 401
        nome_imagem = f"{id_usuario}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "usuarios")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify(
        {
            "message": "Imagem enviada com sucesso"
        }
    ), 200


@app.route('/upload/livro', methods=["POST"])
def enviar_imagem_livro():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    biblio = cur.fetchone()[0]
    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    imagem = request.files.get("imagem")
    data = request.form.to_dict()
    id_livro = data.get("id_livro")

    print(id_livro)

    # Verificações de Imagem
    imagens = [".png", ".jpg", ".WEBP", ".jpeg"]
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
                    "message": "Formato de imagem não autorizado"
                }
            ), 401
        nome_imagem = f"{id_livro}.jpeg"
        pasta_destino = os.path.join(app.config['UPLOAD_FOLDER'], "livros")
        os.makedirs(pasta_destino, exist_ok=True)
        imagem_path = os.path.join(pasta_destino, nome_imagem)
        imagem.save(imagem_path)

    return jsonify(
        {
            "message": "Imagem enviada com sucesso"
        }
    ), 200


@app.route('/cancelar_reserva', methods=["DELETE"])
def deletar_reservas():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401
    data = request.get_json()
    id_reserva = data.get("id_reserva")

    # Checando se todos os dados foram preenchidos
    if not id_reserva:
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se a reserva existe
    cur.execute("SELECT 1 from reservas where id_reserva = ?", (id_reserva, ))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "A reserva selecionada não existe"})

    # Excluir a Reserva
    cur.execute("DELETE FROM reservas WHERE id_reserva = ?", (id_reserva, ))
    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva excluída com sucesso"
    })


@app.route('/usuarios/pesquisa', methods=["GET"])
def pesquisar_usuarios():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401

    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()

    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 2 OR TIPO = 3)", (id_logado,))
    resultado = cur.fetchone()

    if not resultado or resultado[0] is None:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    data = request.get_json()
    pesquisa = data.get("pesquisa")
    filtros = data.get("filtros", [])

    if not pesquisa:
        return jsonify({"mensagem": "Nada pesquisado"}), 400

    sql = """
        SELECT DISTINCT u.nome, u.email, u.telefone, 
                        u.endereco, u.senha, u.tipo
        FROM USUARIOS u
        LEFT JOIN EMPRESTIMOS e ON u.ID_USUARIO = e.ID_USUARIO
        LEFT JOIN RESERVAS r ON u.ID_USUARIO = r.ID_USUARIO
        LEFT JOIN MULTAS m ON u.ID_USUARIO = m.ID_USUARIO
        WHERE u.NOME CONTAINING ?
    """
    params = [pesquisa]

    if "multado" in filtros:
        sql += " OR u.ID_USUARIO IN (SELECT ID_USUARIO FROM MULTAS)"
    if "reservas_validas" in filtros:
        sql += " OR u.ID_USUARIO IN (SELECT ID_USUARIO FROM RESERVAS r WHERE r.DATA_VALIDADE >= CURRENT_DATE)"

    sql += "\nORDER BY u.nome"
    cur.execute(sql, params)
    resultados = cur.fetchall()

    if not resultados:
        cur.close()
        return jsonify({"mensagem": "Nenhum resultado encontrado"}), 404

    return jsonify({
        "mensagem": "Pesquisa realizada com sucesso",
        "resultados": [{"nome": r[0], "email": r[1], "telefone": r[2], "endereco": r[3], "senha": r[4], "tipo": r[5]}
                       for r in resultados]
    }), 200


@app.route('/pesquisa', methods=["GET"])
def pesquisar():
    data = request.get_json()
    pesquisa = data.get("pesquisa")
    filtros = data.get("filtros", [])

    if not pesquisa:
        return jsonify({"message": "Nada pesquisado"})

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
    if "tag" in filtros:
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
        return jsonify({"message": "Nenhum resultado encontrado"}), 404

    return jsonify({
        "message": "Pesquisa realizada com sucesso",
        "resultados": [{"id": r[0], "titulo": r[1], "autor": r[2], "categoria": r[3],
                        "isbn": r[4], "qtd_disponivel": r[5], "descricao": r[6]} for r in resultados]
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
    cur.execute("SELECT id_tag, id_livro from livro_tags where id_livro = ?", (id, ))
    tags = [{'id_tag': r[0], 'id_livro': r[1]} for r in cur.fetchall()]
    cur.close()
    return jsonify(tags), 200


@app.route("/avaliarlivro/<int:id>", methods=["PUT"])
def avaliar_livro(id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401

    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    data = request.get_json()
    valor = data.get("valor")
    cur = con.cursor()
    cur.execute("SELECT VALOR_TOTAL FROM AVALIACOES WHERE ID_LIVRO = ?", (id, ))
    valor_total = cur.fetchone()[0]
    cur.execute("SELECT QTD_AVALIACOES FROM AVALIACOES WHERE ID_LIVRO = ?", (id, ))
    qtd_avaliacoes = cur.fetchone()[0]
    print(f"Valor total: {valor_total}, Avaliações: {qtd_avaliacoes}")

    cur.execute("UPDATE AVALIACOES SET VALOR_TOTAL = ?, QTD_AVALIACOES = ? WHERE ID_LIVRO = ?",
                (valor_total + valor, qtd_avaliacoes + 1, id))
    con.commit()
    cur.close()

    return jsonify({
      "mensagem": "Avaliado com sucesso"
    })


@app.route("/livros/<int:id>", methods=["GET"])
def get_livros_id(id):
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
    """, (id, ))

    livro = cur.fetchone()

    cur.execute("""
            SELECT t.id_tag, t.nome_tag
            FROM LIVRO_TAGS lt
            LEFT JOIN TAGS t ON lt.ID_TAG = t.ID_TAG
            WHERE lt.ID_LIVRO = ?
        """, (id, ))

    tags = cur.fetchall()

    selected_tags = [{'id': tag[0], 'nome': tag[1]} for tag in tags]

    cur.execute("SELECT VALOR_TOTAL FROM AVALIACOES WHERE ID_LIVRO = ?", (id, ))
    valor_total = cur.fetchone()[0]
    cur.execute("SELECT QTD_AVALIACOES FROM AVALIACOES WHERE ID_LIVRO = ?", (id, ))
    qtd = cur.fetchone()[0]
    print(f'\nvalor_total: {valor_total}, qtd: {qtd}\n')
    if qtd != 0:
        avaliacoes = round((valor_total / qtd), 2)
    else:
        avaliacoes = 0.00

    cur.close()

    if not livro:  # Se o livro não existir, retorna erro 404
        return jsonify({"error": "Livro não encontrado"}), 404

    return jsonify({
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
    })


@app.route('/relatorio/livros', methods=['GET'])
def gerar_relatorio_livros():
    cursor = con.cursor()
    cursor.execute("""
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
    livros = cursor.fetchall()
    cursor.close()

    contador_livros = len(livros)  # Definir o contador de livros antes do loop

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de livros", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    for livro in livros:
        pdf.cell(200, 10,
                 f"ID: {livro[0]} Titulo: {livro[1]} Autor: {livro[2]} Categoria: {livro[3]} ISBN: {livro[4]} Quantidade Disponível: {livro[5]} Descrição: {livro[6]} Idiomas: {livro[7]} Ano Publicado: {livro[8]}",
                 ln=True)

    pdf.ln(10)  # Espaço antes do contador
    pdf.set_font("Arial", style='B', size=12)
    pdf.cell(200, 10, f"Total de livros cadastrados: {contador_livros}", ln=True, align='C')

    pdf_path = "relatorio_livros.pdf"
    pdf.output(pdf_path)

    try:
        return send_file(pdf_path, as_attachment=True, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': f"Erro ao gerar o arquivo: {str(e)}"}), 500


@app.route('/relatorio/usuarios', methods=['GET'])
def gerar_relatorio_usuarios():
    cursor = con.cursor()
    cursor.execute("""
        SELECT
            id_usuario, 
            nome, 
            email, 
            telefone, 
            endereco
        FROM USUARIOS
        ORDER BY id_usuario;
    """)
    usuarios = cursor.fetchall()
    cursor.close()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", style='B', size=16)
    pdf.cell(200, 10, "Relatorio de usuarios", ln=True, align='C')
    pdf.ln(5)  # Espaço entre o título e a linha
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())  # Linha abaixo do título
    pdf.ln(5)  # Espaço após a linha
    pdf.set_font("Arial", size=12)

    for usuario in usuarios:
        pdf.cell(200, 10,
                 f"ID: {usuario[0]} - Nome: {usuario[1]} - Email: {usuario[2]} - Telefone: {usuario[3]} - Endereço: {usuario[4]}",
                 ln=True)

    contador_usuarios = len(usuarios)
    pdf.ln(10)  # Espaço antes do contador
    pdf.set_font("Arial", style='B', size=12)
    pdf.cell(200, 10, f"Total de usuarios cadastrados: {contador_usuarios}", ln=True, align='C')

    pdf_path = "relatorio_usuarios.pdf"
    pdf.output(pdf_path)
    try:
        return send_file(pdf_path, as_attachment=True, mimetype='application/pdf')
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
    """, (id, ))

    usuario = cur.fetchone()
    cur.close()

    if not usuario:  # Se o usuário não existir, retorna erro 404
        return jsonify({"error": "Usuário não encontrado"}), 404

    return jsonify({
        "id": usuario[0],
        "nome": usuario[1],
        "email": usuario[2],
        "telefone": usuario[3],
        "endereco": usuario[4],
        "senha": usuario[5],
        "imagem": f"{usuario[0]}.jpeg"
    })

@app.route('/usuarios', methods=["get"])
def usuarios():
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
            'tipo':r[6],
            'ativo':r[7],
            'imagem': f"{r[0]}.jpeg"
        }

        listaUsuarios.append(users)

    return jsonify(listaUsuarios), 200





@app.route('/token', methods=["POST"])
def tokenIsActive():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Token de autenticação necessário'}), 401

    token = remover_bearer(token)

    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Token inválido'}), 401

    return jsonify({'message': 'Token válido', 'id_usuario': payload["id_usuario"]}), 200


@app.route('/uploads/<tipo>/<filename>')
def serve_file(tipo, filename):
    pasta_permitida = ["usuarios", "livros"]  # Apenas pastas permitidas
    if tipo not in pasta_permitida:
        return {"error": "Acesso negado"}, 403  # Evita acesso a outras pastas

    caminho_pasta = os.path.join(config.UPLOAD_FOLDER, tipo)
    caminho_arquivo = os.path.join(caminho_pasta, filename)

    # Verifica se o arquivo existe antes de servir
    if not os.path.isfile(caminho_arquivo):
        return {"error": "Arquivo não encontrado"}, 404

    return send_from_directory(caminho_pasta, filename)


@app.route('/trocar_tipo/<int:id>', methods=["PUT"])
def trocar_tipo(id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3",
                (id_logado,))
    biblio = cur.fetchone()
    if not biblio:
        return jsonify({'error': 'Nível Administrador requerido'}), 401

    cur.execute("select tipo from usuarios where id_usuario = ?", (id,))
    tipo = cur.fetchone()[0]

    if tipo == 1:
        cur.execute("UPDATE USUARIOS SET tipo = 2 WHERE ID_USUARIO = ?", (id,))
    elif tipo == 2:
        cur.execute("UPDATE USUARIOS SET tipo = 1 WHERE ID_USUARIO = ?", (id,))
    elif tipo == 3:
        return jsonify({"error": "Um administrador não tem permissão para retirar um cargo de administrador"}), 401

    con.commit()

    return jsonify({"message": "Usuário atualizado com sucesso"}), 202


@app.route("/tem_permissao", methods=["get"])
def tem_permissao():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'mensagem': 'Token de autenticação necessário'}), 401
    token = remover_bearer(token)
    try:
        payload = jwt.decode(token, senha_secreta, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return jsonify({'mensagem': 'Token expirado'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'mensagem': 'Token inválido'}), 401

    id_logado = payload["id_usuario"]
    cur = con.cursor()
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND (TIPO = 3 or TIPO = 2)", (id_logado,))
    bibli = cur.fetchone()[0]
    if not bibli:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401
    return jsonify({"message": "deu certo"}), 200