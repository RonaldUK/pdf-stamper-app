import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURACIÓN DE LA CARPETA BASE DE DATOS ---
CARPETA_SELLOS = "firmas_sellos"

if not os.path.exists(CARPETA_SELLOS):
    os.makedirs(CARPETA_SELLOS)

def obtener_libreria_sellos():
    """Lee automáticamente todas las imágenes PNG/JPG de la carpeta."""
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


# --- FUNCIÓN PARA AGREGAR FECHA DINÁMICA A LA IMAGEN ---
def estampar_fecha_en_imagen(ruta_imagen_original, texto_fecha):
    """Carga la imagen del sello y le dibuja dinámicamente el texto de la fecha en la parte inferior."""
    # Abrir imagen con OpenCV/PIL
    img_pil = Image.open(ruta_imagen_original).convert("RGBA")
    draw = ImageDraw.Draw(img_pil)
    
    ancho, alto = img_pil.size

    # Intentar cargar una fuente estándar del sistema o usar la por defecto
    try:
        # Tamaños proporcionales al tamaño del sello
        fuente = ImageFont.truetype("arial.ttf", int(alto * 0.18))
    except:
        fuente = ImageFont.load_default()

    texto_a_imprimir = f"FECHA: {texto_fecha}"
    
    # Coordenadas aproximadas para la parte inferior central del sello
    # Puedes ajustar las coordenadas (X, Y) si deseas mover la fecha
    pos_x = int(ancho * 0.15)
    pos_y = int(alto * 0.68)

    # Dibujar el texto en rojo intenso (igual que el estilo de tu imagen)
    color_texto = (220, 20, 20, 255) 
    draw.text((pos_x, pos_y), texto_a_imprimir, fill=color_texto, font=fuente)

    # Guardar temporalmente la imagen modificada con la fecha
    temp_sello = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img_pil.save(temp_sello.name, "PNG")
    temp_sello.close()
    
    return temp_sello.name


# --- BÚSQUEDA VERTICAL (Abajo ➔ Arriba ➔ Izquierda) ---
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


# --- PROCESAMIENTO MULTI-SELLO CON FECHA ---
def procesar_pdf_múltiples_sellos(pdf_bytes, lista_rutas_sellos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    archivos_temporales_fecha = []

    for i in range(len(doc)):
        pagina = doc[i]
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        for ruta_sello in lista_rutas_sellos:
            # Generar sello con la fecha incrustada dinámicamente
            ruta_sello_con_fecha = estampar_fecha_en_imagen(ruta_sello, texto_fecha)
            archivos_temporales_fecha.append(ruta_sello_con_fecha)

            ancho_sello_px = 250
            alto_sello_px = 120

            x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)

            pdf_x = x_px * factor_x
            pdf_y = y_px * factor_y
            pdf_ancho = ancho_sello_px * factor_x
            pdf_alto = alto_sello_px * factor_y

            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)
            pagina.insert_image(rect_sello, filename=ruta_sello_con_fecha)

            # Actualizar memoria para evitar solapamientos
            cv2.rectangle(imagen_gris, (x_px, y_px), (x_px + ancho_sello_px, y_px + alto_sello_px), 0, -1)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    # Limpieza de archivos temporales
    os.remove(path_pdf)
    os.remove(output_pdf_path)
    for tmp_f in archivos_temporales_fecha:
        if os.path.exists(tmp_f):
            os.remove(tmp_f)

    return pdf_final_bytes


# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Stamper IA - Agente RZ", page_icon="🤖", layout="wide")

# Títulos solicitados
st.title("🤖📑 AGENTE PARA ESTAMPAR")
st.caption("Gestion del sellos y copias elaborado por RZ")

st.divider()

# --- 1. CARGAR ARCHIVO PDF PRIMERO (ARRIBA) ---
st.subheader("1. 📂 Cargar Plano / Documento PDF")
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF (A3 o estándar):", type=["pdf"])

st.divider()

# --- 2. CONFIGURACIÓN DE FECHA Y SELLOS ---
st.subheader("2. 🏷️ Configuración de Sellos y Fecha")

# Campo de texto para la fecha con valor por defecto la fecha actual (Formato DD/MM/YYYY)
fecha_actual_str = datetime.now().strftime("%d/%m/%Y")
texto_fecha_ingresada = st.text_input("📅 Fecha a mostrar en el sello (editable):", value=fecha_actual_str)

# Cargar librería de sellos
libreria_actual = obtener_libreria_sellos()

if not libreria_actual:
    st.info("💡 La carpeta `firmas_sellos` está vacía. Usa el panel izquierdo para agregar tus imágenes de sellos.")
else:
    sellos_seleccionados = st.multiselect(
        "Selecciona uno o varios sellos para insertar:",
        options=list(libreria_actual.keys()),
        default=list(libreria_actual.keys())[:1]
    )

    if sellos_seleccionados:
        st.write("**Previsualización de sellos seleccionados:**")
        cols = st.columns(min(len(sellos_seleccionados), 5))
        for idx, nombre_sello in enumerate(sellos_seleccionados):
            col_idx = idx % 5
            with cols[col_idx]:
                st.image(libreria_actual[nombre_sello], caption=f"{idx+1}. {nombre_sello}", use_container_width=True)

st.divider()

# --- 3. ACCIONADOR DE ESTAMPADO ---
if archivo_pdf and libreria_actual and sellos_seleccionados:
    rutas_a_procesar = [libreria_actual[nombre] for nombre in sellos_seleccionados]
    
    if st.button(f"🚀 Estampar {len(sellos_seleccionados)} Sello(s) con Fecha", use_container_width=True):
        with st.spinner("Analizando plano, integrando fecha y ubicando espacios..."):
            try:
                pdf_resultado = procesar_pdf_múltiples_sellos(archivo_pdf.read(), rutas_a_procesar, texto_fecha_ingresada)
                
                st.success("¡Planos sellados con fecha y éxito!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado con Fecha",
                    data=pdf_resultado,
                    file_name=f"ESTAMPADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Error al procesar el documento: {e}")