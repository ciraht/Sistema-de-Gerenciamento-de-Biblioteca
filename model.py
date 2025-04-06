class Usuarios:
    def __init__(self, id_usuario, tipo, nome, email, telefone, endereco, senha, ativo):
        self.id_usuario = id_usuario
        self.nome = nome
        self.email = email
        self.tipo = tipo
        self.telefone = telefone
        self.endereco = endereco
        self.senha = senha
        self.ativo = ativo


class Acervo:
    def __init__(self, id_livro, titulo, autor, categoria, isbn, qtd_disponivel, descricao, ano_publicado, idiomas, disponivel):
        self.id_livro = id_livro
        self.titulo = titulo
        self.autor = autor
        self.categoria = categoria
        self.isbn = isbn
        self.qtd_disponivel = qtd_disponivel
        self.descricao = descricao
        self.idiomas = idiomas
        self.ano_publicado = ano_publicado
        self.disponivel = disponivel


class Emprestimos:
    def __init__(self, id_emprestimo, status, id_usuario, data_retirada, data_devolver, data_devolvido):
        self.id_emprestimo = id_emprestimo
        self.id_usuario = id_usuario
        self.data_retirada = data_retirada
        self.data_devolver = data_devolver
        self.data_devolvido = data_devolvido
        self.status = status


class Reserva:
    def __init__(self, id_reserva, id_usuario, data_reservado, data_criacao, status):
        self.id_reserva = id_reserva
        self.id_usuario = id_usuario
        self.data_reservado = data_reservado
        self.data_criacao = data_criacao
        self.status = status


class Avaliacoes:
    def __init__(self, id_livro, valor_total, qtd_avaliacoes):
        self.id_livro = id_livro
        self.valor_total = valor_total
        self.qtd_avaliacoes = qtd_avaliacoes


class CarrinhoEmprestimos:
    def __init__(self, id_item,  id_usuario, id_livro, data_adicionado):
        self.id_item = id_item
        self.id_usuario = id_usuario
        self.id_livro = id_livro
        self.data_adicionado = data_adicionado


class CarrinhoReservas:
    def __init__(self, id_item, id_usuario, id_livro, data_adicionado):
        self.id_item = id_item
        self.id_usuario = id_usuario
        self.id_livro = id_livro
        self.data_adicionado = data_adicionado


class ItensEmprestimo:
    def __init__(self, id_item, id_livro, id_emprestimo):
        self.id_item = id_item
        self.id_livro = id_livro
        self.id_emprestimo = id_emprestimo


class ItensReserva:
    def __init__(self, id_item, id_livro, id_reserva):
        self.id_item = id_item
        self.id_livro = id_livro
        self.id_reserva = id_reserva


class Notificacoes:
    def __init__(self, id_notificacao, id_usuario, message, status):
        self.id_notificacao = id_notificacao
        self.id_usuario = id_usuario
        self.message = message
        self.status = status


class Tags:
    def __init__(self, id_tag, nome_tag):
        self.id_tag = id_tag
        self.nome_tag = nome_tag


class LivrosTags:
    def __init__(self, id_livro, id_tag):
        self.id_livro = id_livro
        self.id_tag = id_tag


class Multas:
    def __init__(self, id_multa, id_usuario, id_emprestimo, pago, valor_base, valor_acrescimo):
        self.id_multa = id_multa
        self.id_usuario = id_usuario
        self.id_emprestimo = id_emprestimo
        self.pago = pago
        self.valor_base = valor_base
        self.valor_acrescimo = valor_acrescimo


class Valores:
    def __init__(self, id_valor, data_adicionado, valor_base, valor_acrescimo):
        self.id_valor = id_valor
        self.data_adicionado = data_adicionado
        self.valor_base = valor_base
        self.valor_acrescimo = valor_acrescimo
