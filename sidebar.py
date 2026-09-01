import os
import streamlit as st

CARPETA_SELLOS = "firmas_sellos"

def render_sidebar(usuario):
    st.sidebar.markdown(f"👤 **Usuario:** `{usuario}`")
    if st.sidebar.button("🔴 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.usuario = None
        st.rerun()

    st.sidebar.divider()
    st.sidebar.header("⚙️ Ajustes de Escaneo")
    paso_evaluacion = st.sidebar.slider("🎯 Precisión (Paso en px):", 5, 30, 10, 5)

    st.sidebar.header("📁 Cargar Nuevas Firmas")
    with st.sidebar.expander("➕ Subir Imagen a Base de Datos", expanded=False):
        nuevo_nombre = st.sidebar.text_input("Nombre de la firma/sello:")
        archivo_nuevo = st.sidebar.file_uploader("Subir imagen (PNG/JPG):", type=["png", "jpg", "jpeg"])

        if st.sidebar.button("💾 Guardar Sello"):
            if nuevo_nombre.strip() and archivo_nuevo:
                if not os.path.exists(CARPETA_SELLOS):
                    os.makedirs(CARPETA_SELLOS)
                ext = archivo_nuevo.name.split(".")[-1]
                ruta_dest = os.path.join(CARPETA_SELLOS, f"{nuevo_nombre.lower().strip().replace(' ', '_')}.{ext}")
                with open(ruta_dest, "wb") as f:
                    f.write(archivo_nuevo.read())
                st.sidebar.success("¡Sello Guardado!")
                st.rerun()

    return paso_evaluacion