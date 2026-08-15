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
# En especificación PDF: 1 pulgada = 2.54 cm = 72 pt -> 1 cm = 28.34645669 pt
CM_TO_PT = 28.34645669

ANCHO_SELLO_CM = 7.1  # 7.1 cm exactos en papel
ALTO_SELLO_CM = 2.6   # 2.6 cm exactos en papel

# --- CONFIGURACIÓN DE CARPETAS Y PLANTILLA ---
CARPETA_SELLOS = "firmas_sellos"
PLANTILLA_EXCEL = "PLANTILLA_GR.xlsx"

os.makedirs(CARPETA_SELLOS, exist_ok=True)

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

# --- DETECCIÓN DE CÓDIGO Y TÍTULO ---
def extraer_datos_inteligentes_cajetin(pagina):
    rot = pagina.rotation
    texto_completo = pagina.get_text("text")

    patrones_codigo = [
        r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}(?:-R\d+|-REV\d+)?\b',
        r'\b\d+-[A-Z0-9]+-[0-9-]+(?:-R\d+|-REV\d+)?\b'
    ]

    codigo_plano = "No detectado"
    for pat in patrones_codigo:
        matches = re.findall(pat, texto_completo)
        if matches:
            falsos_positivos = ["ESCALA", "FECHA", "PROYECTO", "TUBOS", "INDICADA", "DIBUJO", "PLANO", "REVISION"]
            candidatos = [m.strip() for m in matches if not any(fp in m.upper() for fp in falsos_positivos) and len(m.strip()) > 6]
            if candidatos:
                codigo_plano = candidatos[0]
                break

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

# --- SELLO VECTORIAL OPTIMIZADO (7.1 cm x 2.6 cm) ---
def agregar_sello_vectorial(pagina, view_rect, rect_nativo, tipo_sello, texto_fecha, rotacion):
    if "CC" in tipo_sello:
        color = (0.85, 0.0, 0.0)  # Rojo vivo del sello
        linea_2 = "COPIA CONTROLADA"
    else:
        color = (0.0, 0.25, 0.75) # Azul
        linea_2 = "COPIA INFORMATIVA"

    shape = pagina.new_shape()
    try:
        shape.draw_rect(rect_nativo, radius=3)
    except (TypeError, ValueError):
        shape.draw_rect(rect_nativo)
        
    shape.finish(color=color, fill=None, width=1.8)
    shape.commit()

    linea_1 = "OSP INGENIERÍA"
    linea_3 = f"FECHA: {texto_fecha}"

    filas_texto = [
        (linea_1, 0.23, 0.47 * CM_TO_PT, "hebo"),
        (linea_2, 0.58, 0.67 * CM_TO_PT, "hebo"),
        (linea_3, 0.88, 0.41 * CM_TO_PT, "helv")
    ]

    for texto, prop_y, target_fontsize, fontname in filas_texto:
        ancho_box = view_rect.width
        fontsize = target_fontsize
        ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        max_ancho = ancho_box * 0.92
        if ancho_txt > max_ancho:
            fontsize = fontsize * (max_ancho / ancho_txt)
            ancho_txt = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        x_vista = view_rect.x0 + (ancho_box - ancho_txt) / 2.0
        y_vista = view_rect.y0 + (view_rect.height * prop_y)

        pt_vista = fitz.Point(x_vista, y_vista)
        pt_nativo = pt_vista * pagina.derotation_matrix

        pagina.insert_text(pt_nativo, texto, fontsize=fontsize, fontname=fontname, color=color, rotate=rotacion)

def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

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
                imagen_gris, ancho_sello_px, alto_sello_px, sellos_puestos_hoja
            )
            sellos_puestos_hoja.append((x_px, y_px, ancho_sello_px, alto_sello_px))

            x0_pt_view = x_px * 0.72
            y0_pt_view = y_px * 0.72

            view_rect = fitz.Rect(x0_pt_view, y0_pt_view, x0_pt_view + ancho_sello_pt, y0_pt_view + alto_sello_pt)
            rect_nativo = view_rect * pagina.derotation_matrix

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial(pagina, view_rect, rect_nativo, item_sello, texto_fecha, rot)
            else:
                ruta_img = libreria_archivos[item_sello]
                pagina.insert_image(rect_nativo, filename=ruta_img, rotate=rot)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos

# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Estampador OSP & Guías de Remisión", page_icon="📐", layout="wide")

st.title("📐 ESTAMPADOR Y GENERADOR DE GUÍAS DE REMISIÓN")
st.caption("Procesamiento automático conservando la plantilla Excel exacta")

# Sidebar - Cargar Nuevas Firmas
st.sidebar.header("📁 Cargar Nuevas Firmas")
with st.sidebar.expander("➕ Subir Imagen a Base de Datos", expanded=False):
    with st.sidebar.form("form_subir_sello", clear_on_submit=True):
        nuevo_nombre = st.text_input("Nombre de la firma/sello:")
        archivo_nuevo = st.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])
        btn_guardar = st.form_submit_button("💾 Guardar Sello", use_container_width=True)

        if btn_guardar:
            if nuevo_nombre.strip() and archivo_nuevo is not None:
                ext = archivo_nuevo.name.split(".")[-1].lower()
                nombre_limpio = re.sub(r'[^a-zA-Z0-9_]', '_', nuevo_nombre.lower().strip())
                ruta_dest = os.path.join(CARPETA_SELLOS, f"{nombre_limpio}.{ext}")
                
                with open(ruta_dest, "wb") as f:
                    f.write(archivo_nuevo.getvalue())
                
                st.success(f"¡Sello '{nuevo_nombre}' guardado exitosamente!")
                st.rerun()
            else:
                st.warning("⚠️ Ingresa un nombre y selecciona una imagen antes de guardar.")

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
        with st.spinner("Procesando planos, rellenando Excel y exportando PDFs..."):
            pdf_res, resumen = procesar_pdf(
                archivo_pdf.read(), 
                sellos_seleccionados, 
                libreria_archivos, 
                texto_fecha
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

            if lista_pdfs_guias:
                st.session_state['pdf_guias_unificado'] = unificar_pdfs(lista_pdfs_guias)
            else:
                st.session_state['pdf_guias_unificado'] = None

if 'resumen' in st.session_state:
    st.success("¡Documentos y Guías de Remisión procesados correctamente!")
    
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