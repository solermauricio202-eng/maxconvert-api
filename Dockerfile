# 1. Usamos una imagen de Python ligera
FROM python:3.9-slim

# 2. Instalamos FFmpeg y herramientas del sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Creamos las carpetas de trabajo
WORKDIR /app
RUN mkdir -p uploads temp

# 4. Copiamos los archivos de requerimientos e instalamos librerías
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiamos todo el código de la app
COPY . .

# 6. Comando para arrancar con Gunicorn (más rápido para la nube)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--timeout", "300", "app:app"]
