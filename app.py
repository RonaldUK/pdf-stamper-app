import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import json

# --- CONFIGURACIÓN DE CARPETAS Y GESTIÓN DE SELLOS ---
CARPETA_SELLOS = "firmas_sellos"
ARCHIVO_JSON = os.path.join(CARPETA_SELLOS, "sellos.json")

# Crear la carpeta de sellos si no existe
if not os.path.exists(CARPETA_SELLOS):
    os.makedirs(CARPETA_SELLOS)

def cargar_diccionario_sellos():
    """Carga los sellos guardados en el archivo JSON."""
    if os.path.exists(ARCHIVO_JSON):
        with open(ARCHIVO_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_diccionario_sellos(diccionario):
    """Guarda el diccionario actualizado en un archivo JSON."""
    with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(diccionario, f, ensure_ascii=False, indent=4)

# --- LÓGICA DE BÚSQUEDA VERTICAL (Abajo ➔ Arriba ➔ Izquierda) ---
def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px):
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 15

    for x in range(ancho_img - ancho_sello_px - 20, 20, -paso):
        for y in range(alto_img - alto_sello_px - 20, 20, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            if pixeles_ocupados < (ancho_sello_px * alto_sello_px * 0.002):
                return x, y

    return ancho_img - ancho_sello_px - 30, alto_img - alto_sello_px - 30


def procesar_pdf(pdf_bytes, ruta_sello_png):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    total_paginas = len(doc)

    for i in range(total_paginas):
        pagina = doc[i]
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        ancho_sello_px = 250
        alto_sello_px = 120

        x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        pdf_x = x_px * factor_x
        pdf_y = y_px * factor_y
        pdf_ancho = ancho_sello_px * factor_x
        pdf_alto = alto_sello_px * factor_y

        rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)
        pagina.insert_image(rect_sello, filename=ruta_sello_png)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes


# --- CONFIGURACIÓN DE LA INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Stamper IA - Planos A3", page_icon="📑", layout="centered")

st.markdown("""
    <style>
        .stButton>button {
            width: 100%;
            border-radius: 8px;
            height: 3em;
            background-color: #2e7d32;
            color: white;
            font-weight: bold;
        }
    </style>
""", unsafe_allow_html=True)

st.title("📑 Estampador Inteligente A3")

# --- PANEL LATERAL (SIDEBAR): GESTIÓN DE NUEVOS SELLOS ---
st.sidebar.header("📁 Administración de Sellos")

sellos_dict = cargar_diccionario_sellos()

with st.sidebar.expander("➕ Cargar Nuevo Sello / Firma", expanded=False):
    nuevo_nombre = st.text_input("Nombre del sello (Ej: Copia Controlada):")
    archivo_nuevo_sello = st.file_uploader("Selecciona imagen PNG/JPG:", type=["png", "jpg", "jpeg"])
    
    if st.button("💾 Guardar Sello"):
        if nuevo_nombre.strip() == "":
            st.sidebar.error("Escribe un nombre válido para el sello.")
        elif archivo_nuevo_sello is None:
            st.sidebar.error("Selecciona una imagen.")
        else:
            # Crear un nombre de archivo seguro
            ext = archivo_nuevo_sello.name.split(".")[-1]
            nombre_archivo_limpio = f"{nuevo_nombre.lower().replace(' ', '_')}.{ext}"
            ruta_destino = os.path.join(CARPETA_SELLOS, nombre_archivo_limpio)
            
            # Guardar la imagen en la carpeta
            with open(ruta_destino, "wb") as f:
                f.write(archivo_nuevo_sello.read())
            
            # Registrar en el JSON
            sellos_dict[nuevo_nombre] = ruta_destino
            guardar_diccionario_sellos(sellos_dict)
            
            st.sidebar.success(f"¡Sello '{nuevo_nombre}' guardado!")
            st.rerun()

# --- CUERPO PRINCIPAL ---
st.caption("Procesamiento automático de planos A3 con almacenamiento dinámico de sellos.")
st.divider()

# 1. Cargar archivo PDF
archivo_pdf = st.file_uploader("📂 Selecciona tu archivo PDF (A3):", type=["pdf"])

# 2. Selección del Sello
if not sellos_dict:
    st.info("💡 Aún no tienes sellos registrados. Usa la barra lateral (izquierda) para agregar tu primer sello.")
else:
    opcion_sello = st.selectbox(
        "🏷️ Selecciona el sello que deseas aplicar:",
        options=list(sellos_dict.keys())
    )

    ruta_sello_elegido = sellos_dict[opcion_sello]

    # Mostrar vista previa pequeña del sello seleccionado
    if os.path.exists(ruta_sello_elegido):
        st.image(ruta_sello_elegido, caption=f"Vista previa: {opcion_sello}", width=150)

    st.divider()

    # 3. Accionador
    if archivo_pdf:
        if st.button("🚀 Estampar Plano", use_container_width=True):
            if not os.path.exists(ruta_sello_elegido):
                st.error(f"No se encontró el archivo de sello en '{ruta_sello_elegido}'.")
            else:
                with st.spinner("Escaneando lámina (Abajo ➔ Arriba ➔ Izquierda)..."):
                    try:
                        pdf_resultado = procesar_pdf(archivo_pdf.read(), ruta_sello_elegido)
                        st.success("¡Documento procesado con éxito!")
                        
                        st.download_button(
                            label="📥 Descargar PDF Sellado",
                            data=pdf_resultado,
                            file_name=f"SELLADO_{archivo_pdf.name}",
                            mime="application/pdf",
                            use_container_width=True
                        )
                    except Exception as e:
                        st.error(f"Error procesando el documento: {e}")