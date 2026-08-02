import streamlit as st
import fitz       # PyMuPDF
import cv2
import numpy as np
import tempfile
import os

# --- LÓGICA DE BÚSQUEDA SEGÚN TU ESQUEMA (De Abajo a Arriba, desplazando hacia la Izquierda) ---
def buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px):
    # Convertir a binaria (Fondo = 0, Contenido/Líneas = 255)
    _, binaria = cv2.threshold(imagen_gris, 240, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    paso = 15  # Salto en píxeles

    # 1. Bucle exterior (X): Se mueve de DERECHA a IZQUIERDA
    for x in range(ancho_img - ancho_sello_px - 20, 20, -paso):
        # 2. Bucle interior (Y): Se mueve de ABAJO hacia ARRIBA en esa columna
        for y in range(alto_img - alto_sello_px - 20, 20, -paso):
            caja = binaria[y : y + alto_sello_px, x : x + ancho_sello_px]
            pixeles_ocupados = cv2.countNonZero(caja)
            
            # Si el área está libre de contenido
            if pixeles_ocupados < (ancho_sello_px * alto_sello_px * 0.002):
                return x, y

    # Respaldo: Esquina inferior derecha si no encuentra nada absolutamente limpio
    return ancho_img - ancho_sello_px - 30, alto_img - alto_sello_px - 30


def procesar_pdf(pdf_bytes, ruta_sello_png):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    total_paginas = len(doc)

    for i in range(total_paginas):
        pagina = doc[i]
        pixmap = pagina.get_pixmap(dpi=150)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n).copy()
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        ancho_sello_px = 250
        alto_sello_px = 120

        # Aplica la nueva búsqueda vertical de derecha a izquierda
        x_px, y_px = buscar_zona_vacia(imagen_gris, ancho_sello_px, alto_sello_px)

        factor_x = pagina.rect.width / pixmap.w
        factor_y = pagina.rect.height / pixmap.h

        pdf_x = x_px * factor_x
        pdf_y = y_px * factor_y
        pdf_ancho = ancho_sello_px * factor_x
        pdf_alto = alto_sello_px * factor_y

        rect_sello = fitz.Rect(pdf_x, pdf_y, pdf_x + pdf_ancho, pdf_y + pdf_alto)
        pagina.insert_image(rect_sello, filename=ruta_sello_png)

    output_pdf_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(output_pdf_path)
    doc.close()

    with open(output_pdf_path, "rb") as f:
        pdf_final_bytes = f.read()

    os.remove(path_pdf)
    os.remove(output_pdf_path)

    return pdf_final_bytes


# --- DICCIONARIO DE SELLOS PREDEFINIDOS EN TU CARPETA LOCAL ---
SELLOS_DISPONIBLES = {
    "Copia Controlada": "copia_controlada.png",
    "Copia Informativa": "copia_informativa.png",
    "Firma de Revisión": "sello_firma.png"
}


# --- INTERFAZ ADAPTATIVA Y RESPONSIVE ---
st.set_page_config(
    page_title="Stamper IA - Planos A3", 
    page_icon="📑",
    layout="centered"
)

# Estilos CSS adicionales para asegurar perfecta vista responsive en celulares
st.markdown("""
    <style>
        .stButton>button {
            width: 100%;
            border-radius: 8px;
            height: 3em;
            background-color: #2e7d32;
            color: white;
            font-weight: bold;
        }
        @media (max-width: 600px) {
            .main .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
        }
    </style>
""", unsafe_allow_html=True)

st.title("🤖📑 AGENTE PARA CC-CI-F")
st.caption("Agente elaborado para marcar sellos by ARZ")

st.divider()

# 1. Cargar archivo PDF
archivo_pdf = st.file_uploader("📂 Selecciona tu archivo PDF (A3):", type=["pdf"])

# 2. Selección del Sello mediante Combobox
opcion_sello = st.selectbox(
    "🏷️ Selecciona el sello que deseas aplicar:",
    options=list(SELLOS_DISPONIBLES.keys())
)

ruta_sello_elegido = SELLOS_DISPONIBLES[opcion_sello]

# Verificar si la imagen elegida existe en la carpeta local
if not os.path.exists(ruta_sello_elegido):
    st.warning(f"⚠️ Nota: Asegúrate de guardar la imagen '{ruta_sello_elegido}' en la carpeta de tu proyecto.")

st.divider()

# 3. Accionador
if archivo_pdf:
    if st.button("🚀 Estampar Plano", use_container_width=True):
        if not os.path.exists(ruta_sello_elegido):
            st.error(f"No se encontró el archivo '{ruta_sello_elegido}' en el disco.")
        else:
            with st.spinner("Escaneando lámina (Abajo ➔ Arriba ➔ Izquierda)..."):
                try:
                    pdf_resultado = procesar_pdf(archivo_pdf.read(), ruta_sello_elegido)
                    
                    st.success("¡Documento procesado con éxito!")
                    
                    st.download_button(
                        label="📥 Descargar PDF Sellado",
                        data=pdf_resultado,
                        file_name=f"SELLADO_{archivo_pdf.name}",
                        mime="application/pdf",
                        use_container_width=True
                    )
                except Exception as e:
                    st.error(f"Error procesando el documento: {e}")