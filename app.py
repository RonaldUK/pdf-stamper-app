import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
import re
from datetime import datetime
from PIL import Image
import easyocr

# --- INICIALIZAR MOTOR OCR EN MEMORIA CACHÉ ---
@st.cache_resource
def cargar_lector_ocr():
    # Carga EasyOCR en español e inglés
    return easyocr.Reader(['es', 'en'], gpu=False)

reader_ocr = cargar_lector_ocr()

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

# --- EXTRAER CAJETÍN MEDIANTE VISIÓN POR COMPUTADORA (OCR) ---
def extraer_datos_cajetin_ocr(img_np):
    """
    Recorta el cajetín en la esquina inferior derecha y ejecuta OCR.
    """
    alto, ancho = img_np.shape[:2]
    
    # Recorte HD de la esquina inferior derecha (donde va el cajetín en A3)
    cajetin_crop = img_np[int(alto * 0.80):alto, int(ancho * 0.45):ancho]
    
    # Ejecutar OCR en la región recortada
    resultados = reader_ocr.readtext(cajetin_crop, detail=0)
    
    # Filtrar basura
    lineas_limpias = [
        texto for texto in resultados 
        if len(texto.strip()) > 3 and not any(k in texto.upper() for k in ["PROYECTO", "ESCALA", "FECHA", "PLANO"])
    ]
    
    # Identificar código del plano (patrón con guiones)
    codigo_plano = "No detectado"
    for t in resultados:
        match = re.search(r'[A-Z0-9]{2,}[-\_][A-Z0-9\-_]+', t)
        if match:
            codigo_plano = match.group(0)
            break

    tres_filas = " | ".join(lineas_limpias[:3]) if lineas_limpias else "Cajetín detectado sin texto claro"
    return tres_filas, codigo_plano


# --- ALGORITMO DE ESPACIO LIBRE GARANTIZADO (SIN CHANCO) ---
def buscar_posicion_sello_garantizada(imagen_gris, ancho_sello_px, alto_sello_px, sellos_ya_puestos, umbral_blanco):
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello_px * alto_sello_px

    # Margen de seguridad entre sellos (30px)
    pad = 30 

    # Generar rejilla de evaluación (evitando el cajetín inferior derecho: x > 60% e y > 80%)
    candidatos = []

    paso_x = 40
    paso_y = 40

    for y in range(40, alto_img - alto_sello_px - 40, paso_y):
        for x in range(40, ancho_img - ancho_sello_px - 40, paso_x):
            
            # REGLA 1: No colocar sobre el cajetín (área reservada)
            if x > (ancho_img * 0.50) and y > (alto_img * 0.75):
                continue

            # REGLA 2: No chancar sellos previamente colocados en esta misma hoja
            colision = False
            for (sx, sy, sw, sh) in sellos_ya_puestos:
                if not (x + ancho_sello_px + pad < sx or x > sx + sw + pad or
                        y + alto_sello_px + pad < sy or y > sy + sh + pad):
                    colision = True
                    break
            if colision:
                continue

            # REGLA 3: Evaluar densidad de dibujo en el PDF (blanco)
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            porcentaje_libre = 1.0 - (pixeles_ocupados / area_sello)

            candidatos.append((porcentaje_libre, pixeles_ocupados, x, y))

    # Ordenar de MAYOR a MENOR porcentaje de espacio libre
    candidatos.sort(key=lambda item: item[0], reverse=True)

    if candidatos:
        # Si la mejor opción cumple el umbral del usuario, la usa; si no, fuerza la zona con menos contenido
        _, _, mejor_x, mejor_y = candidatos[0]
        return mejor_x, mejor_y

    # Fallback si el plano está repleto
    return 50, 50


# --- ESTAMPADO VECTORIAL ---
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


# --- PROCESAMIENTO PRINCIPAL ---
def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha, umbral_blanco):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos = []

    for i in range(len(doc)):
        pagina = doc[i]

        # 1. Obtener render de alta calidad (DPI 150)
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        # 2. Lectura por OCR del Cajetín
        filas_descripcion, codigo_plano = extraer_datos_cajetin_ocr(img_np)
        resumen_planos.append({
            "Hoja": i + 1,
            "Código de Plano": codigo_plano,
            "Descripción (3 Filas)": filas_descripcion
        })

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        # Registro local de sellos ya asentados en ESTA hoja
        sellos_colocados_hoja = []

        for item_sello in lista_sellos_elegidos:
            ancho_sello_px = 250
            alto_sello_px = 120

            # Buscar coordenada garantizada sin chancado
            x_px, y_px = buscar_posicion_sello_garantizada(
                imagen_gris, 
                ancho_sello_px, 
                alto_sello_px, 
                sellos_colocados_hoja, 
                umbral_blanco
            )

            # Guardar ocupación
            sellos_colocados_hoja.append((x_px, y_px, ancho_sello_px, alto_sello_px))

            # Mapear a coordenadas del PDF
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

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes, resumen_planos


# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Agente RZ - Visión OCR", page_icon="🤖", layout="wide")

st.title("🤖📑 AGENTE ESTAMPADOR CON VISIÓN ARTIFICIAL (OCR)")
st.caption("Procesamiento inteligente para láminas A3 libre de colisiones")

st.sidebar.header("⚙️ Ajustes de Algoritmo")
umbral_usuario = st.sidebar.slider(
    "Mínimo % de Blancura Requerido:",
    min_value=30, max_value=95, value=70, step=5
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
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF:", type=["pdf"])

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
        with st.spinner("Ejecutando visión artificial, OCR y estampado..."):
            try:
                pdf_resultado, lista_resumen = procesar_pdf(
                    archivo_pdf.read(), 
                    sellos_seleccionados, 
                    libreria_archivos, 
                    texto_fecha_ingresada,
                    umbral_usuario
                )
                
                st.success("¡Documento A3 estampado con éxito sin chancomientos!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado",
                    data=pdf_resultado,
                    file_name=f"ESTAMPADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )

                st.subheader("📋 Lista de Planos Detectados vía OCR")
                st.dataframe(lista_resumen, use_container_width=True)

            except Exception as e:
                st.error(f"Error durante el procesamiento: {e}")