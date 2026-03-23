from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import subprocess
from werkzeug.utils import secure_filename
from io import BytesIO
import logging
import re
import sqlite3
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import datetime

# Configuración de la aplicación Flask
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# Configuración para monetización
app.config['MONETIZATION_ENABLED'] = True  # Cambiar a False para desactivar
app.config['FREE_CONVERSION_LIMIT'] = 20  # Límite de conversiones gratuitas por día
app.config['PREMIUM_API_KEYS'] = ['premium_key_123', 'premium_key_456']  # Keys de pago

# Configuración de directorios
UPLOAD_FOLDER = 'uploads'
TEMP_FOLDER = 'temp'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

# Formatos permitidos
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'aiff'}

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuración de límites de tasa
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["50 per day", "10 per hour"]
)

# Base de datos simple para monetización
def init_db():
    conn = sqlite3.connect('conversions.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usage_stats 
                 (ip TEXT, date TEXT, endpoint TEXT, api_key TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys 
                 (key TEXT PRIMARY KEY, plan TEXT, conversions_left INTEGER)''')
    
    # Insertar clave de prueba gratuita
    try:
        c.execute("INSERT INTO api_keys VALUES (?, ?, ?)", 
                 ('free_key', 'free', app.config['FREE_CONVERSION_LIMIT']))
    except sqlite3.IntegrityError:
        pass  # La clave ya existe
    
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename, file_type):
    """Verifica si la extensión del archivo es permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in (
        ALLOWED_VIDEO_EXTENSIONS if file_type == 'video' else ALLOWED_AUDIO_EXTENSIONS
    )

def sanitize_filename(filename):
    """Limpia el nombre de archivo para que sea válido"""
    return re.sub(r'[\\/*?:"<>|]', "", filename)[:100]

def check_conversion_limit(api_key=None, ip=None):
    """Verifica si el usuario ha excedido el límite de conversiones"""
    if not app.config['MONETIZATION_ENABLED']:
        return True
    
    conn = sqlite3.connect('conversions.db')
    c = conn.cursor()
    
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    if api_key and api_key in app.config['PREMIUM_API_KEYS']:
        conn.close()
        return True
    
    if api_key:
        c.execute("SELECT conversions_left FROM api_keys WHERE key=?", (api_key,))
        result = c.fetchone()
        if result and result[0] > 0:
            c.execute("UPDATE api_keys SET conversions_left = conversions_left - 1 WHERE key=?", (api_key,))
            conn.commit()
            conn.close()
            return True
    
    # Verificar por IP
    c.execute("SELECT COUNT(*) FROM usage_stats WHERE ip=? AND date=?", (ip, today))
    count = c.fetchone()[0]
    
    conn.close()
    
    if count >= app.config['FREE_CONVERSION_LIMIT']:
        return False
    return True

def record_conversion(ip, endpoint, api_key=None):
    """Registra una conversión en la base de datos"""
    conn = sqlite3.connect('conversions.db')
    c = conn.cursor()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    c.execute("INSERT INTO usage_stats VALUES (?, ?, ?, ?)", 
              (ip, today, endpoint, api_key or 'none'))
    conn.commit()
    conn.close()

@app.route('/')
def serve_frontend():
    """Sirve el archivo HTML principal"""
    try:
        return send_from_directory('.', 'index5.html')
    except FileNotFoundError:
        logger.error("Archivo index5.html no encontrado")
        return "Error: Archivo index5.html no encontrado en el directorio actual", 404

@app.route('/<path:path>')
def serve_static(path):
    """Sirve archivos estáticos"""
    return send_from_directory('.', path)

# Nueva ruta para la API de monetización
@app.route('/api/get-key', methods=['GET'])
def get_api_key():
    """Endpoint para obtener una clave API gratuita"""
    return jsonify({
        "api_key": "free_key",
        "plan": "free",
        "conversions_left": app.config['FREE_CONVERSION_LIMIT'],
        "upgrade_url": "/upgrade"
    })

@app.route('/upgrade', methods=['GET'])
def upgrade_info():
    """Información sobre planes premium"""
    return jsonify({
        "plans": [
            {
                "name": "Premium",
                "price": "$9.99/mes",
                "features": ["Conversiones ilimitadas", "Prioridad en cola", "Formatos exclusivos"]
            }
        ]
    })

@app.route('/convert-video', methods=['POST'])
@limiter.limit("5/hour")
def convert_video():
    """Endpoint para conversión de video con límites de monetización"""
    try:
        client_ip = request.remote_addr
        api_key = request.headers.get('X-API-KEY') or request.args.get('api_key')
        
        if app.config['MONETIZATION_ENABLED'] and not check_conversion_limit(api_key, client_ip):
            return jsonify({
                "error": "Límite diario alcanzado",
                "upgrade_url": "/upgrade",
                "conversions_left": 0
            }), 402
        
        if 'file' not in request.files:
            return jsonify({"error": "No se subió ningún archivo"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Nombre de archivo vacío"}), 400

        if not allowed_file(file.filename, 'video'):
            allowed = ', '.join(ALLOWED_VIDEO_EXTENSIONS)
            return jsonify({"error": f"Formato no permitido. Formatos válidos: {allowed}"}), 400

        format = request.form.get('format', 'mp4')
        filename = sanitize_filename(file.filename)
        upload_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(upload_path)

        output_filename = f"converted_{os.path.splitext(filename)[0]}.{format.split('_')[0]}"
        output_path = os.path.join(TEMP_FOLDER, output_filename)

        # Configuración FFmpeg para video (sin cambios)
        cmd = ['ffmpeg', '-y', '-threads', '1', '-t', '300', '-i', upload_path]
        
        if format == 'mp4':
            cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-vf', 'scale=-1:480'])
        else 
            cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast', '-vf', 'scale=-1:480'])
        elif format == 'mp4_hd':
            cmd.extend(['-c:v', 'libx264', '-crf', '35', '-preset', 'ultrafast', '-vf', 'scale=-1:720'])
        elif format == 'mp4_2k':
            cmd.extend(['-c:v', 'libx264', '-crf', '38', '-preset', 'ultrafast', '-vf', 'scale=-1:1080'])
        elif format == 'mp4_4k':
            cmd.extend(['-c:v', 'libx264', '-crf', '40', '-preset', 'ultrafast', '-vf', 'scale=-1:2160'])
        elif format == 'prores':
            cmd.extend(['-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0'])
        elif format == 'webm':
            cmd.extend(['-c:v', 'libvpx-vp9', '-crf', '35', '-b:v', '0', '-deadline', 'realtime'])
        
        cmd.extend(['-c:a', 'aac', '-b:a', '128k', output_path])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Error FFmpeg: {result.stderr}")

        with open(output_path, 'rb') as f:
            file_data = f.read()

        # Registrar la conversión
        record_conversion(client_ip, 'convert-video', api_key)
        
        mimetype = f'video/{format.split("_")[0]}' if format != 'prores' else 'video/quicktime'
        
        return send_file(
            BytesIO(file_data),
            as_attachment=True,
            download_name=output_filename,
            mimetype=mimetype
        )

    except Exception as e:
        logger.error(f"Error en conversión de video: {str(e)}")
        return jsonify({"error": f"Error al convertir video: {str(e)}"}), 500
    finally:
        try:
            os.remove(upload_path)
            os.remove(output_path)
        except:
            pass

@app.route('/convert-audio', methods=['POST'])
@limiter.limit("5/hour")
def convert_audio():
    """Endpoint para conversión de audio con límites de monetización"""
    try:
        client_ip = request.remote_addr
        api_key = request.headers.get('X-API-KEY') or request.args.get('api_key')
        
        if app.config['MONETIZATION_ENABLED'] and not check_conversion_limit(api_key, client_ip):
            return jsonify({
                "error": "Límite diario alcanzado",
                "upgrade_url": "/upgrade",
                "conversions_left": 0
            }), 402
            
        if 'file' not in request.files:
            return jsonify({"error": "No se subió ningún archivo"}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Nombre de archivo vacío"}), 400

        if not allowed_file(file.filename, 'audio'):
            allowed = ', '.join(ALLOWED_AUDIO_EXTENSIONS)
            return jsonify({"error": f"Formato no permitido. Formatos válidos: {allowed}"}), 400

        format = request.form.get('format', 'mp3')
        filename = sanitize_filename(file.filename)
        upload_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(upload_path)

        output_filename = f"converted_{os.path.splitext(filename)[0]}.{format.split('_')[0]}"
        output_path = os.path.join(TEMP_FOLDER, output_filename)

        cmd = ['ffmpeg', '-i', upload_path]
        
        if format in ['mp3', 'mp3_hd', 'mp3_basic']:
            bitrate = '320k' if format == 'mp3_hd' else '128k' if format == 'mp3_basic' else '192k'
            cmd.extend(['-codec:a', 'libmp3lame', '-b:a', bitrate])
        elif format == 'aac':
            cmd.extend(['-codec:a', 'aac', '-b:a', '256k'])
        elif format == 'wav':
            cmd.extend(['-codec:a', 'pcm_s16le'])
        elif format == 'wav_24bit':
            cmd.extend(['-codec:a', 'pcm_s24le', '-ar', '48000'])
        elif format == 'flac':
            cmd.extend(['-codec:a', 'flac'])
        elif format == 'ogg':
            cmd.extend(['-codec:a', 'libvorbis'])
        elif format == 'aiff':
            cmd.extend(['-codec:a', 'pcm_s16be'])
        elif format == 'opus':
            cmd.extend(['-codec:a', 'libopus', '-b:a', '192k'])
        
        cmd.extend(['-y', output_path])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Error FFmpeg: {result.stderr}")

        # Registrar la conversión
        record_conversion(client_ip, 'convert-audio', api_key)
        
        with open(output_path, 'rb') as f:
            file_data = f.read()

        mimetype = f'audio/{format.split("_")[0]}'
        if format == 'aac':
            mimetype = 'audio/aac'
        elif format in ['wav', 'wav_24bit']:
            mimetype = 'audio/wav'
        elif format == 'opus':
            mimetype = 'audio/opus'

        return send_file(
            BytesIO(file_data),
            as_attachment=True,
            download_name=output_filename,
            mimetype=mimetype
        )

    except Exception as e:
        logger.error(f"Error en conversión de audio: {str(e)}")
        return jsonify({"error": f"Error al convertir audio: {str(e)}"}), 500
    finally:
        try:
            os.remove(upload_path)
            os.remove(output_path)
        except:
            pass

if __name__ == '__main__':
    # Verificar dependencias
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        logger.info("FFmpeg está instalado correctamente")
    except Exception as e:
        logger.error("Error: FFmpeg no está instalado o no se encuentra en PATH")
        logger.error("Descarga FFmpeg de: https://ffmpeg.org/download.html")

    # Iniciar aplicación
    app.run(host='0.0.0.0', port=5000, debug=True)
