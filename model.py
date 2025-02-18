class Usuario:
    def __init__(self, id_usuario, tipo, nome, email, telefone, endereco, senha):
        self.id_usuario = id_usuario
        self.nome = nome
        self.email = email
        self.tipo = tipo
        self.telefone = telefone
        self.endereco = endereco
        self.senha = senha


class Livros:
    def __init__(self, id_livro, titulo, autor, categoria, isbn, qtd_disponivel, descricao, ano_publicado, idiomas):
        self.id_livro = id_livro
        self.titulo = titulo
        self.autor = autor
        self.categoria = categoria
        self.isbn = isbn
        self.qtd_disponivel = qtd_disponivel
        self.descricao = descricao
        self.idiomas = idiomas
        self.ano_publicado = ano_publicado


class Avaliacoes:
    def __init__(self, id_livro, valor_total, qtd_avaliacoes):
        self.id_livro = id_livro
        self.valor_total = valor_total
        self.qtd_avaliacoes = qtd_avaliacoes


class Emprestimo:
    def __init__(self, id_emprestimo, id_leitor, id_usuario, data_retirada, data_devolver, data_devolvido):
        self.id_emprestimo = id_emprestimo
        self.id_leitor = id_leitor
        self.id_usuario = id_usuario
        self.data_retirada = data_retirada
        self.data_devolver = data_devolver
        self.data_devolvido = data_devolvido


class ItensEmprestimo:
    def __init__(self, id_item, id_livro, id_emprestimo):
        self.id_item = id_item
        self.id_livro = id_livro
        self.id_emprestimo = id_emprestimo


class Reserva:
    def __init__(self, id_reserva, id_leitor, id_livro, data_reservado, data_validade):
        self.id_reserva = id_reserva
        self.id_leitor = id_leitor
        self.id_livro = id_livro
        self.data_reservado = data_reservado
        self.data_validade = data_validade


class Tags:
    def __init__(self, id_tag, nome_tag):
        self.id_tag = id_tag
        self.nome_tag = nome_tag


class LivrosTags:
    def __init__(self, id_livro, id_tag):
        self.id_livro = id_livro
        self.id_tag = id_tag


class Multas:
    def __init__(self, id_multa, id_usuario, id_emprestimo, id_valor, valor_base, valor_acrescimo):
        self.id_multa = id_multa
        self.id_usuario = id_usuario
        self.id_emprestimo = id_emprestimo
        self.id_valor = id_valor
        self.valor_base = valor_base
        self.valor_acrescimo = valor_acrescimo


class Valores:
    def __init__(self, id_valor, data_adicionado, valor_base, valor_acrescimo):
        self.id_valor = id_valor
        self.data_adicionado = data_adicionado
        self.valor_base = valor_base
        self.valor_acrescimo = valor_acrescimo
