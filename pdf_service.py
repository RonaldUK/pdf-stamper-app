import os
import re
import io
import glob
import fitz  # PyMuPDF
from pypdf import PdfWriter, PdfReader

# Constantes de conversión y rutas
CM_TO_PT = 28.34645669
ANCHO_SELLO_CM = 7.1
ALTO_SELLO_CM = 2.6
CARPETA_SELLOS = "firmas_sellos"

def obtener_libreria_sellos():
    """Busca imágenes de sellos/firmas en la carpeta local."""
    extensiones = ('*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG')
    archivos = []
    if os.path.exists(CARPETA_SELLOS):
        for ext in extensiones:
            archivos.extend(glob.glob(os.path.join(CARPETA_SELLOS, ext)))
    
    libreria = {}
    for ruta in archivos:
        nombre = os.path.splitext(os.path.basename(ruta))[0].replace('_', ' ').title()
        libreria[f"Firma: {nombre}"] = ruta
    return libreria

def unificar_pdfs(lista_pdf_bytes):
    """Junta múltiples archivos PDF en un solo stream de bytes."""
    writer = PdfWriter()
    for pdf_bytes in lista_pdf_bytes:
        if pdf_bytes:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
    
    output_stream = io.BytesIO()
    writer.write(output_stream)
    output_stream.seek(0)
    return output_stream.getvalue()

def convertir_excel_a_pdf(excel_bytes):
    """
    Intenta convertir Excel a PDF. En entornos Cloud como Streamlit Cloud 
    donde no hay MS Excel ni LibreOffice, retorna None de forma segura sin romper la app.
    """
    try:
        # Reservado para entornos de escritorio Windows con MS Excel o servidores con LibreOffice
        return None, "La conversión automática de Excel a PDF requiere entorno local con Excel/LibreOffice."
    except Exception as e:
        return None, str(e)

def procesar_pdf(pdf_bytes, sellos_seleccionados, libreria_archivos, fecha_texto, paso=10):
    """Procesa el PDF agregando las estampas/sellos y generando la lista de resumen."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    resumen_planos = []
    alertas_sellos = []
    es_a4 = False

    for num_pagina in range(len(doc)):
        pagina = doc[num_pagina]
        rect = pagina.rect

        # Determina si el formato es aproximadamente A4 (en puntos)
        if (rect.width < 650 and rect.height < 900) or (rect.height < 650 and rect.width < 900):
            es_a4 = True

        texto_pagina = pagina.get_text("text")
        
        # Búsqueda de código de plano y descripción en el texto
        lineas = [l.strip() for l in texto_pagina.split('\n') if l.strip()]
        codigo_plano = "No detectado"
        descripcion_plano = "Sin descripción"

        for linea in lineas:
            if re.search(r'[\w]+-[\w]+-[\w]+-[\w]+', linea):
                codigo_plano = linea
                break

        if len(lineas) > 1:
            descripcion_plano = lineas[0] if codigo_plano != lineas[0] else lineas[1]

        resumen_planos.append({
            "Página": num_pagina + 1,
            "Código de Plano": codigo_plano,
            "Título / Descripción": descripcion_plano
        })

        # Estampado de sellos en la esquina inferior derecha
        ancho_sello_pt = ANCHO_SELLO_CM * CM_TO_PT
        alto_sello_pt = ALTO_SELLO_CM * CM_TO_PT

        x2 = rect.width - 20
        y2 = rect.height - 20
        x1 = x2 - ancho_sello_pt
        y1 = y2 - alto_sello_pt

        sello_rect = fitz.Rect(x1, y1, x2, y2)

        for sello_nombre in sellos_seleccionados:
            if sello_nombre in libreria_archivos:
                ruta_img = libreria_archivos[sello_nombre]
                if os.path.exists(ruta_img):
                    pagina.insert_image(sello_rect, filename=ruta_img)

    output_buffer = io.BytesIO()
    doc.save(output_buffer)
    doc.close()
    output_buffer.seek(0)

    return output_buffer.getvalue(), resumen_planos, alertas_sellos, es_a4