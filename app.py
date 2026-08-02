import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os
import glob

# --- CONFIGURACIÓN DE LA CARPETA BASE DE DATOS ---
CARPETA_SELLOS = "firmas_sellos"

if not os.path.exists(CARPETA_SELLOS):
    os.makedirs(CARPETA_SELLOS)

def obtener_libreria_sellos():
    """Lee automáticamente todas las imágenes PNG/JPG de la carpeta."""
    extensiones = ('*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG')
    archivos = []
    for ext in extensiones:
        archivos.extend(glob.glob(os.path.join(CARPETA_SELLOS, ext)))
    
    # Retorna un diccionario: {"Nombre Limpio": "Ruta/al/archivo.png"}
    libreria = {}
    for ruta in archivos:
        nombre_base = os.path.basename(ruta)
        nombre_sin_ext = os.path.splitext(nombre_base)[0].replace("_", " ").title()
        libreria[nombre_sin_ext] = ruta
    return libreria

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

# --- PROCESAMIENTO MULTI-SELLO ---
def procesar_pdf_múltiples_sellos(pdf_bytes, lista_rutas_sellos):
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

        # Aplicar cada sello uno tras otro en posiciones vacías independientes
        for ruta_sello in lista_rutas_sellos:
            ancho_sello_px = 250
            alto_sello_px = 120

            # 1. Encontrar espacio libre en la imagen actual
            x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)

            # 2. Insertar el sello en el documento PDF
            pdf_x = x_px * factor_x
            pdf_y = y_px * factor_y
            pdf_ancho = ancho_sello_px * factor_x
            pdf_alto = alto_sello_px * factor_y

            rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)
            pagina.insert_image(rect_sello, filename=ruta_sello)

            # 3. ACTUALIZAR LA IMAGEN EN MEMORIA para que el siguiente sello no ocupe este mismo lugar
            cv2.rectangle(imagen_gris, (x_px, y_px), (x_px + ancho_sello_px, y_px + alto_sello_px), 0, -1)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes


# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Stamper IA - MultiSello", page_icon="📑", layout="wide")

st.title("📑 Estampador Inteligente A3 (Librería Multi-Sello)")
st.caption("Gestiona tu base de datos de sellos y aplica múltiples firmas automáticamente sin solapamientos.")

# --- SIDEBAR: SUBIR NUEVOS SELLOS ---
st.sidebar.header("📥 Agregar a la Base de Datos")
nuevo_nombre = st.sidebar.text_input("Nombre del sello:")
archivo_nuevo_sello = st.sidebar.file_uploader("Subir Imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])

if st.sidebar.button("💾 Guardar en Carpeta"):
    if not nuevo_nombre.strip() or not archivo_nuevo_sello:
        st.sidebar.error("Completa el nombre y selecciona una imagen.")
    else:
        ext = archivo_nuevo_sello.name.split(".")[-1]
        nombre_archivo = f"{nuevo_nombre.lower().strip().replace(' ', '_')}.{ext}"
        ruta_final = os.path.join(CARPETA_SELLOS, nombre_archivo)
        
        with open(ruta_final, "wb") as f:
            f.write(archivo_nuevo_sello.read())
            
        st.sidebar.success(f"¡Sello '{nuevo_nombre}' guardado!")
        st.rerun()

st.divider()

# --- SECCIÓN PRINCIPAL: GALERÍA Y SELECCIÓN MULTIPLE ---
libreria_actual = obtener_libreria_sellos()

st.subheader("🖼️ Librería de Sellos Disponibles")

if not libreria_actual:
    st.info("La carpeta `firmas_sellos` está vacía. Usa el panel izquierdo para agregar sellos.")
else:
    # 1. Multiselect para seleccionar 1 o varios sellos
    sellos_seleccionados = st.multiselect(
        "Selecciona uno o varios sellos para insertar en el PDF (se aplicarán en orden):",
        options=list(libreria_actual.keys()),
        default=list(libreria_actual.keys())[:1] # Por defecto selecciona el primero
    )

    # 2. Previsualización en tarjetas / columnas
    if sellos_seleccionados:
        st.write("**Previsualización de sellos elegidos:**")
        cols = st.columns(min(len(sellos_seleccionados), 5))
        for idx, nombre_sello in enumerate(sellos_seleccionados):
            col_idx = idx % 5
            with cols[col_idx]:
                st.image(libreria_actual[nombre_sello], caption=f"{idx+1}. {nombre_sello}", use_container_width=True)

st.divider()

# --- SECCIÓN DE PROCESAMIENTO DEL PDF ---
st.subheader("📄 Carga y Procesamiento del Plano")
archivo_pdf = st.file_uploader("Selecciona tu archivo PDF (A3):", type=["pdf"])

if archivo_pdf and libreria_actual and sellos_seleccionados:
    rutas_a_procesar = [libreria_actual[nombre] for nombre in sellos_seleccionados]
    
    if st.button(f"🚀 Estampar {len(sellos_seleccionados)} Sello(s) en el PDF", use_container_width=True):
        with st.spinner("Analizando lámina y ubicando espacio para cada sello..."):
            try:
                pdf_resultado = procesar_pdf_múltiples_sellos(archivo_pdf.read(), rutas_a_procesar)
                
                st.success("¡Todos los sellos se insertaron correctamente en zonas libres!")
                
                st.download_button(
                    label="📥 Descargar PDF Sellado",
                    data=pdf_resultado,
                    file_name=f"MULTISELLADO_{archivo_pdf.name}",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Ocurrió un error al procesar: {e}")