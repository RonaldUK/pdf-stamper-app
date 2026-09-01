import streamlit as st

# Credenciales de acceso
USUARIO_CORRECTO = "admin"
PASSWORD_CORRECTO = "123"

def vista_login():
    """Muestra el formulario de acceso estructurado."""
    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        st.subheader("🔑 Iniciar Sesión")
        st.caption("Ingrese sus credenciales para acceder al sistema.")
        
        with st.form("form_login"):
            usuario = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            btn_submit = st.form_submit_button("Ingresar", use_container_width=True)

            if btn_submit:
                if usuario == USUARIO_CORRECTO and password == PASSWORD_CORRECTO:
                    st.session_state.autenticado = True
                    st.session_state.usuario = usuario
                    st.success("¡Acceso concedido!")
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")