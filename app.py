import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
import datetime
import io
import openpyxl

# --- CONFIGURACIÓN DE CARPETAS Y PLANTILLA ---
CARPETA_SELLOS = "firmas_sellos"
PLANTILLA_EXCEL = "PLANTILLA_GR.xlsx"

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

# --- MAPEO DE COPIAS POR ÁREA ---
COPIAS_POR_AREA = {
    "SUPERVISION": 2,
    "CALIDAD": 3,
    "TOPOGRAFIA": 2,
    "PRODUCCION": 2
}

def obtener_texto_celda_b6(area):
    """
    Formatea el título para la celda B6 según el área:
    SUPERVISION -> SUPERVISION
    CALIDAD -> CALIDAD OSP
    """
    area_norm = area.upper().strip()
    if area_norm == "SUPERVISION":
        return "SUPERVISION"
    return f"{area_norm} OSP"

def extraer_numero_revision(codigo_plano):
    """
    Extrae únicamente el número de la revisión (ej. 'ASSA-ALC055-05-10-001-R4' -> 4)
    """
    match = re.search(r'-(?:R|REV)(\d+)$', codigo_plano, re.IGNORECASE)
    if match:
        return int(match.group(1))
    
    # Fallback: buscar cualquier dígito al final tras un guion
    match_alt = re.search(r'-(\d+)$', codigo_plano)
    if match_alt:
        return int(match_alt.group(1))
        
    return ""

# --- GENERADOR DE EXCEL POR ÁREA A PARTIR DE PLANTILLA ---
def generar_excel_por_area(resumen_planos, fecha_texto, secuencia_gr, area, plantilla_path=PLANTILLA_EXCEL):
    if not os.path.exists(plantilla_path):
        st.error(f"⚠️ No se encontró la plantilla `{plantilla_path}` en la carpeta del proyecto.")
        return None

    wb = openpyxl.load_workbook(plantilla_path)
    
    ws = wb["osp"] if "osp" in wb.sheetnames else wb.active

    # 1. Celda B6: Área (ej. 'TOPOGRAFÍA OSP' o 'SUPERVISION')
    ws["B6"] = obtener_texto_celda_b6(area)

    # 2. Celda I5: Código / Secuencia Correlativo de Guía de Remisión
    ws["I5"] = secuencia_gr

    # 3. Celda J5: Fecha
    ws["J5"] = fecha_texto

    # 4. Llenar filas desde la 10
    fila_inicio = 10
    num_copias = COPIAS_POR_AREA.get(area.upper(), 2)

    for idx, plano in enumerate(resumen_planos):
        fila = fila_inicio + idx
        cod = plano["Código de Plano"]
        desc = plano["Título / Descripción"]
        rev_num = extraer_numero_revision(cod)

        ws[f"B{fila}"] = cod        # Código de Documento
        ws[f"D{fila}"] = desc       # Descripción
        ws[f"H{fila}"] = rev_num    # RV. (sin la R)
        ws[f"I{fila}"] = num_copias # N° de Copias entregadas
        ws[f"J{fila}"] = 5          # Propósito de Emisión (Siempre 5)

    output_stream = io.BytesIO()
    wb.save(output_stream)
    output_stream.seek(0)
    return output_stream.getvalue()

# --- DETECCIÓN INTELIGENTE DE CÓDIGO Y TÍTULO POR ETIQUETAS ---
def extraer_datos_inteligentes_cajetin(pagina):
    rot = pagina.rotation
    texto_completo = pagina.get_text("text")

    # Búsqueda del código de plano
    patron_codigo = r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){3,}(?:-R\d+|-REV\d+)?\b'
    matches = re.findall(patron_codigo, texto_completo)

    codigo_plano = "No detectado"
    if matches:
        falsos_positivos = ["ESCALA", "FECHA", "PROYECTO", "TUBOS", "INDICADA"]
        candidatos_validos = [m for m in matches if not any(fp in m for fp in falsos_positivos)]
        if candidatos_validos:
            codigo_plano = candidatos_validos[0]

    # Dimensiones
    rect_pag = pagina.rect
    if rot in [90, 270]:
        ancho_v, alto_v = rect_pag.height, rect_pag.width
    else:
        ancho_v, alto_v = rect_pag.width, rect_pag.height

    words = pagina.get_text("words")
    mat_directa = pagina.rotation_matrix

    rect_proyecto_vista = None
    for w in words:
        texto_word = w[4].upper().strip()
        if "PROYECTO" in texto_word:
            r_nativo = fitz.Rect(w[0], w[1], w[2], w[3])
            r_vista = r_nativo * mat_directa
            if r_vista.y0 > (alto_v * 0.50):
                rect_proyecto_vista = r_vista
                break

    titulo_plano = "No detectado"

    if rect_proyecto_vista:
        view_box_desc = fitz.Rect(
            rect_proyecto_vista.x0 - 40,
            rect_proyecto_vista.y1 + 2,
            rect_proyecto_vista.x0 + 380,
            rect_proyecto_vista.y1 + 110
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
    pad, paso = 20, 25

    candidatos = []
    for x in range(ancho_img - ancho_sello - 30, 30, -paso):
        for y in range(alto_img - alto_sello - 30, 30, -paso):
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
            ancho_ref_vista, alto_ref_vista = rect.height, rect.width
        else:
            ancho_ref_vista, alto_ref_vista = rect.width, rect.height

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

        pagina.insert_text(pt, texto, fontsize=fontsize, fontname=fontname, color=color, rotate=rotacion)

    dibujar_linea_texto("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_linea_texto(linea_2, 0.60, 0.22, "hebo")
    dibujar_linea_texto(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

# --- PROCESAMIENTO PRINCIPAL DEL PDF ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]
        rot = pagina.rotation

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
                imagen_gris, ancho_sello_vista, alto_sello_vista, sellos_puestos_hoja
            )
            sellos_puestos_hoja.append((x_px, y_px, ancho_sello_vista, alto_sello_vista))

            nx0, ny0 = x_px / pixmap.w, y_px / pixmap.h
            nx1, ny1 = (x_px + ancho_sello_vista) / pixmap.w, (y_px + alto_sello_vista) / pixmap.h

            view_rect = fitz.Rect(
                nx0 * pagina.rect.width, ny0 * pagina.rect.height,
                nx1 * pagina.rect.width, ny1 * pagina.rect.height
            )

            rect_sello = view_rect * pagina.derotation_matrix

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
st.set_page_config(page_title="Estampador y Generador de Guías OSP", page_icon="📐", layout="wide")

st.title("📐 ESTAMPADOR Y GENERADOR DE GUÍAS DE REMISIÓN")
st.caption("Procesamiento automático de planos A3 y generación de Excel por Área")

# Sidebar para cargar nuevas firmas
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

col1, col2, col3 = st.columns([1.2, 1, 1])

with col1:
    archivo_pdf = st.file_uploader("1. Selecciona tu PDF consolidado:", type=["pdf"])
    secuencia_gr = st.text_input("2. Secuencia Guía de Remisión (Celda I5):", value="8418-OSP-SG-2026")

with col2:
    fecha_obj = st.date_input("3. Fecha de Sellado (Celda J5):", value=datetime.date.today(), format="DD/MM/YYYY")
    texto_fecha = fecha_obj.strftime("%d/%m/%Y")
    
    areas_opciones = ["SUPERVISION", "CALIDAD", "TOPOGRAFIA", "PRODUCCION"]
    areas_seleccionadas = st.multiselect(
        "4. Selecciona Áreas para Generar Guías:",
        options=areas_opciones,
        default=areas_opciones
    )

libreria_archivos = obtener_libreria_sellos()
opciones_sellos = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

with col3:
    sellos_seleccionados = st.multiselect(
        "5. Selecciona Sellos/Firmas:",
        options=opciones_sellos,
        default=[opciones_sellos[0]]
    )

st.divider()

if archivo_pdf and sellos_seleccionados and areas_seleccionadas:
    if st.button("🚀 Estampar PDF y Generar Guías de Remisión", use_container_width=True):
        with st.spinner("Procesando planos, aplicando sellos y generando guías por área..."):
            pdf_res, resumen = procesar_pdf(
                archivo_pdf.read(), 
                sellos_seleccionados, 
                libreria_archivos, 
                texto_fecha
            )
            
            # Obtener revisión principal del primer plano para nombrado
            rev_tag = "REV"
            if resumen and resumen[0]["Código de Plano"] != "No detectado":
                rev_num = extraer_numero_revision(resumen[0]["Código de Plano"])
                if rev_num:
                    rev_tag = f"R{rev_num}"

            # Generar los Excels por cada área seleccionada
            excels_generados = {}
            for area in areas_seleccionadas:
                excel_bytes = generar_excel_por_area(
                    resumen_planos=resumen,
                    fecha_texto=texto_fecha,
                    secuencia_gr=secuencia_gr,
                    area=area
                )
                if excel_bytes:
                    nombre_excel = f"{secuencia_gr}_{rev_tag}_{area}.xlsx"
                    excels_generados[area] = {
                        "bytes": excel_bytes,
                        "nombre": nombre_excel
                    }

            st.session_state['pdf_res'] = pdf_res
            st.session_state['resumen'] = resumen
            st.session_state['excels_generados'] = excels_generados
            st.session_state['pdf_nombre'] = f"{secuencia_gr}_{rev_tag}_SELLADO.pdf"

if 'resumen' in st.session_state:
    st.success("¡Proceso completado con éxito!")
    
    st.subheader("📥 Descargas de Archivos Generados")
    
    # Botón PDF Sellado
    st.download_button(
        "📄 Descargar PDF Sellado Consolidado", 
        data=st.session_state['pdf_res'], 
        file_name=st.session_state['pdf_nombre'], 
        mime="application/pdf", 
        use_container_width=True
    )

    st.markdown("#### 📊 Guías de Remisión en Excel por Área:")
    cols_excels = st.columns(len(st.session_state['excels_generados']))
    
    for idx, (area, data_excel) in enumerate(st.session_state['excels_generados'].items()):
        with cols_excels[idx]:
            st.download_button(
                label=f"🟢 Excel {area}",
                data=data_excel["bytes"],
                file_name=data_excel["nombre"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

    st.divider()
    st.subheader("📋 Resumen de Planos Detectados")
    st.dataframe(st.session_state['resumen'], use_container_width=True)