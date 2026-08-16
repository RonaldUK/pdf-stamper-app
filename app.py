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
import subprocess
from openpyxl.cell.rich_text import TextBlock, CellRichText
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.drawing.graphic import GroupShape

# --- CONFIGURACIÓN DE MEDIDAS EXACTAS EN PAPEL (CENTÍMETROS REALES) ---
CM_TO_PT = 28.34645669

ANCHO_SELLO_CM = 7.1  # 7.1 cm exactos en papel
ALTO_SELLO_CM = 2.6   # 2.6 cm exactos en papel

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

COPIAS_POR_AREA = {
    "SUPERVISION": 2,
    "CALIDAD": 3,
    "TOPOGRAFIA": 2,
    "PRODUCCION": 2
}

def obtener_texto_celda_b6(area):
    area_norm = area.upper().strip()
    if area_norm == "SUPERVISION":
        return "SUPERVISION"
    return f"{area_norm} OSP"

def extraer_numero_revision(codigo_plano):
    match = re.search(r'-(?:R|REV)(\d+)$', codigo_plano, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match_alt = re.search(r'-(\d+)$', codigo_plano)
    if match_alt:
        return int(match_alt.group(1))
    return ""

def calcular_siguiente_correlativo(secuencia_base, offset):
    match = re.match(r'^(\d+)(-.+)$', secuencia_base.strip())
    if match:
        num_actual = int(match.group(1))
        resto = match.group(2)
        nuevo_num = str(num_actual + offset)
        return nuevo_num, resto
    return secuencia_base, ""

# --- LÍNEA DIAGONAL DE CIERRE EN EXCEL ---
def agregar_linea_diagonal_excel(ws, fila_inicio_linea, fila_fin_linea=44):
    if fila_inicio_linea >= fila_fin_linea:
        return

    try:
        marker_from = AnchorMarker(col=1, colOff=0, row=fila_inicio_linea - 1, rowOff=0)
        marker_to = AnchorMarker(col=10, colOff=0, row=fila_fin_linea, rowOff=0)
        anchor = TwoCellAnchor(_from=marker_from, to=marker_to)
        shape = GroupShape()
        anchor.sp = shape
        ws.add_drawing(anchor)
    except Exception:
        pass

# --- EXPORTAR HOJA EXCEL A PDF ---
def convertir_excel_a_pdf(excel_bytes):
    tmp_excel_path = None
    expected_pdf_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_excel:
            tmp_excel.write(excel_bytes)
            tmp_excel_path = tmp_excel.name

        out_dir = tempfile.gettempdir()
        
        cmd = ["libreoffice", "--headless", "--convert-to", "pdf", tmp_excel_path, "--outdir", out_dir]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        expected_pdf_path = os.path.splitext(tmp_excel_path)[0] + ".pdf"
        
        if os.path.exists(expected_pdf_path):
            with open(expected_pdf_path, "rb") as f:
                pdf_bytes = f.read()
            os.remove(tmp_excel_path)
            os.remove(expected_pdf_path)
            return pdf_bytes, None
        else:
            return None, "No se encontró el archivo PDF generado por LibreOffice."
    except Exception as e:
        if tmp_excel_path and os.path.exists(tmp_excel_path):
            os.remove(tmp_excel_path)
        if expected_pdf_path and os.path.exists(expected_pdf_path):
            os.remove(expected_pdf_path)
        return None, f"Error en conversión: {str(e)}. Verifica que LibreOffice esté instalado."

def unificar_pdfs(lista_pdf_bytes):
    pdf_final = fitz.open()
    for b in lista_pdf_bytes:
        if b:
            doc_temp = fitz.open(stream=b, filetype="pdf")
            pdf_final.insert_pdf(doc_temp)
            doc_temp.close()

    buffer = io.BytesIO()
    pdf_final.save(buffer)
    pdf_final.close()
    buffer.seek(0)
    return buffer.getvalue()

# --- GENERADOR SOBRE LA HOJA OSP DE LA PLANTILLA ---
def generar_excel_por_area(resumen_planos, fecha_texto, secuencia_base, area, offset_correlativo, plantilla_path=PLANTILLA_EXCEL):
    if not os.path.exists(plantilla_path):
        st.error(f"⚠️ No se encontró la plantilla `{plantilla_path}` en la carpeta del proyecto.")
        return None, None

    wb = openpyxl.load_workbook(plantilla_path)
    
    if "osp" in wb.sheetnames:
        ws = wb["osp"]
        wb.active = ws
        for sheet in wb.sheetnames:
            if sheet != "osp":
                del wb[sheet]
    else:
        ws = wb.active

    ws["B6"] = obtener_texto_celda_b6(area)

    num_str, resto_str = calcular_siguiente_correlativo(secuencia_base, offset_correlativo)
    secuencia_completa = f"{num_str}{resto_str}"

    font_bold = InlineFont(b=True, rFont="Calibri", sz=11)
    font_normal = InlineFont(b=False, rFont="Calibri", sz=11)
    
    rich_i5 = CellRichText([
        TextBlock(font_bold, num_str),
        TextBlock(font_normal, resto_str)
    ])
    ws["I5"] = rich_i5
    ws["J5"] = fecha_texto

    fila_inicio = 10
    num_copias = COPIAS_POR_AREA.get(area.upper(), 2)
    cant_items = len(resumen_planos)

    for idx, plano in enumerate(resumen_planos):
        fila = fila_inicio + idx
        cod = plano["Código de Plano"]
        desc = plano["Título / Descripción"]
        rev_num = extraer_numero_revision(cod)

        ws[f"B{fila}"] = cod
        ws[f"D{fila}"] = desc
        ws[f"H{fila}"] = rev_num
        ws[f"I{fila}"] = num_copias
        ws[f"J{fila}"] = 5

    ultima_fila_usada = fila_inicio + cant_items - 1
    fila_diagonal_inicio = ultima_fila_usada + 1
    
    agregar_linea_diagonal_excel(ws, fila_diagonal_inicio, fila_fin_linea=44)

    output_stream = io.BytesIO()
    wb.save(output_stream)
    output_stream.seek(0)
    return output_stream.getvalue(), secuencia_completa

# --- DETECCIÓN DIRECTA Y FOCALIZADA EN EL RÓTULO DEL CAJETÍN ---
def extraer_datos_inteligentes_cajetin(pagina):
    rot = pagina.rotation
    rect_pag = pagina.rect
    
    if rot in [90, 270]:
        ancho_v, alto_v = rect_pag.height, rect_pag.width
    else:
        ancho_v, alto_v = rect_pag.width, rect_pag.height

    mat_directa = pagina.rotation_matrix
    mat_inversa = pagina.derotation_matrix

    # Definimos la ROI del Rótulo (esquina inferior derecha: 40% ancho x 20% alto)
    view_roi_rotulo = fitz.Rect(ancho_v * 0.60, alto_v * 0.80, ancho_v, alto_v)
    nat_roi_rotulo = view_roi_rotulo * mat_inversa

    words = pagina.get_text("words", clip=nat_roi_rotulo)

    # 1. CAPTURA DIRECTA DEL CÓDIGO
    codigo_plano = "No detectado"
    rect_codigo_label = None

    for w in words:
        texto_w = w[4].upper().strip()
        if "CODIGO" in texto_w or "CÓDIGO" in texto_w:
            r_nativo = fitz.Rect(w[0], w[1], w[2], w[3])
            rect_codigo_label = r_nativo * mat_directa
            break

    if rect_codigo_label:
        # Bounding box del recuadro del código (a la derecha y dentro de la celda de CODIGO)
        view_box_codigo = fitz.Rect(
            rect_codigo_label.x0,
            rect_codigo_label.y0 - 2,
            rect_codigo_label.x0 + 300,
            rect_codigo_label.y1 + 25
        )
        texto_cod_celda = pagina.get_text("text", clip=view_box_codigo * mat_inversa).strip()
        
        # Extraer el valor ignorando la etiqueta "CODIGO:"
        match_cod = re.search(r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}(?:-R\d+|-REV\d+)?\b', texto_cod_celda)
        if match_cod:
            codigo_plano = match_cod.group(0)
        else:
            txt_limpio = re.sub(r'(?i)CODIGO\s*:\s*', '', texto_cod_celda).strip()
            if txt_limpio:
                codigo_plano = txt_limpio.split('\n')[0].strip()

    # Fallback para el código dentro de la ROI
    if codigo_plano == "No detectado":
        texto_roi_completo = pagina.get_text("text", clip=nat_roi_rotulo)
        match_fall = re.search(r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}(?:-R\d+|-REV\d+)?\b', texto_roi_completo)
        if match_fall:
            codigo_plano = match_fall.group(0)

    # 2. CAPTURA DIRECTA DE LA DESCRIPCIÓN / TÍTULO
    titulo_plano = "No detectado"
    rect_proyecto_label = None

    for w in words:
        texto_w = w[4].upper().strip()
        if "PROYECTO" in texto_w:
            r_nativo = fitz.Rect(w[0], w[1], w[2], w[3])
            rect_proyecto_label = r_nativo * mat_directa
            break

    if rect_proyecto_label:
        # Bounding box centrado en la celda del título (a la izquierda del recuadro de código/escala)
        view_box_desc = fitz.Rect(
            rect_proyecto_label.x0 - 5,
            rect_proyecto_label.y1 + 2,
            rect_proyecto_label.x0 + 380,
            rect_proyecto_label.y1 + 100
        )
        texto_desc_celda = pagina.get_text("text", clip=view_box_desc * mat_inversa)
        lineas_raw = [l.strip() for l in texto_desc_celda.split('\n') if l.strip()]

        lineas_validas = []
        for line in lineas_raw:
            # Descartar frases entre comillas completas (ej. '"ME - N° 154"')
            if re.match(r'^\s*["“"”\'].*?["“"”\']\s*$', line):
                continue

            line_up = line.upper()
            palabras_filtro = ["PROYECTO", "ESCALA", "FECHA", "CODIGO", "CÓDIGO", "INDICADA"]
            if any(p in line_up for p in palabras_filtro) or line == codigo_plano:
                continue

            # Limpiar comillas residuales y guiones extremos
            line_limpia = re.sub(r'["“"”]', '', line).strip()
            line_limpia = re.sub(r'^\s*[\-\–\—\:]+\s*', '', line_limpia)
            
            if line_limpia and len(line_limpia) > 2:
                lineas_validas.append(line_limpia)

        if lineas_validas:
            titulo_plano = " - ".join(lineas_validas)

    # Fallback para el título dentro de la ROI
    if titulo_plano == "No detectado":
        texto_roi_completo = pagina.get_text("text", clip=nat_roi_rotulo)
        lineas_fall = [l.strip() for l in texto_roi_completo.split('\n') if l.strip()]
        
        lineas_validas = []
        for line in lineas_fall:
            if re.match(r'^\s*["“"”\'].*?["“"”\']\s*$', line):
                continue
            line_up = line.upper()
            if any(p in line_up for p in ["PROYECTO", "ESCALA", "FECHA", "CODIGO", "INDICADA", "2026", "JUNIO", "MTC"]):
                continue
            if line == codigo_plano:
                continue
            line_limpia = re.sub(r'["“"”]', '', line).strip()
            if line_limpia and len(line_limpia) > 3:
                lineas_validas.append(line_limpia)

        if lineas_validas:
            titulo_plano = " - ".join(lineas_validas[:2])

    return codigo_plano, titulo_plano

# --- ALGORITMO DE BÚSQUEDA Y PUNTUACIÓN DE ESPACIO LIBRE (CON PASO CONFIGURABLE) ---
def buscar_posicion_espacio_libre(imagen_gris, ancho_sello, alto_sello, sellos_ya_puestos, paso=10):
    _, binaria = cv2.threshold(imagen_gris, 230, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello * alto_sello
    pad = 20

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
            porcentaje_libre = 1.0 - (pixeles_ocupados / float(area_sello))

            score_libre = porcentaje_libre * 100.0
            factor_x = x / float(ancho_img)
            factor_y = y / float(alto_img)
            bonif_posicion = (factor_x * 5.0) + (factor_y * 5.0)

            score_total = score_libre + bonif_posicion
            candidatos.append((score_total, porcentaje_libre, x, y))

    if not candidatos:
        return 50, 50

    candidatos.sort(key=lambda item: item[0], reverse=True)
    mejor_score, mejor_libre, mejor_x, mejor_y = candidatos[0]

    return mejor_x, mejor_y

# --- ESTAMPADO DE SELLO DINÁMICO DESDE PNG (CC / CI) ---
def agregar_sello_png_dinamico(pagina, view_rect, rect_nativo, nombre_png, color_rgb, texto_fecha, rotacion):
    ruta_png = None
    posibles_rutas = [
        nombre_png,
        os.path.join(CARPETA_SELLOS, nombre_png)
    ]
    
    for r in posibles_rutas:
        if os.path.exists(r):
            ruta_png = r
            break

    if not ruta_png:
        return False, f"⚠️ No se encontró la imagen '{nombre_png}' en la carpeta raíz o en '{CARPETA_SELLOS}'."

    pagina.insert_image(rect_nativo, filename=ruta_png, rotate=rotacion)

    partes = re.split(r'[/.-]', texto_fecha.strip())
    if len(partes) >= 3:
        dia_txt = partes[0].zfill(2)
        mes_txt = partes[1].zfill(2)
        anio_txt = partes[2][-2:].zfill(2)
    else:
        dia_txt, mes_txt, anio_txt = "", "", ""

    fontname = "hebo"          # Helvetica-Bold
    fontsize = 0.50 * CM_TO_PT  # ~14.1pt

    y_vista = view_rect.y0 + (view_rect.height * 0.81)

    posiciones = [
        (dia_txt, 0.38),   # Día
        (mes_txt, 0.62),   # Mes
        (anio_txt, 0.89)   # Año
    ]

    for txt, rel_x in posiciones:
        if not txt:
            continue
        ancho_txt = fitz.get_text_length(txt, fontname=fontname, fontsize=fontsize)
        x_vista = view_rect.x0 + (view_rect.width * rel_x) - (ancho_txt / 2.0)

        pt_vista = fitz.Point(x_vista, y_vista)
        pt_nativo = pt_vista * pagina.derotation_matrix

        # Doble trazo reforzado para mayor grosor
        pagina.insert_text(pt_nativo, txt, fontsize=fontsize, fontname=fontname, color=color_rgb, rotate=rotacion)
        pt_nativo_offset = fitz.Point(pt_nativo.x + 0.3, pt_nativo.y)
        pagina.insert_text(pt_nativo_offset, txt, fontsize=fontsize, fontname=fontname, color=color_rgb, rotate=rotacion)

    return True, None

def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha, paso=10):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []
    alertas_sellos = []

    ancho_sello_pt = ANCHO_SELLO_CM * CM_TO_PT
    alto_sello_pt = ALTO_SELLO_CM * CM_TO_PT

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

        ancho_sello_px = int(ANCHO_SELLO_CM * 39.37007874)
        alto_sello_px = int(ALTO_SELLO_CM * 39.37007874)
        sellos_puestos_hoja = []

        for item_sello in lista_sellos_elegidos:
            x_px, y_px = buscar_posicion_espacio_libre(
                imagen_gris, ancho_sello_px, alto_sello_px, sellos_puestos_hoja, paso=paso
            )

            x0_pt_view = x_px * 0.72
            y0_pt_view = y_px * 0.72

            view_rect = fitz.Rect(x0_pt_view, y0_pt_view, x0_pt_view + ancho_sello_pt, y0_pt_view + alto_sello_pt)
            rect_nativo = view_rect * pagina.derotation_matrix

            exito = True
            msg_err = None

            if item_sello == "CC - Copia Controlada (Rojo)":
                exito, msg_err = agregar_sello_png_dinamico(
                    pagina, view_rect, rect_nativo, 
                    "cc_sin_fondo.png", (0.80, 0.0, 0.0), 
                    texto_fecha, rot
                )
            elif item_sello == "CI - Copia Informativa (Azul)":
                exito, msg_err = agregar_sello_png_dinamico(
                    pagina, view_rect, rect_nativo, 
                    "ci_sin_fondo.png", (0.0, 0.20, 0.65), 
                    texto_fecha, rot
                )
            else:
                ruta_img = libreria_archivos.get(item_sello)
                if ruta_img and os.path.exists(ruta_img):
                    pagina.insert_image(rect_nativo, filename=ruta_img, rotate=rot)
                else:
                    exito = False
                    msg_err = f"⚠️ No se encontró la imagen del sello '{item_sello}'."

            if exito:
                sellos_puestos_hoja.append((x_px, y_px, ancho_sello_px, alto_sello_px))
            else:
                if msg_err and msg_err not in alertas_sellos:
                    alertas_sellos.append(msg_err)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos, alertas_sellos

# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Estampador OSP & Guías de Remisión", page_icon="📐", layout="wide")

st.title("📐 ESTAMPADOR Y GENERADOR DE GUÍAS DE REMISIÓN")
st.caption("Procesamiento automático conservando la plantilla Excel exacta")

# Sidebar
st.sidebar.header("⚙️ Ajustes de Búsqueda")
paso_evaluacion = st.sidebar.slider(
    "🎯 Precisión de Escaneo (Paso en px):",
    min_value=5,
    max_value=30,
    value=10,
    step=5,
    help="Valores menores (ej. 5 o 10 px) ajustan el sello con precisión milimétrica sobre reglas o cajetines."
)

st.sidebar.header("📁 Cargar Nuevas Firmas")
with st.sidebar.expander("➕ Subir Imagen a Base de Datos", expanded=False):
    nuevo_nombre = st.sidebar.text_input("Nombre de la firma/sello:")
    archivo_nuevo = st.sidebar.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])
    
    if st.sidebar.button("💾 Guardar Sello"):
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
    secuencia_gr = st.text_input("2. Secuencia Base (ej: 8418-OSP-SG-2026):", value="8418-OSP-SG-2026")

with col2:
    fecha_obj = st.date_input("3. Fecha de Sellado (J5):", value=datetime.date.today(), format="DD/MM/YYYY")
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
    if st.button("🚀 Estampar PDF y Generar Todo", use_container_width=True):
        with st.spinner(f"Procesando planos con paso de {paso_evaluacion}px, rellenando Excel y exportando PDFs..."):
            pdf_res, resumen, alertas_sellos = procesar_pdf(
                archivo_pdf.read(), 
                sellos_seleccionados, 
                libreria_archivos, 
                texto_fecha,
                paso=paso_evaluacion
            )
            
            rev_tag = "REV"
            if resumen and resumen[0]["Código de Plano"] != "No detectado":
                rev_num = extraer_numero_revision(resumen[0]["Código de Plano"])
                if rev_num:
                    rev_tag = f"R{rev_num}"

            excels_generados = {}
            lista_pdfs_guias = []
            errores_pdf = []

            for idx, area in enumerate(areas_seleccionadas):
                excel_bytes, secuencia_inc = generar_excel_por_area(
                    resumen_planos=resumen,
                    fecha_texto=texto_fecha,
                    secuencia_base=secuencia_gr,
                    area=area,
                    offset_correlativo=idx
                )
                if excel_bytes:
                    nombre_excel = f"{secuencia_inc}_{rev_tag}_{area}.xlsx"
                    pdf_excel, error_msg = convertir_excel_a_pdf(excel_bytes)
                    
                    if pdf_excel:
                        lista_pdfs_guias.append(pdf_excel)
                    elif error_msg:
                        errores_pdf.append(f"{area}: {error_msg}")

                    excels_generados[area] = {
                        "bytes": excel_bytes,
                        "nombre": nombre_excel,
                        "secuencia": secuencia_inc,
                        "pdf_bytes": pdf_excel
                    }

            st.session_state['pdf_res'] = pdf_res
            st.session_state['resumen'] = resumen
            st.session_state['excels_generados'] = excels_generados
            st.session_state['pdf_nombre'] = f"{secuencia_gr}_{rev_tag}_PLANOS_SELLADOS.pdf"
            st.session_state['errores_pdf'] = errores_pdf
            st.session_state['alertas_sellos'] = alertas_sellos

            if lista_pdfs_guias:
                st.session_state['pdf_guias_unificado'] = unificar_pdfs(lista_pdfs_guias)
            else:
                st.session_state['pdf_guias_unificado'] = None

if 'resumen' in st.session_state:
    st.success("¡Documentos y Guías de Remisión procesados correctamente!")
    
    if st.session_state.get('alertas_sellos'):
        for alert in st.session_state['alertas_sellos']:
            st.warning(alert)

    if st.session_state.get('errores_pdf'):
        st.warning("⚠️ Ocurrió una observación al exportar los PDFs de los Excels:")
        for err in st.session_state['errores_pdf']:
            st.caption(f"- {err}")

    st.subheader("📥 Descargas Generales")
    
    col_dl1, col_dl2 = st.columns(2)
    
    with col_dl1:
        st.download_button(
            "📄 Descargar PDF Planos Sellados", 
            data=st.session_state['pdf_res'], 
            file_name=st.session_state['pdf_nombre'], 
            mime="application/pdf", 
            use_container_width=True
        )

    with col_dl2:
        if st.session_state.get('pdf_guias_unificado'):
            st.download_button(
                "📑 Descargar PDF Consolidado de Guías (Todas las Áreas)", 
                data=st.session_state['pdf_guias_unificado'], 
                file_name=f"GUIAS_REMISION_CONSOLIDADAS_{datetime.datetime.now().strftime('%Y%m%d')}.pdf", 
                mime="application/pdf", 
                use_container_width=True
            )

    st.divider()
    st.markdown("#### 📊 Archivos Excel y PDF de Guías por Área:")
    cols_excels = st.columns(len(st.session_state['excels_generados']))
    
    for idx, (area, data_excel) in enumerate(st.session_state['excels_generados'].items()):
        with cols_excels[idx]:
            st.download_button(
                label=f"🟢 Excel: {data_excel['secuencia']} ({area})",
                data=data_excel["bytes"],
                file_name=data_excel["nombre"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"excel_{area}"
            )
            if data_excel.get("pdf_bytes"):
                st.download_button(
                    label=f"🔴 PDF: {data_excel['secuencia']} ({area})",
                    data=data_excel["pdf_bytes"],
                    file_name=f"{data_excel['secuencia']}_{area}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"pdf_{area}"
                )

    st.divider()
    st.subheader("📋 Resumen de Planos Detectados")
    st.dataframe(st.session_state['resumen'], use_container_width=True)