import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURACIÓN DE PARÁMETROS GLOBALES ---
CARPETA_SELLOS = "firmas_sellos"
UMBRAL_BLANCO_SECUNDARIO = 0.70  # Variable editable: 70% de espacio blanco aceptable

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

# --- CAPTURA DEL CAJETÍN EN PLANOS A3 (3 FILAS + CÓDIGO) ---
def extraer_datos_cajetin_a3(pagina):
    """
    Extrae la información clave del cajetín en formato A3:
    1. Las 3 filas del bloque 'PROYECTO' (Descripción, Tramo, Detalle).
    2. El código único del plano.
    """
    rect = pagina.rect
    
    # ROI del bloque central/izquierdo del cajetín (PROYECTO)
    roi_proyecto = fitz.Rect(
        rect.width * 0.50,
        rect.height * 0.82,
        rect.width * 0.80,
        rect.height
    )
    
    # ROI del bloque derecho del cajetín (CÓDIGO Y DATOS TÉCNICOS)
    roi_codigo = fitz.Rect(
        rect.width * 0.78,
        rect.height * 0.82,
        rect.width,
        rect.height
    )
    
    # --- 1. Extraer las 3 filas del Proyecto ---
    texto_proyecto = pagina.get_text("text", clip=roi_proyecto)
    lineas_proj = [l.strip() for l in texto_proyecto.split('\n') if l.strip()]
    
    # Filtrar encabezados no deseados
    lineas_limpias = [
        l for l in lineas_proj 
        if not any(k in l.upper() for k in ["PROYECTO:", "ESCALA", "FECHA"])
    ]
    
    filas_texto = " | ".join(lineas_limpias[:3]) if lineas_limpias else "Sin descripción detectada"

    # --- 2. Extraer Código del Plano ---
    texto_codigo = pagina.get_text("text", clip=roi_codigo)
    lineas_cod = [l.strip() for l in texto_codigo.split('\n') if l.strip()]
    
    codigo_detectado = "No detectado"
    for l in lineas_cod:
        # Buscar patrones alfanuméricos tipo ME154-MCA-06-11-009-R1
        coincidencia = re.search(r'[A-Z0-9]{2,}[-\_][A-Z0-9\-_]+', l)
        if coincidencia:
            codigo_detectado = coincidencia.group(0)
            break
            
    if codigo_detectado == "No detectado" and lineas_cod:
        codigo_detectado = lineas_cod[-1]

    return filas_texto, codigo_detectado


# --- BÚSQUEDA ADAPTATIVA PARA GARANTIZAR EL SELLO EN TODAS LAS HOJAS ---
def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px, umbral_tolerancia=UMBRAL_BLANCO_SECUNDARIO):
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 20
    area_total_sello = ancho_sello_px * alto_sello_px

    # Rangos de escaneo prioritarios en planos A3 (evitando el cajetín inferior derecho)
    rangos_busqueda = [
        # (x_inicio, x_fin, y_inicio, y_fin)
        (ancho_img - ancho_sello_px - 20, int(ancho_img * 0.3), alto_img - alto_sello_px - 200, int(alto_img * 0.2)), # Zona media/derecha
        (int(ancho_img * 0.5), 20, alto_img - alto_sello_px - 200, int(alto_img * 0.2)),                             # Zona media/izquierda
        (ancho_img - ancho_sello_px - 20, 20, int(alto_img * 0.3), 20)                                               # Zona superior
    ]

    min_pixeles = float('inf')
    posicion_respaldo = (ancho_img - ancho_sello_px - 40, alto_img - alto_sello_px - 250)

    # 1. Intentar encontrar una zona que cumpla con el % de blancura
    for x_ini, x_fin, y_ini, y_fin in rangos_busqueda:
        paso_x = -paso if x_ini > x_fin else paso
        paso_y = -paso if y_ini > y_fin else paso

        for x in range(x_ini, x_fin, paso_x):
            for y in range(y_ini, y_fin, paso_y):
                caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
                if caja.shape[0] != alto_sello_px or caja.shape[1] != ancho_sello_px:
                    continue
                
                pixeles_ocupados = cv2.countNonZero(caja)
                porcentaje_libre = 1.0 - (pixeles_ocupados / area_total_sello)

                if porcentaje_libre >= umbral_tolerancia:
                    return x, y

                if pixeles_ocupados < min_pixeles:
                    min_pixeles = pixeles_ocupados
                    posicion_respaldo = (x, y)

    # 2. Si no encontró espacio perfecto, retorna la zona más limpia disponible
    return posicion_respaldo


# --- MOTOR DE PROCESAMIENTO ---
def agregar_sello_vectorial_pdf(pagina, rect, tipo_sello, texto_fecha):
    if "CC" in tipo_sello:
        color = (0.85, 0.05, 0.05)
        linea_2 = "COPIA CONTROLADA"
    else:
        color = (0.0, 0.3, 0.75)
        linea_2 = "COPIA INFORMATIVA"

    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    alto_caja = rect.height
    ancho_caja = rect.width
    centro_x = rect.x0 + (ancho_caja / 2)

    def dibujar_texto_proporcional(texto, prop_y, prop_size, fontname):
        fontsize = alto_caja * prop_size
        ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)
        
        if ancho_texto > (ancho_caja * 0.90):
            fontsize = fontsize * ((ancho_caja * 0.90) / ancho_texto)
            ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        x_calculado = centro_x - (ancho_texto / 2)
        y_calculado = rect.y0 + (alto_caja * prop_y)
        
        pagina.insert_text(
            fitz.Point(x_calculado, y_calculado),
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color
        )

    dibujar_texto_proporcional("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_texto_proporcional(linea_2, 0.62, 0.22, "hebo")
    dibujar_texto_proporcional(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")


def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha, umbral_blanco):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]

        # 1. Extraer 3 filas + código del plano en A3
        filas_descripcion, codigo_plano = extraer_datos_cajetin_a3(pagina)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": codigo_plano,
            "Descripción / Cajetín (3 Filas)": filas_descripcion
        })

        # 2. Renderizar imagen para búsqueda de espacios
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        for item_sello in lista_sellos_elegidos:
            ancho_sello_px = 250
            alto_sello_px = 120

            # Búsqueda optimizada
            x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px, umbral_blanco)

            pdf_x = x_px * factor_x
            pdf_y = y_px * factor_y
            pdf_ancho = ancho_sello_px * factor_x
            pdf_alto = alto_sello_px * factor_y

            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)

            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial_pdf(pagina, rect_sello, item_sello, texto_fecha)
            else:
                ruta_imagen = libreria_archivos[item_sello]
                pagina.insert_image(rect_sello, filename=ruta_imagen)

            # Ocupar el espacio para evitar superposición
            cv2.rectangle(imagen_gris, (x_px, y_px), (x_px + ancho_sello_px, y_px + alto_sello_px), 0, -1)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos


# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Agente RZ - Estampado A3", page_icon="🤖", layout="wide")

st.title("🤖📑 AGENTE PARA ESTAMPAR PLANOS A3")
st.caption("Gestión de sellos, copias y lectura de cajetín elaborado por RZ")

# SIDEBAR DE CONFIGURACIÓN
st.sidebar.header("⚙️ Ajustes de Algoritmo")
umbral_usuario = st.sidebar.slider(
    "Mínimo % de Blancura Requerido:",
    min_value=30,
    max_value=95,
    value=70,
    step=5,
    help="Si no encuentra espacio 100% limpio, usará este umbral mínimo antes de posicionar el sello."
) / 100.0

st.sidebar.divider()
st.sidebar.header("📁 Cargar Nuevos Sellos")
with st.sidebar.expander("➕ Subir Firma/Imagen a Base de Datos", expanded=False):
    nuevo_nombre = st.text_input("Nombre de la firma/sello:")
    archivo_nuevo = st.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])
    
    if st.button("💾 Guardar en Base de Datos"):
        if not nuevo_nombre.strip() or not archivo_nuevo:
            st.sidebar.error("Ingresa un nombre y selecciona la imagen.")
        else:
            ext = archivo_nuevo.name.split(".")[-1]
            nombre_archivo = f"{nuevo_nombre.lower().strip().replace(' ', '_')}.{ext}"
            ruta_destino = os.path.join(CARPETA_SELLOS, nombre_archivo)
            
            with open(ruta_destino, "wb") as f:
                f.write(archivo_nuevo.read())
                
            st.sidebar.success(f"¡Sello '{nuevo_nombre}' agregado con éxito!")
            st.rerun()

st.divider()

st.subheader("1. 📂 Cargar Plano / Documento PDF (A3)")
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF (Formato A3):", type=["pdf"])

st.divider()

st.subheader("2. 🏷️ Selección de Sellos y Firma")
col1, col2 = st.columns([1, 2])

with col1:
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    texto_fecha_ingresada = st.text_input("📅 Fecha de Sellado (Editable):", value=fecha_hoy)

libreria_archivos = obtener_libreria_sellos()
opciones_dinamicas = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]
opciones_totales = opciones_dinamicas + list(libreria_archivos.keys())

with col2:
    sellos_seleccionados = st.multiselect(
        "🏷️ Selecciona uno o varios sellos/firmas a aplicar:",
        options=opciones_totales,
        default=[opciones_totales[0]] if opciones_totales else []
    )

st.divider()

if archivo_pdf and sellos_seleccionados:
    if st.button(f"🚀 Estampar {len(sellos_seleccionados)} Sello(s) Seleccionado(s)", use_container_width=True):
        with st.spinner("Procesando láminas A3, leyendo cajetines y aplicando sellos..."):
            try:
                pdf_resultado, lista_resumen = procesar_pdf(
                    archivo_pdf.read(), 
                    sellos_seleccionados, 
                    libreria_archivos, 
                    texto_fecha_ingresada,
                    umbral_usuario
                )
                
                st.success("¡Documento A3 estampado exitosamente en TODAS las hojas!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado",
                    data=pdf_resultado,
                    file_name=f"ESTAMPADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )

                # Visualización ordenada de la lista requerida
                st.subheader("📋 Lista de Planos Detectados")
                st.dataframe(lista_resumen, use_container_width=True)

            except Exception as e:
                st.error(f"Error durante el procesamiento: {e}")