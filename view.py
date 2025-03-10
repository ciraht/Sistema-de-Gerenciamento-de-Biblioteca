import os
import jwt
import datetime
from flask import Flask, jsonify, request, send_file
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from fpdf import FPDF

senha_secreta = app.config['SECRET_KEY']


def generate_token(user_id):
    payload = {
        "id_usuario": user_id,
        'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30)
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
        data = request.get_json()
        nome = data.get('nome')
        email = data.get('email')
        telefone = data.get('telefone')
        endereco = data.get('endereco')
        senha = data.get('senha')
        tipo = data.get('tipo')

        email = email.lower()

        print([nome, email, telefone, endereco, senha, tipo])
        # Verificando se tem todos os dados
        if not all([nome, email, telefone, endereco, senha, tipo]):
            return jsonify({"message": "Todos os campos são obrigatórios"}), 400

        if len(senha) < 8:
            return jsonify({"message": "Sua senha deve conter pelo menos 8 caracteres"})

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
        cur.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,))
        if cur.fetchone():
            return jsonify({"message": "Email já cadastrado"}), 409

        cur.execute("SELECT 1 FROM usuarios WHERE telefone = ?", (telefone,))
        if cur.fetchone():
            return jsonify({"message": "Telefone já cadastrado"}), 409

        senha = generate_password_hash(senha).decode('utf-8')

        # Inserindo usuário na tabela usuarios conforme seu tipo
        if tipo == 1:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 1)",
                (nome, email, telefone, endereco, senha)
            )
            con.commit()
        elif tipo == 2:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 2)",
                (nome, email, telefone, endereco, senha)
            )
            con.commit()

        elif tipo == 3:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, 3)",
                (nome, email, telefone, endereco, senha)
            )
            con.commit()

        cur.close()
        return jsonify(
            {
                "message": "Usuário cadastrado com sucesso"
            }
        ), 201

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
    cur.execute("SELECT senha, id_usuario FROM usuarios WHERE email = ?", (email,))
    resultado = cur.fetchone()

    if resultado:
        senha_hash = resultado[0]
        id_user = resultado[1]
        cur = con.cursor()
        if check_password_hash(senha_hash, senha):
            ativo = cur.execute("SELECT ATIVO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user,))
            ativo = ativo.fetchone()[0]
            print(ativo)
            if not ativo:
                cur.close()
                return jsonify(
                    {
                        "message": "Este usuário está inativado",
                        "id_user": id_user
                    }
                )

            # Pegar o tipo do usuário para levar à página certa
            tipo = cur.execute("SELECT TIPO FROM USUARIOS WHERE ID_USUARIO = ?", (id_user, ))
            tipo = tipo.fetchone()[0]
            token = generate_token(id_user)
            if tipo == 2:
                return jsonify(
                    {
                        "message": "Bibliotecário entrou com sucesso",
                        "token:": token
                    }
                ), 200
            elif tipo == 3:
                return jsonify(
                    {
                        "message": "Administrador entrou com sucesso",
                        "token:": token
                    })
            else:
                return jsonify(
                    {
                        "message": "Leitor entrou com sucesso",
                        "token:": token
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
                    if global_contagem_erros[id_user_str] > 3:
                        return jsonify({"message": "Tentativas excedidas, usuário já está inativado"}), 401
                    global_contagem_erros[id_user_str] += 1

                    print(f"Segundo: {global_contagem_erros}")

                    if global_contagem_erros[id_user_str] == 3:
                        cur.execute("UPDATE USUARIOS SET ATIVO = FALSE WHERE ID_USUARIO = ?", (id_user,))
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
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 3", (id_logado,))
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


@app.route('/editar_usuario/', methods=["PUT"])
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

    data = request.get_json()
    id_usuario = data.get("id_usuario")

    cur = con.cursor()
    cur.execute("SELECT id_usuario, nome, email, telefone, endereco FROM usuarios WHERE id_usuario = ?", (id_usuario,))
    usuario_data = cur.fetchone()

    if not usuario_data:
        cur.close()
        return jsonify({"message": "Usuário não foi encontrado"}), 404

    # Recebendo novos dados
    nome = data.get('nome')
    email = data.get('email')
    telefone = data.get('telefone')
    endereco = data.get('endereco')
    senha = data.get('senha')

    # Verificando se tem todos os dados
    if not all([nome, email, telefone, endereco, senha]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    # Verificando se os dados novos já existem na DataBase
    cur.execute("select email, telefone from usuarios where id_usuario = ?", (id_usuario, ))
    checagem = cur.fetchone()
    emailvelho = checagem[0]
    telefonevelho = checagem[1]
    emailvelho = emailvelho.lower()
    email = email.lower()
    if telefone != telefonevelho or email != emailvelho:
        cur.execute("select 1 from usuarios where telefone = ? AND ID_USUARIO <> ?", (telefone, id_usuario, ))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Telefone já cadastrado"})
    if email != emailvelho:
        cur.execute("select 1 from usuarios where email = ? AND ID_USUARIO <> ?", (email, id_usuario, ))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "Email já cadastrado"})

    # Verificações de senha
    if len(senha) < 8:
        return jsonify({"message": "Sua senha deve conter pelo menos 8 caracteres"})

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

    senha = generate_password_hash(senha)
    # Atualizando as informações
    cur.execute(
        "UPDATE usuarios SET nome = ?, email = ?, telefone = ?, endereco = ?, senha = ? WHERE id_usuario = ?",
        (nome, email, telefone, endereco, senha, id_usuario)
    )
    con.commit()

    cur.close()

    return jsonify({"message": "Usuário atualizado com sucesso"}), 200


@app.route('/deletar_usuario/', methods=['DELETE'])
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
    cur.execute("SELECT 1 FROM usuarios WHERE ID_usuario = ?", (id_usuario,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "usuario não encontrado"}), 404

    # FAZER UMA VERIFICAÇÃO DE TOKENS PARA VER SE QUEM ESTÁ FAZENDO ISSO É ADMIN
    # (quando puder usar tokens)

    # Excluir os registros que usam o id como chave estrangeira
    cur.execute("""
    DELETE FROM ITENS_EMPRESTIMO i WHERE
     i.ID_EMPRESTIMO IN (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.ID_USUARIO = ?)
     """, (id_usuario, ))
    cur.execute("DELETE FROM EMPRESTIMOS WHERE ID_USUARIO = ?", (id_usuario, ))
    cur.execute("DELETE FROM RESERVAS WHERE ID_USUARIO = ?", (id_usuario, ))
    cur.execute("DELETE FROM MULTAS WHERE ID_USUARIO = ?", (id_usuario, ))

    # Excluir o usuario
    cur.execute("DELETE FROM usuarios WHERE ID_usuario = ?", (id_usuario,))
    con.commit()
    cur.close()

    return jsonify({'message': "usuario excluído com sucesso!", })


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
        }

        livros.append(livro)

    cur.close()
    return jsonify(livros), 200


# @app.route('/adicionar_livros', methods=["POST"])
# def adicionar_livros():
#     data = request.get_json()
#     titulo = data.get('titulo')
#     autor = data.get('autor')
#     categoria = data.get('categoria')
#     isbn = data.get('isbn')
#     qtd_disponivel = data.get('qtd_disponivel')
#     descricao = data.get('descricao')
#     ano_publicado = data.get('ano_publicado')
#     idiomas = data.get('idiomas')

#     tags = data.get('tags')

#     # Verificando se tem todos os dados
#     if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado]):
#         return jsonify({"message": "Todos os campos são obrigatórios"}), 400

#     cur = con.cursor()

#     # Procurando por duplicados pelo ISBN
#     cur.execute("select 1 from acervo where isbn = ?", (isbn, ))
#     if cur.fetchone():
#         cur.close()
#         return jsonify({"error": "ISBN já cadastrada"})

#     # Adicionando os dados na Database
#     cur.execute(
#         "INSERT INTO ACERVO (titulo, autor, categoria, isbn, qtd_disponivel, descricao, ano_publicado, idiomas) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
#         (titulo, autor, categoria, isbn, qtd_disponivel, descricao, ano_publicado, idiomas)
#     )
#     con.commit()

#     pegar_id = cur.execute("SELECT ID_LIVRO FROM ACERVO WHERE ACERVO.ISBN = ?", (isbn, ))
#     livro_id = pegar_id.fetchone()[0]

#     # Associando tags ao livro
#     if tags:
#         for tag in tags:
#             cur.execute("SELECT id_tag FROM tags WHERE nome_tag = ?", (tag, ))
#             tag_id = cur.fetchone()
#             if tag_id:
#                 cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (livro_id, tag_id[0]))

#     con.commit()

#     cur.close()

#     return jsonify({"message": "Livro cadastrado com sucesso"})

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
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 2", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    biblio = cur.fetchone()[0]
    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    data = request.get_json()
    titulo = data.get('titulo')
    autor = data.get('autor')
    categoria = data.get('categoria')
    isbn = data.get('isbn')
    qtd_disponivel = data.get('qtd_disponivel')
    descricao = data.get('descricao')
    idiomas = data.get('idiomas')
    ano_publicado = data.get("ano_publicado")
    tags = data.get('selectedTags', [])

    print(tags)

    if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Verificando se a ISBN já está cadastrada
    cur.execute("SELECT 1 FROM acervo WHERE isbn = ?", (isbn,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "ISBN já cadastrada"}), 404

    # Adicionando os dados na Database
    cur.execute(
        """INSERT INTO 
        ACERVO (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado) 
        VALUES(?,?,?,?,?,?,?,?) RETURNING ID_LIVRO""",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, idiomas, ano_publicado)
    )
    livro_id = cur.fetchone()[0]
    con.commit()

    if not livro_id:
        cur.close()
        return jsonify({"error": "Erro ao recuperar ID do livro"}), 500

    # Associando tags ao livro
    for tag in tags:
        tag_id = tag.get('id')
        if tag_id:
            cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (livro_id, tag_id))

    con.commit()

    cur.close()

    return jsonify({"message": "Livro cadastrado com sucesso"})


@app.route('/editar_livro/', methods=["PUT"])
def editar_livro():
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
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 2 OR TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    biblio = cur.fetchone()[0]
    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    data = request.get_json()
    id_livro = data.get('id_livro')

    cur = con.cursor()
    cur.execute("SELECT titulo, autor, categoria, isbn, qtd_disponivel, descricao FROM acervo WHERE id_livro = ?",
                (id_livro,))
    acervo_data = cur.fetchone()

    if not acervo_data:
        cur.close()
        return jsonify({"message": "Livro não foi encontrado"}), 404

    data = request.get_json()
    titulo = data.get('titulo')
    autor = data.get('autor')
    categoria = data.get('categoria')
    isbn = data.get('isbn')
    qtd_disponivel = data.get('qtd_disponivel')
    descricao = data.get('descricao')
    tags = data.get('tags', [])

    # Verificando se tem todos os dados
    if not all([titulo, autor, categoria, isbn, qtd_disponivel, descricao]):
        cur.close()
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    # Verificando se os dados novos já existem na DataBase
    isbnvelho = acervo_data[3].lower()
    if isbn != isbnvelho:
        cur.execute("SELECT 1 FROM ACERVO WHERE ISBN = ? AND ID_LIVRO <> ?", (isbn, id_livro,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message": "ISBN já cadastrado"})

    cur.execute(
        """UPDATE acervo SET
         titulo = ?, autor = ?, categoria = ?, isbn = ?, qtd_disponivel = ?, descricao = ? 
        WHERE
         id_livro = ?""",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, id_livro)
    )
    con.commit()

    cur.execute("delete from livro_tags where id_livro = ? ", (id_livro,))
    insert_data = []

    # Associando tags ao livro
    for tag in tags:
        cur.execute("SELECT id_tag FROM tags WHERE nome_tag = ?", (tag,))
        tag_id = cur.fetchone()
        if tag_id:
            insert_data.append((id_livro, tag_id[0]))

    # Inserindo as associações
    if insert_data:
        cur.executemany("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", insert_data)

    con.commit()

    cur.close()

    return jsonify({"message": "Livro atualizado com sucesso"}), 200


@app.route('/excluir_livro/', methods=["DELETE"])
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
    cur.execute("SELECT 1 FROM USUARIOS WHERE ID_USUARIO = ? AND TIPO = 2 OR TIPO = 3", (id_logado, ))
    # print(f"cur.fetchone():{cur.fetchone()}, payload:{payload}")
    biblio = cur.fetchone()[0]
    if not biblio:
        return jsonify({'mensagem': 'Nível Bibliotecário requerido'}), 401

    data = request.get_json()
    id_livro = data.get('id_livro')

    cur = con.cursor()

    # Verificar se o livro existe
    cur.execute("SELECT 1 FROM acervo WHERE ID_livro = ?", (id_livro,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Livro não encontrado"}), 404

    # ANTES: excluir todos os registros das outras tabelas relacionados ao livro
    cur.execute("DELETE FROM LIVRO_TAGS WHERE ID_LIVRO = ?", (id_livro,))
    cur.execute("DELETE FROM RESERVAS WHERE ID_LIVRO = ?", (id_livro,))
    cur.execute("DELETE FROM AVALIACOES WHERE ID_LIVRO = ?", (id_livro,))
    cur.execute("DELETE FROM ITENS_EMPRESTIMO WHERE ID_LIVRO = ?", (id_livro,))
    cur.execute("DELETE FROM EMPRESTIMOS WHERE ID_LIVRO = ?", (id_livro,))

    # Excluir o Livro
    cur.execute("DELETE FROM acervo WHERE ID_livro = ?", (id_livro,))
    con.commit()
    cur.close()

    return jsonify({'message': "Livro excluído com sucesso!", })


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
        cur.execute("SELECT 1 from acervo where id_livro = ?", (id_livro,))
        if not cur.fetchone():
            cur.close()
            return jsonify({"message": "Um dos livros selecionados não existe"})

    # Checando se todos os lívros estão disponíveis
    for livro in conjunto_livros:
        cur.execute(
            "SELECT QTD_DISPONIVEL FROM ACERVO WHERE ID_LIVRO = ?",
            (livro[0],)
        )
        qtd_maxima = cur.fetchone()[0]
        # Livros de Itens_Emprestimo que possuem o id do livro e pertencem a um emprestimo sem devolução
        cur.execute(
            """SELECT count(ID_ITEM) FROM ITENS_EMPRESTIMO i WHERE
             i.id_livro = ? AND i.ID_EMPRESTIMO IN 
             (SELECT e.ID_EMPRESTIMO FROM EMPRESTIMOS e WHERE e.DATA_DEVOLVIDO IS NULL)""",
            (livro[0],)
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
        print(f"\nLivros não emprestados: {livros_nao_emprestados}, Livros reservados: {livros_reservados}, {livros_reservados >= livros_nao_emprestados}")
        if livros_reservados >= livros_nao_emprestados:
            cur.close()
            return jsonify({
                "message": "Os exemplares restantes de um dos livros já estão reservados"
            })

    cur.execute(
        """INSERT INTO EMPRESTIMOS (ID_USUARIO, DATA_RETIRADA, DATA_DEVOLVER)
          VALUES (?, CURRENT_DATE, DATEADD(DAY, 7, CURRENT_DATE)) RETURNING ID_EMPRESTIMO""",
        (id_leitor,)
    )
    id_emprestimo = cur.fetchone()[0]
    con.commit()

    # Inserindo os registros individuais de cada livro e os ligando ao empréstimo
    for livro in conjunto_livros:
        cur.execute("INSERT INTO ITENS_EMPRESTIMO (ID_LIVRO, ID_EMPRESTIMO) VALUES (?, ?)", (livro[0], id_emprestimo,))
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
    cur.execute("SELECT 1 from acervo where id_livro = ?", (id_livro,))
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
    cur.execute("SELECT 1 from reservas where id_reserva = ?", (id_reserva,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "A reserva selecionada não existe"})

    # Excluir a Reserva
    cur.execute("DELETE FROM reservas WHERE id_reserva = ?", (id_reserva,))
    con.commit()
    cur.close()

    return jsonify({
        "message": "Reserva excluída com sucesso"
    })


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
    cur.execute("SELECT id_tag, id_livro from livros_tags where id_livro = ?", (id,))
    tags = [{'id_tag': r[0], 'id_livro': r[1]} for r in cur.fetchall()]
    cur.close()
    return jsonify(tags), 200
