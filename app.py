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

# --- BÚSQUEDA DE ESPACIO EN 2 PASADAS (100% -> 80% -> FALLBACK) ---
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
            
            # Reserva para el cajetín (extremo inferior derecho)
            if x > (ancho_img * 0.60) and y > (alto_img * 0.70):
                continue

            # Evitar colisión con sellos colocados previamente
            colision = False
            for (sx, sy, sw, sh) in sellos_ya_puestos:
                if not (x + ancho_sello + pad < sx or x > sx + sw + pad or
                        y + alto_sello + pad < sy or y > sy + sh + pad):
                    colision = True
                    break
            if colision:
                continue

            # Evaluar blancura
            caja = binaria[y : y + alto_sello, x : x + ancho_sello]
            pixeles_ocupados = cv2.countNonZero(caja)
            porcentaje_libre = 1.0 - (pixeles_ocupados / area_sello)

            candidatos.append((porcentaje_libre, x, y))

    if not candidatos:
        return 50, 50

    # PASADA 1: Búsqueda estricta al 100% libre (>= 0.99)
    for libre, x, y in candidatos:
        if libre >= 0.99:
            return x, y

    # PASADA 2: Búsqueda al 80% libre (>= 0.80)
    for libre, x, y in candidatos:
        if libre >= 0.80:
            return x, y

    # PASADA 3 (Fallback): Mejor posición disponible
    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][1], candidatos[0][2]

# --- DIBUJO DE SELLO VECTORIAL CON ROTACIÓN ORIENTADA AL USUARIO ---
def agregar_sello_vectorial_orientado(pagina, rect, tipo_sello, texto_fecha, rotacion):
    color = (0.85, 0.05, 0.05) if "CC" in tipo_sello else (0.0, 0.3, 0.75)
    linea_2 = "COPIA CONTROLADA" if "CC" in tipo_sello else "COPIA INFORMATIVA"

    # Dibujar la caja del sello
    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    # Ángulo para contrarrestar la rotación del PDF y mantener el texto legible horizontalmente
    angulo_texto = (360 - rotacion) % 360

    alto_caja = rect.height
    ancho_caja = rect.width

    # Adaptar posición y dimensiones de texto según la orientación
    if rotacion in [90, 270]:
        # Si la página internamente está rotada 90/270, las proporciones de la caja están invertidas respecto a la vista
        ancho_ref = alto_caja
        alto_ref = ancho_caja
    else:
        ancho_ref = ancho_caja
        alto_ref = alto_caja

    centro_x = rect.x0 + (ancho_caja / 2)
    centro_y = rect.y0 + (alto_caja / 2)

    def dibujar_texto(texto, prop_y, prop_size, fontname):
        fontsize = alto_ref * prop_size
        ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)
        
        if ancho_texto > (ancho_ref * 0.88):
            fontsize = fontsize * ((ancho_ref * 0.88) / ancho_texto)
            ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        if rotacion == 0:
            x_calc = centro_x - (ancho_texto / 2)
            y_calc = rect.y0 + (alto_caja * prop_y)
            pt = fitz.Point(x_calc, y_calc)
        elif rotacion == 90:
            x_calc = rect.x0 + (ancho_caja * prop_y)
            y_calc = centro_y + (ancho_texto / 2)
            pt = fitz.Point(x_calc, y_calc)
        elif rotacion == 270:
            x_calc = rect.x1 - (ancho_caja * prop_y)
            y_calc = centro_y - (ancho_texto / 2)
            pt = fitz.Point(x_calc, y_calc)
        else:
            x_calc = centro_x - (ancho_texto / 2)
            y_calc = rect.y1 - (alto_caja * prop_y)
            pt = fitz.Point(x_calc, y_calc)

        pagina.insert_text(
            pt,
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color,
            rotate=angulo_texto
        )

    dibujar_texto("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_texto(linea_2, 0.62, 0.22, "hebo")
    dibujar_texto(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

# --- PROCESAMIENTO PRINCIPAL ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]
        rotacion = pagina.rotation

        # Renderizar la página exacto a como la ve el usuario
        pixmap = pagina.get_pixmap(dpi=100)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        # Resumen del cajetín
        filas_desc, cod_plano = extraer_datos_cajetin(pagina)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": cod_plano,
            "Detalle Cajetín": filas_desc
        })

        # Dimensiones del sello según la vista del usuario
        ancho_px_vista, alto_px_vista = 220, 110
        sellos_puestos_hoja = []

        for item_sello in lista_sellos_elegidos:
            # Buscar coordenadas de espacio libre en la vista del usuario
            x_px, y_px = buscar_posicion_espacio_libre(imagen_gris, ancho_px_vista, alto_px_vista, sellos_puestos_hoja)
            sellos_puestos_hoja.append((x_px, y_px, ancho_px_vista, alto_px_vista))

            # Transformar a coordenadas del canvas interno de PyMuPDF
            f_x = pagina.rect.width / pixmap.w
            f_y = pagina.rect.height / pixmap.h

            pdf_x0 = x_px * f_x
            pdf_y0 = y_px * f_y
            pdf_x1 = (x_px + ancho_px_vista) * f_x
            pdf_y1 = (y_px + alto_px_vista) * f_y

            rect_sello = fitz.Rect(pdf_x0, pdf_y0, pdf_x1, pdf_y1)

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial_orientado(pagina, rect_sello, item_sello, texto_fecha, rotacion)
            else:
                ruta_img = libreria_archivos[item_sello]
                # Inserción de imagen considerando rotación
                pagina.insert_image(rect_sello, filename=ruta_img, rotate=(360 - rotacion) % 360)

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
st.caption("Alineación automática de sellos siempre horizontal según vista de pantalla")

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
        with st.spinner("Estampando láminas con orientación corregida..."):
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