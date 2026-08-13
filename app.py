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
from openpyxl.cell.rich_text import TextBlock, CellRichText
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.drawing.graphic import GroupShape

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
    """
    Toma '8418-OSP-SG-2026' e incrementa 8418 + offset -> ('8419', '-OSP-SG-2026')
    """
    match = re.match(r'^(\d+)(-.+)$', secuencia_base.strip())
    if match:
        num_actual = int(match.group(1))
        resto = match.group(2)
        nuevo_num = str(num_actual + offset)
        return nuevo_num, resto
    return secuencia_base, ""

def agregar_linea_diagonal_excel(ws, fila_inicio_linea, fila_fin_linea=44):
    """
    Dibuja una línea diagonal que va desde B[fila_inicio_linea] hasta J[fila_fin_linea]
    """
    if fila_inicio_linea >= fila_fin_linea:
        return # No hay espacio libre para tachar

    # B es columna 1 (0-indexed en openpyxl marker) -> col B = 1
    # J es columna 9 -> col J = 9
    marker_from = AnchorMarker(col=1, colOff=0, row=fila_inicio_linea - 1, rowOff=0)
    marker_to = AnchorMarker(col=10, colOff=0, row=fila_fin_linea, rowOff=0)
    
    anchor = TwoCellAnchor(_from=marker_from, to=marker_to)
    linea = Line()
    # Asignar estilo de línea fina azul/gris
    linea.solidFill = "4F81BD"
    anchor.sp = linea
    
    ws.add_drawing(anchor)

# --- GENERADOR DE EXCEL POR ÁREA ---
def generar_excel_por_area(resumen_planos, fecha_texto, secuencia_base, area, offset_correlativo, plantilla_path=PLANTILLA_EXCEL):
    if not os.path.exists(plantilla_path):
        st.error(f"⚠️ No se encontró la plantilla `{plantilla_path}` en la carpeta del proyecto.")
        return None, None

    wb = openpyxl.load_workbook(plantilla_path)
    ws = wb["osp"] if "osp" in wb.sheetnames else wb.active

    # 1. Celda B6
    ws["B6"] = obtener_texto_celda_b6(area)

    # 2. Celda I5 con Correlativo Incremental y Negrita SOLO en el número
    num_str, resto_str = calcular_siguiente_correlativo(secuencia_base, offset_correlativo)
    secuencia_completa = f"{num_str}{resto_str}"

    font_bold = InlineFont(b=True, rFont="Calibri", sz=11)
    font_normal = InlineFont(b=False, rFont="Calibri", sz=11)
    
    # Formato con RichText
    rich_i5 = CellRichText([
        TextBlock(font_bold, num_str),
        TextBlock(font_normal, resto_str)
    ])
    ws["I5"] = rich_i5

    # 3. Celda J5: Fecha
    ws["J5"] = fecha_texto

    # 4. Llenar filas desde la B10
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

    # 5. Agregar línea diagonal desaprovechada desde (última_fila + 1) hasta J44
    ultima_fila_usada = fila_inicio + cant_items - 1
    fila_diagonal_inicio = ultima_fila_usada + 1
    
    try:
        agregar_linea_diagonal_excel(ws, fila_diagonal_inicio, fila_fin_linea=44)
    except Exception as e:
        pass # Si la versión de openpyxl difiere en dibujos vectoriales

    output_stream = io.BytesIO()
    wb.save(output_stream)
    output_stream.seek(0)
    return output_stream.getvalue(), secuencia_completa

# --- GENERADOR DE PDF CONSOLIDADO DE GUÍAS DE REMISIÓN ---
def generar_pdf_guias_consolidado(excels_info, resumen_planos, fecha_texto):
    """
    Crea un PDF limpio que contiene la vista de cada Guía de Remisión generada
    """
    doc_pdf = fitz.open()
    
    for item in excels_info:
        area = item["area"]
        secuencia = item["secuencia"]
        copias = COPIAS_POR_AREA.get(area.upper(), 2)

        # Crear página A4 vertical
        page = doc_pdf.new_page(width=595, height=842)
        
        # Dibujar Marco del Documento
        page.draw_rect(fitz.Rect(30, 30, 565, 812), color=(0, 0, 0), width=1)
        
        # Encabezados
        page.insert_text(fitz.Point(40, 55), "GUIA DE REMISIÓN DE DOCUMENTOS", fontsize=14, fontname="hebo")
        page.insert_text(fitz.Point(380, 50), f"Código Correlativo: {secuencia}", fontsize=10, fontname="hebo")
        page.insert_text(fitz.Point(380, 65), f"Fecha: {fecha_texto}", fontsize=10, fontname="helv")
        
        page.insert_text(fitz.Point(40, 85), f"ÁREA: {obtener_texto_celda_b6(area)}", fontsize=11, fontname="hebo")
        page.draw_line(fitz.Point(30, 95), fitz.Point(565, 95), color=(0,0,0), width=1)

        # Encabezado Tabla
        page.insert_text(fitz.Point(40, 110), "Código de Documento", fontsize=9, fontname="hebo")
        page.insert_text(fitz.Point(200, 110), "Descripción", fontsize=9, fontname="hebo")
        page.insert_text(fitz.Point(430, 110), "RV.", fontsize=9, fontname="hebo")
        page.insert_text(fitz.Point(465, 110), "Copias", fontsize=9, fontname="hebo")
        page.insert_text(fitz.Point(515, 110), "Propósito", fontsize=9, fontname="hebo")
        
        page.draw_line(fitz.Point(30, 118), fitz.Point(565, 118), color=(0,0,0), width=1)

        y = 132
        for plano in resumen_planos:
            cod = plano["Código de Plano"]
            desc = plano["Título / Descripción"][:45]
            rev = str(extraer_numero_revision(cod))

            page.insert_text(fitz.Point(40, y), cod, fontsize=8, fontname="helv")
            page.insert_text(fitz.Point(200, y), desc, fontsize=8, fontname="helv")
            page.insert_text(fitz.Point(435, y), rev, fontsize=8, fontname="helv")
            page.insert_text(fitz.Point(475, y), str(copias), fontsize=8, fontname="helv")
            page.insert_text(fitz.Point(530, y), "5", fontsize=8, fontname="helv")
            y += 16
            
            if y > 700:
                break

        # Dibujar diagonal de cierre si queda espacio
        if y < 700:
            page.draw_line(fitz.Point(40, y + 5), fitz.Point(550, 750), color=(0.4, 0.4, 0.8), width=1)

    pdf_buffer = io.BytesIO()
    doc_pdf.save(pdf_buffer)
    doc_pdf.close()
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

# --- DETECCIÓN INTELIGENTE DE CAJETÍN Y SELLADO PDF ---
def extraer_datos_inteligentes_cajetin(pagina):
    rot = pagina.rotation
    texto_completo = pagina.get_text("text")

    patron_codigo = r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){3,}(?:-R\d+|-REV\d+)?\b'
    matches = re.findall(patron_codigo, texto_completo)

    codigo_plano = "No detectado"
    if matches:
        falsos_positivos = ["ESCALA", "FECHA", "PROYECTO", "TUBOS", "INDICADA"]
        candidatos_validos = [m for m in matches if not any(fp in m for fp in falsos_positivos)]
        if candidatos_validos:
            codigo_plano = candidatos_validos[0]

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
st.set_page_config(page_title="Estampador OSP & Guías de Remisión", page_icon="📐", layout="wide")

st.title("📐 ESTAMPADOR Y GENERADOR DE GUÍAS DE REMISIÓN")
st.caption("Procesamiento automático de planos A3 con incremento correlativo y trazado diagonal")

# Sidebar
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
        with st.spinner("Estampando planos, calculando correlativos e insertando diagonales en guías..."):
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
            listado_guias_info = []

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
                    excels_generados[area] = {
                        "bytes": excel_bytes,
                        "nombre": nombre_excel,
                        "secuencia": secuencia_inc
                    }
                    listado_guias_info.append({
                        "area": area,
                        "secuencia": secuencia_inc
                    })

            # Generar PDF Consolidado de todas las Guías de Remisión
            pdf_guias_bytes = generar_pdf_guias_consolidado(listado_guias_info, resumen, texto_fecha)

            st.session_state['pdf_res'] = pdf_res
            st.session_state['resumen'] = resumen
            st.session_state['excels_generados'] = excels_generados
            st.session_state['pdf_guias_bytes'] = pdf_guias_bytes
            st.session_state['pdf_nombre'] = f"{secuencia_gr}_{rev_tag}_PLANOS_SELLADOS.pdf"

if 'resumen' in st.session_state:
    st.success("¡Documentos y Guías de Remisión procesados correctamente!")
    
    st.subheader("📥 Descargas Consolidadas")
    
    col_pdf1, col_pdf2 = st.columns(2)
    
    with col_pdf1:
        st.download_button(
            "📄 Descargar PDF Planos Sellados", 
            data=st.session_state['pdf_res'], 
            file_name=st.session_state['pdf_nombre'], 
            mime="application/pdf", 
            use_container_width=True
        )
        
    with col_pdf2:
        st.download_button(
            "📑 Descargar PDF Consolidado de Guías de Remisión", 
            data=st.session_state['pdf_guias_bytes'], 
            file_name=f"GUIAS_REMISION_CONSOLIDADAS_{datetime.datetime.now().strftime('%Y%m%d')}.pdf", 
            mime="application/pdf", 
            use_container_width=True
        )

    st.markdown("#### 📊 Archivos Excel Individuales por Área:")
    cols_excels = st.columns(len(st.session_state['excels_generados']))
    
    for idx, (area, data_excel) in enumerate(st.session_state['excels_generados'].items()):
        with cols_excels[idx]:
            st.download_button(
                label=f"🟢 {data_excel['secuencia']} ({area})",
                data=data_excel["bytes"],
                file_name=data_excel["nombre"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

    st.divider()
    st.subheader("📋 Resumen de Planos Detectados")
    st.dataframe(st.session_state['resumen'], use_container_width=True)