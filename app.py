import os
import time
import json
from datetime import datetime
from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit


# === CONFIGURACIÓN ===
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder='static', static_url_path='/')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')


# === RUTAS WEB ===
@app.route('/')
def index():
    """Página principal (control remoto)"""
    return app.send_static_file('index.html')


@app.route('/galeria')
def galeria():
    """Página de galería"""
    return app.send_static_file('galeria.html')


# === ARCHIVOS (fotos y metadata) ===
@app.route('/uploads/<path:filename>')
def get_upload(filename):
    """Sirve fotos y JSON desde /uploads"""
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


# === SOCKET.IO ===
@socketio.on('connect')
def on_connect():
    print('[SOCKET] Cliente conectado')
    emit('server_info', {'message': 'connected', 'time': time.time()})


@socketio.on('disconnect')
def on_disconnect():
    print('[SOCKET] Cliente desconectado')


@socketio.on('command')
def on_command(data):
    """
    Recibe comandos desde la web y los reenvía a todos los clientes conectados.
    data esperado: {"type": "TOGGLE_FLASH" | "TAKE_PHOTO"}
    """
    ctype = (data or {}).get('type')
    print(f"[CMD] {ctype}")
    emit('command', {'type': ctype}, broadcast=True)


# === SEGURIDAD SIMPLE ===
API_TOKEN = os.getenv("API_TOKEN", "secret_token_123")

def check_token(req):
    token = req.headers.get("X-API-Key") or req.args.get("token")
    return token == API_TOKEN


# === SUBIDA DE FOTOS DESDE EL CELULAR ===
@app.route('/upload', methods=['POST'])
def upload():
    """
    Recibe una imagen y datos de ubicación desde el dispositivo móvil.
    Guarda la foto y un archivo JSON con metadata (lat, lon, etc).
    """
    if not check_token(request):
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 401

    if 'photo' not in request.files:
        return jsonify({'ok': False, 'error': 'No file part: photo'}), 400

    f = request.files['photo']
    if f.filename == '':
        return jsonify({'ok': False, 'error': 'Empty filename'}), 400

    # Guardar imagen
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f"photo_{ts}.jpg"
    save_path = os.path.join(UPLOAD_DIR, fname)
    f.save(save_path)

    # Leer metadata opcional enviada por el móvil
    meta = {}
    for key in ['lat', 'lon', 'accuracy', 'location_ts']:
        val = request.form.get(key)
        if val:
            try:
                meta[key] = float(val)
            except ValueError:
                meta[key] = val
    meta['received_ts'] = int(time.time())
    meta['photo_filename'] = fname

    # Guardar metadata como JSON
    meta_path = os.path.join(UPLOAD_DIR, os.path.splitext(fname)[0] + '.json')
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


    socketio.emit('new_photo', {
        'filename': fname,
        'timestamp': meta['received_ts']
    }, broadcast=True)

    print(f"[UPLOAD] Guardada {fname} (+metadata)")
    return jsonify({'ok': True, 'filename': fname})


# === API: ÚLTIMA FOTO ===
@app.route('/api/latest')
def api_latest():
    """Devuelve el nombre de la última imagen subida"""
    try:
        files = [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith('.jpg')]
        files.sort(reverse=True)
        if not files:
            return jsonify({'ok': True, 'filename': None})
        latest = files[0]
        ts = os.path.getmtime(os.path.join(UPLOAD_DIR, latest))
        return jsonify({'ok': True, 'filename': latest, 'timestamp': ts})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# === API: GALERÍA COMPLETA ===
@app.route('/api/all')
def api_all():
    """Devuelve todas las fotos con su metadata (si existe)"""
    try:
        jpgs = sorted(
            [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith('.jpg')],
            reverse=True
        )
        items = []
        for j in jpgs:
            base = os.path.splitext(j)[0]
            meta_file = base + ".json"
            meta_path = os.path.join(UPLOAD_DIR, meta_file)
            meta = None
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as fh:
                    meta = json.load(fh)
            items.append({
                "filename": j,
                "metadata": meta
            })
        return jsonify({'ok': True, 'files': items})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# === MAIN ===
if __name__ == '__main__':
    print("🚀 Servidor Flask corriendo en http://0.0.0.0:5000")
    print("📂 Carpeta de fotos:", UPLOAD_DIR)
    socketio.run(app, host='0.0.0.0', port=5000)
