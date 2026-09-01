import os
import glob
import streamlit as st

CARPETA_SELLOS = "firmas_sellos"

def inicializar_estado_sesion():
    """Inicializa las variables de estado en Streamlit."""
    if "autenticado" not in st.session_state:
        st.session_state.autenticado = True  # Cambia a False si usas un login activo
    if "usuario" not in st.session_state:
        st.session_state.usuario = "Usuario"

def obtener_libreria_sellos():
    """Escanea la carpeta de firmas/sellos y devuelve un diccionario con sus rutas."""
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