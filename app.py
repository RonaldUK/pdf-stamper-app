import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
import datetime

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

# --- DETECCIÓN INTELIGENTE DE CÓDIGO Y TÍTULO POR ETIQUETAS ---
def extraer_datos_inteligentes_cajetin(pagina):
    """
    Extrae el Código del Plano y la Descripción debajo de "PROYECTO:" 
    garantizando la correcta transformación espacial independientemente
    de la rotación nativa (0°, 90°, 180°, 270°).
    """
    rot = pagina.rotation
    texto_completo = pagina.get_text("text")

    # 1. BÚSQUEDA DEL CÓDIGO DE PLANO (Por Expresión Regular)
    patron_codigo = r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){3,}(?:-R\d+|-REV\d+)?\b'
    matches = re.findall(patron_codigo, texto_completo)

    codigo_plano = "No detectado"
    if matches:
        falsos_positivos = ["ESCALA", "FECHA", "PROYECTO", "TUBOS", "INDICADA"]
        candidatos_validos = [m for m in matches if not any(fp in m for fp in falsos_positivos)]
        if candidatos_validos:
            codigo_plano = candidatos_validos[0]

    # 2. DIMENSIONES EN PANTALLA (VISTA REAL)
    rect_pag = pagina.rect
    if rot in [90, 270]:
        ancho_v, alto_v = rect_pag.height, rect_pag.width
    else:
        ancho_v, alto_v = rect_pag.width, rect_pag.height

    # 3. BUSCAR LA PALABRA "PROYECTO" EN MODO PALABRAS CON COORDENADAS DE VISTA
    words = pagina.get_text("words")
    mat_directa = pagina.rotation_matrix  # Convierte Nativo -> Pantalla

    rect_proyecto_vista = None

    for w in words:
        texto_word = w[4].upper().strip()
        if "PROYECTO" in texto_word:
            r_nativo = fitz.Rect(w[0], w[1], w[2], w[3])
            r_vista = r_nativo * mat_directa
            
            # Verificar que la etiqueta 'PROYECTO' esté en el 50% inferior de la pantalla (cajetín)
            if r_vista.y0 > (alto_v * 0.50):
                rect_proyecto_vista = r_vista
                break

    titulo_plano = "No detectado"

    # 4. CAPTURAR LA CASILLA JUSTO DEBAJO DE "PROYECTO" EN VISTA DE PANTALLA
    if rect_proyecto_vista:
        view_box_desc = fitz.Rect(
            rect_proyecto_vista.x0 - 40,   # Izquierda
            rect_proyecto_vista.y1 + 2,    # Justo debajo del texto 'PROYECTO:'
            rect_proyecto_vista.x0 + 380,  # Ancho de la casilla de descripción
            rect_proyecto_vista.y1 + 110   # Alto de las 3 líneas
        )

        mat_inversa = pagina.derotation_matrix
        nat_box_desc = view_box_desc * mat_inversa

        texto_celda = pagina.get_text("text", clip=nat_box_desc).strip()
        lineas = [l.strip() for l in texto_celda.split('\n') if l.strip()]

        lineas_limpias = []
        for l in lineas:
            l_up = l.upper()
            palabras_descarte = [
                "ESCALA", "FECHA", "PROYECTO", "INDICADA", "CODIGO", "CÓDIGO", 
                "500M", "100M", "200M", "300M", "400M", "ESCALA GRAFICA", "REVISION"
            ]
            
            if not any(k in l_up for k in palabras_descarte) and l != codigo_plano:
                if not re.match(r'^[\+\-]?\d+[\.\,\+\d]*\s*m?$', l) and len(l) > 3:
                    lineas_limpias.append(l)

        if lineas_limpias:
            titulo_plano = " - ".join(lineas_limpias[:3])

    # 5. FALLBACK SI NO SE ENCONTRÓ "PROYECTO" EN EL CAJETÍN
    if titulo_plano == "No detectado":
        view_roi_cajetin = fitz.Rect(ancho_v * 0.55, alto_v * 0.70, ancho_v, alto_v)
        nat_roi_cajetin = view_roi_cajetin * pagina.derotation_matrix

        texto_roi = pagina.get_text("text", clip=nat_roi_cajetin)
        lineas_fallback = [l.strip() for l in texto_roi.split('\n') if len(l.strip()) > 3]

        lineas_validas = []
        for l in lineas_fallback:
            l_up = l.upper()
            palabras_descarte = [
                "ESCALA", "FECHA", "PROYECTO", "INDICADA", "CODIGO", "MTC", 
                "MINISTERIO", "INTERSUR", "ESCALA GRAFICA", "500M", "400M", "300M", "200M", "100M"
            ]
            if not any(k in l_up for k in palabras_descarte) and l != codigo_plano:
                if not re.match(r'^[\+\-]?\d+[\.\,\+\d]*\s*m?$', l):
                    lineas_validas.append(l)

        if lineas_validas:
            titulo_plano = " - ".join(lineas_validas[:3])

    return codigo_plano, titulo_plano

# --- BÚSQUEDA DE ESPACIO LIBRE EN PANTALLA ---
def buscar_posicion_espacio_libre(imagen_gris, ancho_sello, alto_sello, sellos_ya_puestos):
    _, binaria = cv2.threshold(imagen_gris, 230, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello * alto_sello
    pad = 20
    paso = 25

    candidatos = []

    for x in range(ancho_img - ancho_sello - 30, 30, -paso):
        for y in range(alto_img - alto_sello - 30, 30, -paso):
            
            # Evitar invadir la zona del cajetín principal (esquina inferior derecha)
            if x > (ancho_img * 0.60) and y > (alto_img * 0.70):
                continue

            colision = False
            for (sx, sy, sw, sh) in sellos_ya_puestos:
                if not (x + ancho_sello + pad < sx or x > sx + sw + pad or
                        y + alto_sello + pad < sy or y > sy + sh + pad):
                    colision = True
                    break
            if colision:
                continue

            caja = binaria[y : y + alto_sello, x : x + ancho_sello]
            pixeles_ocupados = cv2.countNonZero(caja)
            porcentaje_libre = 1.0 - (pixeles_ocupados / area_sello)

            candidatos.append((porcentaje_libre, x, y))

    if not candidatos:
        return 50, 50

    for libre, x, y in candidatos:
        if libre >= 0.99:
            return x, y

    for libre, x, y in candidatos:
        if libre >= 0.80:
            return x, y

    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][1], candidatos[0][2]

# --- DIBUJO DEL SELLO VECTORIAL ---
def agregar_sello_vectorial(pagina, rect, tipo_sello, texto_fecha, rotacion):
    color = (0.85, 0.05, 0.05) if "CC" in tipo_sello else (0.0, 0.3, 0.75)
    linea_2 = "COPIA CONTROLADA" if "CC" in tipo_sello else "COPIA INFORMATIVA"

    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    def dibujar_linea_texto(texto, prop_y, prop_size, fontname):
        if rotacion in [90, 270]:
            ancho_ref_vista = rect.height
            alto_ref_vista = rect.width
        else:
            ancho_ref_vista = rect.width
            alto_ref_vista = rect.height

        fontsize = alto_ref_vista * prop_size
        ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        if ancho_txt > (ancho_ref_vista * 0.88):
            fontsize = fontsize * ((ancho_ref_vista * 0.88) / ancho_txt)
            ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        if rotacion == 0:
            pt = fitz.Point(rect.x0 + (rect.width / 2) - (ancho_txt / 2), rect.y0 + (rect.height * prop_y))
        elif rotacion == 90:
            pt = fitz.Point(rect.x0 + (rect.width * prop_y), rect.y1 - (rect.height / 2) + (ancho_txt / 2))
        elif rotacion == 270:
            pt = fitz.Point(rect.x1 - (rect.width * prop_y), rect.y0 + (rect.height / 2) - (ancho_txt / 2))
        else:
            pt = fitz.Point(rect.x1 - (rect.width / 2) + (ancho_txt / 2), rect.y1 - (rect.height * prop_y))

        pagina.insert_text(
            pt,
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color,
            rotate=rotacion
        )

    dibujar_linea_texto("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_linea_texto(linea_2, 0.60, 0.22, "hebo")
    dibujar_linea_texto(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

# --- PROCESAMIENTO PRINCIPAL ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]
        rot = pagina.rotation

        # Extracción inteligente de datos
        cod_plano, titulo_plano = extraer_datos_inteligentes_cajetin(pagina)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": cod_plano,
            "Título / Descripción": titulo_plano
        })

        pixmap = pagina.get_pixmap(dpi=100)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        ancho_sello_vista, alto_sello_vista = 220, 110
        sellos_puestos_hoja = []

        for item_sello in lista_sellos_elegidos:
            x_px, y_px = buscar_posicion_espacio_libre(
                imagen_gris, 
                ancho_sello_vista, 
                alto_sello_vista, 
                sellos_puestos_hoja
            )
            sellos_puestos_hoja.append((x_px, y_px, ancho_sello_vista, alto_sello_vista))

            nx0 = x_px / pixmap.w
            ny0 = y_px / pixmap.h
            nx1 = (x_px + ancho_sello_vista) / pixmap.w
            ny1 = (y_px + alto_sello_vista) / pixmap.h

            view_rect = fitz.Rect(
                nx0 * pagina.rect.width,
                ny0 * pagina.rect.height,
                nx1 * pagina.rect.width,
                ny1 * pagina.rect.height
            )

            mat = pagina.derotation_matrix
            rect_sello = view_rect * mat

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial(pagina, rect_sello, item_sello, texto_fecha, rot)
            else:
                ruta_img = libreria_archivos[item_sello]
                pagina.insert_image(rect_sello, filename=ruta_img, rotate=rot)

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
st.caption("Detección inteligente de Código y Proyecto por posición geométrica en cajetín")

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
    
    # -------------------------------------------------------------
    # Selector de fecha interactivo con calendario (st.date_input)
    # -------------------------------------------------------------
    fecha_obj = st.date_input(
        "2. Fecha de Sellado:",
        value=datetime.date.today(),
        format="DD/MM/YYYY"
    )
    # Formateamos la fecha seleccionada a cadena string DD/MM/YYYY
    texto_fecha = fecha_obj.strftime("%d/%m/%Y")

libreria_archivos = obtener_libreria_sellos()
opciones = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

with col_der:
    sellos_seleccionados = st.multiselect("3. Selecciona Sellos/Firmas:", options=opciones, default=[opciones[0]])

st.divider()

if archivo_pdf and sellos_seleccionados:
    if st.button("🚀 Estampar Documento", use_container_width=True):
        with st.spinner("Estampando y leyendo datos de cajetines..."):
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
            st.subheader("📋 Resumen de Planos Detectados")
            st.dataframe(resumen, use_container_width=True)