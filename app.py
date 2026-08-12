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

# --- DETECCIÓN RÁPIDA DE CAJETÍN ---
def extraer_datos_cajetin(pagina):
    texto_completo = pagina.get_text("text")
    lineas = [l.strip() for l in texto_completo.split('\n') if len(l.strip()) > 3]
    
    codigo_plano = "No detectado"
    for l in lineas:
        match = re.search(r'[A-Z0-9]{2,}[-\_][A-Z0-9\-_]+', l)
        if match:
            codigo_plano = match.group(0)
            break
            
    tres_filas = " | ".join(lineas[-4:]) if len(lineas) >= 4 else "Cajetín sin datos claros"
    return tres_filas, codigo_plano

# --- BÚSQUEDA EN DOS PASADAS (100% -> 80% -> FALLBACK) ---
def buscar_posicion_espacio_libre(imagen_gris, ancho_sello, alto_sello, sellos_ya_puestos):
    _, binaria = cv2.threshold(imagen_gris, 230, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello * alto_sello
    pad = 20
    paso = 25

    candidatos = []

    # Recorrido de Abajo-Derecha hacia Arriba-Izquierda
    for x in range(ancho_img - ancho_sello - 30, 30, -paso):
        for y in range(alto_img - alto_sello - 30, 30, -paso):
            
            # Reserva para el cajetín en pantalla (esquina inferior derecha)
            if x > (ancho_img * 0.60) and y > (alto_img * 0.70):
                continue

            # Evitar superposición con otros sellos
            colision = False
            for (sx, sy, sw, sh) in sellos_ya_puestos:
                if not (x + ancho_sello + pad < sx or x > sx + sw + pad or
                        y + alto_sello + pad < sy or y > sy + sh + pad):
                    colision = True
                    break
            if colision:
                continue

            # Porcentaje de blancura
            caja = binaria[y : y + alto_sello, x : x + ancho_sello]
            pixeles_ocupados = cv2.countNonZero(caja)
            porcentaje_libre = 1.0 - (pixeles_ocupados / area_sello)

            candidatos.append((porcentaje_libre, x, y))

    if not candidatos:
        return 50, 50

    # PASADA 1: Búsqueda estricta de espacio 100% libre
    for libre, x, y in candidatos:
        if libre >= 0.99:
            return x, y

    # PASADA 2: Búsqueda al 80% libre (solo si falló la pasada 1)
    for libre, x, y in candidatos:
        if libre >= 0.80:
            return x, y

    # PASADA 3 (Fallback): La posición con mayor porcentaje libre disponible
    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][1], candidatos[0][2]

# --- DIBUJO DEL SELLO VECTORIAL SIN ROTAR LA PÁGINA ---
def agregar_sello_vectorial(pagina, rect, tipo_sello, texto_fecha, rotacion):
    color = (0.85, 0.05, 0.05) if "CC" in tipo_sello else (0.0, 0.3, 0.75)
    linea_2 = "COPIA CONTROLADA" if "CC" in tipo_sello else "COPIA INFORMATIVA"

    # Marco rectangular
    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    # Si la página nativa está rotada 90° o 270°, el texto se orienta para leerse horizontalmente en pantalla
    angulo_texto = (360 - rotacion) % 360

    alto_caja = rect.height
    ancho_caja = rect.width
    centro_x = rect.x0 + (ancho_caja / 2)
    centro_y = rect.y0 + (alto_caja / 2)

    def dibujar_linea_texto(texto, prop, prop_size, fontname):
        if rotacion in [90, 270]:
            ref_tam = ancho_caja
            ref_ancho = alto_caja
        else:
            ref_tam = alto_caja
            ref_ancho = ancho_caja

        fontsize = ref_tam * prop_size
        ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        if ancho_txt > (ref_ancho * 0.88):
            fontsize = fontsize * ((ref_ancho * 0.88) / ancho_txt)
            ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        if rotacion == 0:
            pt = fitz.Point(centro_x - (ancho_txt / 2), rect.y0 + (alto_caja * prop))
        elif rotacion == 90:
            pt = fitz.Point(rect.x0 + (ancho_caja * prop), centro_y + (ancho_txt / 2))
        elif rotacion == 270:
            pt = fitz.Point(rect.x1 - (ancho_caja * prop), centro_y - (ancho_txt / 2))
        else:
            pt = fitz.Point(centro_x - (ancho_txt / 2), rect.y1 - (alto_caja * prop))

        pagina.insert_text(
            pt,
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color,
            rotate=angulo_texto
        )

    dibujar_linea_texto("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_linea_texto(linea_2, 0.62, 0.22, "hebo")
    dibujar_linea_texto(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

# --- PROCESAMIENTO SIN ALTERAR LA ROTACIÓN DE LA PÁGINA ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]
        rot = pagina.rotation

        # Resumen del cajetín
        filas_desc, cod_plano = extraer_datos_cajetin(pagina)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": cod_plano,
            "Detalle Cajetín": filas_desc
        })

        # Renderizado exacto de la vista del usuario
        pixmap = pagina.get_pixmap(dpi=100)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        # Definir dimensiones en píxeles para la búsqueda según la orientación
        if rot in [90, 270]:
            ancho_search_px, alto_search_px = 110, 220
        else:
            ancho_search_px, alto_search_px = 220, 110

        sellos_puestos_hoja = []

        for item_sello in lista_sellos_elegidos:
            # 1. Búsqueda directa en la vista del usuario
            x_px, y_px = buscar_posicion_espacio_libre(
                imagen_gris, 
                ancho_search_px, 
                alto_search_px, 
                sellos_puestos_hoja
            )
            sellos_puestos_hoja.append((x_px, y_px, ancho_search_px, alto_search_px))

            # 2. Transformación directa a coordenadas de la página nativa usando matriz de des-rotación
            # Coordenadas normalizadas (0.0 a 1.0)
            nx0 = x_px / pixmap.w
            ny0 = y_px / pixmap.h
            nx1 = (x_px + ancho_search_px) / pixmap.w
            ny1 = (y_px + alto_search_px) / pixmap.h

            # Rectángulo en el sistema de coordenadas visibles
            view_rect = fitz.Rect(
                nx0 * pagina.rect.width,
                ny0 * pagina.rect.height,
                nx1 * pagina.rect.width,
                ny1 * pagina.rect.height
            )

            # Matriz de PyMuPDF que mapea la vista directamente al PDF nativo sin tocar pagina.rotation
            mat = pagina.derotation_matrix
            rect_sello = view_rect * mat

            # 3. Inserción directa
            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial(pagina, rect_sello, item_sello, texto_fecha, rot)
            else:
                ruta_img = libreria_archivos[item_sello]
                pagina.insert_image(rect_sello, filename=ruta_img, rotate=(360 - rot) % 360)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos

# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Estampador de Planos A3", page_icon="📐", layout="wide")

st.title("📐 ESTAMPADOR INTELIGENTE DE PLANOS A3")
st.caption("Detección de ejes y estampa alineada directamente sin alterar la estructura del PDF")

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
            st.sidebar.success("¡Sello Guardado!")
            st.rerun()

col_izq, col_der = st.columns([1, 1])

with col_izq:
    archivo_pdf = st.file_uploader("1. Selecciona tu PDF consolidado:", type=["pdf"])
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    texto_fecha = st.text_input("2. Fecha de Sellado:", value=fecha_hoy)

libreria_archivos = obtener_libreria_sellos()
opciones = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

with col_der:
    sellos_seleccionados = st.multiselect("3. Selecciona Sellos/Firmas:", options=opciones, default=[opciones[0]])

st.divider()

if archivo_pdf and sellos_seleccionados:
    if st.button("🚀 Estampar Documento", use_container_width=True):
        with st.spinner("Estampando láminas..."):
            pdf_res, resumen = procesar_pdf(
                archivo_pdf.read(), 
                sellos_seleccionados, 
                libreria_archivos, 
                texto_fecha
            )
            st.success("¡Láminas estampadas correctamente!")
            st.download_button(
                "📥 Descargar PDF Sellado", 
                data=pdf_res, 
                file_name=f"SELLADO_{archivo_pdf.name}", 
                mime="application/pdf", 
                use_container_width=True
            )
            st.subheader("📋 Resumen de Planos")
            st.dataframe(resumen, use_container_width=True)