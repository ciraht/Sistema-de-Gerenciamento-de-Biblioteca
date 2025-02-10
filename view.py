from flask import Flask, jsonify, request
from main import app, con
from flask_bcrypt import generate_password_hash, check_password_hash


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

        return jsonify({"message": "Usuário cadastrado com sucesso"}), 201

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
    cur.execute("SELECT senha FROM usuarios WHERE email = ?", (email,))
    senha_hash = cur.fetchone()

    if senha_hash:
        senha_hash = senha_hash[0]
        if check_password_hash(senha_hash, senha):
            return jsonify({"message": "Usuário entrou com sucesso"}), 200
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
    cursor = con.cursor()

    # Verificar se o usuario existe
    cursor.execute("SELECT 1 FROM usuarios WHERE ID_usuario = ?", (id,))
    if not cursor.fetchone():
        cursor.close()
        return jsonify({"error": "usuario não encontrado"}), 404

    # Excluir o usuario
    cursor.execute("DELETE FROM usuarios WHERE ID_usuario = ?", (id,))
    con.commit()
    cursor.close()

    return jsonify({'message': "usuario excluído com sucesso!",})
