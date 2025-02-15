from flask import Flask, jsonify, request
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash
from datetime import datetime, timedelta


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
        serBibliotecario = data.get('serBibliotecario')

        email = email.lower()

        if len(senha) < 8:
            return jsonify({"message": "Sua senha deve conter pelo menos 8 caracteres"})

        tem_maiuscula = False
        tem_minuscula = False
        tem_numero = False
        tem_caract_especial = False
        caracteres_especiais = "!@#$%^&*(),.?\":{}|<>"

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

        # Verificando se tem todos os dados
        if not all([nome, email, telefone, endereco, senha]):
            return jsonify({"message": "Todos os campos são obrigatórios"}), 400

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

        # Inserindo usuário na tabela usuarios
        if serBibliotecario:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, ?)",
                (nome, email, telefone, endereco, senha, 'bibliotecario')
            )
            con.commit()
        else:
            cur.execute(
                "INSERT INTO usuarios (nome, email, telefone, endereco, senha, tipo) VALUES (?, ?, ?, ?, ?, ?)",
                (nome, email, telefone, endereco, senha, 'leitor')
            )
            con.commit()

        # Pegando o ID do usuário
        cur.execute("SELECT id_usuario FROM usuarios WHERE email = ?", (email,))
        resultado = cur.fetchone()
        if resultado:
            id_usuario = resultado[0]
        else:
            return jsonify({"message": "Erro ao buscar usuário"}), 500

        # Inserindo na tabela bibliotecários ou leitores
        if serBibliotecario:
            cur.execute("INSERT INTO bibliotecarios (id_usuario) VALUES (?)", (id_usuario,))
        else:
            cur.execute("INSERT INTO leitores (id_usuario) VALUES (?)", (id_usuario,))

        con.commit()

        cur.close()

        return jsonify(
            {
                "message": "Usuário cadastrado com sucesso",
                "id_user" : id_usuario
            }
        ), 201

    except Exception as e:
        return jsonify({"message": f"Erro: {str(e)}"}), 500


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
        if check_password_hash(senha_hash, senha):
            return jsonify(
                {
                    "message": "Usuário entrou com sucesso",
                    "id_user": id_user
                }
            ), 200
        else:
            return jsonify({"message": "Credenciais inválidas"}), 401
    else:
        return jsonify({"message": "Usuário não encontrado"}), 404


@app.route('/editar_usuario/<int:id>', methods=["PUT"])
def usuario_put(id):
    cur = con.cursor()
    cur.execute("SELECT id_usuario, nome, email, telefone, endereco FROM usuarios WHERE id_usuario = ?", (id,))
    usuario_data = cur.fetchone()

    if not usuario_data:
        cur.close()
        return jsonify({"message": "Usuário não foi encontrado"}), 404

    # Recebendo novos dados
    data = request.get_json()
    nome = data.get('nome')
    email = data.get('email')
    telefone = data.get('telefone')
    endereco = data.get('endereco')

    # Verificando se tem todos os dados
    if not all([nome, email, telefone, endereco]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    # Verificando se os dados novos já existem na DataBase
    cur.execute("select email, telefone from usuarios where id_usuario = ?", (id))
    checagem = cur.fetchone()
    emailvelho = checagem[0]
    telefonevelho = checagem[1]
    emailvelho = emailvelho.lower()
    email = email.lower()
    telefone = telefone.lower()
    telefonevelho = telefonevelho.lower()
    if telefone != telefonevelho or email != emailvelho:
        cur.execute("select 1 from usuarios where telefone = ?", (telefone,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message":"Telefone já cadastrado"})
    if email != emailvelho:
        cur.execute("select 1 from usuarios where email = ?", (email,))
        if cur.fetchone():
            cur.close()
            return jsonify({"message":"Email já cadastrado"})

    # Atualizando as informações
    cur.execute(
        "UPDATE usuarios SET nome = ?, email = ?, telefone = ?, endereco = ? WHERE id_usuario = ?",
        (nome, email, telefone, endereco, id)
    )
    con.commit()

    cur.close()

    return jsonify({"message": "Usuário atualizado com sucesso"}), 200

@app.route('/editar_usuario/<int:id>', methods=['DELETE'])
def deletar_usuario(id):
    cur = con.cursor()

    # Verificar se o usuario existe
    cur.execute("SELECT 1 FROM usuarios WHERE ID_usuario = ?", (id,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "usuario não encontrado"}), 404

    # Excluir o usuario
    cur.execute("DELETE FROM usuarios WHERE ID_usuario = ?", (id,))
    con.commit()
    cur.close()

    return jsonify({'message': "usuario excluído com sucesso!",})

@app.route('/adicionar_livros', methods=["POST"])
def adicionar_livros():
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
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Procurando por duplicados pelo ISBN
    cur.execute("select 1 from acervo where isbn = ?", (isbn,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "ISBN já cadastrada"})

    # Adicionando os dados na Database
    cur.execute(
        "INSERT INTO ACERVO (titulo, autor, categoria, isbn, qtd_disponivel, descricao) VALUES(?,?,?,?,?,?)",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao)
    )
    con.commit()

    pegar_id = cur.execute("SELECT ID_LIVRO FROM ACERVO WHERE ACERVO.ISBN = ?", (isbn,))
    livro_id = pegar_id.fetchone()[0]

    # Associando tags ao livro
    for tag in tags:
        cur.execute("SELECT id_tag FROM tags WHERE nome_tag = ?", (tag,))
        tag_id = cur.fetchone()
        if tag_id:
            cur.execute("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", (livro_id, tag_id[0]))

    con.commit()

    cur.close()

    return jsonify({"message": "Livro cadastrado com sucesso"})

@app.route('/editar_livro/<int:id>', methods=["PUT"])
def editar_livro(id):
    cur = con.cursor()
    cur.execute("SELECT titulo, autor, categoria, isbn, qtd_disponivel, descricao FROM acervo WHERE id_livro = ?", (id,))
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
        cur.execute("SELECT 1 FROM ACERVO WHERE ISBN = ? AND ID_LIVRO <> ?", (isbn, id, ))
        if cur.fetchone():
            cur.close()
            return jsonify({"message":"ISBN já cadastrado"})

    cur.execute(
        "UPDATE acervo SET titulo = ?, autor = ?, categoria = ?, isbn = ?, qtd_disponivel = ?, descricao = ? WHERE id_livro = ?",
        (titulo, autor, categoria, isbn, qtd_disponivel, descricao, id)
    )
    con.commit()

    cur.execute("delete from livro_tags where id_livro = ? ", (id,))
    insert_data = []

    # Associando tags ao livro
    for tag in tags:
        cur.execute("SELECT id_tag FROM tags WHERE nome_tag = ?", (tag,))
        tag_id = cur.fetchone()
        if tag_id:
            insert_data.append((id, tag_id[0]))

    # Inserindo as associações
    if insert_data:
        cur.executemany("INSERT INTO livro_tags (id_livro, id_tag) VALUES (?, ?)", insert_data)

    con.commit()

    cur.close()

    return jsonify({"message": "Livro atualizado com sucesso"}), 200

@app.route('/excluir_livro/<int:id>', methods=["DELETE"])
def livro_delete(id):
    cur = con.cursor()

    # Verificar se o livro existe
    cur.execute("SELECT 1 FROM acervo WHERE ID_livro = ?", (id,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"error": "Livro não encontrado"}), 404

    # ANTES: excluir todos os registros das outras tabelas relacionados ao livro??

    # Excluir o Livro
    cur.execute("DELETE FROM acervo WHERE ID_livro = ?", (id,))
    con.commit()
    cur.close()

    return jsonify({'message': "Livro excluído com sucesso!", })


@app.route('/emprestimo_livro/<int:id>', methods=["POST"])
def emprestar_livros(id):
    data = request.get_json()
    id_leitor = data.get('id_leitor')
    data_devolucao = data.get('data_devolucao')

    # Checando se todos os dados foram preenchidos
    if not all([id_leitor, data_devolucao]):
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se o livro existe
    cur.execute("SELECT 1 from acervo where id_livro = ?", (id,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message" : "O livro selecionado não existe"})

    """
    # Checando se o leitor já tem o livro emprestado?
    cur.execute(
        "SELECT 1 FROM EMPRESTIMOS e WHERE e.ID_LEITOR = ? AND id_livro = ? AND id_emprestimo <> (SELECT d.id_emprestimo FROM DEVOLUCOES d WHERE d.id_leitor = ? AND d.id_livro = ?)"
    )
    if cur.fetchone():
        cur.close()
        return jsonify({
            "message" : "O leitor já possui o livro"
        })
    """

    # Checando se o livro está disponivel
    cur.execute(
        "SELECT QTD_DISPONIVEL FROM ACERVO WHERE ID_LIVRO = ?",
        (id,)
    )
    qtd_disponivel = cur.fetchone()[0]
    cur.execute(
        "SELECT count(id_emprestimo) FROM EMPRESTIMOS e WHERE e.id_livro = ? AND e.ID_EMPRESTIMO NOT IN (SELECT d.id_emprestimo FROM DEVOLUCOES d WHERE d.id_livro = ?)",
        (id, id,)
    )
    qtd_emprestada = cur.fetchone()[0]
    livros_nao_emprestados = qtd_disponivel - qtd_emprestada
    if livros_nao_emprestados <= 0:
        cur.close()
        return jsonify({
            "message" : "Todos os exemplares disponiveis desse livro já estão emprestados"
        })

    # Contar as reservas do livro e comparar com a quantidade não emprestada
    cur.execute(
        "SELECT count(id_reserva) FROM RESERVAS r WHERE r.id_livro = ? and r.DATA_VALIDADE >= CURRENT_TIMESTAMP",
        (id,)
    )
    livros_reservados = cur.fetchone()[0]
    # print(f"\nLivros não emprestados: {livros_nao_emprestados}, Livros reservados: {livros_reservados}, {livros_reservados >= livros_nao_emprestados}")
    if livros_reservados >= livros_nao_emprestados:
        cur.close()
        return jsonify({
            "message" : "Os exemplares restantes desse livro já estão reservados"
        })

    # Inserindo o emprestimo no banco de dados
    # print("Emprestando mais 1")
    cur.execute(
        "INSERT INTO EMPRESTIMOS (ID_LEITOR,ID_LIVRO,DATA_DEVOLVER) values (?,?,?)",
        (id_leitor, id, data_devolucao)
    )

    con.commit()
    cur.close()

    return jsonify({
        "message" : "Livro emprestado com sucesso"
    })


@app.route('/devolucao_livro/<int:id>', methods=["POST"])
def devolver_livro(id):
    data = request.get_json()
    id_leitor = data.get('id_leitor')

    # Checando se todos os dados foram preenchidos
    if not id_leitor:
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se o livro existe
    cur.execute("SELECT 1 from acervo where id_livro = ?", (id,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "O livro selecionado não existe"})

    # Checando se o id leitor tem um livro para devolver
    cur.execute(
        "SELECT id_emprestimo FROM EMPRESTIMOS e WHERE e.ID_LEITOR = ? AND id_livro = ? AND id_emprestimo NOT IN (SELECT d.id_emprestimo FROM DEVOLUCOES d WHERE d.id_leitor = ? AND d.id_livro = ?)",
        (id_leitor,id, id_leitor, id))
    emprestimo = cur.fetchone()
    if not emprestimo:
        cur.close()
        return jsonify({"error": "O leitor não emprestou esse livro"})

    emprestimo = emprestimo[0]

    cur.execute("insert into devolucoes (id_leitor, id_livro, id_emprestimo) values (?,?,?)", (id_leitor, id, emprestimo))

    con.commit()
    cur.close()

    return jsonify(
        {
            "message" : "Livro devolvido com sucesso"
        }
    )

@app.route('/reserva_livro/<int:id>', methods=["POST"])
def reservar_livros(id):
    data = request.get_json()
    id_leitor = data.get('id_leitor')

    # Checando se todos os dados foram preenchidos
    if not id_leitor:
        return jsonify({"message": "Todos os campos são obrigatórios"}), 400

    cur = con.cursor()

    # Checando se o livro existe
    cur.execute("SELECT 1 from acervo where id_livro = ?", (id,))
    if not cur.fetchone():
        cur.close()
        return jsonify({"message": "O livro selecionado não existe"})

    # Adicionando a reserva no banco de dados
    cur.execute(
        "INSERT INTO reservas (id_leitor, id_livro, DATA_VALIDADE, DATA_RESERVADO) "
        "VALUES (?, ?, CURRENT_TIMESTAMP + 7, CURRENT_TIMESTAMP)",
        (id_leitor, id)
    )

    con.commit()
    cur.close()

    return jsonify({
        "message" : "Livro reservado com sucesso"
    })

@app.route('/reserva_livro/<int:id_reserva>', methods=["DELETE"])
def deletar_reservas(id_reserva):

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
        "message" : "Reserva excluída com sucesso"
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
    return jsonify(tags),200