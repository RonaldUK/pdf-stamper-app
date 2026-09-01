import os
import datetime
import fitz
import io
import streamlit as st

from styles import aplicar_estilos_custom, obtener_base64_logo
from pdf_processor import procesar_pdf, obtener_libreria_sellos
from excel_generator import generar_excel_por_area, convertir_excel_a_pdf, extraer_numero_revision

def unificar_pdfs(lista_pdf_bytes):
    pdf_final = fitz.open()
    for b in lista_pdf_bytes:
        if b:
            doc_temp = fitz.open(stream=b, filetype="pdf")
            pdf_final.insert_pdf(doc_temp)
            doc_temp.close()

    buffer = io.BytesIO()
    pdf_final.save(buffer)
    pdf_final.close()
    buffer.seek(0)
    return buffer.getvalue()

def vista_login():
    aplicar_estilos_custom()
    logo_b64 = obtener_base64_logo()

    _, col_b, _ = st.columns([1, 1.8, 1])

    with col_b:
        if logo_b64:
            st.markdown(f'''
                <div style="text-align: center;">
                    <img src="data:image/png;base64,{logo_b64}" class="logo-img" alt="AO RNLD US">
                    <p class="sub-title">Ingeniería • Diseño • Construcción</p>
                </div>
            ''', unsafe_allow_html=True)
        else:
            st.markdown("<h1 style='text-align: center; color: #38bdf8;'>AO RNLD US</h1>", unsafe_allow_html=True)

        with st.form("login_form"):
            user = st.text_input("Usuario", placeholder="Tu usuario")
            pas = st.text_input("Contraseña", type="password", placeholder="••••••••")
            if st.form_submit_button("INGRESAR AL SISTEMA", use_container_width=True):
                if user == "admin" and pas == "admin":
                    st.session_state.autenticado = True
                    st.session_state.usuario = user
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas")

def main():
    st.set_page_config(page_title="AO RNLD US - Estampador", page_icon="📐", layout="wide")

    if "autenticado" not in st.session_state or not st.session_state.autenticado:
        vista_login()
        return

    aplicar_estilos_custom()

    st.sidebar.markdown(f"👤 **Usuario:** `{st.session_state.usuario}`")
    if st.sidebar.button("🔴 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.rerun()

    paso_evaluacion = st.sidebar.slider("🎯 Precisión (px):", 5, 30, 10, 5)

    st.title("📐 SISTEMA DE ESTAMPADO Y GUÍAS DE REMISIÓN")
    col1, col2, col3 = st.columns([1.2, 1, 1])

    with col1:
        archivo_pdf = st.file_uploader("1. Selecciona PDF:", type=["pdf"])
        secuencia_gr = st.text_input("2. Secuencia Base:", value="8418-OSP-SG-2026")

    with col2:
        generar_guias_opcion = st.checkbox("📋 Generar Guías (Solo A3)", value=True)
        fecha_obj = st.date_input("3. Fecha:", value=datetime.date.today(), format="DD/MM/YYYY")
        texto_fecha = fecha_obj.strftime("%d/%m/%Y")

    libreria_archivos = obtener_libreria_sellos()
    opciones_sellos = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

    with col3:
        areas_sel = st.multiselect("4. Áreas:", ["SUPERVISION", "CALIDAD", "TOPOGRAFIA", "PRODUCCION"], default=["SUPERVISION", "CALIDAD"], disabled=not generar_guias_opcion)
        sellos_sel = st.multiselect("5. Sellos:", opciones_sellos, default=[opciones_sellos[0]])

    st.divider()

    if archivo_pdf and sellos_sel and st.button("🚀 Estampar PDF y Procesar", use_container_width=True):
        with st.spinner("Procesando planos..."):
            pdf_res, resumen, alertas, es_a4 = procesar_pdf(archivo_pdf.read(), sellos_sel, libreria_archivos, texto_fecha, paso_evaluacion)

            rev_tag = "REV"
            if resumen and resumen[0]["Código de Plano"] != "No detectado":
                rev_num = extraer_numero_revision(resumen[0]["Código de Plano"])
                if rev_num: rev_tag = f"R{rev_num}"

            excels_generados, lista_pdfs_guias = {}, []

            if generar_guias_opcion and not es_a4:
                for idx, area in enumerate(areas_sel):
                    ex_bytes, sec_inc = generar_excel_por_area(resumen, texto_fecha, secuencia_gr, area, idx)
                    if ex_bytes:
                        pdf_ex, _ = convertir_excel_a_pdf(ex_bytes)
                        if pdf_ex: lista_pdfs_guias.append(pdf_ex)
                        excels_generados[area] = {"bytes": ex_bytes, "nombre": f"{sec_inc}_{rev_tag}_{area}.xlsx", "secuencia": sec_inc, "pdf_bytes": pdf_ex}

            st.session_state['pdf_res'] = pdf_res
            st.session_state['resumen'] = resumen
            st.session_state['excels_generados'] = excels_generados
            st.session_state['pdf_nombre'] = f"{secuencia_gr}_{rev_tag}_PLANOS_SELLADOS.pdf"
            st.session_state['es_a4'] = es_a4
            st.session_state['pdf_guias_unificado'] = unificar_pdfs(lista_pdfs_guias) if lista_pdfs_guias else None

    if 'resumen' in st.session_state:
        st.success("¡Procesamiento Completado!")
        if st.session_state.get('es_a4'):
            st.info("ℹ️ Detectado Formato A4: Búsqueda en L invertida (Guías de Remisión desactivadas).")

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button("📄 Descargar PDF Planos Sellados", st.session_state['pdf_res'], st.session_state['pdf_nombre'], "application/pdf", use_container_width=True)
        with col_dl2:
            if st.session_state.get('pdf_guias_unificado'):
                st.download_button("📑 Descargar Consolidado de Guías PDF", st.session_state['pdf_guias_unificado'], "GUIAS_CONSOLIDADAS.pdf", "application/pdf", use_container_width=True)

        st.dataframe(st.session_state['resumen'], use_container_width=True)

if __name__ == "__main__":
    main()