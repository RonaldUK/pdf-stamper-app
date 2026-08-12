import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURACIÓN DE PARÁMETROS GLOBALES ---
CARPETA_SELLOS = "firmas_sellos"
UMBRAL_BLANCO_SECUNDARIO = 0.70  # Variable editable: 70% de espacio blanco aceptable

if not os.path.exists(CARPETA_SELLOS):
    os.makedirs(CARPETA_SELLOS)

def obtener_libreria_sellos():
    extensiones = ('*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG')
    archivos = []
    for ext in extensiones:
        archivos.extend(glob.glob(os.path.join(CARPETA_SELLOS, ext)))
    
    libreria = {}
    for ruta in archivos:
        nombre_base = os.path.basename(ruta)
        nombre_sin_ext = os.path.splitext(nombre_base)[0].replace("_", " ").title()
        libreria[nombre_sin_ext] = ruta
    return libreria

# --- BÚSQUEDA ADAPTATIVA Y MULTI-PASO DE ZONA VACÍA ---
def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px, umbral_tolerancia=UMBRAL_BLANCO_SECUNDARIO):
    """
    Busca espacio libre en 3 fases:
    1. Fase Estricta (98% libre).
    2. Fase Secundaria (Umbral dinámico, ej. 70% libre).
    3. Fase Global (Retorna la zona con MENOR ocupación de toda la hoja).
    """
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 20
    area_total_sello = ancho_sello_px * alto_sello_px

    # Puntos límites de escaneo (evita bordes absolutos)
    x_inicio, x_fin = ancho_img - ancho_sello_px - 20, 20
    y_inicio, y_fin = alto_img - alto_sello_px - 20, 20

    # Puntuación para la fase 3 (mejores coordenadas encontradas)
    min_pixeles = float('inf')
    mejor_posicion = (ancho_img - ancho_sello_px - 30, alto_img - alto_sello_px - 30)

    # PASADA 1: Búsqueda de espacio casi 100% limpio (0.2% de ocupación máxima)
    for x in range(x_inicio, x_fin, -paso):
        for y in range(y_inicio, y_fin, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            if pixeles_ocupados < (area_total_sello * 0.002):
                return x, y
            
            if pixeles_ocupados < min_pixeles:
                min_pixeles = pixeles_ocupados
                mejor_posicion = (x, y)

    # PASADA 2: Búsqueda con umbral editable (ej. 70% blanco = max 30% ocupado)
    max_ocupacion_permitida = area_total_sello * (1.0 - umbral_tolerancia)
    
    for x in range(x_inicio, x_fin, -paso):
        for y in range(y_inicio, y_fin, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            if pixeles_ocupados <= max_ocupacion_permitida:
                return x, y

    # PASADA 3: Si ninguna zona cumplió la meta, devuelve la región menos saturada
    return mejor_posicion


# --- EXTRAER CÓDIGO O NOMBRE DEL PLANO ---
def extraer_codigo_plano(pagina):
    """
    Extrae texto del cajetín (generalmente ubicado en la esquina inferior derecha).
    """
    rect_pagina = pagina.rect
    # Definir el ROI en el cuadrante inferior derecho (último 35% x, 30% y)
    roi_cajetin = fitz.Rect(
        rect_pagina.width * 0.65,
        rect_pagina.height * 0.70,
        rect_pagina.width,
        rect_pagina.height
    )
    
    texto_cajetin = pagina.get_text("text", clip=roi_cajetin)
    
    # Se corrige la sangría en esta línea
    lineas = [linea.strip() for linea in texto_cajetin.split('\n') if linea.strip()]
    
    if lineas:
        # Busca una línea con patrón alfanumérico
        for linea in lineas:
            if re.search(r'[A-Z0-9]{3,}[-\_][A-Z0-9]+', linea):
                return linea
        return lineas[-1]  # Retorna la última línea leída si no coincide el regex
    
    return f"Lámina_{pagina.number + 1}"


# --- MOTOR DE PROCESAMIENTO AUMENTADO ---
def agregar_sello_vectorial_pdf(pagina, rect, tipo_sello, texto_fecha):
    color = (0.85, 0.05, 0.05) if "CC" in tipo_sello else (0.0, 0.3, 0.75)
    linea_2 = "COPIA CONTROLADA" if "CC" in tipo_sello else "COPIA INFORMATIVA"

    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    alto_caja = rect.height
    ancho_caja = rect.width
    centro_x = rect.x0 + (ancho_caja / 2)

    def dibujar_texto_proporcional(texto, prop_y, prop_size, fontname):
        fontsize = alto_caja * prop_size
        ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)
        
        if ancho_texto > (ancho_caja * 0.90):
            fontsize = fontsize * ((ancho_caja * 0.90) / ancho_texto)
            ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        x_calculado = centro_x - (ancho_texto / 2)
        y_calculado = rect.y0 + (alto_caja * prop_y)
        
        pagina.insert_text(
            fitz.Point(x_calculado, y_calculado),
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color
        )

    dibujar_texto_proporcional("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_texto_proporcional(linea_2, 0.62, 0.22, "hebo")
    dibujar_texto_proporcional(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")


def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha, umbral_blanco):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    codigos_planos = []  # Lista para almacenar los códigos detectados

    for i in range(len(doc)):
        pagina = doc[i]

        # 1. Extraer código del plano
        codigo_detectado = extraer_codigo_plano(pagina)
        codigos_planos.append({"Hoja": i + 1, "Código/Nombre": codigo_detectado})

        # 2. Renderizar imagen para búsqueda de espacio vacíos
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        for item_sello in lista_sellos_elegidos:
            ancho_sello_px = 250
            alto_sello_px = 120

            # Búsqueda optimizada con tolerancia configurable
            x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px, umbral_blanco)

            pdf_x = x_px * factor_x
            pdf_y = y_px * factor_y
            pdf_ancho = ancho_sello_px * factor_x
            pdf_alto = alto_sello_px * factor_y

            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial_pdf(pagina, rect_sello, item_sello, texto_fecha)
            else:
                ruta_imagen = libreria_archivos[item_sello]
                pagina.insert_image(rect_sello, filename=ruta_imagen)

            # Marcar el espacio usado en la matriz para no encimar sellos múltiples
            cv2.rectangle(imagen_gris, (x_px, y_px), (x_px + ancho_sello_px, y_px + alto_sello_px), 0, -1)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, codigos_planos


# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Agente RZ - Estampado", page_icon="🤖", layout="wide")

st.title("🤖📑 AGENTE PARA ESTAMPAR")
st.caption("Gestión de sellos y copias elaborado por RZ")

# SIDEBAR DE CONFIGURACIÓN
st.sidebar.header("⚙️ Ajustes de Algoritmo")
umbral_usuario = st.sidebar.slider(
    "Mínimo % de Espacio Blanco Requerido:",
    min_value=30,
    max_value=95,
    value=70,
    step=5,
    help="Si no encuentra espacio 100% libre, usará este porcentaje mínimo de blancura antes de colocar el sello."
) / 100.0

st.sidebar.divider()
st.sidebar.header("📁 Cargar Nuevos Sellos")
with st.sidebar.expander("➕ Subir Firma/Imagen a Base de Datos", expanded=False):
    nuevo_nombre = st.text_input("Nombre de la firma/sello:")
    archivo_nuevo = st.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])
    
    if st.button("💾 Guardar en Base de Datos"):
        if not nuevo_nombre.strip() or not archivo_nuevo:
            st.sidebar.error("Ingresa un nombre y selecciona la imagen.")
        else:
            ext = archivo_nuevo.name.split(".")[-1]
            nombre_archivo = f"{nuevo_nombre.lower().strip().replace(' ', '_')}.{ext}"
            ruta_destino = os.path.join(CARPETA_SELLOS, nombre_archivo)
            
            with open(ruta_destino, "wb") as f:
                f.write(archivo_nuevo.read())
                
            st.sidebar.success(f"¡Sello '{nuevo_nombre}' agregado con éxito!")
            st.rerun()

st.divider()

st.subheader("1. 📂 Cargar Plano / Documento PDF")
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF (A3 o Estándar):", type=["pdf"])

st.divider()

st.subheader("2. 🏷️ Selección de Sellos y Firma")
col1, col2 = st.columns([1, 2])

with col1:
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    texto_fecha_ingresada = st.text_input("📅 Fecha de Sellado (Editable):", value=fecha_hoy)

libreria_archivos = obtener_libreria_sellos()
opciones_dinamicas = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]
opciones_totales = opciones_dinamicas + list(libreria_archivos.keys())

with col2:
    sellos_seleccionados = st.multiselect(
        "🏷️ Selecciona uno o varios sellos/firmas a aplicar:",
        options=opciones_totales,
        default=[opciones_totales[0]] if opciones_totales else []
    )

st.divider()

if archivo_pdf and sellos_seleccionados:
    if st.button(f"🚀 Estampar {len(sellos_seleccionados)} Sello(s) Seleccionado(s)", use_container_width=True):
        with st.spinner("Procesando láminas, leyendo códigos y estampando..."):
            try:
                pdf_resultado, lista_codigos = procesar_pdf(
                    archivo_pdf.read(), 
                    sellos_seleccionados, 
                    libreria_archivos, 
                    texto_fecha_ingresada,
                    umbral_usuario
                )
                
                st.success("¡Documento estampado exitosamente en TODAS las hojas!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado",
                    data=pdf_resultado,
                    file_name=f"ESTAMPADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )

                # Despliegue de la lista de planos/códigos detectados
                with st.expander("📋 Ver lista de planos detectados por página", expanded=True):
                    st.dataframe(lista_codigos, use_container_width=True)

            except Exception as e:
                st.error(f"Error durante el procesamiento: {e}")