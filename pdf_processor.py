import os
import re
import glob
import tempfile
import fitz
import cv2
import numpy as np

CM_TO_PT = 28.34645669
ANCHO_SELLO_CM = 7.1
ALTO_SELLO_CM = 2.6
CARPETA_SELLOS = "firmas_sellos"

def obtener_libreria_sellos():
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

def limpiar_texto_comillas(texto):
    if not texto:
        return ""
    texto_limpio = re.sub(r'["“"”\']', '', texto).strip()
    return re.sub(r'^\s*[\-\–\—\:]+\s*', '', texto_limpio).strip()

def extraer_codigo_plano(pagina):
    mat_view = pagina.rotation_matrix
    view_rect = pagina.rect * mat_view
    words = pagina.get_text("words")
    words_cajetin = []
    
    for w in words:
        r_nat = fitz.Rect(w[0], w[1], w[2], w[3])
        r_view = r_nat * mat_view
        if r_view.x0 >= view_rect.width * 0.45 and r_view.y0 >= view_rect.height * 0.50:
            words_cajetin.append(w[4])

    texto_roi = " ".join(words_cajetin)
    match_cod = re.search(r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}(?:-R\d+|-REV\d+)?\b', texto_roi)
    if match_cod:
        return match_cod.group(0)

    texto_completo = pagina.get_text("text")
    matches = re.findall(r'\b[A-Z0-9]{2,}(?:-[A-Z0-9]+){2,}(?:-R\d+|-REV\d+)?\b', texto_completo)
    if matches:
        falsos_positivos = ["ESCALA", "FECHA", "PROYECTO", "INDICADA", "PLANO", "REVISION"]
        candidatos = [m.strip() for m in matches if not any(fp in m.upper() for fp in falsos_positivos) and len(m.strip()) > 6]
        if candidatos:
            return candidatos[0]

    return "No detectado"

def extraer_descripcion_plano(pagina, codigo_plano="No detectado"):
    rot = pagina.rotation
    rect_pag = pagina.rect
    ancho_v, alto_v = (rect_pag.height, rect_pag.width) if rot in [90, 270] else (rect_pag.width, rect_pag.height)

    words = pagina.get_text("words")
    mat_directa = pagina.rotation_matrix

    rect_proyecto_vista = None
    for w in words:
        if "PROYECTO" in w[4].upper().strip():
            r_vista = fitz.Rect(w[0], w[1], w[2], w[3]) * mat_directa
            if r_vista.y0 > (alto_v * 0.50):
                rect_proyecto_vista = r_vista
                break

    if rect_proyecto_vista:
        view_box_desc = fitz.Rect(rect_proyecto_vista.x0 - 40, rect_proyecto_vista.y1 + 2, rect_proyecto_vista.x0 + 380, rect_proyecto_vista.y1 + 110)
        texto_celda = pagina.get_text("text", clip=view_box_desc * pagina.derotation_matrix).strip()
        lineas = [limpiar_texto_comillas(l) for l in texto_celda.split('\n') if len(l.strip()) > 3]
        lineas_validas = [l for l in lineas if codigo_plano not in l and not any(k in l.upper() for k in ["ESCALA", "FECHA", "PROYECTO"])]
        if lineas_validas:
            return " - ".join(lineas_validas[:3])

    return "No detectado"

def buscar_posicion_espacio_libre(imagen_gris, ancho_sello, alto_sello, sellos_puestos, es_a4, paso=10):
    _, binaria = cv2.threshold(imagen_gris, 230, 255, cv2.THRESH_BINARY_INV)
    alto_img, ancho_img = binaria.shape
    area_sello = ancho_sello * alto_sello
    candidatos = []

    rango_y = range(alto_img - alto_sello - 30, 30, -paso)
    rango_x = range(30, ancho_img - ancho_sello - 30, paso) if es_a4 else range(ancho_img - ancho_sello - 30, 30, -paso)

    for y in rango_y:
        for x in rango_x:
            if x > (ancho_img * 0.60) and y > (alto_img * 0.70):
                continue
            colision = any(not (x + ancho_sello + 20 < sx or x > sx + sw + 20 or y + alto_sello + 20 < sy or y > sy + sh + 20) for (sx, sy, sw, sh) in sellos_puestos)
            if colision:
                continue

            caja = binaria[y : y + alto_sello, x : x + ancho_sello]
            libre = 1.0 - (cv2.countNonZero(caja) / float(area_sello))
            score = (libre * 100.0) + ((1.0 - (x / float(ancho_img))) * 10.0 if es_a4 else (x / float(ancho_img)) * 5.0)
            candidatos.append((score, x, y))

    if not candidatos:
        return (30, alto_img - alto_sello - 30) if es_a4 else (50, 50)

    candidatos.sort(key=lambda item: item[0], reverse=True)
    return candidatos[0][1], candidatos[0][2]

def agregar_sello_png_dinamico(pagina, view_rect, rect_nativo, nombre_png, color_rgb, texto_fecha, rotacion):
    ruta_png = nombre_png if os.path.exists(nombre_png) else os.path.join(CARPETA_SELLOS, nombre_png)
    if not os.path.exists(ruta_png):
        return False, f"Falta imagen `{nombre_png}`"

    pagina.insert_image(rect_nativo, filename=ruta_png, rotate=rotacion)
    partes = re.split(r'[/.-]', texto_fecha.strip())
    dia, mes, anio = (partes[0].zfill(2), partes[1].zfill(2), partes[2][-2:].zfill(2)) if len(partes) >= 3 else ("", "", "")

    fontname, fontsize = "hebo", 0.50 * CM_TO_PT
    y_vista = view_rect.y0 + (view_rect.height * 0.81)

    for txt, rel_x in [(dia, 0.38), (mes, 0.62), (anio, 0.89)]:
        if txt:
            ancho_txt = fitz.get_text_length(txt, fontname=fontname, fontsize=fontsize)
            pt_nativo = fitz.Point(view_rect.x0 + (view_rect.width * rel_x) - (ancho_txt / 2.0), y_vista) * pagina.derotation_matrix
            pagina.insert_text(pt_nativo, txt, fontsize=fontsize, fontname=fontname, color=color_rgb, rotate=rotacion)

    return True, None

def procesar_pdf(pdf_bytes, sellos_elegidos, libreria_archivos, texto_fecha, paso=10):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        path_pdf = tmp_pdf.name

    doc = fitz.open(path_pdf)
    resumen_planos, alertas_sellos, contiene_a4 = [], [], False
    ancho_sello_pt, alto_sello_pt = ANCHO_SELLO_CM * CM_TO_PT, ALTO_SELLO_CM * CM_TO_PT

    for i in range(len(doc)):
        pagina = doc[i]
        cod_plano = extraer_codigo_plano(pagina)
        titulo_plano = extraer_descripcion_plano(pagina, cod_plano)
        resumen_planos.append({"Hoja": i + 1, "Código de Plano": cod_plano, "Título / Descripción": titulo_plano})

        pixmap = pagina.get_pixmap(dpi=100)
        img_np = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.h, pixmap.w, pixmap.n)
        imagen_gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY) if pixmap.n >= 3 else img_np

        es_a4 = max(imagen_gris.shape) < 1300
        if es_a4:
            contiene_a4 = True

        ancho_px, alto_px = int(ANCHO_SELLO_CM * 39.37), int(ALTO_SELLO_CM * 39.37)
        sellos_puestos = []

        for item in sellos_elegidos:
            x_px, y_px = buscar_posicion_espacio_libre(imagen_gris, ancho_px, alto_px, sellos_puestos, es_a4, paso)
            view_rect = fitz.Rect(x_px * 0.72, y_px * 0.72, (x_px * 0.72) + ancho_sello_pt, (y_px * 0.72) + alto_sello_pt)
            rect_nativo = view_rect * pagina.derotation_matrix

            if item == "CC - Copia Controlada (Rojo)":
                exito, err = agregar_sello_png_dinamico(pagina, view_rect, rect_nativo, "cc_sin_fondo.png", (0.0, 0.20, 0.65), texto_fecha, pagina.rotation)
            elif item == "CI - Copia Informativa (Azul)":
                exito, err = agregar_sello_png_dinamico(pagina, view_rect, rect_nativo, "ci_sin_fondo.png", (0.80, 0.0, 0.0), texto_fecha, pagina.rotation)
            else:
                ruta_img = libreria_archivos.get(item)
                if ruta_img and os.path.exists(ruta_img):
                    pagina.insert_image(rect_nativo, filename=ruta_img, rotate=pagina.rotation)
                    exito, err = True, None
                else:
                    exito, err = False, f"Sin imagen `{item}`"

            if exito:
                sellos_puestos.append((x_px, y_px, ancho_px, alto_px))
            elif err:
                alertas_sellos.append(err)

    out_path = path_pdf.replace(".pdf", "_SELLADO.pdf")
    doc.save(out_path)
    doc.close()

    with open(out_path, "rb") as f:
        res_bytes = f.read()

    os.remove(path_pdf)
    os.remove(out_path)
    return res_bytes, resumen_planos, alertas_sellos, contiene_a4