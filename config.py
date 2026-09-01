CARPETA_SELLOS = "firmas_sellos"
RUTA_LOGO = "logo.png"

def aplicar_estilos_custom():
    """Aplica estilos CSS personalizados a la aplicación Streamlit."""
    import streamlit as st
    st.markdown("""
        <style>
            .stButton>button {
                border-radius: 8px;
            }
        </style>
    """, unsafe_allow_html=True)