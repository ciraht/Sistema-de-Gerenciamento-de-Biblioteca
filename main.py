from flask import Flask
import fdb
from flask_cors import CORS
from flask_socketio import SocketIO
import eventlet

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}})
app.config.from_pyfile('config.py')

host = app.config['DB_HOST']
database = app.config['DB_NAME']
user = app.config['DB_USER']
password = app.config['DB_PASSWORD']

eventlet.monkey_patch()


try:
    con = fdb.connect(host=host, database=database, user=user, password=password)
    print(f"Conexão estabelecida com sucesso")
except Exception as e:
    print(f"Erro de conexão com o banco: {e}")

from view import *

@socketio.on("join")
def handle_join(data):
    usuario_id = data.get("usuario_id")
    if usuario_id:
        join_room(f"user_{usuario_id}")

if __name__ == '__main__':
    agendar_tarefas()
    socketio.run(app, host='0.0.0.0', port=5000)
