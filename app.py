import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURACIÓN DE LA CARPETA BASE DE DATOS ---
CARPETA_SELLOS = "firmas_sellos"

if not os.path.exists(CARPETA_SELLOS):
    os.makedirs(CARPETA_SELLOS)

def obtener_libreria_sellos():
    """Lee automáticamente las imágenes PNG/JPG guardadas en la carpeta."""
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


def generar_sello_dinamico(tipo_sello, texto_fecha):
    """Genera en memoria un sello con recuadro exacto y texto grande usando la fuente local."""
    # Mantenemos un lienzo de alta definición proporcional (500x240)
    ancho, alto = 500, 240
    img = Image.new("RGBA", (ancho, alto), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Configuración de colores
    if "CC" in tipo_sello:
        color = (210, 15, 15, 255)   # Rojo intenso
        linea_2 = "COPIA CONTROLADA"
    else:
        color = (0, 75, 190, 255)    # Azul
        linea_2 = "COPIA INFORMATIVA"

    # 1. Dibujar Borde / Recuadro
    grosor_linea = 8
    draw.rounded_rectangle([10, 10, ancho - 10, alto - 10], radius=18, outline=color, width=grosor_linea)

    # 2. Cargar la fuente local del proyecto (Garantiza tamaño y soporte de tildes)
    ruta_fuente = "font_bold.ttf"
    
    if os.path.exists(ruta_fuente):
        f_titulo = ImageFont.truetype(ruta_fuente, 32)    # OSP INGENIERÍA
        f_principal = ImageFont.truetype(ruta_fuente, 42) # COPIA CONTROLADA (GIGANTE)
        f_fecha = ImageFont.truetype(ruta_fuente, 32)     # FECHA: DD/MM/AAAA
    else:
        # Si por alguna razón no la encuentra en la carpeta, intenta buscar arialbd del sistema
        try:
            f_titulo = ImageFont.truetype("arialbd.ttf", 32)
            f_principal = ImageFont.truetype("arialbd.ttf", 42)
            f_fecha = ImageFont.truetype("arialbd.ttf", 32)
        except:
            f_titulo = f_principal = f_fecha = ImageFont.load_default()

    # 3. Dibujar textos centrados con proporciones perfectas
    draw.text((ancho / 2, 50), "OSP INGENIERÍA", fill=color, font=f_titulo, anchor="mm")
    draw.text((ancho / 2, 120), linea_2, fill=color, font=f_principal, anchor="mm")
    draw.text((ancho / 2, 185), f"FECHA:  {texto_fecha}", fill=color, font=f_fecha, anchor="mm")

    # Guardar en archivo temporal
    temp_sello = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.save(temp_sello.name, "PNG")
    temp_sello.close()
    
    return temp_sello.name

# --- BÚSQUEDA VERTICAL (Abajo ➔ Arriba ➔ Izquierda) ---
def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px):
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 15

    for x in range(ancho_img - ancho_sello_px - 20, 20, -paso):
        for y in range(alto_img - alto_sello_px - 20, 20, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            if pixeles_ocupados < (ancho_sello_px * alto_sello_px * 0.002):
                return x, y

    return ancho_img - ancho_sello_px - 30, alto_img - alto_sello_px - 30


# --- MOTOR DE PROCESAMIENTO ---
def agregar_sello_vectorial_pdf(pagina, rect, tipo_sello, texto_fecha):
    """Dibuja el marco y texto calculando proporciones dinámicas para que NUNCA se desborde."""
    # Color según el sello
    if "CC" in tipo_sello:
        color = (0.85, 0.05, 0.05)  # Rojo
        linea_2 = "COPIA CONTROLADA"
    else:
        color = (0.0, 0.3, 0.75)   # Azul
        linea_2 = "COPIA INFORMATIVA"

    # 1. Dibujar Recuadro Vectorial
    shape = pagina.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=color, fill=None, width=2.0)
    shape.commit()

    # 2. Obtener dimensiones dinámicas exactas de LA CAJA en este PDF
    alto_caja = rect.height
    ancho_caja = rect.width
    centro_x = rect.x0 + (ancho_caja / 2)

    # 3. Función interna para escalar y centrar
    def dibujar_texto_proporcional(texto, prop_y, prop_size, fontname):
        # El tamaño de la letra es un porcentaje del alto total de la caja
        fontsize = alto_caja * prop_size
        
        # Medimos cuánto mide el texto de ancho con ese tamaño de fuente
        ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)
        
        # Sistema de seguridad: si el texto es más ancho que la caja, achicamos la letra
        if ancho_texto > (ancho_caja * 0.90):
            fontsize = fontsize * ((ancho_caja * 0.90) / ancho_texto)
            ancho_texto = fitz.get_text_length(texto, fontname=fontname, fontsize=fontsize)

        # Calculamos X (para centrar perfecto) e Y (posición vertical proporcional)
        x_calculado = centro_x - (ancho_texto / 2)
        y_calculado = rect.y0 + (alto_caja * prop_y)
        
        pagina.insert_text(
            fitz.Point(x_calculado, y_calculado),
            texto,
            fontsize=fontsize,
            fontname=fontname,
            color=color
        )

    # 4. Inyectamos los textos (prop_y: % desde arriba, prop_size: % altura de letra)
    # Ejemplo: 0.30 significa que la base de la letra estará al 30% de la altura de la caja
    dibujar_texto_proporcional("OSP INGENIERIA", 0.30, 0.18, "helv")
    dibujar_texto_proporcional(linea_2, 0.62, 0.22, "hebo") # hebo = Negrita y más grande
    dibujar_texto_proporcional(f"FECHA: {texto_fecha}", 0.88, 0.16, "hebo")

def procesar_pdf(pdf_bytes, lista_sellos_elegidos, libreria_archivos, texto_fecha):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)

    for i in range(len(doc)):
        pagina = doc[i]
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        for item_sello in lista_sellos_elegidos:
            ancho_sello_px = 250
            alto_sello_px = 120

            # 1. Encontrar espacio libre
            x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)

            # 2. Calcular coordenadas en el PDF
            pdf_x = x_px * factor_x
            pdf_y = y_px * factor_y
            pdf_ancho = ancho_sello_px * factor_x
            pdf_alto = alto_sello_px * factor_y

            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)

            # 3. Aplicar sello (Vectorial o Imagen según selección)
            if item_sello in ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"]:
                agregar_sello_vectorial_pdf(pagina, rect_sello, item_sello, texto_fecha)
            else:
                ruta_imagen = libreria_archivos[item_sello]
                pagina.insert_image(rect_sello, filename=ruta_imagen)

            # Ocupar espacio en memoria
            cv2.rectangle(imagen_gris, (x_px, y_px), (x_px + ancho_sello_px, y_px + alto_sello_px), 0, -1)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes


# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Agente RZ - Estampado", page_icon="🤖", layout="wide")

# Encabezado
st.title("🤖📑 AGENTE PARA ESTAMPAR")
st.caption("Gestion del sellos y copias elaborado por RZ")

# --- SIDEBAR: ADMINISTRACIÓN DE NUEVAS FOTOS/SELLOS ---
st.sidebar.header("📁 Cargar Nuevos Sellos")
with st.sidebar.expander("➕ Subir Firma/Imagen a Base de Datos", expanded=True):
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

# --- 1. SUBIR PLANO PDF (ARRIBA DE TODO) ---
st.subheader("1. 📂 Cargar Plano / Documento PDF")
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF (A3 o Estándar):", type=["pdf"])

st.divider()

# --- 2. CONFIGURACIÓN Y SELECCIÓN DE SELLOS (COMBOBOX INTEGRADO) ---
st.subheader("2. 🏷️ Selección de Sellos y Firma")

col1, col2 = st.columns([1, 2])

with col1:
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    texto_fecha_ingresada = st.text_input("📅 Fecha de Sellado (Editable):", value=fecha_hoy)

# Construir opciones del Combobox consolidando CC/CI + Carpeta
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

# --- 3. BOTÓN Y PROCESAMIENTO ---
if archivo_pdf and sellos_seleccionados:
    if st.button(f"🚀 Estampar {len(sellos_seleccionados)} Sello(s) Seleccionado(s)", use_container_width=True):
        with st.spinner("Procesando lámina y aplicando firmas/sellos..."):
            try:
                pdf_resultado = procesar_pdf(
                    archivo_pdf.read(), 
                    sellos_seleccionados, 
                    libreria_archivos, 
                    texto_fecha_ingresada
                )
                
                st.success("¡Documento estampado exitosamente!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado",
                    data=pdf_resultado,
                    file_name=f"ESTAMPADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Error durante el procesamiento: {e}")