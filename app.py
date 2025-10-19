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
CORS(app, resources={r"/*": {"origins": "*"}})

# 🔧 Configuración mejorada de SocketIO
socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    logger=True,
    engineio_logger=True
)


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


# === SEGURIDAD SIMPLE ===
API_TOKEN = os.getenv("API_TOKEN", "secret_token_123")

def check_token(req):
    """Verifica el token de API en headers o query params"""
    token = req.headers.get("X-API-Key") or req.args.get("token")
    return token == API_TOKEN


# === SOCKET.IO ===
@socketio.on('connect')
def on_connect(auth=None):
    """Maneja nueva conexión de cliente"""
    print(f'[SOCKET] Cliente conectado - SID: {request.sid}')
    print(f'[SOCKET] Auth recibido: {auth}')
    print(f'[SOCKET] Headers: {dict(request.headers)}')
    
    # Enviar info del servidor
    emit('server_info', {
        'message': 'connected',
        'time': time.time(),
        'sid': request.sid
    })
    
    return True  # Aceptar conexión


@socketio.on('disconnect')
def on_disconnect():
    """Maneja desconexión de cliente"""
    print(f'[SOCKET] Cliente desconectado - SID: {request.sid}')


@socketio.on('command')
def on_command(data):
    """
    Recibe comandos desde la web y los reenvía a todos los clientes conectados.
    data esperado: {"type": "TOGGLE_FLASH" | "TAKE_PHOTO"}
    """
    ctype = (data or {}).get('type')
    print(f"[CMD] Comando recibido: {ctype} - Broadcasting a todos los clientes")
    emit('command', {'type': ctype}, broadcast=True)


@socketio.on('ping')
def handle_ping():
    """Responde a pings de clientes para mantener conexión viva"""
    emit('pong', {'timestamp': time.time()})


@socketio.on_error_default
def default_error_handler(e):
    """Maneja errores de Socket.IO"""
    print(f'[SOCKET ERROR] {str(e)}')


# === SUBIDA DE FOTOS DESDE EL CELULAR ===
@app.route('/upload', methods=['POST'])
def upload():
    """
    Recibe una imagen y datos de ubicación desde el dispositivo móvil.
    Guarda la foto y un archivo JSON con metadata (lat, lon, etc).
    """
    print(f"[UPLOAD] Request recibido - Method: {request.method}")
    print(f"[UPLOAD] Headers: {dict(request.headers)}")
    print(f"[UPLOAD] Files: {list(request.files.keys())}")
    
    if not check_token(request):
        print("[UPLOAD] ❌ Token inválido")
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 401

    if 'photo' not in request.files:
        print("[UPLOAD] ❌ No se encontró 'photo' en files")
        return jsonify({'ok': False, 'error': 'No file part: photo'}), 400

    f = request.files['photo']
    if f.filename == '':
        print("[UPLOAD] ❌ Filename vacío")
        return jsonify({'ok': False, 'error': 'Empty filename'}), 400

    # Guardar imagen
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f"photo_{ts}.jpg"
    save_path = os.path.join(UPLOAD_DIR, fname)
    
    try:
        f.save(save_path)
        print(f"[UPLOAD] ✅ Foto guardada: {fname}")
    except Exception as e:
        print(f"[UPLOAD] ❌ Error guardando foto: {e}")
        return jsonify({'ok': False, 'error': f'Error saving file: {str(e)}'}), 500

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
    try:
        with open(meta_path, 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        print(f"[UPLOAD] ✅ Metadata guardada")
    except Exception as e:
        print(f"[UPLOAD] ⚠️ Error guardando metadata: {e}")

    # Notificar a todos los clientes conectados
    try:
        socketio.emit('new_photo', {
            'filename': fname,
            'timestamp': meta['received_ts'],
            'has_location': 'lat' in meta and 'lon' in meta
        })
        print(f"[UPLOAD] 📢 Evento 'new_photo' emitido")
    except Exception as e:
        print(f"[UPLOAD] ⚠️ Error emitiendo evento: {e}")

    return jsonify({
        'ok': True,
        'filename': fname,
        'timestamp': meta['received_ts']
    })


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


# === HEALTH CHECK ===
@app.route('/health')
def health():
    """Endpoint para verificar que el servidor está funcionando"""
    return jsonify({
        'ok': True,
        'status': 'running',
        'timestamp': time.time()
    })


# === MAIN ===
if __name__ == '__main__':
    print("🚀 Servidor Flask corriendo en http://0.0.0.0:4321")
    print("📂 Carpeta de fotos:", UPLOAD_DIR)
    print("🔑 API Token:", API_TOKEN)
    socketio.run(app, host='0.0.0.0', port=4321, debug=True)