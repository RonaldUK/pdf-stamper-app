import os
import datetime
import streamlit as st

# Importaciones locales desde la raíz
from excel_service import generar_excel_por_area, extraer_numero_revision
from sidebar import render_sidebar

# Importaciones existentes de tus módulos
from pdf_service import procesar_pdf, convertir_excel_a_pdf, unificar_pdfs
from auth_service import inicializar_estado_sesion, obtener_libreria_sellos
from login_view import vista_login
from config import aplicar_estilos_custom, CARPETA_SELLOS, RUTA_LOGO

def main():
    st.set_page_config(page_title="AO RNLD US - Estampador & GR", page_icon="📐", layout="wide")
    inicializar_estado_sesion()

    if not st.session_state.autenticado:
        vista_login()
        return

    aplicar_estilos_custom()

    if os.path.exists(RUTA_LOGO):
        st.sidebar.image(RUTA_LOGO, use_container_width=True)

    paso_evaluacion = render_sidebar(st.session_state.usuario)

    st.title("📐 SISTEMA DE ESTAMPADO Y GUÍAS DE REMISIÓN")
    st.caption("AO RNLD US • INGENIERÍA • DISEÑO • CONSTRUCCIÓN")

    col1, col2, col3 = st.columns([1.2, 1, 1])

    with col1:
        archivo_pdf = st.file_uploader("1. Selecciona tu PDF consolidado:", type=["pdf"])
        secuencia_gr = st.text_input("2. Secuencia Base (ej: 8418-OSP-SG-2026):", value="8418-OSP-SG-2026")

    with col2:
        generar_guias_opcion = st.checkbox("📋 Generar Guías de Remisión (Solo A3)", value=True)
        fecha_obj = st.date_input("3. Fecha de Sellado (J5):", value=datetime.date.today(), format="DD/MM/YYYY")
        texto_fecha = fecha_obj.strftime("%d/%m/%Y")

    libreria_archivos = obtener_libreria_sellos()
    opciones_sellos = ["CC - Copia Controlada (Rojo)", "CI - Copia Informativa (Azul)"] + list(libreria_archivos.keys())

    with col3:
        areas_opciones = ["SUPERVISION", "CALIDAD", "TOPOGRAFIA", "PRODUCCION"]
        areas_seleccionadas = st.multiselect(
            "4. Áreas para Guías:",
            options=areas_opciones,
            default=areas_opciones,
            disabled=not generar_guias_opcion
        )

        sellos_seleccionados = st.multiselect(
            "5. Selecciona Sellos/Firmas:",
            options=opciones_sellos,
            default=[opciones_sellos[0]]
        )

    st.divider()

    if archivo_pdf and sellos_seleccionados:
        if st.button("🚀 Estampar PDF y Procesar Documentos", use_container_width=True):
            with st.spinner("Procesando plano con algoritmos adaptativos..."):
                pdf_res, resumen, alertas_sellos, es_a4 = procesar_pdf(
                    archivo_pdf.read(), 
                    sellos_seleccionados, 
                    libreria_archivos, 
                    texto_fecha,
                    paso=paso_evaluacion
                )

                rev_tag = "REV"
                if resumen and resumen[0]["Código de Plano"] != "No detectado":
                    rev_num = extraer_numero_revision(resumen[0]["Código de Plano"])
                    if rev_num:
                        rev_tag = f"R{rev_num}"

                excels_generados = {}
                lista_pdfs_guias = []
                errores_pdf = []

                if generar_guias_opcion and not es_a4:
                    for idx, area in enumerate(areas_seleccionadas):
                        excel_bytes, secuencia_inc = generar_excel_por_area(
                            resumen_planos=resumen,
                            fecha_texto=texto_fecha,
                            secuencia_base=secuencia_gr,
                            area=area,
                            offset_correlativo=idx
                        )
                        if excel_bytes:
                            nombre_excel = f"{secuencia_inc}_{rev_tag}_{area}.xlsx"
                            pdf_excel, error_msg = convertir_excel_a_pdf(excel_bytes)

                            if pdf_excel:
                                lista_pdfs_guias.append(pdf_excel)
                            elif error_msg:
                                errores_pdf.append(f"{area}: {error_msg}")

                            excels_generados[area] = {
                                "bytes": excel_bytes,
                                "nombre": nombre_excel,
                                "secuencia": secuencia_inc,
                                "pdf_bytes": pdf_excel
                            }

                st.session_state['pdf_res'] = pdf_res
                st.session_state['resumen'] = resumen
                st.session_state['excels_generados'] = excels_generados
                st.session_state['pdf_nombre'] = f"{secuencia_gr}_{rev_tag}_PLANOS_SELLADOS.pdf"
                st.session_state['errores_pdf'] = errores_pdf
                st.session_state['alertas_sellos'] = alertas_sellos
                st.session_state['es_a4'] = es_a4
                st.session_state['guias_activadas'] = generar_guias_opcion

                if lista_pdfs_guias:
                    st.session_state['pdf_guias_unificado'] = unificar_pdfs(lista_pdfs_guias)
                else:
                    st.session_state['pdf_guias_unificado'] = None

    if 'resumen' in st.session_state:
        st.success("¡Planos estampados y procesados con éxito!")

        if st.session_state.get('es_a4'):
            st.info("ℹ️ Se detectó formato A4: La búsqueda se ejecutó en 'L Invertida' y no se generaron Guías de Remisión.")
        elif not st.session_state.get('guias_activadas'):
            st.info("ℹ️ La opción de Guías estuvo desactivada.")

        if st.session_state.get('alertas_sellos'):
            for alert in st.session_state['alertas_sellos']:
                st.warning(alert)

        st.subheader("📥 Descargas Generales")
        col_dl1, col_dl2 = st.columns(2)

        with col_dl1:
            st.download_button(
                "📄 Descargar PDF Planos Sellados", 
                data=st.session_state['pdf_res'], 
                file_name=st.session_state['pdf_nombre'], 
                mime="application/pdf", 
                use_container_width=True
            )

        with col_dl2:
            if st.session_state.get('pdf_guias_unificado'):
                st.download_button(
                    "📑 Descargar PDF Consolidado de Guías", 
                    data=st.session_state['pdf_guias_unificado'], 
                    file_name=f"GUIAS_REMISION_CONSOLIDADAS_{datetime.datetime.now().strftime('%Y%m%d')}.pdf", 
                    mime="application/pdf", 
                    use_container_width=True
                )

        if st.session_state.get('excels_generados'):
            st.divider()
            st.markdown("#### 📊 Archivos Excel y PDF de Guías por Área:")
            cols_excels = st.columns(len(st.session_state['excels_generados']))

            for idx, (area, data_excel) in enumerate(st.session_state['excels_generados'].items()):
                with cols_excels[idx]:
                    st.download_button(
                        label=f"🟢 Excel: {data_excel['secuencia']} ({area})",
                        data=data_excel["bytes"],
                        file_name=data_excel["nombre"],
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"excel_{area}"
                    )
                    if data_excel.get("pdf_bytes"):
                        st.download_button(
                            label=f"🔴 PDF: {data_excel['secuencia']} ({area})",
                            data=data_excel["pdf_bytes"],
                            file_name=f"{data_excel['secuencia']}_{area}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            key=f"pdf_{area}"
                        )

        st.divider()
        st.subheader("📋 Resumen de Planos Detectados")
        st.dataframe(st.session_state['resumen'], use_container_width=True)

if __name__ == "__main__":
    main()