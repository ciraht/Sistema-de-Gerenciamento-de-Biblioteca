class Usuario:
    def __init__(self, id_usuario, nome, email, telefone, endereco):
        self.id_usuario = id_usuario
        self.nome = nome
        self.email = email
        self.telefone = telefone
        self.endereco = endereco

class Bibliotecario:
    def __init__(self, id_bibliotecario, id_usuario, senha):
        self.id_bibliotecario = id_bibliotecario
        self.id_usuario = id_usuario

class Leitor:
    def __init__(self, id_leitor, id_usuario, senha):
        self.id_leitor = id_leitor
        self.id_usuario = id_usuario

class Acervo:
    def __init__(self, id_livro, titulo, autor, categoria, isbn, qtd_disponivel, descricao):
        self.id_livro = id_livro
        self.titulo = titulo
        self.autor = autor
        self.categoria = categoria
        self.isbn = isbn
        self.qtd_disponivel = qtd_disponivel
        self.descricao = descricao

class Emprestimo:
    def __init__(self, id_emprestimo, id_leitor, id_bibliotecario, data_retirada, data_devolver):
        self.id_emprestimo = id_emprestimo
        self.id_leitor = id_leitor
        self.id_bibliotecario = id_bibliotecario
        self.data_retirada = data_retirada
        self.data_devolver = data_devolver

class Reserva:
    def __init__(self, id_reserva, id_leitor, id_livro, data_reservado, data_validade):
        self.id_reserva = id_reserva
        self.id_leitor = id_leitor
        self.id_livro = id_livro
        self.data_reservado = data_reservado
        self.data_validade = data_validade

class Devolucao:
    def __init__(self, id_devolucao, id_leitor, id_emprestimo,id_livro,data):
        self.id_devolucao = id_devolucao
        self.id_leitor = id_leitor
        self.id_emprestimo = id_emprestimo
        self.id_livro = id_livro
        self.data = data

class Tags:
    def __init__(self, id_tag, nome_tag):
        self.id_tag = id_tag
        self.nome_tag = nome_tag

class Livros_Tags:
    def __init__(self, id_livro, id_tag):
        self.id_livro = id_livro
        self.id_tag = id_tag