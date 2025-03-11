import os
SECRET_KEY = 'CHAVE_SUPER_SECRETA'
DEBUG = True
DB_HOST = 'localhost'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'BANCO.FDB')
DB_USER = 'sysdba'
DB_PASSWORD = 'sysdba'
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')