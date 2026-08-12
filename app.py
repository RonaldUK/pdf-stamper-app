import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
from datetime import datetime

# --- CONFIGURACIÓN DE CARPETAS ---
CARPETA_SELLOS = "firmas_sellos"
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

# --- DETECCIÓN RÁPIDA DE CAJETÍN SIN OCR PESADO ---
def extraer_datos_cajetin_rapido(pagina):
    """
    Extrae texto de la página des-rotada de forma instantánea.
    """
    texto_completo = pagina.get_text("text")
    lineas = [l.strip() for l in texto_completo.split('\n') if len(l.strip()) > 3]
    
    # Buscar patrón de código de plano (ej. ME-154, PL-001, etc.)
    codigo_plano = "No detectado"
    for l in lineas:
        match = re.search(r'[A-Z0-9]{2,}[-\_][A-Z0-9\-_]+', l)
        if match:
            codigo_plano = match.group(0)
            break
            
    # Tomar las últimas líneas (donde suele estar el cajetín)
    tres_filas = " | ".join(lineas[-4:]) if len(lineas) >= 4 else "Cajetín sin datos claros"
    return tres_filas, codigo_plano

# --- ALGORITMO DE ESPACIO LIBRE ROBUSTO ---
def buscar_posicion_sello(imagen_gris, ancho_sello, alto_sello, sellos_ya_puestos):
    # Invertir para detectar dibujo (negro = dibujo = 255)
    _, binaria = cv2.threshold(imagen_gris, 230, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello * alto_sello
    pad = 20

    candidatos = []
    paso = 30

    # Escanear la hoja
    for y in range(30, alto_img - alto_sello - 30, paso):
        for x in range(30, ancho_img - ancho_sello - 30, paso):
            
            # Evitar Cajetín Inferior Derecho
            if x > (ancho_img * 0.55) and y > (alto_img * 0.70):
                continue

            # Evitar Traslape/Chancado con sellos anteriores
            colision = False
            for (sx, sy, sw, sh) in sellos_ya_puestos:
                if not (x + ancho_sello + pad < sx or x > sx + sw + pad or
                        y + alto_sello + pad < sy or y > sy + sh + pad):
                    colision = True
                    break
            if colision:
                continue

            # Contar cuánto dibujo hay en este recuadro
            caja = binaria[y : y + alto_sello, x : x + ancho_sello]
            pixeles_ocupados = cv2.countNonZero(caja)
            porcentaje_libre = 1.0 - (pixeles_ocupados / area_sello)

            candidatos.append((porcentaje_libre, pixeles_ocupados, x, y))

    # Priorizar la zona con MÁS espacio en blanco (1.0 = 100% blanco)
    candidatos.sort(key=lambda c: c[0], reverse=True)

    if candidatos:
        return candidatos[0][2], candidatos[0][3]
    return 50, 50

# --- DIBUJO DE SELLO VECTORIAL SIN GIRAR ---
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

    def dibujar_texto(texto, prop_y, prop_size, fontname):
        fontsize = alto_caja * prop_size
        ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)
        if ancho_texto > (ancho_caja * 0.88):
            fontsize = fontsize * ((ancho_caja * 0.88) / ancho_texto)
            ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        x_calc = centro_x - (ancho_texto / 2)
        y_calc = rect.y0 + (alto_caja * prop_y)
        
        pagina.insert_text(
            fitz.Point(x_calc, y_calc),
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color
        )

    dibujar_texto("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_texto(linea_2, 0.62, 0.22, "hebo")
    dibujar_texto(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

# --- PROCESAMIENTO PRINCIPAL CON NORMALIZACIÓN DE ROTACIÓN ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]

        # CORRECCIÓN DE AUTOCAD: Si la página tiene rotación interna (90, 180, 270), la normalizamos a 0
        rotacion_original = pagina.rotation
        if rotacion_original != 0:
            pagina.set_rotation(0)

        # 1. Obtener imagen para detección espacial
        pixmap = pagina.get_pixmap(dpi=100) # DPI 100 es súper rápido y liviano
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        # 2. Resumen rápido
        filas_desc, cod_plano = extraer_datos_cajetin_rapido(pagina)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": cod_plano,
            "Detalle Cajetín": filas_desc
        })

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        sellos_puestos_hoja = []

        for item_sello in lista_sellos_elegidos:
            ancho_px, alto_px = 220, 110  # Mantener proporción horizontal siempre

            # Buscar mejor espacio blanco
            x_px, y_px = buscar_posicion_sello(imagen_gris, ancho_px, alto_px, sellos_puestos_hoja)
            sellos_puestos_hoja.append((x_px, y_px, ancho_px, alto_px))

            # Convertir a coordenadas nativas del PDF
            pdf_x0 = x_px * factor_x
            pdf_y0 = y_px * factor_y
            pdf_x1 = pdf_x0 + (ancho_px * factor_x)
            pdf_y1 = pdf_y0 + (alto_px * factor_y)

            rect_sello = fitz.Rect(pdf_x0, pdf_y0, pdf_x1, pdf_y1)

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial_pdf(pagina, rect_sello, item_sello, texto_fecha)
            else:
                ruta_img = libreria_archivos[item_sello]
                # Inserción con alineación horizontal estricta
                pagina.insert_image(rect_sello, filename=ruta_img)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos

# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Estampador Rápido de Planos", page_icon="⚡", layout="wide")

st.title("⚡ ESTAMPADOR ULTRA-RÁPIDO (AUTOCAD & REVIT)")
st.caption("Corrección automática de rotación + Detección de espacio limpio sin demoras")

st.sidebar.header("📁 Cargar Nuevas Firmas")
with st.sidebar.expander("➕ Subir Imagen a Base de Datos", expanded=False):
    nuevo_nombre = st.text_input("Nombre de la firma/sello:")
    archivo_nuevo = st.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])
    
    if st.button("💾 Guardar Sello"):
        if nuevo_nombre.strip() and archivo_nuevo:
            ext = archivo_nuevo.name.split(".")[-1]
            ruta_dest = os.path.join(CARPETA_SELLOS, f"{nuevo_nombre.lower().strip().replace(' ', '_')}.{ext}")
            with open(ruta_dest, "wb") as f:
                f.write(archivo_nuevo.read())
            st.sidebar.success("¡Guardado!")
            st.rerun()

archivo_pdf = st.file_uploader("1. Selecciona tu PDF consolidado:", type=["pdf"])

fecha_hoy = datetime.now().strftime("%d/%m/%Y")
texto_fecha = st.text_input("2. Fecha de Sellado:", value=fecha_hoy)

libreria_archivos = obtener_libreria_sellos()
opciones = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

sellos_seleccionados = st.multiselect("3. Selecciona Sellos/Firmas:", options=opciones, default=[opciones[0]])

if archivo_pdf and sellos_seleccionados:
    if st.button("🚀 Estampar Ahora", use_container_width=True):
        with st.spinner("Procesando planos al instante..."):
            pdf_res, resumen = procesar_pdf(
                archivo_pdf.read(), 
                sellos_seleccionados, 
                libreria_archivos, 
                texto_fecha
            )
            st.success("¡Proceso completado en segundos!")
            st.download_button("📥 Descargar PDF Corregido", data=pdf_res, file_name=f"SELLADO_{archivo_pdf.name}", mime="application/pdf", use_container_width=True)
            st.dataframe(resumen, use_container_width=True)