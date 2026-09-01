import streamlit as st

def vista_login():
    """Muestra la interfaz de inicio de sesión."""
    st.title("🔑 Iniciar Sesión")
    usuario = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    
    if st.button("Ingresar", use_container_width=True):
        if usuario and password:
            st.session_state.autenticado = True
            st.session_state.usuario = usuario
            st.success("¡Bienvenido!")
            st.rerun()
        else:
            st.error("Por favor ingrese usuario y contraseña.")